from __future__ import annotations

import struct
import unittest
from typing import cast

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.ratchet_tod.level_collision import (
    decode_level_collision_resource,
)
from insomniac_asset_io.games.ratchet_tod.reader import RatchetTodReader

YARD_TO_METERS = 0.9144
RECORDS_OFFSET = 0x80


def _face_word(surface: int, a: int, b: int, c: int) -> int:
    return (surface << 27) | (a << 18) | (b << 9) | c


def _triangle_prototype(*, no_op: bool = False) -> bytes:
    face_offset = 0xA0
    total_data_length = 0xB0
    data = bytearray(0xD0)
    struct.pack_into(
        ">8H",
        data,
        0,
        4,
        0x60,
        1,
        4,
        face_offset,
        0,
        total_data_length,
        0,
    )
    struct.pack_into(">3iI", data, 0x60, 0, 0, 0, 0)
    struct.pack_into(
        ">8H",
        data,
        0x70,
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
        0x80,
        0,
        0,
        1024,
        512,
        0,
        0,
        0,
        0,
    )
    faces = (
        (
            _face_word(7, 0, 1, 1),
            _face_word(7, 1, 2, 2),
            _face_word(7, 2, 3, 3),
            _face_word(7, 3, 0, 0),
        )
        if no_op
        else (
            _face_word(7, 0, 1, 2),
            _face_word(7, 0, 2, 3),
            _face_word(7, 2, 1, 0),
            _face_word(7, 0, 1, 1),
        )
    )
    struct.pack_into(">4I", data, face_offset, *faces)
    return bytes(data)


def _collision_file(
    prototypes: list[bytes],
    records: list[dict],
) -> tuple[bytes, dict[str, int | list[int]]]:
    for prototype in prototypes:
        if len(prototype) % 0x10:
            raise AssertionError("prototype fixture must be aligned")

    prototypes_offset = RECORDS_OFFSET + len(records) * 0x80
    prototype_offsets: list[int] = []
    prototype_blob = bytearray()
    for prototype in prototypes:
        prototype_offsets.append(len(prototype_blob))
        prototype_blob.extend(prototype)

    prototypes_end = prototypes_offset + len(prototype_blob)
    data = bytearray(prototypes_end + 0x40)
    struct.pack_into(
        ">4I",
        data,
        0,
        RECORDS_OFFSET,
        len(records),
        prototypes_offset,
        len(prototype_blob),
    )
    struct.pack_into(">I", data, 0x30, prototypes_end)
    struct.pack_into(">I", data, 0x38, 0x47)
    struct.pack_into(">I", data, 0x3C, prototypes_end + 0x20)
    data[prototypes_offset:prototypes_end] = prototype_blob

    for index, values in enumerate(records):
        record = RECORDS_OFFSET + index * 0x80
        prototype_index = values["prototype"]
        prototype = prototypes[prototype_index]
        matrix = np.asarray(
            values.get("matrix", np.eye(4)),
            dtype=np.float32,
        )
        stored = np.zeros((3, 4), dtype=np.float32)
        stored[:, :3] = matrix[:3, :3].T
        struct.pack_into(">12f", data, record, *stored.ravel())
        struct.pack_into(
            ">4f",
            data,
            record + 0x30,
            *matrix[:3, 3],
            1.0,
        )
        minimum = np.asarray(values["minimum"], dtype=np.float32)
        maximum = np.asarray(values["maximum"], dtype=np.float32)
        center = (minimum + maximum) * 0.5
        radius = float(np.linalg.norm(maximum - center))
        struct.pack_into(">4f", data, record + 0x40, *center, radius)
        struct.pack_into(">3f", data, record + 0x50, *minimum)
        struct.pack_into(">3f", data, record + 0x60, *maximum)
        flags = values.get("flags", 0)
        component_index = values.get("component_index", 0)
        struct.pack_into(
            ">IIHHI",
            data,
            record + 0x70,
            (flags << 24) | component_index,
            prototype_offsets[prototype_index],
            values.get("group", 0x6E),
            len(prototype),
            0,
        )

    return bytes(data), {
        "prototypes": prototypes_offset,
        "prototype_offsets": prototype_offsets,
        "records": RECORDS_OFFSET,
    }


def _shared_fixture() -> tuple[bytes, dict[str, int | list[int]]]:
    first_matrix = np.eye(4, dtype=np.float32)
    first_matrix[:3, 3] = (10.0, 20.0, 30.0)
    second_matrix = np.eye(4, dtype=np.float32)
    second_matrix[0, 0] = 2.0
    second_matrix[:3, 3] = (-5.0, 0.0, 2.0)
    return _collision_file(
        [_triangle_prototype(), _triangle_prototype()],
        [
            {
                "prototype": 0,
                "minimum": (10.0, 20.0, 30.0),
                "maximum": (11.0, 21.0, 30.0),
                "flags": 0x20,
                "component_index": 3,
                "matrix": first_matrix,
            },
            {
                "prototype": 0,
                "minimum": (-5.0, 0.0, 2.0),
                "maximum": (-3.0, 1.0, 2.0),
                "flags": 0x20,
                "component_index": 4,
                "matrix": second_matrix,
            },
            {
                "prototype": 1,
                "minimum": (0.0, 0.0, 0.0),
                "maximum": (1.0, 1.0, 0.0),
            },
        ],
    )


