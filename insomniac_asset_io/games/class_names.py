"""Old-engine gameplay class names.

Tools of Destruction and Resistance: Fall of Man ship a game-wide debug file
of enum lookup tables (TOD ``BUILT/GAME/DEBUG.DAT``, RFoM
``packed/game/ps3debug.dat`` inside ``GAME.PSARC``). Each table row is
``{u32 string-offset array, u32 count, u32 count, u32 flags}``; row 0 names
the tables, and the ``kVTGenEnums`` row is indexed by gameplay class number.

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


def _class_names(
    ighw: IghwFile,
    rows_offset: int,
    row_count: int,
    label: str,
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
        index = lookups.index(CLASS_TABLE_NAME)
    except ValueError as exc:
        raise FormatError(
            f"{label}: enum lookups have no {CLASS_TABLE_NAME} row"
        ) from exc
    if index >= row_count:
        raise FormatError(
            f"{label}: {CLASS_TABLE_NAME} row {index} is outside "
            f"{row_count} rows"
        )
    base = rows_offset + index * ENUM_ROW_STRIDE
    # Dense tables repeat the count and have no flags; the class table is
    # indexed directly by class number, so anything else is rejected.
    if ighw.u32(base + 8) != ighw.u32(base + 4) or ighw.u32(base + 12):
        raise FormatError(
            f"{label}: {CLASS_TABLE_NAME} row is not a dense table"
        )
    return _row(ighw, rows_offset, index, label)


def parse_tod_class_names(data: bytes, *, label: str) -> ClassNames:
    """Decode the TOD ``BUILT/GAME/DEBUG.DAT`` class-name table."""

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
    return _class_names(ighw, section.offset, section.count, label)


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
    return _class_names(
        ighw,
        ighw.u32(section.offset),
        ighw.u32(section.offset + 4),
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
