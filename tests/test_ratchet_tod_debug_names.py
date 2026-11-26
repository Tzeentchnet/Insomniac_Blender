from __future__ import annotations

import struct
import tempfile
import unittest
import uuid
from pathlib import Path

from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.ratchet_tod.debug_names import (
    parse_debug_names,
)
from insomniac_asset_io.games.ratchet_tod.reader import RatchetTodReader
from insomniac_asset_io.games.resistance_2.formats import AssetKind

from tests.test_ratchet_tod import _write_fixture


def _guid(index: int) -> bytes:
    return uuid.UUID(int=0x1000 + index).bytes_le


def _debug_dat(
    *,
    prototypes: dict[int, list[str]] | None = None,
    instances: dict[int, list[str]] | None = None,
    shaders: list[tuple[str, tuple[str, ...]]] | None = None,
) -> bytes:
    """Build a big-endian IGHW 1.1 DEBUG.DAT with the retail record layouts."""

    prototypes = prototypes or {}
    instances = instances or {}
    shaders = shaders or []
    tables: list[tuple[int, int, int]] = []
    for section_id, names in prototypes.items():
        tables.append((section_id, len(names), 0x10))
    for section_id, names in instances.items():
        tables.append((section_id, len(names), 0x18))
    if shaders:
        tables.append((0x5D00, len(shaders), 0x30))

    header = 0x20 + len(tables) * 16
    cursor = (header + 0xF) & ~0xF
    layout: list[tuple[int, int, int, int]] = []
    for section_id, count, stride in tables:
        layout.append((section_id, cursor, count, stride))
        cursor += (count * stride + 0xF) & ~0xF
    strings = bytearray()
    string_base = cursor

    def string(value: str) -> int:
        offset = string_base + len(strings)
        strings.extend(value.encode("ascii") + b"\0")
        return offset

    data = bytearray(cursor)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(layout))
    for index, (section_id, offset, count, stride) in enumerate(layout):
        struct.pack_into(">4I", data, 0x20 + index * 16, section_id, offset, count, stride)
        if section_id in prototypes:
            for record, name in enumerate(prototypes[section_id]):
                base = offset + record * stride
                struct.pack_into(">QI", data, base, 0x0500_0000 + record, string(name))
        elif section_id in instances:
            for record, name in enumerate(instances[section_id]):
                base = offset + record * stride
                data[base : base + 16] = _guid(record)
                struct.pack_into(">IH", data, base + 0x10, string(name), 7)
        else:
            empty = string("")
            for record, (name, paths) in enumerate(shaders):
                base = offset + record * stride
                struct.pack_into(">I", data, base + 8, string(name))
                for slot in range(4):
                    path = paths[slot] if slot < len(paths) else ""
                    struct.pack_into(
                        ">I",
                        data,
                        base + 0x20 + slot * 4,
                        string(path) if path else empty,
                    )
    return bytes(data + strings)


def _counts(root: Path) -> tuple[int, int, int, int]:
    with GameSource.open(root) as source:
        reader = RatchetTodReader(source)
        zone = reader.read_zones()[0]
        gameplay = reader.read_gameplay_regions()[0]
        return (
            len(zone.tie_instances),
            len(zone.foliage_instances),
            len(zone.shrub_instances),
            len(gameplay.moby_instances),
        )


