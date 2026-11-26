"""Old-engine gameplay class names.

Tools of Destruction and Resistance: Fall of Man ship a game-wide debug file
of enum lookup tables (TOD ``BUILT/GAME/DEBUG.DAT``, RFoM
``packed/game/ps3debug.dat`` inside ``GAME.PSARC``). Each table row is
``{u32 string-offset array, u32 count, u32 count, u32 flags}``; row 0 names
the tables, and the ``kVTGenEnums`` row is indexed by gameplay class number.

Rows whose flags are ``0x01000000`` are also indexed by value. Their second
count is the length of a hand-written head of special values, whose names
can repeat later in the generated part of the row. ``kMaterialID`` is one:
values 1-8 name special surfaces (metal, magnet, slide, water, lava,
quicksand), and the generated list restarts at value 9. TOD collision
faces store ``kMaterialID`` values directly (see ``ratchet_tod/
level_collision.py``).

A placed gameplay Moby record starts with a pointer to a small IGHW v0.2
block whose ``0x25200`` payload begins with that class number. A null
pointer means the record carries no class data.

ReLunacy (VELD-Dev, NefariousTechSupport, and Neigy / Fianshoo) first
published the class-number location and the TOD table. The RFoM table
location, the row layout, and the Moby-to-class pairing were re-verified on
all levels of our disc builds.
"""

from __future__ import annotations

from ..core.binary import BinaryView, IghwFile
from ..core.errors import FormatError

TOD_ENUM_ROWS_SECTION = 0x25600
RFOM_ENUM_HEADER_SECTION = 0x25500
ENUM_ROW_STRIDE = 0x10
LOOKUP_TABLE_NAME = "kEnumLookups"
CLASS_TABLE_NAME = "kVTGenEnums"
COLLISION_MATERIAL_TABLE_NAME = "kMaterialID"
ENUM_ROW_HEAD_FLAG = 0x0100_0000
CLASS_SECTION = 0x25200
MAX_ENUM_ROWS = 256
MAX_ENUM_VALUES = 65_536
MAX_NAME_LENGTH = 256
MAX_CLASS_BLOCK_SECTIONS = 16

ClassNames = tuple[str | None, ...]


def _row(
    ighw: IghwFile,
    rows_offset: int,
    index: int,
    label: str,
) -> tuple[str | None, ...]:
    base = rows_offset + index * ENUM_ROW_STRIDE
    strings_offset = ighw.u32(base)
    count = ighw.u32(base + 4)
    if count > MAX_ENUM_VALUES:
        raise FormatError(
            f"{label}: enum row {index} has an unreasonable count {count}"
        )
    ighw.require(strings_offset, count * 4, purpose=f"enum row {index}")
    values: list[str | None] = []
    for value in range(count):
        pointer = ighw.u32(strings_offset + value * 4)
        if not pointer:
            values.append(None)
            continue
        text = ighw.cstring(pointer, max_length=MAX_NAME_LENGTH)
        if not text or not text.isprintable():
            raise FormatError(
                f"{label}: enum row {index} value {value} is not a "
                "printable name"
            )
        values.append(text)
    return tuple(values)


def _enum_table(
    ighw: IghwFile,
    rows_offset: int,
    row_count: int,
    table_name: str,
    label: str,
    *,
    allow_head: bool = False,
) -> ClassNames:
    if not 0 < row_count <= MAX_ENUM_ROWS:
        raise FormatError(
            f"{label}: unreasonable enum row count {row_count}"
        )
    ighw.require(
        rows_offset,
        row_count * ENUM_ROW_STRIDE,
        purpose="enum lookup rows",
    )
    lookups = _row(ighw, rows_offset, 0, label)
    if not lookups or lookups[0] != LOOKUP_TABLE_NAME:
        raise FormatError(f"{label}: enum row 0 is not {LOOKUP_TABLE_NAME}")
    try:
        index = lookups.index(table_name)
    except ValueError as exc:
        raise FormatError(
            f"{label}: enum lookups have no {table_name} row"
        ) from exc
    if index >= row_count:
        raise FormatError(
            f"{label}: {table_name} row {index} is outside "
            f"{row_count} rows"
        )
    base = rows_offset + index * ENUM_ROW_STRIDE
    # Dense rows repeat the count and have no flags. Where allowed, a row may
    # instead carry the head flag with a head no longer than the row. Other
    # shapes (for example the spec tables, whose second count is 0) are
    # rejected. The class table is accepted only as a dense row, the shape
    # verified on both games.
    count = ighw.u32(base + 4)
    head = ighw.u32(base + 8)
    flags = ighw.u32(base + 12)
    if not (
        (flags == 0 and head == count)
        or (
            allow_head
            and flags == ENUM_ROW_HEAD_FLAG
            and 0 < head <= count
        )
    ):
        raise FormatError(
            f"{label}: {table_name} row is not a dense table"
        )
    return _row(ighw, rows_offset, index, label)


