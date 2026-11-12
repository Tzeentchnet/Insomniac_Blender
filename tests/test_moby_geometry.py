from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


def _single_section_file(section_id: int, payload: bytes) -> IghwFile:
    offset = 0x40
    data = bytearray(offset + len(payload))
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(
        ">IIII",
        data,
        0x20,
        section_id,
        offset,
        1,
        len(payload),
    )
    data[offset:] = payload
    return IghwFile(data, label="collision fixture")


def _moby_fixture() -> IghwFile:
    data = bytearray(0x400)
    sections = (
        (0xD100, 0x100, 1, 0xC0),
        (0xD200, 0x1C0, 1, 0x10),
        (0xE100, 0x200, 6, 0x0C),
        (0xE200, 0x240, 6, 0x78),
    )
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, section in enumerate(sections):
        struct.pack_into(">IIII", data, 0x20 + index * 0x10, *section)

    header = 0x100
    struct.pack_into(">H", data, header + 0x18, 2)
    struct.pack_into(">I", data, header + 0x24, 0x300)
    struct.pack_into(">f", data, header + 0x70, 1.0)
    struct.pack_into(">I", data, header + 0x90, 1)
    struct.pack_into(">Q", data, header + 0x98, 1)
    struct.pack_into(">Q", data, header + 0xB0, 0x1234)
    data[0x1C0:0x1CC] = b"fixture.irb\0"
    struct.pack_into(">6H", data, 0x200, 0, 1, 2, 0, 1, 2)

    for segment_index in range(2):
        mesh_table = 0x320 + segment_index * 0x40
        struct.pack_into(
            ">II",
            data,
            0x300 + segment_index * 8,
            mesh_table,
            1,
        )
        struct.pack_into(
            ">IIHHBB",
            data,
            mesh_table,
            segment_index * 3,
            segment_index * 3 * 0x14,
            0,
            3,
            0,
            0,
        )
        struct.pack_into(">I", data, mesh_table + 0x10, 3)
        for vertex_index, position in enumerate(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        ):
            vertex = (
                0x240
                + segment_index * 3 * 0x14
                + vertex_index * 0x14
            )
            struct.pack_into(">3h", data, vertex, *position)
            struct.pack_into(">2e", data, vertex + 0x08, 0.0, 0.0)
    return IghwFile(data, label="moby fixture")


class MobyGeometryTests(unittest.TestCase):
    def test_classifies_segments_outside_default_draw_as_damage(self):
        reader = Resistance2Reader.__new__(Resistance2Reader)
        asset = reader._parse_moby(_moby_fixture(), 0)

        self.assertEqual([mesh.segment_index for mesh in asset.meshes], [0, 1])
        self.assertEqual(
            [mesh.is_damage_permutation for mesh in asset.meshes],
            [False, True],
        )

    def test_decodes_collision_primitives(self):
        payload = bytearray(0x90 + 2 * 0x50)
        struct.pack_into(">HH", payload, 0x80, 0x10, 2)
        box = (
            (1.0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 2.0),
            (0.0, 0.0, 1.0, 3.0),
            (4.0, 5.0, 6.0, 1.0),
        )
        payload[0x90] = 4
        payload[0x93] = 3
        struct.pack_into(">16f", payload, 0xA0, *np.asarray(box).ravel())
        payload[0xE0] = 5
        payload[0xE3] = 0xFF
        struct.pack_into(
            ">16f",
            payload,
            0xF0,
            *np.eye(4, dtype=np.float32).ravel(),
        )
        parsed = _single_section_file(0xD900, payload)

        primitives = Resistance2Reader._read_collision_primitives(
            parsed,
            0x40,
        )

        self.assertEqual([primitive.kind for primitive in primitives], [4, 5])
        self.assertEqual(primitives[0].bone_index, 3)
        self.assertIsNone(primitives[1].bone_index)
        np.testing.assert_array_equal(primitives[0].data, box)

    def test_decodes_quantized_collision_mesh(self):
        payload = bytearray(0xE0)
        struct.pack_into(
            ">6H",
            payload,
            0x80,
            8,
            16,
            256,
            2,
            0x50,
            0,
        )
        struct.pack_into(">H", payload, 0x8C, 0x60)
        struct.pack_into(">3i", payload, 0x90, -1024, 2048, 0)
        struct.pack_into(">8H", payload, 0xA0, *range(8))
        struct.pack_into(">8H", payload, 0xB0, *(value * 2 for value in range(8)))
        struct.pack_into(">8H", payload, 0xC0, *(value * 3 for value in range(8)))
        struct.pack_into(
            ">2I",
            payload,
            0xD0,
            (4 << 27) | (0 << 18) | (1 << 9) | 2,
            (7 << 27) | (5 << 18) | (6 << 9) | 7,
        )
        parsed = _single_section_file(0xDA00, payload)

        collision = Resistance2Reader._read_collision_mesh(parsed, 0x40)

        np.testing.assert_allclose(
            collision.positions[7],
            (-1017 / 1024, 2062 / 1024, 21 / 1024),
        )
        np.testing.assert_array_equal(
            collision.indices,
            ((0, 1, 2), (5, 6, 7)),
        )
        np.testing.assert_array_equal(collision.face_types, (4, 7))

    def test_rejects_out_of_range_collision_face(self):
        payload = bytearray(0xE0)
        struct.pack_into(">5H", payload, 0x80, 8, 16, 256, 1, 0x50)
        struct.pack_into(">H", payload, 0x8C, 0x60)
        struct.pack_into(">I", payload, 0xD0, (0 << 18) | (1 << 9) | 8)
        parsed = _single_section_file(0xDA00, payload)

        with self.assertRaises(FormatError):
            Resistance2Reader._read_collision_mesh(parsed, 0x40)


if __name__ == "__main__":
    unittest.main()