class RatchetTodDebugNameTests(unittest.TestCase):
    def test_parses_retail_record_layouts(self) -> None:
        names = parse_debug_names(
            _debug_dat(
                prototypes={0x9480: ["object/prop/crate"]},
                instances={0x73C0: ["crate_1", "crate_2"]},
                shaders=[("crate_shader", ("x:/crate_c.tga",))],
            ),
            label="debug.dat",
        )
        self.assertEqual(names.prototypes[AssetKind.MOBY], ("object/prop/crate",))
        moby = names.instances["moby"]
        self.assertEqual([item.name for item in moby], ["crate_1", "crate_2"])
        self.assertEqual(moby[1].guid, str(uuid.UUID(int=0x1001)))
        self.assertEqual(moby[0].zone, 7)
        self.assertEqual(names.shaders, ("crate_shader",))
        self.assertEqual(
            names.shader_texture_paths,
            (("x:/crate_c.tga", None, None, None),),
        )

    def test_rejects_malformed_tables(self) -> None:
        bad_stride = bytearray(_debug_dat(instances={0x73C0: ["a"]}))
        struct.pack_into(">I", bad_stride, 0x2C, 0x10)
        with self.assertRaises(FormatError):
            parse_debug_names(bytes(bad_stride), label="debug.dat")
        data = bytearray(_debug_dat(prototypes={0x9480: ["object/a"]}))
        offset = struct.unpack_from(">I", data, 0x24)[0]
        struct.pack_into(">I", data, offset + 8, len(data) + 0x100)
        with self.assertRaises(FormatError):
            parse_debug_names(bytes(data), label="debug.dat")

    def test_applies_names_to_level_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            ties, foliage, shrubs, mobys = _counts(root)
            self.assertGreater(shrubs, 0)
            (root / "debug.dat").write_bytes(
                _debug_dat(
                    prototypes={
                        0x9480: ["character/enemy/test_enemy"],
                        0x9280: ["object/rock/test_rock"],
                        0x9380: ["object/plant/test_foliage"],
                        0x9580: ["object/plant/test_shrub"],
                    },
                    instances={
                        0x73C0: [f"enemy{index}" for index in range(mobys)],
                        0x72C0: [f"rock{index}" for index in range(ties)],
                        0x74C0: [f"leaves{index}" for index in range(foliage)],
                        0x7560: [f"grass{index}" for index in range(shrubs)],
                    },
                    shaders=[("surface", ()), ("leaves", ())],
                )
            )
            with GameSource.open(root) as source:
                reader = RatchetTodReader(source)
                self.assertEqual(
                    reader.read_asset_name(AssetKind.MOBY, 0),
                    "character/enemy/test_enemy",
                )
                self.assertEqual(reader.read_moby(0).name, "character/enemy/test_enemy")
                tie_id = reader.asset_pointers(AssetKind.TIE)[0].tuid
                self.assertEqual(
                    reader.read_asset_name(AssetKind.TIE, tie_id),
                    "object/rock/test_rock",
                )
                shrub_id = reader.asset_pointers(AssetKind.SHRUB)[0].tuid
                self.assertEqual(
                    reader.read_asset_name(AssetKind.SHRUB, shrub_id),
                    "object/plant/test_shrub",
                )
                foliage_id = reader.asset_pointers(AssetKind.FOLIAGE)[0].tuid
                self.assertEqual(
                    reader.read_asset_name(AssetKind.FOLIAGE, foliage_id),
                    "object/plant/test_foliage",
                )
                self.assertEqual(reader.read_shader(1).lookup_path, "leaves")
                self.assertEqual(
                    reader.read_asset_name(AssetKind.SHADER, 0),
                    "surface",
                )

                zone = reader.read_zones()[0]
                self.assertEqual(zone.tie_instances[0].name, "rock0")
                self.assertEqual(
                    zone.tie_instances[0].source_guid,
                    str(uuid.UUID(int=0x1000)),
                )
                self.assertEqual(
                    [item.name for item in zone.shrub_instances],
                    [f"grass{index}" for index in range(shrubs)],
                )
                self.assertEqual(zone.foliage_instances[0].name, "leaves0")
                gameplay = reader.read_gameplay_regions()[0]
                self.assertEqual(gameplay.moby_instances[0].name, "enemy0")
                # The numeric identity stays the stable key.
                self.assertEqual(gameplay.moby_instances[0].instance_tuid, 0)
                self.assertEqual(reader.consume_import_warnings(), ())

    def test_incomplete_tables_keep_numeric_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            ties, _foliage, _shrubs, _mobys = _counts(root)
            (root / "debug.dat").write_bytes(
                _debug_dat(
                    prototypes={0x9480: ["a", "b"]},
                    instances={0x72C0: [f"rock{index}" for index in range(ties + 1)]},
                )
            )
            with GameSource.open(root) as source:
                reader = RatchetTodReader(source)
                self.assertEqual(
                    reader.read_asset_name(AssetKind.MOBY, 0),
                    "Moby_0000",
                )
                self.assertIsNone(
                    reader.read_asset_name(AssetKind.SHADER, 0)
                )
                zone = reader.read_zones()[0]
                self.assertTrue(
                    zone.tie_instances[0].name.startswith("TieInstance_")
                )
                self.assertIsNone(zone.tie_instances[0].source_guid)
                warnings = reader.consume_import_warnings()
                self.assertEqual(len(warnings), 2)
                self.assertTrue(
                    all("using numeric names" in item for item in warnings)
                )

    def test_malformed_debug_dat_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            (root / "debug.dat").write_bytes(b"IGHW\0\1\0\1" + bytes(8))
            with GameSource.open(root) as source:
                reader = RatchetTodReader(source)
                self.assertEqual(
                    reader.read_asset_name(AssetKind.MOBY, 0),
                    "Moby_0000",
                )
                self.assertEqual(len(reader.consume_import_warnings()), 1)

    def test_rendering_mode_table(self) -> None:
        expected = {
            0: "OPAQUE",
            1: "BLEND",
            2: "ADDITIVE",
            3: "BLEND",
            4: "MASK",
            5: "MASK",
            6: "BLEND",
            7: "OPAQUE",
        }
        for byte, alpha_mode in expected.items():
            with self.subTest(byte=byte), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _write_fixture(root)
                main = bytearray((root / "main.dat").read_bytes())
                main[0x340 + 0x11] = byte
                (root / "main.dat").write_bytes(main)
                with GameSource.open(root) as source:
                    reader = RatchetTodReader(source)
                    shader = reader.read_shader(0)
                    self.assertEqual(shader.alpha_mode, alpha_mode)
                    self.assertEqual(shader.blend_mode, byte)
                    warnings = reader.consume_import_warnings()
                    self.assertEqual(len(warnings), 1 if byte == 7 else 0)


if __name__ == "__main__":
    unittest.main()
