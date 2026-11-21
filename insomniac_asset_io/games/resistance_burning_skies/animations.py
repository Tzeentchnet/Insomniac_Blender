from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from numpy.typing import NDArray

from ...core.errors import FormatError
from .formats import NsirsrcFile, StreamDescriptor

ANIMATION_SET_ROOT_TYPE = 0x05
ANIMATION_CLIP_ROOT_TYPE = 0x08
ANIMATION_STREAM_CLASS = 0x00020000
ANIMATION_FRAME_RATE = 48
ANIMATION_DESCRIPTOR_OFFSET = 0x8C
ANIMATION_HEADER_SIZE = 0x30
ANIMATION_TRACK_SIZE = 0x10
ANIMATION_KEY_SIZE = 0x0C
ANIMATION_FILL = 0xDDDDDDDD

MAX_ANIMATION_TRACKS = 4096
MAX_ANIMATION_KEYS = 1_000_000
MAX_ANIMATION_BONES = 1024


class PackedTrackKind(IntEnum):
    RAW_0 = 0
    RAW_1 = 1
    RAW_2 = 2


@dataclass(slots=True)
class PackedAnimationTrack:
    bone_index: int
    kind: PackedTrackKind
    times: NDArray[np.float32]
    values: NDArray[np.int16]

    @property
    def key_count(self) -> int:
        return int(self.times.shape[0])


@dataclass(slots=True)
class PackedAnimationClip:
    frame_rate: int
    duration: float
    event_count: int
    event_offset: int
    tracks: tuple[PackedAnimationTrack, ...]

    @property
    def is_placeholder(self) -> bool:
        return self.frame_rate == 0 and not self.tracks


def packed_animation_descriptor(resource: NsirsrcFile) -> StreamDescriptor:
    if resource.root_type != ANIMATION_CLIP_ROOT_TYPE:
        raise FormatError(
            f"{resource.label}: root type 0x{resource.root_type:X} "
            "is not an animation clip"
        )
    resource.require(
        ANIMATION_DESCRIPTOR_OFFSET,
        0x20,
        purpose="packed animation stream descriptor",
    )
    scale = struct.unpack_from(
        "<f",
        resource.data,
        ANIMATION_DESCRIPTOR_OFFSET,
    )[0]
    (
        primary_size,
        primary_offset,
        primary_reserved,
        secondary_size,
        secondary_offset,
        secondary_reserved,
        trailing_reserved,
    ) = struct.unpack_from(
        "<7I",
        resource.data,
        ANIMATION_DESCRIPTOR_OFFSET + 4,
    )
    if scale != 1.0:
        raise FormatError(
            f"{resource.label}: unsupported animation stream scale {scale}"
        )
    if primary_reserved or secondary_reserved or trailing_reserved:
        raise FormatError(
            f"{resource.label}: animation stream descriptor has "
            "nonzero reserved fields"
        )
    if not primary_size or not secondary_size:
        raise FormatError(
            f"{resource.label}: animation stream descriptor is empty"
        )
    if primary_offset != 0 or secondary_offset != primary_size:
        raise FormatError(
            f"{resource.label}: unsupported packed animation stream offsets "
            f"0x{primary_offset:X} and 0x{secondary_offset:X}"
        )
    return StreamDescriptor(
        primary_size=primary_size,
        primary_offset=primary_offset,
        secondary_size=secondary_size,
        secondary_offset=secondary_offset,
    )


def animation_set_clip_names(resource: NsirsrcFile) -> tuple[str, ...]:
    if resource.root_type != ANIMATION_SET_ROOT_TYPE:
        raise FormatError(
            f"{resource.label}: root type 0x{resource.root_type:X} "
            "is not an animation set"
        )
    names: list[str] = []
    for index, dependency in enumerate(resource.dependencies()):
        if dependency.type_id != 0x02:
            raise FormatError(
                f"{resource.label}: animation dependency {index} has "
                f"unsupported type 0x{dependency.type_id:X}"
            )
        if not dependency.name:
            raise FormatError(
                f"{resource.label}: animation dependency {index} is unnamed"
            )
        names.append(dependency.name)
    if len(names) != len(set(names)):
        raise FormatError(
            f"{resource.label}: animation set contains duplicate clip names"
        )
    return tuple(names)


