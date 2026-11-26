"""Retail Resistance: Fall of Man ``ps3debug.dat`` instance names.

Each ``BCUS98107`` level ships ``packed/levels/<level>/ps3debug.dat`` inside
``GAME.PSARC``. Section ``0x25500`` holds ``{u32 start, u32 count}``; each
``0x10``-byte record at ``start`` is
``{u32 name, u32 guid string, u32 local index, u32 kind}``. The layout was
first published by the ReLunacy project (VELD-Dev and contributors). The kind
mapping to ``ps3gameplay.dat`` descriptor families and the alignment were
re-verified against all 41 levels of our disc build.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from ...core.binary import IghwFile
from ...core.errors import FormatError

LOGGER = logging.getLogger(__name__)

# The game-wide enum lookup file (gameplay class names), stored beside the
# level files under its own name so it never matches the level ps3debug.dat.
GAME_DEBUG_PSARC_MEMBER = "packed/game/ps3debug.dat"
GAME_DEBUG_CACHE_NAME = "game_ps3debug.dat"

SECT_DEBUG_NAMES = 0x25500
RECORD_STRIDE = 0x10
MAX_RECORDS = 1_000_000
MAX_NAME_LENGTH = 1024
MAX_DEBUG_DAT_SIZE = 64 * 1024 * 1024

# Debug record kind -> GameplayInstances descriptor family. Kinds 7 and 8
# occur twice each on two levels and have no decoded family.
KIND_FAMILIES = {
    0: "moby",  # +0x40
    1: "path",  # +0x50
    2: "trigger",  # +0x60
    3: "effect",  # +0x70
    6: "marker",  # +0x80
    4: "moby_group",  # +0x90
    5: "area",  # +0xA0
}


@dataclass(frozen=True, slots=True)
class FomDebugName:
    name: str
    guid: str


def _string(ighw: IghwFile, pointer: int, label: str) -> str:
    if pointer <= 0 or pointer >= ighw.size:
        raise FormatError(f"{label}: string pointer {pointer:#x} is out of range")
    value = ighw.cstring(pointer, max_length=MAX_NAME_LENGTH)
    if not value.isprintable():
        raise FormatError(f"{label}: string at {pointer:#x} is not printable")
    return value


def parse_debug_names(
    data: bytes,
    *,
    label: str,
) -> dict[str, tuple[FomDebugName, ...]]:
    """Return names per gameplay family, indexed by their local index.

    A family is returned only when its local indices are exactly
    ``0..n-1``; repeated or missing indices make the family ambiguous.
    """

    ighw = IghwFile(data, label=label)
    section = ighw.section(SECT_DEBUG_NAMES)
    if section is None:
        return {}
    ighw.require(section.offset, 8, purpose="debug name header")
    start = ighw.u32(section.offset)
    count = ighw.u32(section.offset + 4)
    if count > MAX_RECORDS:
        raise FormatError(f"{label}: unreasonable debug name count {count}")
    ighw.require(start, count * RECORD_STRIDE, purpose="debug name records")
    by_kind: dict[int, dict[int, FomDebugName]] = {}
    duplicates: set[int] = set()
    for index in range(count):
        base = start + index * RECORD_STRIDE
        kind = ighw.u32(base + 0x0C)
        family = KIND_FAMILIES.get(kind)
        if family is None:
            continue
        local_index = ighw.u32(base + 0x08)
        entries = by_kind.setdefault(kind, {})
        if local_index in entries:
            duplicates.add(kind)
            continue
        entries[local_index] = FomDebugName(
            name=_string(
                ighw,
                ighw.u32(base),
                f"{label} {family} name {local_index}",
            ),
            guid=_string(
                ighw,
                ighw.u32(base + 0x04),
                f"{label} {family} guid {local_index}",
            ),
        )
    names: dict[str, tuple[FomDebugName, ...]] = {}
    for kind, entries in by_kind.items():
        if kind in duplicates or sorted(entries) != list(range(len(entries))):
            continue
        names[KIND_FAMILIES[kind]] = tuple(
            entries[index] for index in range(len(entries))
        )
    return names


def prepare_debug_dat(
    iso_path,
    level_name: str,
    cached_level,
    *,
    container_member: str,
    game_label: str,
) -> Path | None:
    """Extract the optional level ``ps3debug.dat`` into the cached level."""

    from ...core.errors import InsomniacError
    from ...core.iso import _extract_psarc_members_from_iso

    folder = Path(cached_level)
    if folder.is_file():
        folder = folder.parent
    target = folder / "ps3debug.dat"
    anchor = folder / "ps3levelmain.dat"
    if (
        target.is_file()
        and anchor.is_file()
        and target.stat().st_mtime >= anchor.stat().st_mtime
    ):
        return target
    level = level_name.strip().replace("\\", "/").strip("/")
    try:
        _extract_psarc_members_from_iso(
            Path(iso_path),
            container_member,
            {f"packed/levels/{level}/ps3debug.dat": target},
            game_label=game_label,
        )
        if target.stat().st_size > MAX_DEBUG_DAT_SIZE:
            raise FormatError(f"{target}: ps3debug.dat is unreasonably large")
        parse_debug_names(target.read_bytes(), label=str(target))
    except (InsomniacError, OSError) as exc:
        target.unlink(missing_ok=True)
        LOGGER.warning("%s: ps3debug.dat names unavailable: %s", level, exc)
        return None
    return target


def prepare_game_debug_dat(
    iso_path,
    cached_level,
    *,
    container_member: str,
    game_label: str,
) -> Path | None:
    """Extract the game-wide ``ps3debug.dat`` (class names) for a level."""

    from ...core.errors import InsomniacError
    from ...core.iso import _extract_psarc_members_from_iso
    from ..class_names import parse_rfom_class_names

    folder = Path(cached_level)
    if folder.is_file():
        folder = folder.parent
    target = folder / GAME_DEBUG_CACHE_NAME
    if target.is_file():
        return target
    try:
        _extract_psarc_members_from_iso(
            Path(iso_path),
            container_member,
            {GAME_DEBUG_PSARC_MEMBER: target},
            game_label=game_label,
        )
        if target.stat().st_size > MAX_DEBUG_DAT_SIZE:
            raise FormatError(f"{target}: ps3debug.dat is unreasonably large")
        parse_rfom_class_names(target.read_bytes(), label=str(target))
    except (InsomniacError, OSError) as exc:
        target.unlink(missing_ok=True)
        LOGGER.warning("Gameplay class names unavailable: %s", exc)
        return None
    return target
