from __future__ import annotations

import struct
import unittest

import numpy as np

from insomniac_asset_io.games.resistance_2.formats import (
    DecodedTexture,
    TextureInfo,
)
from insomniac_asset_io.games.resistance_2.materials import (
    has_emissive_mask,
    reconstruct_normal_map,
)
from insomniac_asset_io.games.resistance_2.textures import decode_texture


class TextureTests(unittest.TestCase):
    def test_decodes_solid_red_dxt1_block(self):
        block = struct.pack("<HHI", 0xF800, 0x07E0, 0)
        info = TextureInfo(
            id=1,
            tuid=1,
            format_code=0x06,
            mip_count=1,
            width=4,
            height=4,
            offset=0,
            length=8,
        )
        rgba = decode_texture(info, block).rgba
        self.assertEqual(rgba.shape, (4, 4, 4))
        np.testing.assert_array_equal(rgba[0, 0], [255, 0, 0, 255])

    def test_decodes_morton_r8(self):
        info = TextureInfo(
            id=2,
            tuid=2,
            format_code=0x01,
            mip_count=1,
            width=2,
            height=2,
            offset=0,
            length=4,
        )
        rgba = decode_texture(info, bytes([10, 20, 30, 40])).rgba
        self.assertEqual(sorted(rgba[..., 0].reshape(-1).tolist()), [10, 20, 30, 40])
        self.assertTrue(np.all(rgba[..., 3] == 255))

    def test_decodes_r2_low_format_codes(self):
        rgba4 = TextureInfo(
            id=5,
            tuid=5,
            format_code=0x03,
            mip_count=1,
            width=1,
            height=1,
            offset=0,
            length=2,
        )
        rgba = decode_texture(rgba4, bytes([0xF1, 0x2A])).rgba
        np.testing.assert_array_equal(rgba[0, 0], [255, 17, 34, 170])

        r5g6b5 = TextureInfo(
            id=6,
            tuid=6,
            format_code=0x04,
            mip_count=1,
            width=1,
            height=1,
            offset=0,
            length=2,
        )
        rgba = decode_texture(r5g6b5, bytes([0xF8, 0x00])).rgba
        np.testing.assert_array_equal(rgba[0, 0], [255, 0, 0, 255])

        rg8 = TextureInfo(
            id=7,
            tuid=7,
            format_code=0x0B,
            mip_count=1,
            width=1,
            height=1,
            offset=0,
            length=2,
        )
        rgba = decode_texture(rg8, bytes([64, 192])).rgba
        np.testing.assert_array_equal(rgba[0, 0], [64, 192, 0, 255])

    def test_detects_only_nonzero_blue_as_emission(self):
        info = TextureInfo(
            id=3,
            tuid=3,
            format_code=0x06,
            mip_count=1,
            width=2,
            height=2,
            offset=0,
            length=0,
        )
        rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        rgba[..., 0] = 255
        texture = DecodedTexture(info=info, rgba=rgba)
        self.assertFalse(has_emissive_mask(texture))

        rgba[1, 0, 2] = 1
        self.assertTrue(has_emissive_mask(texture))

    def test_reconstructs_normal_from_alpha_and_inverted_green(self):
        info = TextureInfo(
            id=4,
            tuid=4,
            format_code=0x08,
            mip_count=1,
            width=2,
            height=1,
            offset=0,
            length=0,
        )
        rgba = np.array(
            [[[0, 127, 200, 128], [0, 127, 200, 255]]],
            dtype=np.uint8,
        )
        texture = DecodedTexture(info=info, rgba=rgba)

        normal = reconstruct_normal_map(texture)

        np.testing.assert_array_equal(normal[0, 0], [128, 128, 255, 255])
        np.testing.assert_array_equal(normal[0, 1], [255, 128, 128, 255])
        np.testing.assert_array_equal(texture.rgba, rgba)

    def test_reconstructs_rg8_normal_from_red_and_inverted_green(self):
        info = TextureInfo(
            id=8,
            tuid=8,
            format_code=0x0B,
            mip_count=1,
            width=1,
            height=1,
            offset=0,
            length=2,
        )
        rgba = np.array([[[128, 127, 0, 255]]], dtype=np.uint8)
        texture = DecodedTexture(info=info, rgba=rgba)

        normal = reconstruct_normal_map(texture)

        np.testing.assert_array_equal(normal[0, 0], [128, 128, 255, 255])


if __name__ == "__main__":
    unittest.main()