def inspect_packed_animation(
    data: bytes,
    *,
    label: str,
) -> PackedAnimationClip:
    if len(data) < 0x28:
        raise FormatError(
            f"{label}: packed animation header exceeds the "
            f"0x{len(data):X}-byte primary stream"
        )
    (
        track_count,
        frame_rate,
        reserved_0,
        reserved_1,
        reserved_2,
        reserved_3,
        event_count,
        event_offset,
        reserved_4,
    ) = struct.unpack_from("<9I", data)
    duration = struct.unpack_from("<f", data, 0x24)[0]
    if any((reserved_0, reserved_1, reserved_2, reserved_3, reserved_4)):
        raise FormatError(f"{label}: animation header has nonzero reserved fields")
    if not math.isfinite(duration) or duration < 0.0:
        raise FormatError(f"{label}: invalid animation duration {duration}")
    if track_count > MAX_ANIMATION_TRACKS:
        raise FormatError(
            f"{label}: animation track count {track_count} exceeds "
            f"{MAX_ANIMATION_TRACKS}"
        )
    if event_count:
        if event_offset < 0x28 or event_offset >= len(data):
            raise FormatError(
                f"{label}: animation event table offset "
                f"0x{event_offset:X} is outside the primary stream"
            )
    elif event_offset:
        raise FormatError(
            f"{label}: animation has an event offset but no events"
        )

    if not track_count:
        if frame_rate != 0 or duration != 0.0:
            raise FormatError(
                f"{label}: trackless animation has nonzero timing metadata"
            )
        return PackedAnimationClip(
            frame_rate=0,
            duration=0.0,
            event_count=event_count,
            event_offset=event_offset,
            tracks=(),
        )

    if frame_rate != ANIMATION_FRAME_RATE:
        raise FormatError(
            f"{label}: unsupported animation frame rate {frame_rate}"
        )
    if len(data) < ANIMATION_HEADER_SIZE:
        raise FormatError(
            f"{label}: animation track header exceeds the primary stream"
        )
    fill_0, fill_1 = struct.unpack_from("<2I", data, 0x28)
    if (fill_0, fill_1) != (ANIMATION_FILL, ANIMATION_FILL):
        raise FormatError(f"{label}: animation header has invalid fill bytes")

    table_end = ANIMATION_HEADER_SIZE + track_count * ANIMATION_TRACK_SIZE
    if table_end > len(data):
        raise FormatError(
            f"{label}: animation track table exceeds the primary stream"
        )
    tracks: list[PackedAnimationTrack] = []
    identities: set[tuple[int, PackedTrackKind]] = set()
    key_dtype = np.dtype(
        [
            ("time", "<f4"),
            ("value", "<i2", (4,)),
        ]
    )
    for index in range(track_count):
        record_offset = ANIMATION_HEADER_SIZE + index * ANIMATION_TRACK_SIZE
        bone_index, raw_kind, key_count, keys_offset = struct.unpack_from(
            "<4I",
            data,
            record_offset,
        )
        if bone_index >= MAX_ANIMATION_BONES:
            raise FormatError(
                f"{label}: animation track {index} references bone "
                f"{bone_index}, above {MAX_ANIMATION_BONES - 1}"
            )
        try:
            kind = PackedTrackKind(raw_kind)
        except ValueError as exc:
            raise FormatError(
                f"{label}: animation track {index} has unknown kind "
                f"{raw_kind}"
            ) from exc
        identity = (bone_index, kind)
        if identity in identities:
            raise FormatError(
                f"{label}: duplicate raw kind {kind.value} track for "
                f"bone {bone_index}"
            )
        identities.add(identity)
        if not key_count or key_count > MAX_ANIMATION_KEYS:
            raise FormatError(
                f"{label}: animation track {index} has invalid key count "
                f"{key_count}"
            )
        keys_size = key_count * ANIMATION_KEY_SIZE
        if (
            keys_offset < table_end
            or keys_offset > len(data)
            or keys_size > len(data) - keys_offset
        ):
            raise FormatError(
                f"{label}: animation track {index} key range "
                f"[0x{keys_offset:X}, 0x{keys_offset + keys_size:X}) "
                "is outside the primary stream"
            )
        records = np.frombuffer(
            data,
            dtype=key_dtype,
            count=key_count,
            offset=keys_offset,
        )
        times = records["time"].astype(np.float32, copy=True)
        values = records["value"].astype(np.int16, copy=True)
        if not np.all(np.isfinite(times)):
            raise FormatError(
                f"{label}: animation track {index} has non-finite key times"
            )
        if np.any(times[1:] < times[:-1]):
            raise FormatError(
                f"{label}: animation track {index} key times are unordered"
            )
        if not math.isclose(float(times[0]), 0.0, abs_tol=1e-5):
            raise FormatError(
                f"{label}: animation track {index} does not start at zero"
            )
        if not math.isclose(
            float(times[-1]),
            duration,
            abs_tol=1e-5,
        ):
            raise FormatError(
                f"{label}: animation track {index} ends at "
                f"{float(times[-1])}, not {duration}"
            )
        tracks.append(
            PackedAnimationTrack(
                bone_index=bone_index,
                kind=kind,
                times=times,
                values=values,
            )
        )
    return PackedAnimationClip(
        frame_rate=frame_rate,
        duration=duration,
        event_count=event_count,
        event_offset=event_offset,
        tracks=tuple(tracks),
    )
