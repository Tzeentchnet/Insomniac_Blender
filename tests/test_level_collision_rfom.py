from __future__ import annotations

import struct
import unittest
from typing import cast

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.registry import get_game_adapter
from insomniac_asset_io.games.resistance_fom.level_collision import (
    decode_level_collision_resource,
)
from insomniac_asset_io.games.resistance_fom.reader import (
    ResistanceFomReader,
)

YARD_TO_METERS = 0.9144
RECORDS_OFFSET = 0x80


def _face_word(surface: int, a: int, b: int, c: int) -> int:
    return (surface << 27) | (a << 18) | (b << 9) | c


def _triangle_prototype(*, no_op: bool = False) -> bytes:
    face_offset = 0x80
    total_data_length = 0x90
    data = bytearray(0xB0)
    struct.pack_into(
        ">8H",
        data,
        0,
        4,
        0x50,
        1,
        4,
        face_offset,
        0xFFFF,
        total_data_length,
        0,
    )
    struct.pack_into(">3iI", data, 0x50, 0, 0, 0, 0)
    struct.pack_into(
        ">12H",
        data,
        0x60,
        0,
        0,
        0,
        1024,
        0,
        0,
        0,
        1024,
        0,
        0,
        512,
        0,
    )
    faces = (
        (
            _face_word(4, 0, 1, 1),
            _face_word(4, 1, 2, 2),
            _face_word(4, 2, 3, 3),
            _face_word(4, 3, 0, 0),
        )
        if no_op
        else (
            _face_word(4, 0, 1, 2),
            _face_word(4, 0, 2, 3),
            _face_word(4, 2, 1, 0),
            _face_word(4, 0, 1, 1),
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
    struct.pack_into(">2f", data, 0x20, 16.0, 1.0 / 16.0)
    struct.pack_into(">I", data, 0x30, prototypes_end)
    struct.pack_into(">I", data, 0x38, 0x75)
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
        family = values.get("family", 0)
        source_index = values.get("source_index", 0)
        component_index = values.get("component_index", 0)
        struct.pack_into(
            ">IIHHI",
            data,
            record + 0x70,
            (
                (family << 28)
                | (source_index << 12)
                | component_index
            ),
            prototype_offsets[prototype_index],
            values.get("group", 0x2A),
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
    placed_matrix = np.eye(4, dtype=np.float32)
    placed_matrix[:3, 3] = (4.0, 5.0, 6.0)
    return _collision_file(
        [
            _triangle_prototype(),
            _triangle_prototype(),
            _triangle_prototype(),
        ],
        [
            {
                "prototype": 0,
                "minimum": (10.0, 20.0, 30.0),
                "maximum": (11.0, 21.0, 30.0),
                "family": 0x2,
                "source_index": 5,
                "component_index": 3,
                "matrix": first_matrix,
            },
            {
                "prototype": 0,
                "minimum": (-5.0, 0.0, 2.0),
                "maximum": (-3.0, 1.0, 2.0),
                "family": 0x2,
                "source_index": 6,
                "component_index": 4,
                "matrix": second_matrix,
            },
            {
                "prototype": 1,
                "minimum": (0.0, 0.0, 0.0),
                "maximum": (1.0, 1.0, 0.0),
            },
            {
                "prototype": 2,
                "minimum": (4.0, 5.0, 6.0),
                "maximum": (5.0, 6.0, 6.0),
                "family": 0x3,
                "source_index": 7,
                "matrix": placed_matrix,
            },
        ],
    )


class ResistanceFomLevelCollisionTests(unittest.TestCase):
    def test_decodes_world_tie_and_placed_collision(self) -> None:
        data, _offsets = _shared_fixture()

        scene = decode_level_collision_resource(
            data,
            resource_path="packed/levels/test/ps3levelcoll.dat",
        )
        validate_collision_scene(scene, label="RFoM collision fixture")

        self.assertEqual(len(scene.meshes), 3)
        self.assertEqual(len(scene.instances), 4)
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
            scene.instances[0].metadata["tie_instance_index"],
            5,
        )
        self.assertEqual(
            scene.instances[2].source_group,
            "Terrain - test",
        )
        self.assertEqual(
            scene.instances[3].source_group,
            "Placed Collision - test",
        )
        np.testing.assert_allclose(
            scene.meshes[0].positions.max(axis=0),
            (YARD_TO_METERS, YARD_TO_METERS, 0.0),
        )
        np.testing.assert_array_equal(scene.meshes[0].surface_ids, (4,))
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
            resource_path="packed/levels/test/ps3levelcoll.dat",
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

        wrong_marker = bytearray(valid)
        struct.pack_into(">I", wrong_marker, 0x38, 0x47)
        cases.append(
            ("header marker", wrong_marker, "header marker")
        )

        bad_face = bytearray(valid)
        struct.pack_into(
            ">I",
            bad_face,
            prototypes + prototype_offsets[0] + 0x80,
            _face_word(4, 0, 1, 4),
        )
        cases.append(("face index", bad_face, "collision face index"))

        bad_sentinel = bytearray(valid)
        struct.pack_into(
            ">H",
            bad_sentinel,
            prototypes + prototype_offsets[0] + 0x0A,
            0,
        )
        cases.append(
            ("sentinel", bad_sentinel, "header sentinel")
        )

        bad_transform = bytearray(valid)
        struct.pack_into(
            ">f",
            bad_transform,
            RECORDS_OFFSET,
            float("nan"),
        )
        cases.append(("transform", bad_transform, "non-finite values"))

        transformed_world = bytearray(valid)
        struct.pack_into(
            ">f",
            transformed_world,
            RECORDS_OFFSET + 2 * 0x80,
            2.0,
        )
        cases.append(
            (
                "world transform",
                transformed_world,
                "non-identity transform",
            )
        )

        bad_family = bytearray(valid)
        struct.pack_into(
            ">I",
            bad_family,
            RECORDS_OFFSET + 0x70,
            0x1000_0000,
        )
        cases.append(("family", bad_family, "unsupported family"))

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
                    resource_path=(
                        "packed/levels/test/ps3levelcoll.dat"
                    ),
                )

    def test_reader_and_iso_configuration_use_rfom_resource(self) -> None:
        data, _offsets = _shared_fixture()

        class Source:
            paths = (
                "packed/levels/test/ps3levelmain.dat",
                "packed/levels/test/ps3levelcoll.dat",
            )

            @staticmethod
            def read(path: str) -> bytes:
                if path.endswith("ps3levelcoll.dat"):
                    return data
                raise AssertionError(f"unexpected read {path}")

        reader = object.__new__(ResistanceFomReader)
        reader.source = Source()
        reader.warnings = []

        scene = reader.read_level_collision()

        self.assertEqual(len(scene.meshes), 3)
        self.assertEqual(len(scene.instances), 4)
        self.assertIn(
            "ps3levelcoll.dat",
            get_game_adapter("RESISTANCE_FOM").level_archives,
        )
        validate_collision_scene(scene, label="RFoM reader fixture")


if __name__ == "__main__":
    unittest.main()
