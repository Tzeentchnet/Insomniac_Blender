from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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


class AnimationProfile(Enum):
    RESISTANCE_2 = "RESISTANCE_2"
    RESISTANCE_3 = "RESISTANCE_3"


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
        frame_map_offset=ighw.u32(offset + 0x0C),
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


def _validate_track_masks(
    header: AnimationHeader,
    masks: tuple[TrackMask, ...],
    bone_count: int,
    label: str,
) -> None:
    for index, mask in enumerate(masks):
        prefix = (
            f"Animation {header.name!r} {label} mask {index}"
        )
        if mask.marker != 2:
            raise FormatError(
                f"{prefix} has invalid marker {mask.marker}"
            )
        if mask.kind is TrackKind.UNKNOWN:
            raise FormatError(f"{prefix} has an unknown track kind")
        component_count = (
            4 if mask.kind is TrackKind.ROTATION else 3
        )
        if mask.component >= component_count:
            raise FormatError(
                f"{prefix} has invalid component {mask.component}"
            )
        if mask.bone_index >= bone_count:
            raise FormatError(
                f"{prefix} references bone {mask.bone_index}, "
                f"but the control contains {bone_count} bones"
            )


def _read_control(
    ighw: IghwFile,
    header: AnimationHeader,
    *,
    bone_count: int | None = None,
) -> _AnimationControl:
    control_bone_count = (
        header.bone_count if bone_count is None else bone_count
    )
    if not header.control_offset:
        return _AnimationControl(
            reference_rotations=np.zeros(
                (control_bone_count, 4),
                dtype=np.int16,
            ),
            reference_values=np.empty(0, dtype=np.int16),
            reference_masks=(),
            track16_masks=(),
            track8_masks=(),
            track8_bases=np.empty(0, dtype=np.int16),
            blend_masks=np.ones(control_bone_count, dtype=np.uint8),
        )

    base = header.control_offset
    reference_count = header.reference_value_count
    track16_count = header.track16_count
    track8_count = header.track8_count

    rotations_offset = 0
    values_offset = _pad(
        rotations_offset + control_bone_count * 8,
        16,
    )
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
        ighw,
        base + rotations_offset,
        control_bone_count * 4,
    ).reshape(control_bone_count, 4)
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
    ighw.require(
        base + blend_offset,
        control_bone_count,
        purpose="animation blend masks",
    )
    blend_masks = np.frombuffer(
        ighw.data,
        dtype=np.uint8,
        count=control_bone_count,
        offset=base + blend_offset,
    ).copy()
    control = _AnimationControl(
        reference_rotations=rotations,
        reference_values=values,
        reference_masks=reference_masks,
        track16_masks=track16_masks,
        track8_masks=track8_masks,
        track8_bases=track8_bases,
        blend_masks=blend_masks,
    )
    _validate_track_masks(
        header,
        control.reference_masks,
        control_bone_count,
        "reference",
    )
    _validate_track_masks(
        header,
        control.track16_masks,
        control_bone_count,
        "16-bit",
    )
    _validate_track_masks(
        header,
        control.track8_masks,
        control_bone_count,
        "8-bit",
    )
    return control


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


