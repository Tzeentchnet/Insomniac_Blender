from __future__ import annotations

import struct
import unittest

from insomniac_asset_io.core.binary import BinaryView
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.class_names import (
    class_name,
    parse_rfom_class_names,
    parse_tod_class_names,
    parse_tod_collision_material_names,
    read_class_id,
)

LOOKUPS = ["kEnumLookups", "kVTGenEnums", "kFxPackageID", "kNumLookups"]
CLASSES = ["Handle_CLASS", None, "BreakableBoltCrate_CLASS"]


def _enum_file(
    *,
    rfom: bool,
    lookups: list[str] = LOOKUPS,
    classes: list[str | None] = CLASSES,
    class_flags: int = 0,
    class_head: int | None = None,
) -> bytes:
    """Build an IGHW 1.1 enum lookup file in the TOD or RFoM arrangement."""

    rows = [lookups, classes, ["FX_PKG_TEST"]]
    section_id = 0x25500 if rfom else 0x25600
    section_offset = 0x40
    rows_offset = section_offset + (0x10 if rfom else 0)
    arrays_offset = rows_offset + len(rows) * 0x10
    strings_offset = arrays_offset + sum(len(row) for row in rows) * 4
    data = bytearray(strings_offset)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(
        ">4I",
        data,
        0x20,
        section_id,
        section_offset,
        1 if rfom else len(rows),
        0x10,
    )
    if rfom:
        struct.pack_into(">II", data, section_offset, rows_offset, len(rows))
    cursor = arrays_offset
    for index, row in enumerate(rows):
        flags = class_flags if index == 1 else 0
        head = class_head if index == 1 and class_head is not None else len(row)
        struct.pack_into(
            ">4I",
            data,
            rows_offset + index * 0x10,
            cursor,
            len(row),
            head,
            flags,
        )
        for value in row:
            pointer = 0
            if value is not None:
                pointer = len(data)
                data.extend(value.encode("ascii") + b"\0")
            struct.pack_into(">I", data, cursor, pointer)
            cursor += 4
    return bytes(data)


def _class_block(class_id: int, *, section_id: int = 0x25200) -> bytes:
    data = bytearray(0x30)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 0, 2, 1)
    struct.pack_into(">4I", data, 0x10, section_id, 0x20, 0x10, 0)
    struct.pack_into(">I", data, 0x20, class_id)
    return bytes(data)


class ClassNameTests(unittest.TestCase):
    def test_parses_tod_and_rfom_tables(self) -> None:
        for rfom, parse in (
            (False, parse_tod_class_names),
            (True, parse_rfom_class_names),
        ):
            with self.subTest(rfom=rfom):
                names = parse(_enum_file(rfom=rfom), label="fixture")
                self.assertEqual(names, tuple(CLASSES))
                self.assertEqual(
                    class_name(names, 2),
                    "BreakableBoltCrate_CLASS",
                )
                self.assertIsNone(class_name(names, 1))
                self.assertIsNone(class_name(names, 3))
                self.assertIsNone(class_name(names, None))
                self.assertIsNone(class_name(None, 0))

    def test_rejects_missing_or_sparse_class_table(self) -> None:
        cases = {
            "not kEnumLookups": _enum_file(
                rfom=False,
                lookups=["kOther", "kVTGenEnums"],
            ),
            "no kVTGenEnums": _enum_file(
                rfom=False,
                lookups=["kEnumLookups", "kOther"],
            ),
            "dense": _enum_file(rfom=False, class_flags=0x0100_0000),
        }
        for message, data in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(FormatError, message):
                    parse_tod_class_names(data, label="fixture")

    def test_parses_tod_collision_material_table_with_head(self) -> None:
        materials = [
            "COLL_MATERIAL_INVALID",
            "COLL_MATERIAL_METAL_MEDIUM",
            "COLL_MATERIAL_INVALID",
            "COLL_MATERIAL_METAL_SMALL",
            "COLL_MATERIAL_METAL_MEDIUM",
        ]
        data = _enum_file(
            rfom=False,
            lookups=["kEnumLookups", "kMaterialID"],
            classes=materials,
            class_flags=0x0100_0000,
            class_head=2,
        )
        self.assertEqual(
            parse_tod_collision_material_names(data, label="fixture"),
            tuple(materials),
        )
        for head, flags in ((0, 0x0100_0000), (6, 0x0100_0000), (2, 0)):
            with (
                self.subTest(head=head, flags=flags),
                self.assertRaisesRegex(FormatError, "dense"),
            ):
                parse_tod_collision_material_names(
                    _enum_file(
                        rfom=False,
                        lookups=["kEnumLookups", "kMaterialID"],
                        classes=materials,
                        class_flags=flags,
                        class_head=head,
                    ),
                    label="fixture",
                )

    def test_reads_class_number_from_class_block(self) -> None:
        data = bytes(0x40) + _class_block(0x43A)
        view = BinaryView(data, endian=">", label="gameplay")

        self.assertEqual(read_class_id(view, 0x40, label="moby"), 0x43A)
        self.assertIsNone(read_class_id(view, 0, label="moby"))

    def test_rejects_malformed_class_blocks(self) -> None:
        cases = {
            "IGHW magic": bytes(0x40) + b"XXXX" + _class_block(1)[4:],
            "no 0x25200": bytes(0x40) + _class_block(1, section_id=0x25300),
        }
        for message, data in cases.items():
            view = BinaryView(data, endian=">", label="gameplay")
            with self.subTest(message=message):
                with self.assertRaisesRegex(FormatError, message):
                    read_class_id(view, 0x40, label="moby")


if __name__ == "__main__":
    unittest.main()
