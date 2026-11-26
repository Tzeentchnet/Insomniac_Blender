"""Tools of Destruction gameplay context: volumes, paths, Clues, and more.

All records live in the level ``gameplay.dat`` and use absolute, big-endian
offsets into it. Gameplay coordinates are already in meters, like the
gameplay Moby placements.

ReLunacy (``docs/reverse/tod_volumes_clues_paths.md``) published the section
IDs and most record layouts. The following were re-derived or corrected on
all 20 levels of our ``BCUS98127`` disc:

- The volume shape is the byte at ``+0x80``: 0 box, 1 cylinder, 2 sphere,
  3 capsule (6,450 / 320 / 34 / 3 records). Shape 2 has equal axis lengths
  on 31 of 34 records. Hazard Clue volumes enclose their collision faces
  only when the unit shape spans -1..1, so the stored axes are half-extents:
  3,015 lava faces lie inside their ``ClueLava`` boxes at half-extent 1.0
  and 13 at 0.5.
- A Clue record is ``{u32 volume pointer, u32 payload pointer, u32 payload
  size, u32 class number}``; every volume pointer lands on a volume record.
- An area record is ``{u32 path-list pointer, u32 volume-list pointer, u16
  path count, u16 volume count, u32 0}`` with ``u16`` indices.
- Every path's cumulative point lengths match its segment lengths, and its
  total length is the open polyline length.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...core.binary import IghwFile
from ...core.errors import FormatError
from ..class_names import read_class_id
from ..resistance_2.formats import (
    GameplayArea,
    GameplayClue,
    GameplayClueGroup,
    GameplayMarker,
    GameplayMobyGroup,
    GameplayPath,
    GameplayTrigger,
)
from ..resistance_2.reader import _matrix_from_zyx

SECT_PATHS = 0x7700
SECT_PATH_POINTS = 0x7710
SECT_VOLUMES = 0x7740
SECT_CLUES = 0x7780
SECT_CONTROLLERS = 0x77C0
SECT_PODS = 0x7900
SECT_POD_HANDLES = 0x7910
SECT_AREAS = 0x7940
SECT_AREA_INDICES = 0x7950
SECT_SCENTS = 0x7980
SECT_SCENT_HANDLES = 0x7990

PATH_STRIDE = 0x10
VOLUME_STRIDE = 0x90
CLUE_STRIDE = 0x10
CONTROLLER_STRIDE = 0x30
LIST_STRIDE = 0x10
MAX_PATH_POINTS = 65_536
MAX_LIST_ENTRIES = 65_536

# TOD volume shape byte -> shared trigger shape kind
# (0 cuboid, 1 sphere, 2 cylinder, 3 pill).
_VOLUME_SHAPES = {0: 0, 1: 2, 2: 1, 3: 3}
# The unit shape spans -1..1 along each stored axis.
VOLUME_HALF_EXTENT = 1.0


@dataclass(slots=True)
class TodGameplayContext:
    paths: list[GameplayPath] = field(default_factory=list)
    volumes: list[GameplayTrigger] = field(default_factory=list)
    clues: list[GameplayClue] = field(default_factory=list)
    controllers: list[GameplayMarker] = field(default_factory=list)
    pods: list[GameplayMobyGroup] = field(default_factory=list)
    areas: list[GameplayArea] = field(default_factory=list)
    scents: list[GameplayClueGroup] = field(default_factory=list)
    # Controller class numbers (None without a class block), for naming.
    controller_class_ids: list[int | None] = field(default_factory=list)


def read_tod_gameplay_context(
    gameplay: IghwFile,
    *,
    moby_count: int,
    label: str,
) -> TodGameplayContext:
    context = TodGameplayContext()
    context.paths = _read_paths(gameplay, label)
    context.volumes = _read_volumes(gameplay, label)
    context.clues = _read_clues(gameplay, len(context.volumes), label)
    (
        context.controllers,
        context.controller_class_ids,
    ) = _read_controllers(gameplay, label)
    context.pods = _read_pods(gameplay, moby_count, label)
    context.areas = _read_areas(
        gameplay,
        len(context.paths),
        len(context.volumes),
        label,
    )
    context.scents = _read_scents(gameplay, len(context.clues), label)
    return context


def _table(gameplay: IghwFile, section_id: int, stride: int, label: str):
    section = gameplay.section(section_id)
    if section is None:
        return None
    if section.length != stride:
        raise FormatError(
            f"{label}: section 0x{section_id:X} stride is "
            f"{section.length:#x}; expected {stride:#x}"
        )
    gameplay.require(
        section.offset,
        section.count * stride,
        purpose=f"section 0x{section_id:X}",
    )
    return section


def _blob_range(
    gameplay: IghwFile,
    section_id: int,
    label: str,
) -> tuple[int, int] | None:
    section = gameplay.section(section_id)
    if section is None:
        return None
    size = section.count * section.length
    gameplay.require(section.offset, size, purpose=f"section 0x{section_id:X}")
    return section.offset, section.offset + size


def _require_in(
    blob: tuple[int, int] | None,
    pointer: int,
    size: int,
    what: str,
    label: str,
) -> None:
    if blob is None or not (blob[0] <= pointer and pointer + size <= blob[1]):
        raise FormatError(
            f"{label}: {what} [{pointer:#x}, {pointer + size:#x}) lies "
            "outside its data section"
        )


def _finite(values: np.ndarray, what: str, label: str) -> None:
    if not np.all(np.isfinite(values)):
        raise FormatError(f"{label}: {what} contains non-finite values")


def _read_paths(gameplay: IghwFile, label: str) -> list[GameplayPath]:
    section = _table(gameplay, SECT_PATHS, PATH_STRIDE, label)
    if section is None:
        return []
    points_blob = _blob_range(gameplay, SECT_PATH_POINTS, label)
    paths: list[GameplayPath] = []
    for index in range(section.count):
        base = section.offset + index * PATH_STRIDE
        first = gameplay.u32(base)
        count = gameplay.u32(base + 4)
        if not 0 < count <= MAX_PATH_POINTS:
            raise FormatError(
                f"{label}: path {index} has unreasonable point count {count}"
            )
        _require_in(points_blob, first, count * 16, f"path {index}", label)
        points = np.frombuffer(
            gameplay.data,
            dtype=">f4",
            count=count * 4,
            offset=first,
        ).reshape((count, 4))
        _finite(points, f"path {index}", label)
        paths.append(
            GameplayPath(
                index=index,
                points=points[:, :3].astype(np.float32, copy=True),
                flags=gameplay.u32(base + 12),
                length=gameplay.f32(base + 8),
            )
        )
    return paths


def _read_volumes(gameplay: IghwFile, label: str) -> list[GameplayTrigger]:
    section = _table(gameplay, SECT_VOLUMES, VOLUME_STRIDE, label)
    if section is None:
        return []
    volumes: list[GameplayTrigger] = []
    for index in range(section.count):
        base = section.offset + index * VOLUME_STRIDE
        stored = np.frombuffer(
            gameplay.data,
            dtype=">f4",
            count=16,
            offset=base,
        ).reshape((4, 4))
        _finite(stored, f"volume {index}", label)
        if not np.array_equal(stored[:, 3], (0.0, 0.0, 0.0, 1.0)):
            raise FormatError(
                f"{label}: volume {index} transform is not affine"
            )
        shape = gameplay.data[base + 0x80]
        if shape not in _VOLUME_SHAPES:
            raise FormatError(
                f"{label}: volume {index} has unknown shape {shape}"
            )
        if any(gameplay.data[base + 0x81 : base + VOLUME_STRIDE]):
            raise FormatError(
                f"{label}: volume {index} has non-zero reserved fields"
            )
        # Rows 0-2 are the scaled axes and row 3 the translation
        # (row-vector convention); transpose to column-vector form.
        matrix = stored.T.astype(np.float32, copy=True)
        volumes.append(
            GameplayTrigger(
                index=index,
                shape_kind=_VOLUME_SHAPES[shape],
                matrix=matrix,
                half_extent=VOLUME_HALF_EXTENT,
            )
        )
    return volumes


def _read_clues(
    gameplay: IghwFile,
    volume_count: int,
    label: str,
) -> list[GameplayClue]:
    section = _table(gameplay, SECT_CLUES, CLUE_STRIDE, label)
    if section is None:
        return []
    volumes = gameplay.section(SECT_VOLUMES)
    clues: list[GameplayClue] = []
    for index in range(section.count):
        base = section.offset + index * CLUE_STRIDE
        volume_pointer = gameplay.u32(base)
        payload_pointer = gameplay.u32(base + 4)
        payload_size = gameplay.u32(base + 8)
        class_id = gameplay.u32(base + 12)
        if volumes is None:
            raise FormatError(f"{label}: Clue {index} has no volume table")
        relative = volume_pointer - volumes.offset
        volume_index, remainder = divmod(relative, VOLUME_STRIDE)
        if relative < 0 or remainder or volume_index >= volume_count:
            raise FormatError(
                f"{label}: Clue {index} volume pointer "
                f"{volume_pointer:#x} is not a volume record"
            )
        if payload_pointer:
            gameplay.require(
                payload_pointer,
                payload_size,
                purpose=f"Clue {index} payload",
            )
        clues.append(
            GameplayClue(
                index=index,
                class_id=class_id,
                trigger_index=volume_index,
                payload_size=payload_size,
            )
        )
    return clues


def _read_controllers(
    gameplay: IghwFile,
    label: str,
) -> tuple[list[GameplayMarker], list[int | None]]:
    section = _table(gameplay, SECT_CONTROLLERS, CONTROLLER_STRIDE, label)
    if section is None:
        return [], []
    controllers: list[GameplayMarker] = []
    class_ids: list[int | None] = []
    for index in range(section.count):
        base = section.offset + index * CONTROLLER_STRIDE
        values = np.frombuffer(
            gameplay.data,
            dtype=">f4",
            count=9,
            offset=base + 4,
        )
        _finite(values, f"controller {index}", label)
        position = tuple(float(value) for value in values[0:3])
        rotation = tuple(float(value) for value in values[3:6])
        scale = tuple(float(value) for value in values[6:9])
        matrix = _matrix_from_zyx(position, rotation, 1.0)
        matrix[:3, :3] *= np.asarray(scale, dtype=np.float32)
        class_ids.append(
            read_class_id(
                gameplay,
                gameplay.u32(base),
                label=f"{label} controller {index}",
            )
        )
        controllers.append(
            GameplayMarker(
                index=index,
                marker_class=class_ids[-1] if class_ids[-1] is not None else 0,
                matrix=matrix,
                scale=scale,
                payload_size=gameplay.u32(base + 0x28),
            )
        )
    return controllers, class_ids


def _handle_list(
    gameplay: IghwFile,
    list_section: int,
    table_section: int,
    family: str,
    label: str,
) -> list[tuple[int, list[int]]]:
    section = _table(gameplay, table_section, LIST_STRIDE, label)
    if section is None:
        return []
    blob = _blob_range(gameplay, list_section, label)
    result: list[tuple[int, list[int]]] = []
    for index in range(section.count):
        base = section.offset + index * LIST_STRIDE
        pointer = gameplay.u32(base)
        count = gameplay.u32(base + 4)
        if count > MAX_LIST_ENTRIES:
            raise FormatError(
                f"{label}: {family} {index} has unreasonable count {count}"
            )
        if gameplay.u32(base + 8) or gameplay.u32(base + 12):
            raise FormatError(
                f"{label}: {family} {index} has non-zero reserved fields"
            )
        if count:
            _require_in(blob, pointer, count * 4, f"{family} {index}", label)
        result.append(
            (
                index,
                [gameplay.u32(pointer + item * 4) for item in range(count)],
            )
        )
    return result


def _read_pods(
    gameplay: IghwFile,
    moby_count: int,
    label: str,
) -> list[GameplayMobyGroup]:
    pods: list[GameplayMobyGroup] = []
    for index, handles in _handle_list(
        gameplay,
        SECT_POD_HANDLES,
        SECT_PODS,
        "pod",
        label,
    ):
        members: list[int] = []
        for handle in handles:
            # Moby handles pack (index + 1) << 16 | index.
            moby_index = handle & 0xFFFF
            if handle >> 16 != moby_index + 1 or moby_index >= moby_count:
                raise FormatError(
                    f"{label}: pod {index} has invalid Moby handle "
                    f"{handle:#x}"
                )
            members.append(moby_index)
        pods.append(
            GameplayMobyGroup(index=index, moby_indices=tuple(members))
        )
    return pods


def _read_scents(
    gameplay: IghwFile,
    clue_count: int,
    label: str,
) -> list[GameplayClueGroup]:
    scents: list[GameplayClueGroup] = []
    for index, handles in _handle_list(
        gameplay,
        SECT_SCENT_HANDLES,
        SECT_SCENTS,
        "scent",
        label,
    ):
        members: list[int] = []
        for handle in handles:
            # Clue handles pack 0x10000 | index.
            clue_index = handle & 0xFFFF
            if handle >> 16 != 1 or clue_index >= clue_count:
                raise FormatError(
                    f"{label}: scent {index} has invalid Clue handle "
                    f"{handle:#x}"
                )
            members.append(clue_index)
        scents.append(
            GameplayClueGroup(index=index, clue_indices=tuple(members))
        )
    return scents


def _read_areas(
    gameplay: IghwFile,
    path_count: int,
    volume_count: int,
    label: str,
) -> list[GameplayArea]:
    section = _table(gameplay, SECT_AREAS, LIST_STRIDE, label)
    if section is None:
        return []
    blob = _blob_range(gameplay, SECT_AREA_INDICES, label)
    areas: list[GameplayArea] = []
    for index in range(section.count):
        base = section.offset + index * LIST_STRIDE
        path_pointer = gameplay.u32(base)
        volume_pointer = gameplay.u32(base + 4)
        path_total = gameplay.u16(base + 8)
        volume_total = gameplay.u16(base + 10)
        if gameplay.u32(base + 12):
            raise FormatError(
                f"{label}: area {index} has non-zero reserved fields"
            )
        lists: list[tuple[int, ...]] = []
        for pointer, count, limit, what in (
            (path_pointer, path_total, path_count, "path"),
            (volume_pointer, volume_total, volume_count, "volume"),
        ):
            if not count:
                lists.append(())
                continue
            _require_in(
                blob,
                pointer,
                count * 2,
                f"area {index} {what} list",
                label,
            )
            values = tuple(
                gameplay.u16(pointer + item * 2) for item in range(count)
            )
            if any(value >= limit for value in values):
                raise FormatError(
                    f"{label}: area {index} references a {what} beyond "
                    f"{limit}"
                )
            lists.append(values)
        areas.append(
            GameplayArea(
                index=index,
                name="",
                path_indices=lists[0],
                trigger_indices=lists[1],
                flags=0,
            )
        )
    return areas
