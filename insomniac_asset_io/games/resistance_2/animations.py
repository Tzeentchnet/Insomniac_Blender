from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...core.binary import IghwFile
from ...core.errors import FormatError
from .formats import (
    AnimationHeader,
    DecodedClip,
    Skeleton,
    TrackKind,
    TrackMask,
)


SECT_ANIMATION = 0xF000


@dataclass(slots=True)
class _AnimationControl:
    reference_rotations: np.ndarray
    reference_values: np.ndarray
    reference_masks: tuple[TrackMask, ...]
    track16_masks: tuple[TrackMask, ...]
    track8_masks: tuple[TrackMask, ...]
    track8_bases: np.ndarray
    blend_masks: np.ndarray


def _pad(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def animation_offsets(ighw: IghwFile) -> list[int]:
    offsets: list[int] = []
    for section in ighw.sections_for(SECT_ANIMATION):
        count = max(1, section.count)
        for index in range(count):
            offsets.append(section.offset + index * section.length)
    return offsets


def _read_header(ighw: IghwFile, offset: int) -> AnimationHeader:
    name_offset = ighw.u32(offset + 0x08)
    name = ighw.cstring(name_offset) if name_offset else ""
    flags = ighw.u16(offset + 0x02)
    raw_stride = ighw.u16(offset + 0x32)
    frame_stride = raw_stride if flags & 0x04 else _pad(raw_stride, 0x80)
    return AnimationHeader(
        index=ighw.u16(offset),
        flags=flags,
        bone_count=ighw.u16(offset + 0x04),
        frame_count=ighw.u16(offset + 0x06),
        name=name,
        linear_speed=ighw.f32(offset + 0x14),
        frame_rate=ighw.f32(offset + 0x18),
        control_offset=ighw.u32(offset + 0x20),
        frames_offset=ighw.u32(offset + 0x24),
        frame_stride=frame_stride,
        reference_value_count=ighw.u16(offset + 0x34),
        track16_count=ighw.u16(offset + 0x36),
        track8_count=ighw.u16(offset + 0x38),
    )


def _read_i16_array(
    ighw: IghwFile,
    offset: int,
    count: int,
) -> np.ndarray:
    ighw.require(offset, count * 2, purpose="animation i16 array")
    return np.frombuffer(
        ighw.data,
        dtype=ighw.endian + "i2",
        count=count,
        offset=offset,
    ).astype(np.int16, copy=True)


def _read_u16_masks(
    ighw: IghwFile,
    offset: int,
    count: int,
) -> tuple[TrackMask, ...]:
    ighw.require(offset, count * 2, purpose="animation mask array")
    values = np.frombuffer(
        ighw.data,
        dtype=ighw.endian + "u2",
        count=count,
        offset=offset,
    )
    return tuple(TrackMask.unpack(int(value)) for value in values)


def _read_control(
    ighw: IghwFile,
    header: AnimationHeader,
) -> _AnimationControl:
    if not header.control_offset:
        return _AnimationControl(
            reference_rotations=np.zeros((header.bone_count, 4), dtype=np.int16),
            reference_values=np.empty(0, dtype=np.int16),
            reference_masks=(),
            track16_masks=(),
            track8_masks=(),
            track8_bases=np.empty(0, dtype=np.int16),
            blend_masks=np.ones(header.bone_count, dtype=np.uint8),
        )

    base = header.control_offset
    bone_count = header.bone_count
    reference_count = header.reference_value_count
    track16_count = header.track16_count
    track8_count = header.track8_count

    rotations_offset = 0
    values_offset = _pad(rotations_offset + bone_count * 8, 16)
    value_masks_offset = _pad(values_offset + reference_count * 2, 16)
    track16_masks_offset = _pad(
        value_masks_offset + reference_count * 2, 16
    )
    track8_masks_offset = _pad(
        track16_masks_offset + track16_count * 2, 16
    )
    track8_bases_offset = _pad(
        track8_masks_offset + track8_count * 2, 16
    )
    blend_offset = _pad(track8_bases_offset + track8_count * 2, 16)

    rotations = _read_i16_array(
        ighw, base + rotations_offset, bone_count * 4
    ).reshape(bone_count, 4)
    values = _read_i16_array(ighw, base + values_offset, reference_count)
    reference_masks = _read_u16_masks(
        ighw, base + value_masks_offset, reference_count
    )
    track16_masks = _read_u16_masks(
        ighw, base + track16_masks_offset, track16_count
    )
    track8_masks = _read_u16_masks(
        ighw, base + track8_masks_offset, track8_count
    )
    track8_bases = _read_i16_array(
        ighw, base + track8_bases_offset, track8_count
    )
    ighw.require(base + blend_offset, bone_count, purpose="animation blend masks")
    blend_masks = np.frombuffer(
        ighw.data,
        dtype=np.uint8,
        count=bone_count,
        offset=base + blend_offset,
    ).copy()
    return _AnimationControl(
        reference_rotations=rotations,
        reference_values=values,
        reference_masks=reference_masks,
        track16_masks=track16_masks,
        track8_masks=track8_masks,
        track8_bases=track8_bases,
        blend_masks=blend_masks,
    )


def _matrix_quaternion_xyzw(matrix: np.ndarray) -> np.ndarray:
    rotation = matrix[:3, :3].astype(np.float64, copy=True)
    scale = np.linalg.norm(rotation, axis=0)
    scale[scale <= 1e-12] = 1.0
    rotation /= scale
    trace = float(np.trace(rotation))
    if trace > 0.0:
        s = np.sqrt(trace + 1.0) * 2.0
        quat = np.array(
            [
                (rotation[2, 1] - rotation[1, 2]) / s,
                (rotation[0, 2] - rotation[2, 0]) / s,
                (rotation[1, 0] - rotation[0, 1]) / s,
                0.25 * s,
            ]
        )
    else:
        diagonal = np.diag(rotation)
        index = int(np.argmax(diagonal))
        if index == 0:
            s = np.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2
            quat = np.array(
                [
                    0.25 * s,
                    (rotation[0, 1] + rotation[1, 0]) / s,
                    (rotation[0, 2] + rotation[2, 0]) / s,
                    (rotation[2, 1] - rotation[1, 2]) / s,
                ]
            )
        elif index == 1:
            s = np.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2
            quat = np.array(
                [
                    (rotation[0, 1] + rotation[1, 0]) / s,
                    0.25 * s,
                    (rotation[1, 2] + rotation[2, 1]) / s,
                    (rotation[0, 2] - rotation[2, 0]) / s,
                ]
            )
        else:
            s = np.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2
            quat = np.array(
                [
                    (rotation[0, 2] + rotation[2, 0]) / s,
                    (rotation[1, 2] + rotation[2, 1]) / s,
                    0.25 * s,
                    (rotation[1, 0] - rotation[0, 1]) / s,
                ]
            )
    length = np.linalg.norm(quat)
    if length <= 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    return (quat / length).astype(np.float32)


def _bind_trs(skeleton: Skeleton) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = skeleton.bone_count
    translations = skeleton.bind_local[:, :3, 3].astype(np.float32, copy=True)
    scales = np.linalg.norm(skeleton.bind_local[:, :3, :3], axis=1).astype(
        np.float32
    )
    scales[scales <= 1e-12] = 1.0
    rotations = np.empty((count, 4), dtype=np.float32)
    for index in range(count):
        rotations[index] = _matrix_quaternion_xyzw(
            skeleton.bind_local[index]
        )
    return translations, rotations, scales


def _quat_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lx, ly, lz, lw = np.moveaxis(left, -1, 0)
    rx, ry, rz, rw = np.moveaxis(right, -1, 0)
    result = np.stack(
        (
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        ),
        axis=-1,
    )
    lengths = np.linalg.norm(result, axis=-1, keepdims=True)
    lengths[lengths <= 1e-12] = 1.0
    return (result / lengths).astype(np.float32)


def _decode_clip(
    ighw: IghwFile,
    header: AnimationHeader,
    skeleton: Skeleton,
) -> DecodedClip:
    control = _read_control(ighw, header)
    frame_count = header.frame_count
    bone_count = skeleton.bone_count
    if frame_count <= 0:
        raise FormatError(f"Animation {header.name!r} has no frames")

    bind_t, bind_q, bind_s = _bind_trs(skeleton)
    reference_q = np.zeros((bone_count, 4), dtype=np.int16)
    reference_q[:, 3] = 32767
    copied = min(bone_count, len(control.reference_rotations))
    reference_q[:copied] = control.reference_rotations[:copied]
    rotation_raw = np.broadcast_to(
        reference_q[None, :, :], (frame_count, bone_count, 4)
    ).copy()
    rotation_touched = np.zeros(bone_count, dtype=np.bool_)

    position_raw = np.zeros((frame_count, bone_count, 3), dtype=np.int16)
    position_mask = np.zeros((frame_count, bone_count, 3), dtype=np.bool_)
    scale_raw = np.zeros((frame_count, bone_count, 3), dtype=np.int16)
    scale_mask = np.zeros((frame_count, bone_count, 3), dtype=np.bool_)

    for value, mask in zip(
        control.reference_values, control.reference_masks, strict=False
    ):
        if mask.bone_index >= bone_count:
            continue
        component = mask.component
        if mask.kind is TrackKind.ROTATION and component < 4:
            rotation_raw[:, mask.bone_index, component] = value
            rotation_touched[mask.bone_index] = True
        elif mask.kind is TrackKind.POSITION and component < 3:
            position_raw[:, mask.bone_index, component] = value
            position_mask[:, mask.bone_index, component] = True
        elif mask.kind is TrackKind.SCALE and component < 3:
            scale_raw[:, mask.bone_index, component] = value
            scale_mask[:, mask.bone_index, component] = True

    frame_8_offset = _pad(header.track16_count * 2, 16)
    for frame in range(frame_count):
        frame_offset = header.frames_offset + frame * header.frame_stride
        values16 = _read_i16_array(
            ighw, frame_offset, header.track16_count
        )
        ighw.require(
            frame_offset + frame_8_offset,
            header.track8_count,
            purpose="animation i8 tracks",
        )
        values8 = np.frombuffer(
            ighw.data,
            dtype=np.int8,
            count=header.track8_count,
            offset=frame_offset + frame_8_offset,
        )
        for value, mask in zip(
            values16, control.track16_masks, strict=False
        ):
            _assign_track(
                frame,
                int(value),
                mask,
                bone_count,
                rotation_raw,
                rotation_touched,
                position_raw,
                position_mask,
                scale_raw,
                scale_mask,
            )
        for index, (delta, mask) in enumerate(
            zip(values8, control.track8_masks, strict=False)
        ):
            base = (
                int(control.track8_bases[index])
                if index < len(control.track8_bases)
                else 0
            )
            value = np.int16(base + int(delta)).item()
            _assign_track(
                frame,
                value,
                mask,
                bone_count,
                rotation_raw,
                rotation_touched,
                position_raw,
                position_mask,
                scale_raw,
                scale_mask,
            )

    rotations = rotation_raw.astype(np.float32) / 32767.0
    lengths = np.linalg.norm(rotations, axis=2, keepdims=True)
    invalid = lengths[..., 0] <= 1e-12
    lengths[invalid] = 1.0
    rotations /= lengths
    rotations[invalid] = (0.0, 0.0, 0.0, 1.0)

    position_scale = _quantization_scale(skeleton.translation_shift)
    scale_scale = _quantization_scale(skeleton.scale_shift)
    translations = np.broadcast_to(
        bind_t[None, :, :], (frame_count, bone_count, 3)
    ).copy()
    scales = np.broadcast_to(
        bind_s[None, :, :], (frame_count, bone_count, 3)
    ).copy()
    decoded_positions = position_raw.astype(np.float32) * position_scale
    decoded_scales = scale_raw.astype(np.float32) * scale_scale

    delta_position_scale = bool(header.flags & 0x0200)
    if header.additive or delta_position_scale:
        translated = bind_t[None, :, :] + decoded_positions
    else:
        translated = decoded_positions
    np.copyto(translations, translated, where=position_mask)

    if delta_position_scale:
        scaled = bind_s[None, :, :] + decoded_scales
    elif header.additive:
        scaled = bind_s[None, :, :] * decoded_scales
    else:
        scaled = decoded_scales
    np.copyto(scales, scaled, where=scale_mask)

    if header.additive:
        rotations = _quat_multiply(bind_q[None, :, :], rotations)
    elif delta_position_scale:
        untouched = ~rotation_touched
        rotations[:, untouched] = bind_q[untouched]

    animated = (
        rotation_touched
        | position_mask.any(axis=(0, 2))
        | scale_mask.any(axis=(0, 2))
    )
    name = header.name or f"animation_{header.index:04d}"
    return DecodedClip(
        name=name,
        frame_rate=max(header.frame_rate, 1.0),
        looping=header.looping,
        additive=header.additive,
        translations=translations.astype(np.float32),
        rotations_xyzw=rotations.astype(np.float32),
        scales=scales.astype(np.float32),
        animated_bones=animated,
    )


def _assign_track(
    frame: int,
    value: int,
    mask: TrackMask,
    bone_count: int,
    rotation_raw: np.ndarray,
    rotation_touched: np.ndarray,
    position_raw: np.ndarray,
    position_mask: np.ndarray,
    scale_raw: np.ndarray,
    scale_mask: np.ndarray,
) -> None:
    bone = mask.bone_index
    component = mask.component
    if bone >= bone_count:
        return
    if mask.kind is TrackKind.ROTATION and component < 4:
        rotation_raw[frame, bone, component] = value
        rotation_touched[bone] = True
    elif mask.kind is TrackKind.POSITION and component < 3:
        position_raw[frame, bone, component] = value
        position_mask[frame, bone, component] = True
    elif mask.kind is TrackKind.SCALE and component < 3:
        scale_raw[frame, bone, component] = value
        scale_mask[frame, bone, component] = True


def _quantization_scale(shift: int) -> float:
    if shift < 0 or shift >= 15:
        return 1.0 / 32768.0
    return 1.0 / float(0x8000 >> shift)


def decode_animset(data: bytes, skeleton: Skeleton) -> list[DecodedClip]:
    ighw = IghwFile(data, label="animset")
    clips: list[DecodedClip] = []
    for offset in animation_offsets(ighw):
        header = _read_header(ighw, offset)
        clips.append(_decode_clip(ighw, header, skeleton))
    return clips
