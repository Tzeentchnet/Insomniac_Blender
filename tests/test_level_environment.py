from __future__ import annotations

import struct
import unittest

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.level_environment import (
    parse_rfom_environment,
    parse_tod_environment,
)


def _ighw(sections: dict[int, tuple[bytes, int, int]]) -> IghwFile:
    """Build a big-endian IGHW 1.1 file; values are (payload, count, len)."""

    data = bytearray(0x100)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, (section_id, (payload, count, length)) in enumerate(
        sorted(sections.items())
    ):
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


def _tod_root(
    *,
    version: int = 2,
    direction: tuple[float, float, float] = (0.0, 1.0, 0.0),
) -> bytes:
    data = bytearray(0x80)
    struct.pack_into(">III", data, 0, version, 0, 5)
    struct.pack_into(">3f", data, 0x20, 0.2, 0.3, 0.4)
    struct.pack_into(">3f", data, 0x30, 1.5, 1.0, 0.5)
    struct.pack_into(">3f", data, 0x40, 0.1, 0.2, 0.3)
    struct.pack_into(">3f", data, 0x50, *direction)
    struct.pack_into(">3f", data, 0x60, 1.0, 0.0, 0.0)
    return bytes(data)


def _tod_fog(*, near: float = 20.0, bloom: bool = True) -> bytes:
    data = bytearray(0xF0)
    struct.pack_into(">3f", data, 0x40, 0.0, 0.25, 0.6)
    struct.pack_into(">3f", data, 0x4C, near, 360.0, 0.6)
    struct.pack_into(">I2f", data, 0x70, int(bloom), 0.01, 0.3)
    struct.pack_into(">3fI", data, 0x80, 0.6, 2.2, 0.6, 1)
    return bytes(data)


class LevelEnvironmentTests(unittest.TestCase):
    def test_parses_tod_fog_bloom_and_lights(self) -> None:
        environment = parse_tod_environment(
            _ighw(
                {
                    0x7100: (_tod_fog(), 1, 0xF0),
                    0x8B00: (_tod_root(), 1, 0x80),
                }
            )
        )

        self.assertEqual(environment.version, 2)
        self.assertEqual(environment.light_volume_count, 5)
        self.assertAlmostEqual(environment.ambient[2], 0.4, places=6)
        self.assertEqual(len(environment.lights), 2)
        self.assertEqual(environment.lights[0].direction, (0.0, 1.0, 0.0))
        self.assertAlmostEqual(environment.lights[0].colour[0], 1.5)
        self.assertAlmostEqual(environment.fog.near, 20.0)
        self.assertAlmostEqual(environment.fog.far, 360.0)
        self.assertAlmostEqual(environment.fog.amount, 0.6, places=6)
        self.assertAlmostEqual(environment.bloom.extract_exponent, 2.2, 6)
        self.assertTrue(environment.bloom.colour_extract)

    def test_bloom_off_and_missing_sections(self) -> None:
        environment = parse_tod_environment(
            _ighw({0x7100: (_tod_fog(bloom=False), 1, 0xF0)})
        )
        self.assertIsNone(environment.bloom)
        self.assertEqual(environment.lights, ())
        self.assertIsNone(parse_tod_environment(_ighw({})))
        self.assertIsNone(parse_rfom_environment(_ighw({})))

    def test_rejects_implausible_tod_records(self) -> None:
        cases = {
            "unsupported lighting root version": {
                0x8B00: (_tod_root(version=3), 1, 0x80)
            },
            "not a unit vector": {
                0x8B00: (_tod_root(direction=(0.0, 2.0, 0.0)), 1, 0x80)
            },
            "implausible fog range": {
                0x7100: (_tod_fog(near=400.0), 1, 0xF0)
            },
            "expected 1": {0x8B00: (_tod_root() * 2, 2, 0x80)},
        }
        for message, sections in cases.items():
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(FormatError, message),
            ):
                parse_tod_environment(_ighw(sections))

    def test_parses_rfom_root_with_light_volumes(self) -> None:
        root = bytearray(0x80 + 2 * 0x80)
        struct.pack_into(">III", root, 0, 0x80, 2, 1)
        struct.pack_into(">3f", root, 0x10, 0.34, 0.35, 0.41)
        struct.pack_into(">3f", root, 0x20, 1.21, 0.94, 0.66)
        struct.pack_into(">3f", root, 0x40, 0.0, 1.0, 0.0)
        environment = parse_rfom_environment(
            _ighw({0x8A00: (bytes(root), 1, len(root))})
        )

        self.assertEqual(environment.version, 1)
        self.assertEqual(environment.light_volume_count, 2)
        self.assertEqual(len(environment.lights), 1)
        self.assertIsNone(environment.fog)

        with self.assertRaisesRegex(FormatError, "light volumes"):
            parse_rfom_environment(
                _ighw({0x8A00: (bytes(root), 1, 0x80)})
            )


if __name__ == "__main__":
    unittest.main()
