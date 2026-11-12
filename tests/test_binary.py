from __future__ import annotations

import struct
import unittest

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError


def _ighw(section_id: int = 0xD100) -> bytes:
    data = bytearray(0x40)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(">IIII", data, 0x20, section_id, 0x30, 2, 0x10)
    return bytes(data)


class IghwTests(unittest.TestCase):
    def test_reads_version_and_sections(self):
        parsed = IghwFile(_ighw())
        self.assertEqual(parsed.version, (1, 1))
        section = parsed.require_section(0xD100)
        self.assertEqual(section.offset, 0x30)
        self.assertEqual(section.count, 2)
        self.assertEqual(section.length, 0x10)

    def test_rejects_bad_magic(self):
        with self.assertRaises(FormatError):
            IghwFile(b"NOPE" + bytes(32))

    def test_bounds_checked(self):
        parsed = IghwFile(_ighw())
        with self.assertRaises(FormatError):
            parsed.u64(0x3D)


if __name__ == "__main__":
    unittest.main()
