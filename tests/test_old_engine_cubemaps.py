from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.old_engine_cubemaps import (
    face_stride,
    read_cubemaps,
    sample_directions,
)

FACE_COLOURS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (0, 255, 255),
    (255, 0, 255),
)


def _main(*, cube_bit: bool = True, location: int = 2) -> IghwFile:
    size = 4
    stride = face_stride(size, 2, 0x85)
    resident = bytearray(6 * stride)
    for face, (r, g, b) in enumerate(FACE_COLOURS):
        # Decoder byte order is A, B, G, R; uniform faces ignore swizzling.
        texel = bytes((255, b, g, r))
        resident[face * stride : face * stride + size * size * 4] = (
            texel * (size * size)
        )
    descriptor = bytearray(0x20)
    format_word = (0x85 << 8) | (int(cube_bit) << 2) | location
    struct.pack_into(">IHH", descriptor, 0, 0, 2, format_word)
    struct.pack_into(">HH", descriptor, 0x18, size, size)

    data = bytearray(0x80)
    sections = [(0x5920, bytes(descriptor), 1, 0x20)]
    sections.append((0x9650, bytes(resident), 1, len(resident)))
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
    return IghwFile(bytes(data), label="main.dat")


class OldEngineCubemapTests(unittest.TestCase):
    def test_face_stride_aligns_mip_chain(self) -> None:
        # 32x32 A8R8G8B8 with six mips is 0x1554 bytes, padded to 0x1580.
        self.assertEqual(face_stride(32, 6, 0x85), 0x1580)
        # 64x64 R5G6B5 with six mips is 0x2AA8 bytes, padded to 0x2B00.
        self.assertEqual(face_stride(64, 6, 0x84), 0x2B00)

    def test_reads_main_memory_faces_in_order(self) -> None:
        cubemaps = read_cubemaps(
            _main(),
            resident_section=0x9650,
            read_local=None,
        )

        self.assertEqual(len(cubemaps), 1)
        faces = cubemaps[0].faces
        self.assertEqual(faces.shape, (6, 4, 4, 4))
        for face, colour in enumerate(FACE_COLOURS):
            self.assertEqual(tuple(faces[face, 0, 0, :3]), colour)

    def test_samples_axis_directions_from_matching_faces(self) -> None:
        faces = read_cubemaps(
            _main(),
            resident_section=0x9650,
            read_local=None,
        )[0].faces
        directions = np.asarray(
            (
                (1, 0, 0),
                (-1, 0, 0),
                (0, 1, 0),
                (0, -1, 0),
                (0, 0, 1),
                (0, 0, -1),
            ),
            dtype=np.float64,
        )
        sampled = sample_directions(faces, directions)
        for face, colour in enumerate(FACE_COLOURS):
            self.assertEqual(tuple(sampled[face, :3]), colour)

    def test_rejects_non_cube_and_skips_unknown_location(self) -> None:
        with self.assertRaisesRegex(FormatError, "cube bit"):
            read_cubemaps(
                _main(cube_bit=False),
                resident_section=0x9650,
                read_local=None,
            )
        warnings: list[str] = []
        self.assertEqual(
            read_cubemaps(
                _main(location=3),
                resident_section=0x9650,
                read_local=None,
                warn=warnings.append,
            ),
            [],
        )
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
