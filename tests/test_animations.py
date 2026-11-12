from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.games.resistance_2.animations import _decode_clip
from insomniac_asset_io.games.resistance_2.formats import (
    AnimationHeader,
    Bone,
    Skeleton,
)


def _track_mask(bone: int, component: int) -> int:
    return (bone << 6) | (component << 2) | 0b10


class AnimationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
