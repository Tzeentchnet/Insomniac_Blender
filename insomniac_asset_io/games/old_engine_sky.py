"""Old-engine sky: definitions and the shared shell mesh.

``main.dat`` section ``0x8150`` is one ``0x100``-byte sky definition. Its
``+0xF8``/``+0xFC`` words locate a shared shell vertex stream in
``vertices.dat`` ``0x9000`` and an index stream in ``0x9100``. Sections
``0x8160`` (drawn before the scene) and ``0x8170`` (after it) hold ``0x20``
shell records:

- ``+0x00`` first index in the shared ``u16`` stream;
- ``+0x04`` ``u16`` index count, ``+0x06`` ``u16`` draw order;
- ``+0x08`` ``0x5200`` texture index;
- ``+0x0C`` bit 0: the shell feeds bloom (destination alpha), not colour;
- ``+0x10``/``+0x14`` fixed Z and Y rotations, ``+0x18`` spin rate and
  ``+0x1C`` phase, composed as ``Ry(phase) Rz(+0x10) Ry(+0x14)``.

Shell vertices are 16 bytes: ``half4`` position (W = 1), ``u8x4`` colour,
and ``half2`` UV. The definition also names three cloud textures at
``+0xE8``/``+0xEC``/``+0xF0`` and a sky time scale at ``+0x5C``.

ReLunacy (``Loading/Readers/SkyReader.cs``, ``Assets/Sky/SkyData.cs``)
published this layout. On our TOD disc 19 of 20 levels have a sky; every
shell's indices and texture index are in range and every vertex has W = 1,
with positions inside a radius of about 1 to 3.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..core.binary import IghwFile
from ..core.errors import FormatError

SKY_DEFS_SECTION = 0x8150
SKY_DEFS_SIZE = 0x100
SHELL_SECTIONS = (0x8160, 0x8170)
SHELL_STRIDE = 0x20
VERTEX_SECTION = 0x9000
INDEX_SECTION = 0x9100
SHELL_VERTEX_STRIDE = 0x10
NO_TEXTURE = 0xFFFF_FFFF
MAX_SHELLS = 256


@dataclass(frozen=True, slots=True)
class SkyShell:
    section_id: int
    index: int
    texture_index: int
    draw_order: int
    bloom: bool
    rotation_z: float
    rotation_y: float
    spin_rate: float
    phase: float
    positions: np.ndarray
    colors: np.ndarray
    uvs: np.ndarray
    indices: np.ndarray


@dataclass(frozen=True, slots=True)
class LevelSky:
    shells: tuple[SkyShell, ...]
    cloud_texture_indices: tuple[int | None, ...]
    time_scale: float


def parse_sky(
    main: IghwFile,
    vertices: IghwFile,
    read_vertices: Callable[[int, int], bytes],
) -> LevelSky | None:
    """Decode the sky; ``vertices`` supplies section offsets and
    ``read_vertices(offset, length)`` reads ``vertices.dat`` bytes."""

    defs = main.section(SKY_DEFS_SECTION)
    if defs is None:
        return None
    if defs.count != 1 or defs.length != SKY_DEFS_SIZE:
        raise FormatError(
            f"{main.label}: sky definition is {defs.count} x "
            f"{defs.length:#x} bytes; expected one {SKY_DEFS_SIZE:#x} record"
        )
    main.require(defs.offset, SKY_DEFS_SIZE, purpose="sky definition")
    base = defs.offset
    vertex_offset = main.u32(base + 0xF8)
    index_offset = main.u32(base + 0xFC)
    texture_section = main.section(0x5200)
    texture_count = texture_section.count if texture_section else 0

    records: list[tuple[int, int, int, int, int, int, int, tuple]] = []
    for section_id in SHELL_SECTIONS:
        section = main.section(section_id)
        if section is None:
            continue
        if section.length != SHELL_STRIDE or section.count > MAX_SHELLS:
            raise FormatError(
                f"{main.label}: sky shell section 0x{section_id:X} has "
                f"{section.count} x {section.length:#x} records"
            )
        for index in range(section.count):
            record = section.offset + index * SHELL_STRIDE
            texture_index = main.u32(record + 8)
            if texture_index != NO_TEXTURE and texture_index >= texture_count:
                raise FormatError(
                    f"{main.label}: sky shell 0x{section_id:X}[{index}] "
                    f"texture {texture_index} is outside the texture table"
                )
            records.append(
                (
                    section_id,
                    index,
                    main.u32(record),
                    main.u16(record + 4),
                    main.u16(record + 6),
                    texture_index,
                    main.u32(record + 0x0C),
                    tuple(main.f32(record + offset) for offset in range(0x10, 0x20, 4)),
                )
            )
    if not records:
        return LevelSky(shells=(), cloud_texture_indices=(), time_scale=1.0)

    vertex_section = vertices.require_section(VERTEX_SECTION)
    index_section = vertices.require_section(INDEX_SECTION)
    vertex_end = vertex_section.offset + vertex_section.count * vertex_section.length
    index_end = index_section.offset + index_section.count * index_section.length
    shells: list[SkyShell] = []
    for (
        section_id,
        index,
        first_index,
        index_count,
        draw_order,
        texture_index,
        flags,
        (rotation_z, rotation_y, spin_rate, phase),
    ) in records:
        if not index_count:
            continue
        if index_count % 3:
            raise FormatError(
                f"{main.label}: sky shell 0x{section_id:X}[{index}] index "
                f"count {index_count} is not a triangle list"
            )
        start = index_section.offset + index_offset + first_index * 2
        if start + index_count * 2 > index_end:
            raise FormatError(
                f"{main.label}: sky shell 0x{section_id:X}[{index}] indices "
                "overrun vertices.dat 0x9100"
            )
        global_indices = np.frombuffer(
            read_vertices(start, index_count * 2),
            dtype=">u2",
            count=index_count,
        ).astype(np.int64)
        used, local = np.unique(global_indices, return_inverse=True)
        vertex_start = vertex_section.offset + vertex_offset
        if vertex_start + (int(used.max()) + 1) * SHELL_VERTEX_STRIDE > vertex_end:
            raise FormatError(
                f"{main.label}: sky shell 0x{section_id:X}[{index}] vertices "
                "overrun vertices.dat 0x9000"
            )
        raw = np.frombuffer(
            read_vertices(
                vertex_start,
                (int(used.max()) + 1) * SHELL_VERTEX_STRIDE,
            ),
            dtype=np.uint8,
        ).reshape((-1, SHELL_VERTEX_STRIDE))[used]
        positions = raw[:, 0:8].copy().view(">f2").astype(np.float32)
        uvs = raw[:, 12:16].copy().view(">f2").astype(np.float32)
        if not (np.isfinite(positions).all() and np.isfinite(uvs).all()):
            raise FormatError(
                f"{main.label}: sky shell 0x{section_id:X}[{index}] has "
                "non-finite vertices"
            )
        shells.append(
            SkyShell(
                section_id=section_id,
                index=index,
                texture_index=texture_index,
                draw_order=draw_order,
                bloom=bool(flags & 1),
                rotation_z=rotation_z,
                rotation_y=rotation_y,
                spin_rate=spin_rate,
                phase=phase,
                positions=positions[:, :3].copy(),
                colors=raw[:, 8:12].astype(np.float32) / 255.0,
                uvs=uvs,
                indices=local.reshape((-1, 3)).astype(np.int32),
            )
        )
    clouds = tuple(
        None if value == NO_TEXTURE else value
        for value in (main.u32(base + offset) for offset in (0xE8, 0xEC, 0xF0))
    )
    return LevelSky(
        shells=tuple(shells),
        cloud_texture_indices=clouds,
        time_scale=main.f32(base + 0x5C),
    )


def shell_rotation(shell: SkyShell) -> np.ndarray:
    """The shell's 3x3 local rotation, ``Ry(phase) Rz(z) Ry(y)``."""

    def rotation_y(angle: float) -> np.ndarray:
        c, s = np.cos(angle), np.sin(angle)
        return np.array(((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c)))

    def rotation_z(angle: float) -> np.ndarray:
        c, s = np.cos(angle), np.sin(angle)
        return np.array(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))

    return (
        rotation_y(shell.phase)
        @ rotation_z(shell.rotation_z)
        @ rotation_y(shell.rotation_y)
    ).astype(np.float32)
