"""Retail Tools of Destruction ``DEBUG.DAT`` name tables.

Every retail ``BCUS98127`` level ships a loose
``PS3_GAME/USRDIR/BUILT/LEVELS/<LEVEL>/DEBUG.DAT`` IGHW file beside its
packed archives. Its tables are index-parallel to the level's ``main.dat`` and
``gameplay.dat`` record tables. The section IDs and instance record layout
were first published by the ReLunacy project (VELD-Dev, NefariousTechSupport
and contributors); the counts, index pairing, and GUID encoding were
re-verified against all 20 levels of our disc build.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import uuid

from ...core.binary import IghwFile
from ...core.errors import FormatError
from ..resistance_2.formats import AssetKind

PROTOTYPE_STRIDE = 0x10
INSTANCE_STRIDE = 0x18
SHADER_STRIDE = 0x30
MAX_DEBUG_RECORDS = 1_000_000
MAX_NAME_LENGTH = 1024
MAX_DEBUG_DAT_SIZE = 64 * 1024 * 1024

# Prototype name tables: {u64 tuid, u32 name pointer, u32 0}.
PROTOTYPE_SECTIONS = {
    AssetKind.MOBY: 0x9480,
    AssetKind.TIE: 0x9280,
    AssetKind.FOLIAGE: 0x9380,
    AssetKind.SHRUB: 0x9580,
}

# Instance name tables: {u8 guid[16], u32 name pointer, u16 zone, u16 0}.
INSTANCE_SECTIONS = {
    "moby": 0x73C0,
    "tie": 0x72C0,
    "foliage": 0x74C0,
    "shrub": 0x7560,
    "path": 0x7720,
    "volume": 0x7760,
    "clue": 0x77A0,
    "controller": 0x77D0,
    "pod": 0x7920,
    "area": 0x7960,
    "scent": 0x79A0,
}

# The game-wide enum lookup file (gameplay class names), cached beside the
# level archives under its own name so it never matches a level DEBUG.DAT.
GAME_DEBUG_DAT_MEMBER = "PS3_GAME/USRDIR/BUILT/GAME/DEBUG.DAT"
GAME_DEBUG_CACHE_NAME = "game_debug.dat"

# Shader name records: name pointer at +0x08 and four texture source paths
# at +0x20.
SHADER_SECTION = 0x5D00

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DebugInstanceName:
    name: str
    guid: str
    zone: int


@dataclass(frozen=True, slots=True)
class TodDebugNames:
    prototypes: dict[AssetKind, tuple[str, ...]]
    instances: dict[str, tuple[DebugInstanceName, ...]]
    shaders: tuple[str, ...]
    shader_texture_paths: tuple[tuple[str | None, ...], ...]


def _name(ighw: IghwFile, pointer: int, label: str) -> str:
    if pointer <= 0 or pointer >= ighw.size:
        raise FormatError(f"{label}: name pointer {pointer:#x} is out of range")
    value = ighw.cstring(pointer, max_length=MAX_NAME_LENGTH)
    if not value.isprintable():
        raise FormatError(f"{label}: name at {pointer:#x} is not printable")
    return value


def _table(ighw: IghwFile, section_id: int, stride: int, label: str):
    section = ighw.section(section_id)
    if section is None:
        return None
    if section.length != stride:
        raise FormatError(
            f"{label}: section 0x{section_id:X} stride is "
            f"{section.length:#x}; expected {stride:#x}"
        )
    if section.count > MAX_DEBUG_RECORDS:
        raise FormatError(
            f"{label}: section 0x{section_id:X} has an unreasonable "
            f"record count {section.count}"
        )
    ighw.require(
        section.offset,
        section.count * stride,
        purpose=f"debug section 0x{section_id:X}",
    )
    return section


def parse_debug_names(data: bytes, *, label: str) -> TodDebugNames:
    """Decode the name tables of one retail TOD ``DEBUG.DAT``."""

    ighw = IghwFile(data, label=label)
    prototypes: dict[AssetKind, tuple[str, ...]] = {}
    for kind, section_id in PROTOTYPE_SECTIONS.items():
        section = _table(ighw, section_id, PROTOTYPE_STRIDE, label)
        if section is None:
            continue
        prototypes[kind] = tuple(
            _name(
                ighw,
                ighw.u32(section.offset + index * PROTOTYPE_STRIDE + 8),
                f"{label} {kind.name.lower()} prototype {index}",
            )
            for index in range(section.count)
        )

    instances: dict[str, tuple[DebugInstanceName, ...]] = {}
    for family, section_id in INSTANCE_SECTIONS.items():
        section = _table(ighw, section_id, INSTANCE_STRIDE, label)
        if section is None:
            continue
        records: list[DebugInstanceName] = []
        for index in range(section.count):
            base = section.offset + index * INSTANCE_STRIDE
            records.append(
                DebugInstanceName(
                    name=_name(
                        ighw,
                        ighw.u32(base + 0x10),
                        f"{label} {family} instance {index}",
                    ),
                    # instances.lua prints the same 16 bytes as a
                    # little-endian (Microsoft) GUID.
                    guid=str(uuid.UUID(bytes_le=ighw.bytes(base, 16))),
                    zone=ighw.u16(base + 0x14),
                )
            )
        instances[family] = tuple(records)

    shaders: tuple[str, ...] = ()
    shader_texture_paths: tuple[tuple[str | None, ...], ...] = ()
    section = _table(ighw, SHADER_SECTION, SHADER_STRIDE, label)
    if section is not None:
        names: list[str] = []
        paths: list[tuple[str | None, ...]] = []
        for index in range(section.count):
            base = section.offset + index * SHADER_STRIDE
            names.append(
                _name(ighw, ighw.u32(base + 8), f"{label} shader {index}")
            )
            slots: list[str | None] = []
            for slot in range(4):
                path = _name(
                    ighw,
                    ighw.u32(base + 0x20 + slot * 4),
                    f"{label} shader {index} texture {slot}",
                ) if ighw.u32(base + 0x20 + slot * 4) else ""
                slots.append(path or None)
            paths.append(tuple(slots))
        shaders = tuple(names)
        shader_texture_paths = tuple(paths)

    return TodDebugNames(
        prototypes=prototypes,
        instances=instances,
        shaders=shaders,
        shader_texture_paths=shader_texture_paths,
    )


def debug_dat_iso_member(level_name: str) -> str:
    level = level_name.strip().replace("\\", "/").strip("/")
    return f"PS3_GAME/USRDIR/BUILT/LEVELS/{level}/DEBUG.DAT"


def prepare_debug_dat(
    iso_path,
    level_name: str,
    cached_level,
    *,
    game_label: str,
) -> Path | None:
    """Copy the level's loose DEBUG.DAT beside its cached level archive.

    The file is optional: a missing or malformed member leaves numeric names
    in place instead of failing the level import.
    """

    from ...core.errors import InsomniacError
    from ...core.iso import read_game_iso_member

    level_file = Path(cached_level)
    folder = level_file if level_file.is_dir() else level_file.parent
    target = folder / "debug.dat"
    if (
        target.is_file()
        and level_file.exists()
        and target.stat().st_mtime >= level_file.stat().st_mtime
    ):
        return target
    try:
        data = read_game_iso_member(
            iso_path,
            debug_dat_iso_member(level_name),
            game_label=game_label,
            max_size=MAX_DEBUG_DAT_SIZE,
        )
        parse_debug_names(data, label=f"{level_name}/DEBUG.DAT")
    except (InsomniacError, OSError) as exc:
        LOGGER.warning("%s: DEBUG.DAT names unavailable: %s", level_name, exc)
        return None
    folder.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


def prepare_game_debug_dat(
    iso_path,
    cached_level,
    *,
    game_label: str,
) -> Path | None:
    """Copy the game-wide DEBUG.DAT (class names) beside the level archive.

    Optional, like the level file: failure leaves class names unset.
    """

    from ...core.errors import InsomniacError
    from ...core.iso import read_game_iso_member
    from ..class_names import parse_tod_class_names

    level_file = Path(cached_level)
    folder = level_file if level_file.is_dir() else level_file.parent
    target = folder / GAME_DEBUG_CACHE_NAME
    if target.is_file():
        return target
    try:
        data = read_game_iso_member(
            iso_path,
            GAME_DEBUG_DAT_MEMBER,
            game_label=game_label,
            max_size=MAX_DEBUG_DAT_SIZE,
        )
        parse_tod_class_names(data, label="BUILT/GAME/DEBUG.DAT")
    except (InsomniacError, OSError) as exc:
        LOGGER.warning("Gameplay class names unavailable: %s", exc)
        return None
    folder.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target
