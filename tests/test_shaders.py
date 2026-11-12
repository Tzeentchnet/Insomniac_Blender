from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import ShaderInfo
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


SHADER_TUID = 0x1234_5678_90AB_CDEF
TEXTURE_IDS = (0x1111_1111, 0x2222_2222, 0x3333_3333, 0x4444_4444)
MATERIAL_WORD = 0x1D00_0009


def _assetlookup(shader_length: int) -> bytes:
    data = bytearray(0x40)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(">IIII", data, 0x20, 0x1D100, 0x30, 1, 0x10)
    struct.pack_into(">QII", data, 0x30, SHADER_TUID, 0, shader_length)
    return bytes(data)


def _shader() -> bytes:
    data = bytearray(0x300)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 3)
    struct.pack_into(">IIII", data, 0x20, 0x5000, 0x50, 1, 0x80)
    struct.pack_into(">IIII", data, 0x30, 0x5D00, 0xD0, 1, 0x30)

    cursor = 0x100

    def add_string(value: str) -> int:
        nonlocal cursor
        encoded = value.encode("utf-8") + b"\0"
        offset = cursor
        data[offset : offset + len(encoded)] = encoded
        cursor += len(encoded)
        return offset

    lookup_path = add_string("fixture/fixture")
    texture_paths = (
        add_string("fixture_c"),
        add_string("fixture_n"),
        add_string("fixture_e"),
        add_string("fixture_d"),
    )
    struct.pack_into(
        ">IIII",
        data,
        0x40,
        0x11300,
        0x100,
        1,
        cursor - 0x100,
    )

    struct.pack_into(">4iI", data, 0x50, 0, 1, 2, 3, MATERIAL_WORD)
    values = tuple(float(index) / 4.0 for index in range(24))
    struct.pack_into(">24f", data, 0x70, *values)

    struct.pack_into(">QII", data, 0xD0, SHADER_TUID, lookup_path, 0)
    struct.pack_into(">4I", data, 0xE0, *TEXTURE_IDS)
    struct.pack_into(">4I", data, 0xF0, *texture_paths)
    return bytes(data[:cursor])


class ShaderTests(unittest.TestCase):
    def test_classifies_source_and_known_override_alpha_modes(self):
        opaque = ShaderInfo(1, None, None, None, None, material_word=0)
        mask = ShaderInfo(
            2,
            None,
            None,
            None,
            None,
            material_word=0x0004_0000,
        )
        blend = ShaderInfo(
            3,
            None,
            None,
            None,
            None,
            material_word=0x0006_0000,
        )
        glowy_glass = ShaderInfo(
            0x3B7E_8841_9D2A_C85A,
            None,
            None,
            None,
            None,
            material_word=0x0C00_0003,
        )

        self.assertEqual(opaque.alpha_mode, "OPAQUE")
        self.assertEqual(mask.alpha_mode, "MASK")
        self.assertEqual(blend.alpha_mode, "BLEND")
        self.assertEqual(glowy_glass.alpha_mode, "BLEND")
        self.assertTrue(glowy_glass.uses_implicit_albedo_alpha)
        self.assertFalse(opaque.uses_implicit_albedo_alpha)

    def test_identifies_source_authored_default_pink_placeholder(self):
        placeholder = ShaderInfo(
            0x19EC_CFD6_FFE5_4E65,
            0x6078_A29C,
            None,
            None,
            None,
            lookup_path=(
                "defaults/default_pink_shader/default_pink_shader"
            ),
        )
        ordinary = ShaderInfo(
            1,
            None,
            None,
            None,
            None,
            lookup_path="materials/ordinary",
        )

        self.assertEqual(
            placeholder.source_placeholder_kind,
            "DEFAULT_PINK",
        )
        self.assertIsNone(ordinary.source_placeholder_kind)

    def test_reads_texture_paths_and_material_parameters(self):
        shader_data = _shader()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(
                _assetlookup(len(shader_data))
            )
            (root / "shaders.dat").write_bytes(shader_data)

            with GameSource.open(root) as source:
                shader = Resistance2Reader(source).read_shader(SHADER_TUID)

        self.assertEqual(shader.albedo_texture_id, TEXTURE_IDS[0])
        self.assertEqual(shader.normal_texture_id, TEXTURE_IDS[1])
        self.assertEqual(shader.expensive_texture_id, TEXTURE_IDS[2])
        self.assertEqual(shader.detail_texture_id, TEXTURE_IDS[3])
        self.assertEqual(shader.lookup_path, "fixture/fixture")
        self.assertEqual(
            shader.texture_lookup_paths,
            ("fixture_c", "fixture_n", "fixture_e", "fixture_d"),
        )
        self.assertEqual(shader.material_word, MATERIAL_WORD)
        self.assertEqual(shader.material_flags, 0x1D)
        self.assertEqual(shader.blend_mode, 0)
        self.assertEqual(shader.material_unknown_0, 0)
        self.assertEqual(shader.material_unknown_1, 9)
        self.assertFalse(shader.use_specular)
        self.assertTrue(shader.use_glossiness)
        self.assertTrue(shader.use_normal_map)
        self.assertTrue(shader.use_detail_map)
        self.assertEqual(len(shader.material_values), 6)
        self.assertEqual(shader.material_values[0], (0.0, 0.25, 0.5, 0.75))
        self.assertEqual(shader.base_color_factor, (0.0, 0.25, 0.5, 0.75))
        self.assertEqual(
            shader.material_values[-1],
            (5.0, 5.25, 5.5, 5.75),
        )


if __name__ == "__main__":
    unittest.main()
