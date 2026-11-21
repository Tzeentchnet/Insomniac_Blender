from __future__ import annotations

import math
import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.resistance_2.level_collision import (
    decode_level_collision_resource,
)
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader

TIE_TUID = 0x1122_3344_5566_7788
SECTION_OFFSET = 0x40
TUID_TABLE_LENGTH = 0x80
RECORDS_RELATIVE = 0x100


def _triangle_prototype() -> bytes:
    data = bytearray(0x80)
    struct.pack_into(
        ">6H",
        data,
        0,
        4,
        16,
        256,
        3,
        0x50,
        0,
    )
    struct.pack_into(">H", data, 0x0C, 0x60)
    struct.pack_into(">3i", data, 0x10, 0, 0, 0)
    struct.pack_into(
        ">8H",
        data,
        0x20,
        0,
        1024,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    struct.pack_into(
        ">8H",
        data,
        0x30,
        0,
        0,
        1024,
        0,
        512,
        0,
        0,
        0,
    )
    struct.pack_into(
        ">3I",
        data,
        0x50,
        (7 << 27) | (1 << 9) | 2,
        (7 << 27) | (1 << 9) | 3,
        (7 << 27) | (1 << 9) | 2,
    )
    return bytes(data)


def _primitive_prototype(
    shapes: dict[int, list[tuple[int, tuple[float, ...]]]],
) -> bytes:
    kinds = (4, 5, 6, 8)
    batch_sizes = {4: 0x130, 5: 0x80, 6: 0xB0, 8: 0xB0}
    value_counts = {4: 15, 5: 4, 6: 7, 8: 7}
    counts = tuple(len(shapes.get(kind, ())) for kind in kinds)
    extent = 0x30 + sum(
        math.ceil(count / 4) * batch_sizes[kind]
        for kind, count in zip(kinds, counts, strict=True)
    )
    data = bytearray(extent)
    struct.pack_into(">HH", data, 0, 0x20, sum(counts))
    struct.pack_into(">4B", data, 0x10, *counts)
    cursor = 0x20
    for kind, count in zip(kinds, counts, strict=True):
        kind_shapes = shapes.get(kind, ())
        for batch_start in range(0, count, 4):
            batch = kind_shapes[batch_start : batch_start + 4]
            for lane, (surface_id, values) in enumerate(batch):
                if len(values) != value_counts[kind]:
                    raise AssertionError("invalid primitive fixture values")
                struct.pack_into(
                    ">4B",
                    data,
                    cursor + lane * 0x10,
                    kind,
                    surface_id,
                    0,
                    0xFF,
                )
                for value_index, value in enumerate(values):
                    struct.pack_into(
                        ">f",
                        data,
                        cursor + 0x40 + value_index * 0x10 + lane * 4,
                        value,
                    )
            cursor += batch_sizes[kind]
    return bytes(data)


def _box_values(
    center: tuple[float, float, float],
    half_extents: tuple[float, float, float],
) -> tuple[float, ...]:
    return (
        *center,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        *half_extents,
    )


def _collision_file(
    prototypes: list[bytes],
    records: list[dict],
) -> tuple[bytes, dict[str, int]]:
    for prototype in prototypes:
        if len(prototype) % 0x10:
            raise AssertionError("prototype fixture must be aligned")
    prototype_relative = RECORDS_RELATIVE + len(records) * 0x80
    prototype_offsets: list[int] = []
    prototype_blob = bytearray()
    for prototype in prototypes:
        prototype_offsets.append(len(prototype_blob))
        prototype_blob.extend(prototype)

    section_length = prototype_relative + len(prototype_blob) + 0x40
    data = bytearray(SECTION_OFFSET + section_length)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(
        ">4I",
        data,
        0x20,
        0x2900,
        SECTION_OFFSET,
        1,
        section_length,
    )
    base = SECTION_OFFSET
    struct.pack_into(
        ">4I",
        data,
        base,
        TUID_TABLE_LENGTH,
        len(records) + 1,
        prototype_relative,
        len(prototype_blob),
    )
    struct.pack_into(
        ">I",
        data,
        base + 0x30,
        prototype_relative + len(prototype_blob) + 0x40,
    )
    struct.pack_into(">Q", data, base + 0x80, TIE_TUID)
    data[
        base + prototype_relative :
        base + prototype_relative + len(prototype_blob)
    ] = prototype_blob

    for index, record_values in enumerate(records):
        record = base + RECORDS_RELATIVE + index * 0x80
        prototype_index = record_values["prototype"]
        prototype = prototypes[prototype_index]
        minimum = record_values["minimum"]
        maximum = record_values["maximum"]
        flags = record_values.get("flags", 0)
        asset_index = record_values.get("asset_index", 0)
        matrix = np.asarray(
            record_values.get("matrix", np.eye(4)),
            dtype=np.float32,
        )
        struct.pack_into(">3f", data, record, *minimum)
        struct.pack_into(">3f", data, record + 0x10, *maximum)
        struct.pack_into(
            ">IHHI",
            data,
            record + 0x20,
            prototype_offsets[prototype_index],
            len(prototype) - 0x10,
            record_values.get("group", 0x12A),
            (flags << 24) | asset_index,
        )
        if flags == 0x20:
            stored = np.zeros((3, 4), dtype=np.float32)
            stored[:, :3] = matrix[:3, :3].T
            struct.pack_into(">12f", data, record + 0x30, *stored.ravel())
            struct.pack_into(">3f", data, record + 0x60, *matrix[:3, 3])
        center = (np.asarray(minimum) + np.asarray(maximum)) * 0.5
        radius = float(np.linalg.norm(np.asarray(maximum) - center))
        struct.pack_into(">4f", data, record + 0x70, *center, radius)

    return bytes(data), {
        "base": base,
        "prototype": base + prototype_relative,
        "records": base + RECORDS_RELATIVE,
    }


def _shared_fixture() -> tuple[bytes, dict[str, int]]:
    box = _primitive_prototype(
        {
            4: [
                (
                    3,
                    _box_values(
                        (2.0, 3.0, 4.0),
                        (0.5, 1.0, 1.5),
                    ),
                )
            ]
        }
    )
    first_matrix = np.eye(4, dtype=np.float32)
    first_matrix[:3, 3] = (10.0, 20.0, 30.0)
    second_matrix = np.eye(4, dtype=np.float32)
    second_matrix[:3, 3] = (-5.0, 0.0, 2.0)
    return _collision_file(
        [_triangle_prototype(), box],
        [
            {
                "prototype": 0,
                "minimum": (10.0, 20.0, 30.0),
                "maximum": (11.0, 21.0, 30.0),
                "flags": 0x20,
                "matrix": first_matrix,
            },
            {
                "prototype": 0,
                "minimum": (-5.0, 0.0, 2.0),
                "maximum": (-4.0, 1.0, 2.0),
                "flags": 0x20,
                "matrix": second_matrix,
            },
            {
                "prototype": 1,
                "minimum": (1.5, 2.0, 2.5),
                "maximum": (2.5, 4.0, 5.5),
            },
        ],
    )


class Resistance23LevelCollisionTests(unittest.TestCase):
    def test_decodes_world_and_shared_tie_collision(self) -> None:
        data, _offsets = _shared_fixture()

        scene = decode_level_collision_resource(
            IghwFile(data, label="collision fixture"),
            resource_path="built/levels/test/a/collision.dat",
        )
        validate_collision_scene(scene, label="collision fixture")

        self.assertEqual(len(scene.meshes), 2)
        self.assertEqual(len(scene.instances), 3)
        self.assertEqual(
            scene.instances[0].mesh_id,
            scene.instances[1].mesh_id,
        )
        np.testing.assert_array_equal(
            scene.instances[0].matrix[:3, 3],
            (10.0, 20.0, 30.0),
        )
        self.assertEqual(scene.instances[0].metadata["asset_tuid"], TIE_TUID)
        self.assertEqual(scene.instances[2].source_group, "Terrain - a")
        np.testing.assert_array_equal(
            scene.meshes[0].surface_ids,
            (7,),
        )
        self.assertEqual(
            scene.meshes[0].metadata["discarded_degenerate_faces"],
            1,
        )
        self.assertEqual(
            scene.meshes[0].metadata["discarded_duplicate_faces"],
            1,
        )
        self.assertTrue(np.all(scene.meshes[1].surface_ids == 3))
        np.testing.assert_allclose(
            scene.meshes[1].positions.min(axis=0),
            (1.5, 2.0, 2.5),
        )
        np.testing.assert_allclose(
            scene.meshes[1].positions.max(axis=0),
            (2.5, 4.0, 5.5),
        )

    def test_decodes_all_primitive_families(self) -> None:
        primitive = _primitive_prototype(
            {
                4: [(1, _box_values((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)))],
                5: [(2, (4.0, 0.0, 0.0, 1.0))],
                6: [(3, (0.0, 4.0, 0.0, 0.0, 6.0, 0.0, 0.5))],
                8: [(4, (-4.0, 0.0, 0.0, -4.0, 0.0, 2.0, 0.75))],
            }
        )
        data, _offsets = _collision_file(
            [primitive],
            [
                {
                    "prototype": 0,
                    "minimum": (-5.0, -2.0, -3.0),
                    "maximum": (5.0, 6.5, 3.0),
                }
            ],
        )

        scene = decode_level_collision_resource(
            IghwFile(data, label="primitive fixture"),
            resource_path="built/levels/test/primitives/collision.dat",
        )
        validate_collision_scene(scene, label="primitive fixture")

        mesh = scene.meshes[0]
        self.assertEqual(mesh.metadata["box_count"], 1)
        self.assertEqual(mesh.metadata["sphere_count"], 1)
        self.assertEqual(mesh.metadata["capsule_count"], 1)
        self.assertEqual(mesh.metadata["cylinder_count"], 1)
        self.assertEqual(set(mesh.surface_ids.tolist()), {1, 2, 3, 4})
        self.assertTrue(np.isfinite(mesh.positions).all())
        self.assertGreater(len(mesh.positions), 100)
        self.assertGreater(len(mesh.indices), 200)

    def test_skips_no_op_degenerate_prototypes(self) -> None:
        valid, offsets = _shared_fixture()
        data = bytearray(valid)
        degenerate = (7 << 27) | (1 << 9) | 3
        struct.pack_into(
            ">3I",
            data,
            offsets["prototype"] + 0x50,
            degenerate,
            degenerate,
            degenerate,
        )

        warnings: list[str] = []
        scene = decode_level_collision_resource(
            IghwFile(data, label="no-op fixture"),
            resource_path="built/levels/test/a/collision.dat",
            warnings=warnings,
        )

        self.assertEqual(len(scene.meshes), 1)
        self.assertEqual(len(scene.instances), 1)
        self.assertEqual(scene.meshes[0].metadata["prototype_type"], "PRIMITIVES")
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 no-op collision prototypes", warnings[0])

    def test_rejects_malformed_records_and_prototypes(self) -> None:
        valid, offsets = _shared_fixture()
        cases: list[tuple[str, bytearray, str]] = []

        wrong_blob = bytearray(valid)
        struct.pack_into(
            ">I",
            wrong_blob,
            offsets["base"] + 0x08,
            0x1230,
        )
        cases.append(("blob offset", wrong_blob, "prototype blob begins"))

        bad_face = bytearray(valid)
        struct.pack_into(
            ">I",
            bad_face,
            offsets["prototype"] + 0x50,
            (7 << 27) | (1 << 9) | 4,
        )
        cases.append(("face index", bad_face, "collision face index"))

        bad_transform = bytearray(valid)
        struct.pack_into(
            ">f",
            bad_transform,
            offsets["records"] + 0x30,
            float("nan"),
        )
        cases.append(("transform", bad_transform, "non-finite transform"))

        inverted_bounds = bytearray(valid)
        struct.pack_into(
            ">3f",
            inverted_bounds,
            offsets["records"],
            12.0,
            20.0,
            30.0,
        )
        cases.append(("bounds", inverted_bounds, "inverted AABB"))

        bad_flags = bytearray(valid)
        packed = struct.unpack_from(
            ">I",
            bad_flags,
            offsets["records"] + 0x28,
        )[0]
        struct.pack_into(
            ">I",
            bad_flags,
            offsets["records"] + 0x28,
            (0x21 << 24) | (packed & 0x00FF_FFFF),
        )
        cases.append(("flags", bad_flags, "unsupported flags"))

        for name, data, message in cases:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(FormatError, message),
            ):
                decode_level_collision_resource(
                    IghwFile(data, label=f"{name} fixture"),
                    resource_path="built/levels/test/a/collision.dat",
                )

    def test_reader_merges_all_level_collision_resources(self) -> None:
        data, _offsets = _shared_fixture()

        class Source:
            paths = (
                "built/levels/test/b/collision.dat",
                "built/levels/test/assetlookup.dat",
                "built/levels/test/a/collision.dat",
            )

            @staticmethod
            def read(_path: str) -> bytes:
                return data

        reader = object.__new__(Resistance2Reader)
        reader.source = Source()
        reader.resource_prefix = "built/levels/test"
        reader.warnings = []

        scene = reader.read_level_collision()

        self.assertEqual(len(scene.meshes), 4)
        self.assertEqual(len(scene.instances), 6)
        self.assertTrue(scene.meshes[0].mesh_id.startswith("r23:built/levels/test/a"))
        validate_collision_scene(scene, label="merged fixture")


if __name__ == "__main__":
    unittest.main()
