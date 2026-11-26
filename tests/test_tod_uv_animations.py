from __future__ import annotations

import struct
import unittest

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.ratchet_tod.uv_animations import (
    parse_uv_animations,
)


def _record(tag: int, owner: int, frag: int, scroll, rotation: float) -> bytes:
    data = bytearray(0x40)
    struct.pack_into(">HHHxxI", data, 0, tag, 8, owner, frag)
    struct.pack_into(">3f", data, 0x10, *scroll, rotation)
    return bytes(data)


def _main(records: list[bytes], counts: tuple[int, int, int]) -> IghwFile:
    payload = struct.pack(">3I", *counts) + bytes(0x14) + b"".join(records)
    data = bytearray(0x40)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(">4I", data, 0x20, 0x5C00, 0x40, 1, len(payload))
    data.extend(payload)
    return IghwFile(bytes(data), label="main.dat")


class TodUvAnimationTests(unittest.TestCase):
    def test_parses_each_family_by_owner_and_frag(self) -> None:
        animations = parse_uv_animations(
            _main(
                [
                    _record(0x1000, 7, 0, (0.5, 0.0), 0.0),
                    _record(0x0000, 3, 0x40, (0.0, -1.0), 0.0),
                    _record(0x3000, 2, 0x280, (0.0, 0.0), 0.01),
                    _record(0x2000, 2, 0x300, (0.0, 0.0), 0.0),
                ],
                (1, 1, 2),
            )
        )

        self.assertEqual(animations.ufrags[7].scroll_u, 0.5)
        self.assertEqual(animations.ties[(3, 0x40)].scroll_v, -1.0)
        # Rotation is per 60 Hz frame and negated into the R(angle) form.
        self.assertAlmostEqual(
            animations.mobys[(2, 0x280)].rotation,
            -0.6,
            places=5,
        )
        # A record that neither scrolls nor rotates is dropped.
        self.assertNotIn((2, 0x300), animations.mobys)

    def test_rejects_misordered_tags_and_overlong_counts(self) -> None:
        with self.assertRaisesRegex(FormatError, "in the tie block"):
            parse_uv_animations(
                _main([_record(0x2000, 1, 0, (1.0, 0.0), 0.0)], (0, 1, 0))
            )
        with self.assertRaisesRegex(FormatError, "exceed the section"):
            parse_uv_animations(
                _main([_record(0x1000, 1, 0, (1.0, 0.0), 0.0)], (2, 0, 0))
            )


if __name__ == "__main__":
    unittest.main()
