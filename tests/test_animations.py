from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.resistance_2.animations import (
    AnimationProfile,
    _compose_r3_overlays,
    _decode_clip,
    _matrix_quaternion_xyzw,
    _quat_multiply,
    _wrap_i16,
)
from insomniac_asset_io.games.resistance_2.formats import (
    AnimationHeader,
    Bone,
    DecodedClip,
    Skeleton,
)


def _track_mask(bone: int, component: int, kind: int = 0) -> int:
    return (bone << 6) | (kind << 4) | (component << 2) | 0b10


class AnimationTests(unittest.TestCase):
    @staticmethod
    def _single_bone_skeleton() -> Skeleton:
        bind = np.identity(4, dtype=np.float32)[None]
        return Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )

    def test_tod_scales_signed_8_bit_deltas_by_four(self) -> None:
        data = bytearray(0x140)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frames_offset = 0x100
        struct.pack_into(">4h", data, control_offset, 0, 0, 0, 32767)
        position_x = _track_mask(0, 0, kind=2)
        struct.pack_into(">H", data, control_offset + 0x10, position_x)
        struct.pack_into(">h", data, control_offset + 0x20, 1000)
        data[control_offset + 0x30] = 0xFF
        struct.pack_into(">b", data, frames_offset, 2)
        header = AnimationHeader(
            index=0,
            flags=0x04,
            bone_count=1,
            frame_count=1,
            name="tod_delta",
            frame_rate=30.0,
            linear_speed=0.0,
            frame_stride=0x11,
            reference_value_count=0,
            track16_count=0,
            track8_count=1,
            control_offset=control_offset,
            frames_offset=frames_offset,
        )

        clip = _decode_clip(
            IghwFile(data),
            header,
            self._single_bone_skeleton(),
            profile=AnimationProfile.RATCHET_TOD,
        )

        self.assertAlmostEqual(
            float(clip.translations[0, 0, 0]),
            1008.0 / 32768.0,
        )

    def test_tod_simple_clips_decode_real_half_rate_frames(self) -> None:
        data = bytearray(0x180)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frames_offset = 0x100
        struct.pack_into(">4h", data, control_offset, 0, 0, 0, 32767)
        position_x = _track_mask(0, 0, kind=2)
        struct.pack_into(">H", data, control_offset + 0x10, position_x)
        data[control_offset + 0x20] = 0xFF
        for index, value in enumerate((111, 200, 222, 400, 333)):
            struct.pack_into(
                ">h",
                data,
                frames_offset + index * 0x10,
                value,
            )
        header = AnimationHeader(
            index=0,
            flags=0x04,
            bone_count=1,
            frame_count=5,
            name="tod_pairs",
            frame_rate=60.0,
            linear_speed=0.0,
            frame_stride=0x10,
            reference_value_count=0,
            track16_count=1,
            track8_count=0,
            control_offset=control_offset,
            frames_offset=frames_offset,
        )

        clip = _decode_clip(
            IghwFile(data),
            header,
            self._single_bone_skeleton(),
            profile=AnimationProfile.RATCHET_TOD,
        )

        self.assertEqual(clip.frame_rate, 30.0)
        np.testing.assert_allclose(
            clip.translations[:, 0, 0],
            np.asarray((200, 400), dtype=np.float32) / 32768.0,
        )
    def test_additive_subset_uses_full_skeleton_control_layout(self) -> None:
        data = bytearray(0x120)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frames_offset = 0x100
        reference_rotations = (
            (0, 0, 23170, 23170),
            (23170, 0, 0, 23170),
            (0, 0, 0, 32767),
        )
        struct.pack_into(
            ">12h",
            data,
            control_offset,
            *(value for rotation in reference_rotations for value in rotation),
        )
        struct.pack_into(
            ">4H",
            data,
            control_offset + 0x20,
            *(_track_mask(2, component) for component in range(4)),
        )
        data[control_offset + 0x30 : control_offset + 0x33] = (
            b"\x00\x00\xff"
        )
        animated_rotation = (0, 0, 12539, 30273)
        struct.pack_into(">4h", data, frames_offset, *animated_rotation)

        bind = np.repeat(
            np.identity(4, dtype=np.float32)[None],
            3,
            axis=0,
        )
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 0xFFFF, 0xFFFF),
                Bone(0, -1, 0xFFFF, 0xFFFF),
                Bone(0, -1, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        header = AnimationHeader(
            index=0,
            flags=0x06,
            bone_count=1,
            frame_count=1,
            name="additive_overlay",
            frame_rate=30.0,
            linear_speed=0.0,
            frame_stride=0x10,
            reference_value_count=0,
            track16_count=4,
            track8_count=0,
            control_offset=control_offset,
            frames_offset=frames_offset,
        )

        clip = _decode_clip(IghwFile(data), header, skeleton)

        identity = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        np.testing.assert_allclose(
            clip.rotations_xyzw[0, :2],
            np.repeat(identity[None], 2, axis=0),
            atol=1e-5,
        )
        expected = np.asarray(animated_rotation, dtype=np.float32)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(
            clip.rotations_xyzw[0, 2],
            expected,
            atol=1e-5,
        )
        np.testing.assert_array_equal(
            clip.animated_bones,
            np.array([False, False, True]),
        )

        struct.pack_into(
            ">H",
            data,
            control_offset + 0x20,
            _track_mask(2, 0) & ~0b11,
        )
        with self.assertRaisesRegex(FormatError, "invalid marker"):
            _decode_clip(IghwFile(data), header, skeleton)

    def test_r3_additive_rotation_uses_direct_track_values(self) -> None:
        data = bytearray(0x120)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frames_offset = 0x100
        struct.pack_into(
            ">8h",
            data,
            control_offset,
            0,
            0,
            0,
            32767,
            0,
            0,
            0,
            32767,
        )
        struct.pack_into(
            ">4H",
            data,
            control_offset + 0x10,
            *(_track_mask(1, component) for component in range(4)),
        )
        data[control_offset + 0x20 : control_offset + 0x22] = b"\x00\xff"
        encoded_rotation = (12539, 0, 0, 30273)
        struct.pack_into(">4h", data, frames_offset, *encoded_rotation)

        bind = np.repeat(
            np.identity(4, dtype=np.float32)[None], 2, axis=0
        )
        bind[0, :3, :3] = np.array(
            ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            dtype=np.float32,
        )
        bind[1, :3, :3] = bind[0, :3, :3]
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 0xFFFF, 0xFFFF),
                Bone(0, -1, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=np.linalg.inv(bind).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        header = AnimationHeader(
            index=0,
            flags=0x06,
            bone_count=1,
            frame_count=1,
            name="r3_overlay",
            frame_rate=30.0,
            linear_speed=0.0,
            frame_stride=0x10,
            reference_value_count=0,
            track16_count=4,
            track8_count=0,
            control_offset=control_offset,
            frames_offset=frames_offset,
        )

        clip = _decode_clip(
            IghwFile(data),
            header,
            skeleton,
            profile=AnimationProfile.RESISTANCE_3,
        )

        expected = np.asarray(encoded_rotation, dtype=np.float32)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(
            clip.rotations_xyzw[0, 0],
            _matrix_quaternion_xyzw(bind[0]),
            atol=1e-5,
        )
        np.testing.assert_allclose(
            clip.rotations_xyzw[0, 1],
            expected,
            atol=1e-5,
        )
        np.testing.assert_array_equal(
            clip.rotation_animated_bones,
            np.array([False, True]),
        )
        np.testing.assert_array_equal(
            clip.blend_weights,
            np.array([0, 255], dtype=np.uint8),
        )

    def test_r3_frame_map_and_position_rebase(self) -> None:
        data = bytearray(0x140)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frame_map_offset = 0xF0
        frames_offset = 0x100
        struct.pack_into(">4h", data, control_offset, 0, 0, 0, 32767)
        struct.pack_into(">h", data, control_offset + 0x10, 100)
        position_x = _track_mask(0, 0, kind=2)
        struct.pack_into(">H", data, control_offset + 0x20, position_x)
        struct.pack_into(">H", data, control_offset + 0x30, position_x)
        data[control_offset + 0x40] = 0xFF
        struct.pack_into(">2H", data, frame_map_offset, 1, 0)
        struct.pack_into(">h", data, frames_offset, 200)
        struct.pack_into(">h", data, frames_offset + 0x10, 300)

        bind = np.identity(4, dtype=np.float32)[None]
        bind[0, 0, 3] = 10.0
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=np.linalg.inv(bind).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        header = AnimationHeader(
            index=0,
            flags=0x04,
            bone_count=1,
            frame_count=2,
            name="r3_remapped",
            frame_rate=30.0,
            linear_speed=0.0,
            frame_stride=0x10,
            reference_value_count=1,
            track16_count=1,
            track8_count=0,
            control_offset=control_offset,
            frames_offset=frames_offset,
            frame_map_offset=frame_map_offset,
        )

        clip = _decode_clip(
            IghwFile(data),
            header,
            skeleton,
            profile=AnimationProfile.RESISTANCE_3,
        )

        scale = 1.0 / 32768.0
        np.testing.assert_allclose(
            clip.translations[:, 0, 0],
            np.array(
                [10.0 + 200.0 * scale, 10.0 + 100.0 * scale],
                dtype=np.float32,
            ),
            atol=1e-6,
        )

    def test_r3_additive_positions_rebase_from_clip_reference(self) -> None:
        data = bytearray(0x120)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        control_offset = 0x40
        frames_offset = 0x100
        struct.pack_into(">4h", data, control_offset, 0, 0, 0, 32767)
        position_x = _track_mask(0, 0, kind=2)
        struct.pack_into(">h", data, control_offset + 0x10, 1000)
        struct.pack_into(">H", data, control_offset + 0x20, position_x)
        struct.pack_into(">H", data, control_offset + 0x30, position_x)
        data[control_offset + 0x40] = 0xFF
        struct.pack_into(">h", data, frames_offset, 1100)

        bind = np.identity(4, dtype=np.float32)[None]
        bind[0, 0, 3] = 2.0
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=np.linalg.inv(bind).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        header = AnimationHeader(
            index=0,
            flags=0x06,
            bone_count=1,
            frame_count=1,
            name="r3_additive_position",
            frame_rate=30.0,
            linear_speed=0.0,
            frame_stride=0x10,
            reference_value_count=1,
            track16_count=1,
            track8_count=0,
            control_offset=control_offset,
            frames_offset=frames_offset,
        )

        clip = _decode_clip(
            IghwFile(data),
            header,
            skeleton,
            profile=AnimationProfile.RESISTANCE_3,
        )

        self.assertAlmostEqual(
            float(clip.translations[0, 0, 0]),
            2.0 + 100.0 / 32768.0,
        )

    def test_r3_partial_overlay_is_baked_over_sighted_idle(self) -> None:
        bind = np.repeat(
            np.identity(4, dtype=np.float32)[None], 2, axis=0
        )
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 0xFFFF, 0xFFFF),
                Bone(0, -1, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        identity = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        base_rotation = np.array(
            [0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)],
            dtype=np.float32,
        )
        delta_rotation = np.array(
            [np.sqrt(0.5), 0.0, 0.0, np.sqrt(0.5)],
            dtype=np.float32,
        )
        base = DecodedClip(
            name="ravager_r3_idle_sighted",
            frame_rate=30.0,
            looping=True,
            additive=False,
            translations=np.array(
                [[[10.0, 0.0, 0.0], [20.0, 0.0, 0.0]]],
                dtype=np.float32,
            ),
            rotations_xyzw=np.array(
                [[identity, base_rotation]], dtype=np.float32
            ),
            scales=np.ones((1, 2, 3), dtype=np.float32),
            animated_bones=np.zeros(2, dtype=np.bool_),
        )
        overlay = DecodedClip(
            name="ravager_partial_aiming_horizontal",
            frame_rate=30.0,
            looping=False,
            additive=True,
            translations=np.array(
                [[[5.0, 0.0, 0.0], [2.0, 0.0, 0.0]]],
                dtype=np.float32,
            ),
            rotations_xyzw=np.array(
                [[delta_rotation, delta_rotation]], dtype=np.float32
            ),
            scales=np.array(
                [[[2.0, 1.0, 1.0], [1.5, 1.0, 1.0]]],
                dtype=np.float32,
            ),
            animated_bones=np.ones(2, dtype=np.bool_),
            source_flags=0x0202,
            blend_weights=np.array([0, 255], dtype=np.uint8),
            reference_rotations_xyzw=np.array(
                [identity, identity], dtype=np.float32
            ),
            rotation_animated_bones=np.ones(2, dtype=np.bool_),
            translation_components=np.array(
                [[True, False, False], [True, False, False]]
            ),
            scale_components=np.array(
                [[True, False, False], [True, False, False]]
            ),
        )

        _compose_r3_overlays([base, overlay], skeleton)

        self.assertEqual(
            overlay.overlay_base_name, "ravager_r3_idle_sighted"
        )
        np.testing.assert_allclose(
            overlay.translations[0, 0],
            base.translations[0, 0],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.translations[0, 1],
            np.array([22.0, 0.0, 0.0], dtype=np.float32),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.rotations_xyzw[0, 0],
            base.rotations_xyzw[0, 0],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.rotations_xyzw[0, 1],
            _quat_multiply(base_rotation, delta_rotation),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.scales[0, 0],
            base.scales[0, 0],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.scales[0, 1],
            np.array([1.5, 1.0, 1.0], dtype=np.float32),
            atol=1e-6,
        )

    def test_signed_8_bit_delta_uses_wrapping_i16_addition(self) -> None:
        self.assertEqual(_wrap_i16(32767, 1), -32768)
        self.assertEqual(_wrap_i16(-32768, -1), 32767)
        self.assertEqual(_wrap_i16(1234, -12), 1222)


if __name__ == "__main__":
    unittest.main()
