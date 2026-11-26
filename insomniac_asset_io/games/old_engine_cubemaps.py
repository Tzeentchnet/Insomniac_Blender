"""Old-engine environment cubemaps (``main.dat`` section ``0x5920``).

Each ``0x20`` record is the RSX texture descriptor used by ``0x5200``, with
the cube bit set. Its offset follows the descriptor's GCM location: MAIN is
relative to a resident ``main.dat`` section (TOD ``0x9650``, RFoM
``0x9950``) and LOCAL to ``textures.dat``. A cube stores six faces in
``+X, -X, +Y, -Y, +Z, -Z`` order, each with its complete mip chain padded to
a 128-byte stride.

ReLunacy (``Loading/Readers/CubemapReader.cs``) published this layout. On
our discs every TOD cube is a swizzled 32x32 A8R8G8B8 six-mip cube in MAIN
memory (one to seven per level) and every checked RFoM cube a 64x64 R5G6B5
one; the resident sections end exactly at the last cube's sixth face.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..core.binary import IghwFile
from ..core.errors import FormatError, InsomniacError
from .resistance_2.formats import TextureInfo
from .resistance_2.textures import decode_texture, texture_mip_size

CUBEMAP_SECTION = 0x5920
DESCRIPTOR_STRIDE = 0x20
FACE_COUNT = 6
FACE_ALIGNMENT = 0x80
LOCATION_LOCAL = 1
LOCATION_MAIN = 2
MAX_CUBE_SIZE = 1024


@dataclass(frozen=True, slots=True)
class EnvironmentCubemap:
    index: int
    size: int
    format_code: int
    # (6, size, size, 4) RGBA8, faces in +X, -X, +Y, -Y, +Z, -Z order.
    faces: np.ndarray


def face_stride(size: int, mip_count: int, format_code: int) -> int:
    total = 0
    for level in range(mip_count):
        extent = max(1, size >> level)
        total += texture_mip_size(extent, extent, format_code)
    return (total + FACE_ALIGNMENT - 1) & ~(FACE_ALIGNMENT - 1)


def read_cubemaps(
    main: IghwFile,
    *,
    resident_section: int,
    read_local: Callable[[int, int], bytes] | None,
    warn: Callable[[str], None] | None = None,
) -> list[EnvironmentCubemap]:
    section = main.section(CUBEMAP_SECTION)
    if section is None:
        return []
    if section.length != DESCRIPTOR_STRIDE:
        raise FormatError(
            f"{main.label}: cubemap descriptor stride is "
            f"{section.length:#x}; expected {DESCRIPTOR_STRIDE:#x}"
        )
    resident = main.section(resident_section)
    cubemaps: list[EnvironmentCubemap] = []
    for index in range(section.count):
        base = section.offset + index * DESCRIPTOR_STRIDE
        format_word = main.u16(base + 6)
        location = format_word & 3
        if not (format_word >> 2) & 1:
            raise FormatError(
                f"{main.label}: cubemap {index} descriptor lacks the cube bit"
            )
        # Keep the linear (0x20) bit; drop the unnormalized (0x40) bit.
        format_code = (format_word >> 8) & ~0x40
        size = main.u16(base + 0x18)
        if size != main.u16(base + 0x1A) or not 0 < size <= MAX_CUBE_SIZE:
            raise FormatError(
                f"{main.label}: cubemap {index} is not a square face "
                f"({size}x{main.u16(base + 0x1A)})"
            )
        mip_count = max(1, main.u16(base + 4))
        offset = main.u32(base) & 0x7FFF_FFFF
        try:
            stride = face_stride(size, mip_count, format_code)
            face_length = texture_mip_size(size, size, format_code)
        except InsomniacError as exc:
            if warn is not None:
                warn(f"cubemap {index} skipped: {exc}")
            continue
        if location == LOCATION_MAIN:
            if resident is None:
                raise FormatError(
                    f"{main.label}: cubemap {index} is in MAIN memory but "
                    f"section 0x{resident_section:X} is missing"
                )
            resident_size = resident.count * resident.length
            if offset + FACE_COUNT * stride > resident_size:
                raise FormatError(
                    f"{main.label}: cubemap {index} overruns section "
                    f"0x{resident_section:X}"
                )
            start = resident.offset + offset

            def read_face(face: int) -> bytes:
                return main.bytes(start + face * stride, face_length)

        elif location == LOCATION_LOCAL and read_local is not None:

            def read_face(face: int) -> bytes:
                return read_local(offset + face * stride, face_length)

        else:
            if warn is not None:
                warn(f"cubemap {index} has an unsupported memory location")
            continue
        info = TextureInfo(
            id=base,
            tuid=base,
            format_code=format_code,
            mip_count=1,
            width=size,
            height=size,
            offset=0,
            length=face_length,
        )
        faces = np.stack(
            [
                decode_texture(info, read_face(face)).rgba
                for face in range(FACE_COUNT)
            ]
        )
        cubemaps.append(
            EnvironmentCubemap(
                index=index,
                size=size,
                format_code=format_code,
                faces=faces,
            )
        )
    return cubemaps


# OpenGL cube-map face selection, which the RSX follows: for each face the
# major axis and the (s, t) axes as (axis index, sign).
_FACES = (
    (0, 1.0, (2, -1.0), (1, -1.0)),
    (0, -1.0, (2, 1.0), (1, -1.0)),
    (1, 1.0, (0, 1.0), (2, 1.0)),
    (1, -1.0, (0, 1.0), (2, -1.0)),
    (2, 1.0, (0, 1.0), (1, -1.0)),
    (2, -1.0, (0, -1.0), (1, -1.0)),
)


def sample_directions(faces: np.ndarray, directions: np.ndarray) -> np.ndarray:
    """Nearest-texel lookup of unit ``directions`` (N, 3) in source space."""

    size = faces.shape[1]
    directions = np.asarray(directions, dtype=np.float64)
    major = np.argmax(np.abs(directions), axis=1)
    result = np.zeros((len(directions), 4), dtype=np.uint8)
    for face, (axis, sign, (s_axis, s_sign), (t_axis, t_sign)) in enumerate(
        _FACES
    ):
        mask = (major == axis) & (np.sign(directions[:, axis]) == sign)
        if not mask.any():
            continue
        chosen = directions[mask]
        magnitude = np.abs(chosen[:, axis])
        s = (s_sign * chosen[:, s_axis] / magnitude + 1.0) * 0.5
        t = (t_sign * chosen[:, t_axis] / magnitude + 1.0) * 0.5
        column = np.clip((s * size).astype(int), 0, size - 1)
        row = np.clip((t * size).astype(int), 0, size - 1)
        result[mask] = faces[face, row, column]
    return result