class RatchetTodLevelCollisionTests(unittest.TestCase):
    def test_decodes_world_and_shared_tie_collision(self) -> None:
        data, _offsets = _shared_fixture()

        scene = decode_level_collision_resource(
            data,
            resource_path="built/levels/test/collision.dat",
        )
        validate_collision_scene(scene, label="TOD collision fixture")

        self.assertEqual(len(scene.meshes), 2)
        self.assertEqual(len(scene.instances), 3)
        self.assertEqual(
            scene.instances[0].mesh_id,
            scene.instances[1].mesh_id,
        )
        np.testing.assert_allclose(
            scene.instances[0].matrix[:3, 3],
            np.asarray((10.0, 20.0, 30.0)) * YARD_TO_METERS,
        )
        np.testing.assert_allclose(
            scene.instances[1].matrix[:3, :3],
            np.diag((2.0, 1.0, 1.0)),
        )
        self.assertEqual(
            scene.instances[0].metadata["component_index"],
            3,
        )
        self.assertEqual(
            scene.instances[2].source_group,
            "Terrain - test",
        )
        np.testing.assert_allclose(
            scene.meshes[0].positions.max(axis=0),
            (YARD_TO_METERS, YARD_TO_METERS, 0.0),
        )
        np.testing.assert_array_equal(scene.meshes[0].surface_ids, (7,))
        self.assertEqual(
            scene.meshes[0].metadata["discarded_degenerate_faces"],
            2,
        )
        self.assertEqual(
            scene.meshes[0].metadata["discarded_duplicate_faces"],
            1,
        )

    def test_skips_all_degenerate_prototypes(self) -> None:
        data, _offsets = _collision_file(
            [_triangle_prototype(no_op=True), _triangle_prototype()],
            [
                {
                    "prototype": 0,
                    "minimum": (0.0, 0.0, 0.0),
                    "maximum": (1.0, 1.0, 0.0),
                },
                {
                    "prototype": 1,
                    "minimum": (0.0, 0.0, 0.0),
                    "maximum": (1.0, 1.0, 0.0),
                },
            ],
        )

        warnings: list[str] = []
        scene = decode_level_collision_resource(
            data,
            resource_path="built/levels/test/collision.dat",
            warnings=warnings,
        )

        self.assertEqual(len(scene.meshes), 1)
        self.assertEqual(len(scene.instances), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 no-op collision prototypes", warnings[0])

    def test_rejects_malformed_records_and_prototypes(self) -> None:
        valid, offsets = _shared_fixture()
        prototypes = int(offsets["prototypes"])
        prototype_offsets = cast(
            list[int],
            offsets["prototype_offsets"],
        )
        cases: list[tuple[str, bytearray, str]] = []

        wrong_blob = bytearray(valid)
        struct.pack_into(">I", wrong_blob, 0x08, 0x1230)
        cases.append(
            ("blob offset", wrong_blob, "prototype blob begins")
        )

        wrong_end = bytearray(valid)
        struct.pack_into(">I", wrong_end, 0x30, 0x1230)
        cases.append(("blob end", wrong_end, "prototype end"))

        bad_face = bytearray(valid)
        struct.pack_into(
            ">I",
            bad_face,
            prototypes + prototype_offsets[0] + 0xA0,
            _face_word(7, 0, 1, 4),
        )
        cases.append(("face index", bad_face, "collision face index"))

        bad_marker = bytearray(valid)
        struct.pack_into(
            ">H",
            bad_marker,
            prototypes + prototype_offsets[0] + 0x04,
            2,
        )
        cases.append(
            ("marker", bad_marker, "quantization marker")
        )

        bad_transform = bytearray(valid)
        struct.pack_into(
            ">f",
            bad_transform,
            RECORDS_OFFSET,
            float("nan"),
        )
        cases.append(("transform", bad_transform, "non-finite values"))

        bad_translation_w = bytearray(valid)
        struct.pack_into(
            ">f",
            bad_translation_w,
            RECORDS_OFFSET + 0x3C,
            0.0,
        )
        cases.append(
            ("translation W", bad_translation_w, "translation W")
        )

        inverted_bounds = bytearray(valid)
        struct.pack_into(
            ">3f",
            inverted_bounds,
            RECORDS_OFFSET + 0x50,
            12.0,
            20.0,
            30.0,
        )
        cases.append(("bounds", inverted_bounds, "inverted AABB"))

        bad_flags = bytearray(valid)
        struct.pack_into(
            ">I",
            bad_flags,
            RECORDS_OFFSET + 0x70,
            0x2100_0000,
        )
        cases.append(("flags", bad_flags, "unsupported flags"))

        world_component = bytearray(valid)
        struct.pack_into(
            ">I",
            world_component,
            RECORDS_OFFSET + 2 * 0x80 + 0x70,
            1,
        )
        cases.append(
            (
                "world component",
                world_component,
                "unexpected component index",
            )
        )

        conflicting_extent = bytearray(valid)
        struct.pack_into(
            ">H",
            conflicting_extent,
            RECORDS_OFFSET + 0x80 + 0x7A,
            len(_triangle_prototype()) - 0x10,
        )
        cases.append(
            (
                "prototype extent",
                conflicting_extent,
                "conflicting extents",
            )
        )

        for name, data, message in cases:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(FormatError, message),
            ):
                decode_level_collision_resource(
                    data,
                    resource_path="built/levels/test/collision.dat",
                )

    def test_reader_uses_raw_tod_collision_resource(self) -> None:
        data, _offsets = _shared_fixture()

        class Source:
            paths = (
                "built/levels/test/main.dat",
                "built/levels/test/collision.dat",
            )

            @staticmethod
            def read(path: str) -> bytes:
                if path.endswith("collision.dat"):
                    return data
                raise AssertionError(f"unexpected read {path}")

        reader = object.__new__(RatchetTodReader)
        reader.source = Source()
        reader.warnings = []

        scene = reader.read_level_collision()

        self.assertEqual(len(scene.meshes), 2)
        self.assertEqual(len(scene.instances), 3)
        validate_collision_scene(scene, label="TOD reader fixture")


if __name__ == "__main__":
    unittest.main()