def _normalize_quaternions(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy()
    lengths = np.linalg.norm(result, axis=-1, keepdims=True)
    invalid = lengths[..., 0] <= 1e-12
    lengths[invalid] = 1.0
    result /= lengths
    result[invalid] = (0.0, 0.0, 0.0, 1.0)
    return result


def _quat_inverse(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy()
    result[..., :3] *= -1.0
    lengths_squared = np.sum(result * result, axis=-1, keepdims=True)
    lengths_squared[lengths_squared <= 1e-12] = 1.0
    return result / lengths_squared


def _quat_slerp(
    start: np.ndarray,
    end: np.ndarray,
    factor: np.ndarray,
) -> np.ndarray:
    start = np.asarray(start, dtype=np.float32)
    end = np.asarray(end, dtype=np.float32)
    dots = np.sum(start * end, axis=-1, keepdims=True)
    end = np.where(dots < 0.0, -end, end)
    dots = np.abs(dots)
    angles = np.arccos(np.clip(dots, -1.0, 1.0))
    sine = np.sin(angles)
    linear = start + (end - start) * factor
    spherical = (
        np.sin((1.0 - factor) * angles) * start
        + np.sin(factor * angles) * end
    ) / np.maximum(sine, 1e-8)
    result = np.where(sine < 1e-5, linear, spherical)
    lengths = np.linalg.norm(result, axis=-1, keepdims=True)
    lengths[lengths <= 1e-12] = 1.0
    return (result / lengths).astype(np.float32)


def _stabilize_quaternions(values: np.ndarray) -> None:
    for frame in range(1, values.shape[0]):
        flips = np.sum(values[frame - 1] * values[frame], axis=-1) < 0.0
        values[frame, flips] *= -1.0


def _wrap_i16(base: int, delta: int) -> int:
    return ((base + delta + 0x8000) & 0xFFFF) - 0x8000


def _stored_frame_index(
    ighw: IghwFile,
    header: AnimationHeader,
    frame: int,
    profile: AnimationProfile,
) -> int:
    if (
        profile is not AnimationProfile.RESISTANCE_3
        or not header.frame_map_offset
        or not header.frames_offset
    ):
        return frame
    table_size = header.frame_count * 2
    gap = header.frames_offset - header.frame_map_offset
    if gap < table_size or gap >= table_size + 0x80:
        return frame
    mapped = ighw.u16(header.frame_map_offset + frame * 2)
    return mapped if mapped < header.frame_count else frame


def _decode_clip(
    ighw: IghwFile,
    header: AnimationHeader,
    skeleton: Skeleton,
    *,
    profile: AnimationProfile = AnimationProfile.RESISTANCE_2,
) -> DecodedClip:
    frame_count = header.frame_count
    bone_count = skeleton.bone_count
    if frame_count <= 0:
        raise FormatError(f"Animation {header.name!r} has no frames")
    if header.additive:
        if header.bone_count > bone_count:
            raise FormatError(
                f"Animation {header.name!r} layer contains "
                f"{header.bone_count} bones, but the skeleton contains "
                f"{bone_count}"
            )
    elif header.bone_count != bone_count:
        raise FormatError(
            f"Animation {header.name!r} contains {header.bone_count} "
            f"bones, but the skeleton contains {bone_count}"
        )
    # Additive headers store a layer subset count, but their control arrays
    # and offsets use the complete skeleton bone count.
    control = _read_control(
        ighw,
        header,
        bone_count=bone_count if header.additive else header.bone_count,
    )

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
    position_reference_raw = np.zeros((bone_count, 3), dtype=np.int16)
    position_reference_mask = np.zeros((bone_count, 3), dtype=np.bool_)
    scale_raw = np.zeros((frame_count, bone_count, 3), dtype=np.int16)
    scale_mask = np.zeros((frame_count, bone_count, 3), dtype=np.bool_)

    for value, mask in zip(
        control.reference_values, control.reference_masks, strict=False
    ):
        component = mask.component
        if mask.kind is TrackKind.ROTATION:
            rotation_raw[:, mask.bone_index, component] = value
            rotation_touched[mask.bone_index] = True
        elif mask.kind is TrackKind.POSITION:
            position_raw[:, mask.bone_index, component] = value
            position_mask[:, mask.bone_index, component] = True
            position_reference_raw[mask.bone_index, component] = value
            position_reference_mask[mask.bone_index, component] = True
        elif mask.kind is TrackKind.SCALE:
            scale_raw[:, mask.bone_index, component] = value
            scale_mask[:, mask.bone_index, component] = True

    frame_8_offset = _pad(header.track16_count * 2, 16)
    for index, mask in enumerate(control.track8_masks):
        if (
            mask.kind is TrackKind.POSITION
            and not position_reference_mask[mask.bone_index, mask.component]
        ):
            base = (
                int(control.track8_bases[index])
                if index < len(control.track8_bases)
                else 0
            )
            position_reference_raw[mask.bone_index, mask.component] = base
            position_reference_mask[mask.bone_index, mask.component] = True

    for frame in range(frame_count):
        stored_frame = _stored_frame_index(ighw, header, frame, profile)
        frame_offset = (
            header.frames_offset + stored_frame * header.frame_stride
        )
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
            value = _wrap_i16(base, int(delta))
            _assign_track(
                frame,
                value,
                mask,
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
    rebase_positions = (
        profile is AnimationProfile.RESISTANCE_3
        and not header.additive
        and not delta_position_scale
    )
    if rebase_positions:
        translated = decoded_positions
        rebased = (
            bind_t[None, :, :]
            + (
                position_raw.astype(np.float32)
                - position_reference_raw[None, :, :]
            )
            * position_scale
        )
        translated = np.where(
            position_reference_mask[None, :, :],
            rebased,
            translated,
        )
    elif header.additive or delta_position_scale:
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

    if (
        header.additive
        and profile is AnimationProfile.RESISTANCE_2
    ):
        rotations = _quat_multiply(bind_q[None, :, :], rotations)
        blend_enabled = np.ones(bone_count, dtype=np.bool_)
        copied = min(bone_count, len(control.blend_masks))
        blend_enabled[:copied] = control.blend_masks[:copied] != 0
        excluded = ~rotation_touched & ~blend_enabled
        rotations[:, excluded] = bind_q[excluded]
    elif header.additive or delta_position_scale:
        untouched = ~rotation_touched
        rotations[:, untouched] = bind_q[untouched]
    if (
        header.additive
        and profile is AnimationProfile.RESISTANCE_3
    ):
        blend_enabled = np.ones(bone_count, dtype=np.bool_)
        copied = min(bone_count, len(control.blend_masks))
        blend_enabled[:copied] = control.blend_masks[:copied] != 0
        excluded = ~blend_enabled
        translations[:, excluded] = bind_t[excluded]
        rotations[:, excluded] = bind_q[excluded]
        scales[:, excluded] = bind_s[excluded]

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
        source_flags=header.flags,
        blend_weights=control.blend_masks.astype(np.uint8, copy=True),
        reference_rotations_xyzw=_normalize_quaternions(reference_q),
        rotation_animated_bones=rotation_touched.copy(),
        translation_components=position_mask.any(axis=0),
        scale_components=scale_mask.any(axis=0),
    )


def _assign_track(
    frame: int,
    value: int,
    mask: TrackMask,
    rotation_raw: np.ndarray,
    rotation_touched: np.ndarray,
    position_raw: np.ndarray,
    position_mask: np.ndarray,
    scale_raw: np.ndarray,
    scale_mask: np.ndarray,
) -> None:
    bone = mask.bone_index
    component = mask.component
    if mask.kind is TrackKind.ROTATION:
        rotation_raw[frame, bone, component] = value
        rotation_touched[bone] = True
    elif mask.kind is TrackKind.POSITION:
        position_raw[frame, bone, component] = value
        position_mask[frame, bone, component] = True
    elif mask.kind is TrackKind.SCALE:
        scale_raw[frame, bone, component] = value
        scale_mask[frame, bone, component] = True


def _quantization_scale(shift: int) -> float:
    if shift < 0 or shift >= 15:
        return 1.0 / 32768.0
    return 1.0 / float(0x8000 >> shift)


def _resample_vectors(
    values: np.ndarray,
    source_rate: float,
    target_count: int,
    target_rate: float,
    *,
    looping: bool,
) -> np.ndarray:
    source_count = values.shape[0]
    if source_count == 1:
        return np.broadcast_to(
            values, (target_count, *values.shape[1:])
        ).copy()
    source_frames = (
        np.arange(target_count, dtype=np.float64)
        * source_rate
        / target_rate
    )
    lower = np.floor(source_frames).astype(np.int64)
    factors = (source_frames - lower).astype(np.float32)
    if looping:
        lower %= source_count
        upper = (lower + 1) % source_count
    else:
        lower = np.clip(lower, 0, source_count - 1)
        upper = np.clip(lower + 1, 0, source_count - 1)
    factor_shape = (target_count,) + (1,) * (values.ndim - 1)
    factors = factors.reshape(factor_shape)
    return (
        values[lower] + (values[upper] - values[lower]) * factors
    ).astype(np.float32)


def _resample_quaternions(
    values: np.ndarray,
    source_rate: float,
    target_count: int,
    target_rate: float,
    *,
    looping: bool,
) -> np.ndarray:
    source_count = values.shape[0]
    if source_count == 1:
        return np.broadcast_to(
            values, (target_count, *values.shape[1:])
        ).copy()
    source_frames = (
        np.arange(target_count, dtype=np.float64)
        * source_rate
        / target_rate
    )
    lower = np.floor(source_frames).astype(np.int64)
    factors = (source_frames - lower).astype(np.float32)
    if looping:
        lower %= source_count
        upper = (lower + 1) % source_count
    else:
        lower = np.clip(lower, 0, source_count - 1)
        upper = np.clip(lower + 1, 0, source_count - 1)
    result = _quat_slerp(
        values[lower],
        values[upper],
        factors[:, None, None],
    )
    _stabilize_quaternions(result)
    return result


def _common_prefix_length(left: str, right: str) -> int:
    return next(
        (
            index
            for index, pair in enumerate(zip(left, right, strict=False))
            if pair[0] != pair[1]
        ),
        min(len(left), len(right)),
    )


def _select_overlay_base(
    overlay: DecodedClip,
    clips: list[DecodedClip],
) -> DecodedClip | None:
    name = overlay.name.casefold()
    candidates: list[tuple[int, DecodedClip]] = []
    if "_partial_aiming_" in name:
        stem = name.split("_partial_aiming_", 1)[0]
        for clip in clips:
            candidate = clip.name.casefold()
            if (
                not clip.additive
                and candidate.endswith("_idle_sighted")
                and candidate.startswith(stem + "_")
            ):
                candidates.append(
                    (_common_prefix_length(name, candidate), clip)
                )
    elif name in {"visemes", "head_visemes"} or (
        name.startswith("exp_")
        and name.endswith(("_lower", "_upper"))
    ):
        candidates.extend(
            (100, clip)
            for clip in clips
            if not clip.additive and clip.name.casefold() == "head_idle"
        )
    else:
        base_names: list[str] = []
        for suffix in ("_alt_fire_p", "_fire_cycle_p", "_fire_p"):
            if name.endswith(suffix):
                base_names.append(name[: -len(suffix)] + "_idle_p")
                break
        if name.endswith("_p") and not name.endswith("_idle_p"):
            base_names.append(name[:-2] + "_idle_p")
        candidates.extend(
            (100, clip)
            for clip in clips
            if not clip.additive and clip.name.casefold() in base_names
        )
    if not candidates and "_" in name:
        family = name.split("_", 1)[0] + "_"
        for clip in clips:
            candidate = clip.name.casefold()
            if (
                not clip.additive
                and candidate.startswith(family)
                and candidate.endswith("_idle_sighted")
            ):
                candidates.append(
                    (_common_prefix_length(name, candidate), clip)
                )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]


def _compose_overlay(
    overlay: DecodedClip,
    base: DecodedClip,
    skeleton: Skeleton,
) -> None:
    frame_count = overlay.frame_count
    base_t = _resample_vectors(
        base.translations,
        base.frame_rate,
        frame_count,
        overlay.frame_rate,
        looping=base.looping,
    )
    base_q = _resample_quaternions(
        base.rotations_xyzw,
        base.frame_rate,
        frame_count,
        overlay.frame_rate,
        looping=base.looping,
    )
    base_s = _resample_vectors(
        base.scales,
        base.frame_rate,
        frame_count,
        overlay.frame_rate,
        looping=base.looping,
    )
    bind_t, _, bind_s = _bind_trs(skeleton)
    blend = (
        overlay.blend_weights.astype(np.float32) / 255.0
        if overlay.blend_weights is not None
        else np.ones(overlay.bone_count, dtype=np.float32)
    )
    rotation_animated = (
        overlay.rotation_animated_bones
        if overlay.rotation_animated_bones is not None
        else overlay.animated_bones
    )
    reference_q = (
        overlay.reference_rotations_xyzw
        if overlay.reference_rotations_xyzw is not None
        else np.broadcast_to(
            np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
            (overlay.bone_count, 4),
        )
    )
    rotation_delta = _quat_multiply(
        overlay.rotations_xyzw,
        _quat_inverse(reference_q)[None, :, :],
    )
    composed_q = _quat_multiply(base_q, rotation_delta)
    rotation_weight = (
        blend * rotation_animated.astype(np.float32)
    )[None, :, None]
    overlay.rotations_xyzw = _quat_slerp(
        base_q, composed_q, rotation_weight
    )
    _stabilize_quaternions(overlay.rotations_xyzw)

    translation_components = (
        overlay.translation_components
        if overlay.translation_components is not None
        else np.broadcast_to(
            overlay.animated_bones[:, None],
            (overlay.bone_count, 3),
        )
    )
    translation_weight = (
        translation_components.astype(np.float32)
        * blend[:, None]
    )[None, :, :]
    translation_delta = overlay.translations - bind_t[None, :, :]
    overlay.translations = (
        base_t + translation_delta * translation_weight
    ).astype(np.float32)

    scale_components = (
        overlay.scale_components
        if overlay.scale_components is not None
        else np.broadcast_to(
            overlay.animated_bones[:, None],
            (overlay.bone_count, 3),
        )
    )
    scale_weight = (
        scale_components.astype(np.float32) * blend[:, None]
    )[None, :, :]
    if overlay.source_flags & 0x0200:
        composed_s = base_s + (
            overlay.scales - bind_s[None, :, :]
        )
    else:
        safe_bind = np.where(np.abs(bind_s) > 1e-12, bind_s, 1.0)
        composed_s = base_s * (
            overlay.scales / safe_bind[None, :, :]
        )
    overlay.scales = (
        base_s + (composed_s - base_s) * scale_weight
    ).astype(np.float32)

    base_rotation_animated = (
        base.rotation_animated_bones
        if base.rotation_animated_bones is not None
        else base.animated_bones
    )
    overlay.rotation_animated_bones = (
        base_rotation_animated | rotation_animated
    )
    base_translation_components = (
        base.translation_components
        if base.translation_components is not None
        else np.broadcast_to(
            base.animated_bones[:, None], (base.bone_count, 3)
        )
    )
    overlay.translation_components = (
        base_translation_components | translation_components
    )
    base_scale_components = (
        base.scale_components
        if base.scale_components is not None
        else np.broadcast_to(
            base.animated_bones[:, None], (base.bone_count, 3)
        )
    )
    overlay.scale_components = base_scale_components | scale_components
    overlay.animated_bones = (
        overlay.rotation_animated_bones
        | overlay.translation_components.any(axis=1)
        | overlay.scale_components.any(axis=1)
    )
    overlay.overlay_base_name = base.name


def _compose_r3_overlays(
    clips: list[DecodedClip],
    skeleton: Skeleton,
) -> None:
    for overlay in clips:
        if not overlay.additive:
            continue
        base = _select_overlay_base(overlay, clips)
        if base is not None:
            _compose_overlay(overlay, base, skeleton)


def decode_animset(
    data: bytes,
    skeleton: Skeleton,
    *,
    profile: AnimationProfile = AnimationProfile.RESISTANCE_2,
) -> list[DecodedClip]:
    ighw = IghwFile(data, label="animset")
    clips: list[DecodedClip] = []
    for offset in animation_offsets(ighw):
        header = _read_header(ighw, offset)
        clips.append(
            _decode_clip(ighw, header, skeleton, profile=profile)
        )
    if profile is AnimationProfile.RESISTANCE_3:
        _compose_r3_overlays(clips, skeleton)
    return clips
