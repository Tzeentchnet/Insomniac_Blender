"""Old-engine level fog, bloom, and global lighting.

Tools of Destruction keeps one ``0xF0`` fog record in ``main.dat`` section
``0x7100`` and one ``0x80`` lighting root in ``0x8B00``. Resistance: Fall of
Man keeps its lighting root in ``0x8A00``, followed by its light volumes, and
has no ``0x7100`` fog record.

ReLunacy (``Assets/Lighting/LevelFog.cs`` and
``Loading/Readers/LightingEnvironmentReader.cs``) published these offsets
from the games' section consumers. They were checked on our discs: all 20
TOD levels carry exactly one fog record and one version-2 lighting root,
every fog has ``near < far`` and colour channels within 0..1, bloom is
enabled everywhere, and both light directions are unit vectors. The bloom
values match ReLunacy's cited shader constants (Metropolis 0.6 / 2.2 / 0.6,
Meridian City 0.85 / 1.8 / 0.8). The RFoM roots are version 1 with unit
directions, and their length is ``0x80`` plus ``0x80`` per light volume.

A light direction points towards the light: the game shades with
``max(dot(N, direction), 0)``.

How the fog coordinate reaches the GPU is still unknown, so fog values are
authored settings, not an exact visual match.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.binary import IghwFile
from ..core.errors import FormatError

TOD_FOG_SECTION = 0x7100
TOD_FOG_STRIDE = 0xF0
TOD_LIGHTING_SECTION = 0x8B00
TOD_LIGHTING_STRIDE = 0x80
TOD_LIGHTING_VERSION = 2
RFOM_LIGHTING_SECTION = 0x8A00
RFOM_LIGHTING_ROOT = 0x80
RFOM_LIGHTING_VERSION = 1
LIGHT_VOLUME_STRIDE = 0x80
_UNIT_TOLERANCE = 1e-3

Vector3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class LevelLight:
    colour: Vector3
    # Unit vector towards the light.
    direction: Vector3


@dataclass(frozen=True, slots=True)
class LevelFog:
    colour: Vector3
    near: float
    far: float
    amount: float


@dataclass(frozen=True, slots=True)
class LevelBloom:
    mip_weight: float
    history: float
    extract_scale: float
    extract_exponent: float
    intensity: float
    colour_extract: bool


@dataclass(frozen=True, slots=True)
class LevelEnvironment:
    ambient: Vector3
    lights: tuple[LevelLight, ...]
    light_volume_count: int
    version: int
    fog: LevelFog | None = None
    bloom: LevelBloom | None = None
    source: str = ""


def _vec3(ighw: IghwFile, offset: int, what: str, label: str) -> Vector3:
    values = tuple(float(value) for value in ighw.vec3(offset))
    if not all(np.isfinite(values)):
        raise FormatError(f"{label}: {what} contains non-finite values")
    return values  # type: ignore[return-value]


def _lights(
    ighw: IghwFile,
    colours_offset: int,
    directions_offset: int,
    label: str,
) -> tuple[LevelLight, ...]:
    lights: list[LevelLight] = []
    for index in range(2):
        direction = _vec3(
            ighw,
            directions_offset + index * 0x10,
            f"light {index} direction",
            label,
        )
        length = float(np.linalg.norm(direction))
        if length == 0.0:
            continue
        if abs(length - 1.0) > _UNIT_TOLERANCE:
            raise FormatError(
                f"{label}: light {index} direction is not a unit vector "
                f"(length {length:.4f})"
            )
        lights.append(
            LevelLight(
                colour=_vec3(
                    ighw,
                    colours_offset + index * 0x10,
                    f"light {index} colour",
                    label,
                ),
                direction=direction,
            )
        )
    return tuple(lights)


def _single(ighw: IghwFile, section_id: int, label: str):
    section = ighw.section(section_id)
    if section is None:
        return None
    if section.count != 1:
        raise FormatError(
            f"{label}: section 0x{section_id:X} has {section.count} records; "
            "expected 1"
        )
    ighw.require(
        section.offset,
        section.length,
        purpose=f"section 0x{section_id:X}",
    )
    return section


def parse_tod_environment(main: IghwFile) -> LevelEnvironment | None:
    """Decode TOD ``main.dat`` fog (``0x7100``) and lighting (``0x8B00``)."""

    label = main.label
    root = _single(main, TOD_LIGHTING_SECTION, label)
    fog_section = _single(main, TOD_FOG_SECTION, label)
    if root is None and fog_section is None:
        return None

    ambient: Vector3 = (0.0, 0.0, 0.0)
    lights: tuple[LevelLight, ...] = ()
    volume_count = 0
    version = 0
    if root is not None:
        if root.length != TOD_LIGHTING_STRIDE:
            raise FormatError(
                f"{label}: lighting root is {root.length:#x} bytes; expected "
                f"{TOD_LIGHTING_STRIDE:#x}"
            )
        base = root.offset
        version = main.u32(base)
        if version != TOD_LIGHTING_VERSION:
            raise FormatError(
                f"{label}: unsupported lighting root version {version}"
            )
        volume_count = main.u32(base + 0x08)
        ambient = _vec3(main, base + 0x20, "ambient colour", label)
        lights = _lights(main, base + 0x30, base + 0x50, label)

    fog: LevelFog | None = None
    bloom: LevelBloom | None = None
    if fog_section is not None:
        if fog_section.length != TOD_FOG_STRIDE:
            raise FormatError(
                f"{label}: fog record is {fog_section.length:#x} bytes; "
                f"expected {TOD_FOG_STRIDE:#x}"
            )
        base = fog_section.offset
        near = main.f32(base + 0x4C)
        far = main.f32(base + 0x50)
        amount = main.f32(base + 0x54)
        if not (
            np.isfinite((near, far, amount)).all()
            and 0.0 <= near < far
            and 0.0 <= amount <= 1.0
        ):
            raise FormatError(
                f"{label}: implausible fog range near={near} far={far} "
                f"amount={amount}"
            )
        fog = LevelFog(
            colour=_vec3(main, base + 0x40, "fog colour", label),
            near=near,
            far=far,
            amount=amount,
        )
        if main.u32(base + 0x70):
            values = [
                main.f32(base + offset)
                for offset in (0x74, 0x78, 0x80, 0x84, 0x88)
            ]
            if not np.isfinite(values).all():
                raise FormatError(f"{label}: bloom has non-finite values")
            bloom = LevelBloom(
                mip_weight=values[0],
                history=values[1],
                extract_scale=values[2],
                extract_exponent=values[3],
                intensity=values[4],
                colour_extract=bool(main.u32(base + 0x8C)),
            )
    return LevelEnvironment(
        ambient=ambient,
        lights=lights,
        light_volume_count=volume_count,
        version=version,
        fog=fog,
        bloom=bloom,
        source="main.dat 0x7100/0x8B00",
    )


def parse_rfom_environment(main: IghwFile) -> LevelEnvironment | None:
    """Decode the RFoM ``ps3levelmain.dat`` lighting root (``0x8A00``)."""

    label = main.label
    root = _single(main, RFOM_LIGHTING_SECTION, label)
    if root is None:
        return None
    base = root.offset
    volume_offset = main.u32(base)
    volume_count = main.u32(base + 0x04)
    version = main.u32(base + 0x08)
    if version != RFOM_LIGHTING_VERSION:
        raise FormatError(
            f"{label}: unsupported lighting root version {version}"
        )
    if (
        volume_offset != RFOM_LIGHTING_ROOT
        or root.length != RFOM_LIGHTING_ROOT + volume_count * LIGHT_VOLUME_STRIDE
    ):
        raise FormatError(
            f"{label}: lighting root length {root.length:#x} does not hold "
            f"{volume_count} light volumes"
        )
    return LevelEnvironment(
        ambient=_vec3(main, base + 0x10, "ambient colour", label),
        lights=_lights(main, base + 0x20, base + 0x40, label),
        light_volume_count=volume_count,
        version=version,
        source="ps3levelmain.dat 0x8A00",
    )