def _tod_enum_table(
    data: bytes,
    table_name: str,
    label: str,
    *,
    allow_head: bool = False,
) -> ClassNames:
    ighw = IghwFile(data, label=label)
    section = ighw.section(TOD_ENUM_ROWS_SECTION)
    if section is None:
        raise FormatError(
            f"{label}: no enum lookup section "
            f"0x{TOD_ENUM_ROWS_SECTION:X}"
        )
    if section.length != ENUM_ROW_STRIDE:
        raise FormatError(
            f"{label}: enum row stride is {section.length:#x}; expected "
            f"{ENUM_ROW_STRIDE:#x}"
        )
    return _enum_table(
        ighw,
        section.offset,
        section.count,
        table_name,
        label,
        allow_head=allow_head,
    )


def parse_tod_class_names(data: bytes, *, label: str) -> ClassNames:
    """Decode the TOD ``BUILT/GAME/DEBUG.DAT`` class-name table."""

    return _tod_enum_table(data, CLASS_TABLE_NAME, label)


def parse_tod_collision_material_names(
    data: bytes,
    *,
    label: str,
) -> ClassNames:
    """Decode the TOD ``kMaterialID`` table, indexed by collision byte."""

    return _tod_enum_table(
        data,
        COLLISION_MATERIAL_TABLE_NAME,
        label,
        allow_head=True,
    )


def parse_rfom_class_names(data: bytes, *, label: str) -> ClassNames:
    """Decode the RFoM game-wide ``ps3debug.dat`` class-name table."""

    ighw = IghwFile(data, label=label)
    section = ighw.section(RFOM_ENUM_HEADER_SECTION)
    if section is None:
        raise FormatError(
            f"{label}: no enum lookup section "
            f"0x{RFOM_ENUM_HEADER_SECTION:X}"
        )
    ighw.require(section.offset, 8, purpose="enum lookup header")
    return _enum_table(
        ighw,
        ighw.u32(section.offset),
        ighw.u32(section.offset + 4),
        CLASS_TABLE_NAME,
        label,
    )


def read_class_id(
    view: BinaryView,
    pointer: int,
    *,
    label: str,
) -> int | None:
    """Return the class number of a gameplay record's class block.

    ``pointer`` is the record's first word. Zero means no class data.
    """

    if not pointer:
        return None
    view.require(pointer, 0x10, purpose="gameplay class block header")
    if view.bytes(pointer, 4) != IghwFile.MAGIC_BIG:
        raise FormatError(
            f"{label}: class block at {pointer:#x} has no IGHW magic"
        )
    version = (view.u16(pointer + 4), view.u16(pointer + 6))
    if version != (0, 2):
        raise FormatError(
            f"{label}: class block at {pointer:#x} has IGHW version "
            f"{version[0]}.{version[1]}"
        )
    section_count = view.u32(pointer + 8)
    if not 0 < section_count <= MAX_CLASS_BLOCK_SECTIONS:
        raise FormatError(
            f"{label}: class block at {pointer:#x} has {section_count} "
            "sections"
        )
    view.require(
        pointer + 0x10,
        section_count * 0x10,
        purpose="gameplay class block sections",
    )
    for index in range(section_count):
        entry = pointer + 0x10 + index * 0x10
        if view.u32(entry) != CLASS_SECTION:
            continue
        offset = view.u32(entry + 4)
        view.require(pointer + offset, 4, purpose="gameplay class number")
        return view.u32(pointer + offset)
    raise FormatError(
        f"{label}: class block at {pointer:#x} has no "
        f"0x{CLASS_SECTION:X} section"
    )


def class_name(names: ClassNames | None, class_id: int | None) -> str | None:
    if names is None or class_id is None or not 0 <= class_id < len(names):
        return None
    return names[class_id]
