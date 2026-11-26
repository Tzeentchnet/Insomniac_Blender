from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.old_engine_sky import parse_sky, shell_rotation


def _ighw(sections: list[tuple[int, bytes, int, int]]) -> IghwFile:
    data = bytearray(0x100)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, (section_id, payload, count, length) in enumerate(sections):
        offset = len(data)
        data.extend(payload)
        struct.pack_into(
            ">4I",
            data,
            0x20 + index * 0x10,
            section_id,
            offset,
            count,
            length,
        )
    return IghwFile(bytes(data), label="fixture")


def _vertex(x, y, z, colour, u, v) -> bytes:
    return (
        np.asarray((x, y, z, 1.0), dtype=">f2").tobytes()
        + bytes(colour)
        + np.asarray((u, v), dtype=">f2").tobytes()
    )


def _fixture(*, texture_index: int = 0, index_count: int = 3):
    defs = bytearray(0x100)
    struct.pack_into(">f", defs, 0x5C, 1.5)
    struct.pack_into(">3I", defs, 0xE8, 0, 0xFFFF_FFFF, 0)
    struct.pack_into(">II", defs, 0xF8, 0x10, 2)
    shell = bytearray(0x20)
    struct.pack_into(">IHHII", shell, 0, 0, index_count, 5, texture_index, 1)
    struct.pack_into(">4f", shell, 0x10, np.pi / 2, 0.0, 0.01, 0.0)
    main = _ighw(
        [
            (0x5200, bytes(0x20), 1, 0x20),
            (0x8150, bytes(defs), 1, 0x100),
            (0x8170, bytes(shell), 1, 0x20),
        ]
    )
    # One leading pad vertex: the stream starts at +0x10.
    vertex_stream = bytes(0x10) + b"".join(
        (
            _vertex(1, 0, 0, (255, 0, 0, 255), 0, 0),
            _vertex(0, 1, 0, (0, 255, 0, 128), 1, 0),
            _vertex(0, 0, 1, (0, 0, 255, 0), 0, 1),
        )
    )
    # The index stream starts at +2: one leading pad index.
    index_stream = struct.pack(">4H", 0xFFFF, 2, 1, 0)
    vertices = _ighw(
        [
            (0x9000, vertex_stream, 1, len(vertex_stream)),
            (0x9100, index_stream, 1, len(index_stream)),
        ]
    )
    return main, vertices


def _parse(main: IghwFile, vertices: IghwFile):
    return parse_sky(
        main,
        vertices,
        lambda offset, length: vertices.data[offset : offset + length],
    )


class OldEngineSkyTests(unittest.TestCase):
    def test_decodes_shell_mesh_and_definition(self) -> None:
        sky = _parse(*_fixture())

        self.assertEqual(sky.cloud_texture_indices, (0, None, 0))
        self.assertEqual(sky.time_scale, 1.5)
        self.assertEqual(len(sky.shells), 1)
        shell = sky.shells[0]
        self.assertTrue(shell.bloom)
        self.assertEqual(shell.draw_order, 5)
        self.assertEqual(shell.section_id, 0x8170)
        np.testing.assert_array_equal(shell.indices, ((2, 1, 0),))
        np.testing.assert_allclose(shell.positions[0], (1, 0, 0))
        np.testing.assert_allclose(shell.colors[1], (0, 1, 0, 128 / 255))
        np.testing.assert_allclose(shell.uvs[2], (0, 1))
        # Rz(pi / 2): +X turns to +Y.
        np.testing.assert_allclose(
            shell_rotation(shell) @ np.asarray((1.0, 0.0, 0.0)),
            (0.0, 1.0, 0.0),
            atol=1e-6,
        )

    def test_rejects_out_of_range_texture_and_bad_triangles(self) -> None:
        with self.assertRaisesRegex(FormatError, "outside the texture table"):
            _parse(*_fixture(texture_index=4))
        with self.assertRaisesRegex(FormatError, "not a triangle list"):
            _parse(*_fixture(index_count=2))

    def test_missing_sky_is_none(self) -> None:
        main = _ighw([(0x5200, bytes(0x20), 1, 0x20)])
        self.assertIsNone(parse_sky(main, main, lambda _o, _l: b""))


if __name__ == "__main__":
    unittest.main()
