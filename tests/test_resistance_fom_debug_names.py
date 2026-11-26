from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_fom.debug_names import (
    parse_debug_names,
)
from insomniac_asset_io.games.resistance_fom.reader import ResistanceFomReader

from tests.test_resistance_fom import _write_fixture

FAMILY_KINDS = {
    "moby": 0,
    "path": 1,
    "trigger": 2,
    "effect": 3,
    "moby_group": 4,
    "area": 5,
    "marker": 6,
}


def _guid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012x}"


def _ps3debug(records: list[tuple[str, int, int]]) -> bytes:
    """Build an IGHW 0.2 ps3debug.dat with a 0x25500 name table."""

    header = 0x10 + 16
    table = 0x40
    start = table + 8
    strings_base = start + len(records) * 0x10
    strings = bytearray()

    def string(value: str) -> int:
        offset = strings_base + len(strings)
        strings.extend(value.encode("ascii") + b"\0")
        return offset

    data = bytearray(strings_base)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 0, 2, 1)
    struct.pack_into(">4I", data, 0x10, 0x25500, table, 8, 0)
    assert header <= table
    struct.pack_into(">II", data, table, start, len(records))
    for index, (name, local_index, kind) in enumerate(records):
        struct.pack_into(
            ">4I",
            data,
            start + index * 0x10,
            string(name),
            string(_guid(index)),
            local_index,
            kind,
        )
    return bytes(data + strings)


def _set_group_count(root: Path, count: int) -> None:
    path = root / "ps3gameplay.dat"
    gameplay = bytearray(path.read_bytes())
    struct.pack_into(">I", gameplay, 0x124, count)
    path.write_bytes(gameplay)


class ResistanceFomDebugNameTests(unittest.TestCase):
    def test_parses_families_and_rejects_ambiguous_indices(self) -> None:
        names = parse_debug_names(
            _ps3debug(
                [
                    ("Hyb_2", 1, 0),
                    ("Hyb_1", 0, 0),
                    ("Path_A", 0, 1),
                    ("Dup_A", 0, 2),
                    ("Dup_B", 0, 2),
                    ("Gap", 1, 3),
                    ("Unknown", 0, 8),
                ]
            ),
            label="ps3debug.dat",
        )
        self.assertEqual([item.name for item in names["moby"]], ["Hyb_1", "Hyb_2"])
        self.assertEqual(names["moby"][0].guid, _guid(1))
        self.assertEqual(names["path"][0].name, "Path_A")
        self.assertNotIn("trigger", names)
        self.assertNotIn("effect", names)

    def test_rejects_out_of_range_strings(self) -> None:
        data = bytearray(_ps3debug([("a", 0, 0)]))
        struct.pack_into(">I", data, 0x48, len(data) + 4)
        with self.assertRaises(FormatError):
            parse_debug_names(bytes(data), label="ps3debug.dat")

    def test_names_single_group_gameplay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            _set_group_count(root, 1)
            (root / "ps3debug.dat").write_bytes(
                _ps3debug(
                    [
                        (f"{family}_name", 0, kind)
                        for family, kind in FAMILY_KINDS.items()
                    ]
                )
            )
            with GameSource.open(root) as source:
                reader = ResistanceFomReader(source)
                region = reader.read_gameplay_regions()[0]
                self.assertEqual(region.moby_instances[0].name, "moby_name")
                self.assertEqual(region.moby_instances[0].instance_tuid, 0)
                self.assertEqual(region.moby_instances[0].source_guid, _guid(0))
                self.assertEqual(region.paths[0].source_name, "path_name")
                self.assertEqual(region.triggers[0].source_name, "trigger_name")
                self.assertEqual(region.effects[0].source_name, "effect_name")
                self.assertEqual(region.markers[0].source_name, "marker_name")
                self.assertEqual(
                    region.moby_groups[0].source_name,
                    "moby_group_name",
                )
                self.assertEqual(region.areas[0].source_name, "area_name")
                self.assertEqual(region.areas[0].name, "FixtureArea")
                self.assertEqual(reader.consume_import_warnings(), ())

    def test_multi_group_and_mismatched_tables_keep_numeric_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            _set_group_count(root, 9)
            (root / "ps3debug.dat").write_bytes(
                _ps3debug([("Hyb_1", 0, 0)])
            )
            with GameSource.open(root) as source:
                region = ResistanceFomReader(source).read_gameplay_regions()[0]
                self.assertTrue(region.moby_instances[0].name.startswith("Moby_"))
                self.assertIsNone(region.moby_instances[0].source_guid)

            _set_group_count(root, 1)
            (root / "ps3debug.dat").write_bytes(
                _ps3debug([("Hyb_1", 0, 0), ("Hyb_2", 1, 0), ("P", 0, 1)])
            )
            with GameSource.open(root) as source:
                reader = ResistanceFomReader(source)
                region = reader.read_gameplay_regions()[0]
                self.assertTrue(region.moby_instances[0].name.startswith("Moby_"))
                self.assertEqual(region.paths[0].source_name, "P")
                warnings = reader.consume_import_warnings()
                self.assertEqual(len(warnings), 1)
                self.assertIn("2 moby names for 1 decoded records", warnings[0])


if __name__ == "__main__":
    unittest.main()
