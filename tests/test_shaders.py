from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch

import numpy as np

from insomniac_asset_io.core.errors import (
    AmbiguousResourceError,
    MissingResourceError,
)
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import (
    DecodedTexture,
    ShaderInfo,
    TextureInfo,
)
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
        self.assertEqual(shader.texture_map_indices, (0, 1, 2, 3))
        self.assertEqual(shader.material_pre_control_values, ())
        self.assertTrue(shader.is_texture_slot_bound(0))
        self.assertTrue(shader.is_texture_slot_bound(3))
        self.assertFalse(shader.use_specular)
        self.assertTrue(shader.use_glossiness)
        self.assertTrue(shader.use_normal_map)
        self.assertTrue(shader.use_detail_map)
        self.assertIsNone(shader.detail_sampler_address)
        self.assertFalse(shader.detail_uses_signed_remap)
        self.assertEqual(len(shader.material_values), 6)
        self.assertEqual(shader.material_values[0], (0.0, 0.25, 0.5, 0.75))
        self.assertEqual(shader.base_color_factor, (0.0, 0.25, 0.5, 0.75))
        self.assertEqual(shader.detail_uv_scale, 4.5)
        self.assertEqual(
            shader.material_values[-1],
            (5.0, 5.25, 5.5, 5.75),
        )

    def test_missing_texture_resolves_by_identical_lookup_path(self):
        requested_id = 0xD780_B93E
        resolved_id = 0xFE6E_A434
        texture = self._texture(resolved_id, 32)
        reader = Resistance2Reader.__new__(Resistance2Reader)
        reader._texture_cache = {}
        reader._texture_fallback_cache = {}
        reader.texture_infos = {resolved_id: texture.info}
        reader.global_texture_locations = {}
        reader._texture_path_index = {
            "artdepartment/character/attackdrone/attackdrone_c": (
                requested_id,
                resolved_id,
            )
        }

        def read_exact(texture_id: int) -> DecodedTexture:
            if texture_id == resolved_id:
                return texture
            raise MissingResourceError(f"Missing 0x{texture_id:08X}")

        with patch.object(
            reader,
            "_read_texture_exact",
            side_effect=read_exact,
        ):
            actual = reader.read_texture(
                requested_id,
                lookup_path=(
                    r"ArtDepartment\Character\AttackDrone\AttackDrone_C"
                ),
            )

        self.assertIs(actual, texture)
        self.assertIs(
            reader._texture_fallback_cache[
                (
                    requested_id,
                    "artdepartment/character/attackdrone/attackdrone_c",
                )
            ],
            texture,
        )

    def test_lookup_path_rejects_different_available_payloads(self):
        requested_id = 0x1111_1111
        candidate_ids = (0x2222_2222, 0x3333_3333)
        textures = {
            candidate_ids[0]: self._texture(candidate_ids[0], 32),
            candidate_ids[1]: self._texture(candidate_ids[1], 64),
        }
        reader = Resistance2Reader.__new__(Resistance2Reader)
        reader._texture_cache = {}
        reader._texture_fallback_cache = {}
        reader.texture_infos = {
            texture_id: texture.info
            for texture_id, texture in textures.items()
        }
        reader.global_texture_locations = {}
        reader._texture_path_index = {
            "fixture/texture": (requested_id, *candidate_ids)
        }

        def read_exact(texture_id: int) -> DecodedTexture:
            try:
                return textures[texture_id]
            except KeyError as exc:
                raise MissingResourceError(
                    f"Missing 0x{texture_id:08X}"
                ) from exc

        with patch.object(
            reader,
            "_read_texture_exact",
            side_effect=read_exact,
        ):
            with self.assertRaisesRegex(
                AmbiguousResourceError,
                "multiple different available payloads",
            ):
                reader.read_texture(
                    requested_id,
                    lookup_path="fixture/texture",
                )

    def test_lookup_path_includes_extracted_sibling_shader_paths(self):
        requested_id = 0x1111_1111
        resolved_id = 0x2222_2222
        texture = self._texture(resolved_id, 32)
        reader = Resistance2Reader.__new__(Resistance2Reader)
        reader._texture_cache = {}
        reader._texture_fallback_cache = {}
        reader.texture_infos = {}
        reader.global_texture_locations = {}
        reader._texture_paths_by_id = {
            requested_id: ("fixture/texture",),
        }
        reader._texture_path_index = None
        reader._sibling_texture_index = {
            resolved_id: (Path("owner/level_cached.psarc"),),
        }
        reader._sibling_texture_path_index = {
            "fixture/texture": (resolved_id,),
        }

        def read_exact(texture_id: int) -> DecodedTexture:
            if texture_id == resolved_id:
                return texture
            raise MissingResourceError(f"Missing 0x{texture_id:08X}")

        with patch.object(
            reader,
            "_read_texture_exact",
            side_effect=read_exact,
        ):
            actual = reader.read_texture(
                requested_id,
                lookup_path="fixture/texture",
            )

        self.assertIs(actual, texture)

    def test_lookup_path_prefers_selected_level_alias_over_sibling_alias(self):
        requested_id = 0x1111_1111
        local_id = 0x2222_2222
        sibling_id = 0x3333_3333
        local = self._texture(local_id, 32)
        sibling = self._texture(sibling_id, 64)
        reader = Resistance2Reader.__new__(Resistance2Reader)
        reader._texture_cache = {}
        reader._texture_fallback_cache = {}
        reader.texture_infos = {local_id: local.info}
        reader.global_texture_locations = {}
        reader._texture_paths_by_id = {
            requested_id: ("fixture/texture",),
            local_id: ("fixture/texture",),
        }
        reader._texture_path_index = None
        reader._sibling_texture_index = {
            sibling_id: (Path("owner/level_cached.psarc"),),
        }
        reader._sibling_texture_path_index = {
            "fixture/texture": (sibling_id,),
        }

        def read_exact(texture_id: int) -> DecodedTexture:
            if texture_id == local_id:
                return local
            if texture_id == sibling_id:
                return sibling
            raise MissingResourceError(f"Missing 0x{texture_id:08X}")

        with patch.object(
            reader,
            "_read_texture_exact",
            side_effect=read_exact,
        ):
            actual = reader.read_texture(
                requested_id,
                lookup_path="fixture/texture",
            )

        self.assertIs(actual, local)

    def test_sibling_textures_allow_identical_payloads_with_distinct_tuids(self):
        texture_id = 0x1234_5678
        first = self._texture(texture_id, 32)
        second = self._texture(texture_id, 32)
        second.info = TextureInfo(
            id=texture_id,
            tuid=0xABCD_EF01_1234_5678,
            format_code=second.info.format_code,
            mip_count=second.info.mip_count,
            width=second.info.width,
            height=second.info.height,
            offset=second.info.offset,
            length=second.info.length,
        )
        reader = Resistance2Reader.__new__(Resistance2Reader)
        paths = (
            Path("owner_a/level_cached.psarc"),
            Path("owner_b/level_cached.psarc"),
        )
        reader._sibling_texture_index = {texture_id: paths}
        reader.warnings = []
        contexts = []
        siblings = []
        for texture in (first, second):
            context = mock.MagicMock()
            context.__enter__.return_value = object()
            contexts.append(context)
            sibling = mock.Mock()
            sibling.read_texture.return_value = texture
            siblings.append(sibling)

        read_sibling = Resistance2Reader._read_sibling_texture
        with (
            patch.object(
                GameSource,
                "open",
                side_effect=contexts,
            ),
            patch(
                "insomniac_asset_io.games.resistance_2.reader."
                "Resistance2Reader",
                side_effect=siblings,
            ),
        ):
            actual = read_sibling(reader, texture_id)

        self.assertIs(actual, first)

    @staticmethod
    def _texture(texture_id: int, value: int) -> DecodedTexture:
        return DecodedTexture(
            info=TextureInfo(
                id=texture_id,
                tuid=texture_id,
                format_code=0x08,
                mip_count=1,
                width=1,
                height=1,
                offset=0,
                length=4,
            ),
            rgba=np.full((1, 1, 4), value, dtype=np.uint8),
        )


if __name__ == "__main__":
    unittest.main()
