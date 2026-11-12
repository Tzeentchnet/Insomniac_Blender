from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


def _assetlookup() -> bytes:
    data = bytearray(0x40)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 2)
    struct.pack_into(">IIII", data, 0x20, 0x1D140, 0x40, 0, 0)
    struct.pack_into(">IIII", data, 0x30, 0x1D1C0, 0x40, 0, 0)
    return bytes(data)


def _texture_header() -> bytes:
    data = bytearray(0x80)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(">IIII", data, 0x20, 0x5200, 0x40, 2, 0x20)
    struct.pack_into(">IHB", data, 0x40, 0, 1, 0xA6)
    struct.pack_into(">HH", data, 0x58, 4, 4)
    struct.pack_into(">IHB", data, 0x60, 8, 1, 0xA6)
    struct.pack_into(">HH", data, 0x78, 4, 4)
    return bytes(data)


def _texture_texels() -> bytes:
    data = bytearray(0x60)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 2)
    struct.pack_into(">IIII", data, 0x20, 0x5300, 0x40, 1, 16)
    struct.pack_into(">IIII", data, 0x30, 0x5A00, 0x50, 2, 8)
    struct.pack_into("<HHI", data, 0x40, 0xF800, 0x0000, 0)
    struct.pack_into("<HHI", data, 0x48, 0x07E0, 0x0000, 0)
    struct.pack_into(">II", data, 0x50, 0x1111_1111, 8)
    struct.pack_into(">II", data, 0x58, 0xDEAD_BEEF, 8)
    return bytes(data)


class GlobalTextureTests(unittest.TestCase):
    def test_decodes_per_tuid_global_texture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            level = root / "built" / "levels" / "fixture"
            level.mkdir(parents=True)
            (level / "assetlookup.dat").write_bytes(_assetlookup())

            full_tuid = 0x1234_5678_90AB_CDEF
            folder = (
                root
                / "built"
                / "tuids"
                / "123"
                / f"{full_tuid:016x}"
            )
            folder.mkdir(parents=True)
            (folder / "header.dat").write_bytes(_texture_header())
            (folder / "texel.dat").write_bytes(_texture_texels())

            with GameSource.open(root) as source:
                texture = Resistance2Reader(source).read_texture(0xDEAD_BEEF)

        self.assertEqual(texture.info.tuid, full_tuid)
        self.assertEqual(texture.rgba.shape, (4, 4, 4))
        self.assertEqual(texture.rgba[0, 0].tolist(), [0, 255, 0, 255])


if __name__ == "__main__":
    unittest.main()
