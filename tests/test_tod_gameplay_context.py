from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.ratchet_tod.gameplay_context import (
    read_tod_gameplay_context,
)

DATA_START = 0x200


class _Builder:
    """Lay out a big-endian IGHW 1.1 gameplay.dat with chosen sections."""

    def __init__(self) -> None:
        self.data = bytearray(DATA_START)
        self.sections: list[tuple[int, int, int, int]] = []

    def add(self, section_id: int, payload: bytes, count: int, length: int):
        offset = len(self.data)
        self.data.extend(payload)
        while len(self.data) % 0x10:
            self.data.append(0)
        self.sections.append((section_id, offset, count, length))
        return offset

    def reserve(self, size: int) -> int:
        offset = len(self.data)
        self.data.extend(bytes(size))
        return offset

    def build(self) -> bytes:
        struct.pack_into(
            ">4sHHI",
            self.data,
            0,
            b"IGHW",
            1,
            1,
            len(self.sections),
        )
        for index, entry in enumerate(sorted(self.sections)):
            struct.pack_into(">4I", self.data, 0x20 + index * 0x10, *entry)
        return bytes(self.data)


def _class_block(class_id: int) -> bytes:
    data = bytearray(0x30)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 0, 2, 1)
    struct.pack_into(">4I", data, 0x10, 0x25200, 0x20, 0x10, 0)
    struct.pack_into(">I", data, 0x20, class_id)
    return bytes(data)


def _volume(matrix: np.ndarray, shape: int) -> bytes:
    data = bytearray(0x90)
    struct.pack_into(">16f", data, 0, *matrix.T.ravel())
    data[0x80] = shape
    return bytes(data)


def _gameplay(**overrides) -> tuple[bytes, dict[str, int]]:
    builder = _Builder()
    offsets: dict[str, int] = {}

    points = np.asarray(
        ((0, 0, 0, 0), (3, 4, 0, 5), (3, 4, 2, 7)),
        dtype=">f4",
    )
    points_offset = builder.add(0x7710, points.tobytes(), 1, 0x30)
    offsets["paths"] = builder.add(
        0x7700,
        struct.pack(">IIfI", points_offset, 3, 7.0, 0x100),
        1,
        0x10,
    )

    box = np.diag((2.0, 1.0, 3.0, 1.0)).astype(np.float32)
    box[:3, 3] = (10.0, 20.0, 30.0)
    cylinder = np.eye(4, dtype=np.float32)
    offsets["volumes"] = builder.add(
        0x7740,
        _volume(box, overrides.get("shape", 0)) + _volume(cylinder, 1),
        2,
        0x90,
    )

    payload = builder.reserve(0x10)
    clue_volume = offsets["volumes"] + overrides.get("clue_volume", 0x90)
    offsets["clues"] = builder.add(
        0x7780,
        struct.pack(">4I", clue_volume, payload, 0x10, 0x371),
        1,
        0x10,
    )

    class_block = builder.reserve(0x30)
    builder.data[class_block : class_block + 0x30] = _class_block(0x2A)
    controller = struct.pack(
        ">I3f3f3fII",
        class_block,
        1.0,
        2.0,
        3.0,
        0.0,
        0.0,
        0.0,
        0.5,
        0.5,
        0.5,
        0xA0,
        0,
    )
    offsets["controllers"] = builder.add(0x77C0, controller, 1, 0x30)

    pod_handles = builder.add(
        0x7910,
        struct.pack(">2I", overrides.get("pod_handle", (2 << 16) | 1), 1),
        1,
        0x10,
    )
    offsets["pods"] = builder.add(
        0x7900,
        struct.pack(">4I", pod_handles, 1, 0, 0),
        1,
        0x10,
    )

    area_indices = builder.add(
        0x7950,
        struct.pack(">3H", 0, 1, overrides.get("area_volume", 0)),
        1,
        0x10,
    )
    offsets["areas"] = builder.add(
        0x7940,
        struct.pack(">IIHHI", area_indices, area_indices + 2, 1, 2, 0),
        1,
        0x10,
    )

    scent_handles = builder.add(
        0x7990,
        struct.pack(">I", overrides.get("scent_handle", 0x10000)),
        1,
        0x10,
    )
    offsets["scents"] = builder.add(
        0x7980,
        struct.pack(">4I", scent_handles, 1, 0, 0),
        1,
        0x10,
    )
    return builder.build(), offsets


def _read(data: bytes, moby_count: int = 2):
    return read_tod_gameplay_context(
        IghwFile(data, label="gameplay.dat"),
        moby_count=moby_count,
        label="gameplay.dat",
    )


class TodGameplayContextTests(unittest.TestCase):
    def test_decodes_every_family(self) -> None:
        context = _read(_gameplay()[0])

        path = context.paths[0]
        np.testing.assert_allclose(
            path.points,
            ((0, 0, 0), (3, 4, 0), (3, 4, 2)),
        )
        self.assertEqual(path.flags, 0x100)
        self.assertAlmostEqual(path.length, 7.0)

        box, cylinder = context.volumes
        self.assertEqual(box.shape_kind, 0)
        self.assertEqual(cylinder.shape_kind, 2)
        self.assertEqual(box.half_extent, 1.0)
        np.testing.assert_allclose(box.matrix[:3, 3], (10.0, 20.0, 30.0))
        np.testing.assert_allclose(
            np.diag(box.matrix)[:3],
            (2.0, 1.0, 3.0),
        )

        clue = context.clues[0]
        self.assertEqual(clue.trigger_index, 1)
        self.assertEqual(clue.class_id, 0x371)
        self.assertEqual(clue.payload_size, 0x10)

        controller = context.controllers[0]
        self.assertEqual(context.controller_class_ids, [0x2A])
        self.assertEqual(controller.payload_size, 0xA0)
        self.assertEqual(controller.scale, (0.5, 0.5, 0.5))
        np.testing.assert_allclose(controller.matrix[:3, 3], (1, 2, 3))
        np.testing.assert_allclose(
            np.diag(controller.matrix)[:3],
            (0.5, 0.5, 0.5),
        )

        self.assertEqual(context.pods[0].moby_indices, (1,))
        area = context.areas[0]
        self.assertEqual(area.path_indices, (0,))
        self.assertEqual(area.trigger_indices, (1, 0))
        self.assertEqual(context.scents[0].clue_indices, (0,))

    def test_rejects_malformed_records(self) -> None:
        cases = {
            "unknown shape": {"shape": 4},
            "not a volume record": {"clue_volume": 0x48},
            "invalid Moby handle": {"pod_handle": (5 << 16) | 1},
            "references a volume": {"area_volume": 2},
            "invalid Clue handle": {"scent_handle": 0x20000},
        }
        for message, overrides in cases.items():
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(FormatError, message),
            ):
                _read(_gameplay(**overrides)[0])

    def test_rejects_moby_handle_beyond_placements(self) -> None:
        with self.assertRaisesRegex(FormatError, "invalid Moby handle"):
            _read(_gameplay()[0], moby_count=1)

    def test_missing_sections_yield_empty_families(self) -> None:
        context = _read(_Builder().build())
        self.assertEqual(context.paths, [])
        self.assertEqual(context.volumes, [])
        self.assertEqual(context.clues, [])


if __name__ == "__main__":
    unittest.main()
