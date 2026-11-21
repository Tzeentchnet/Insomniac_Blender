from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader

SHADER_TUID = 0x1234_5678_90AB_CDEF
TEXTURE_IDS = (
    0x1111_1111,
    0x2222_2222,
    0x3333_3333,
    0x4444_4444,
    0x5555_5555,
    0x6666_6666,
)
MATERIAL_WORD = 0x1C04_1328
MATERIAL_PRE_CONTROL_VALUES = (0x1234_5678, 0x9ABC_DEF0)
FOLIAGE_TUID = 0x1122_3344_5566_7788
HIGHLIGHT_TUID = 0x8877_6655_4433_2211
EMBEDDED_TEXTURE_TUID = 0xFA8D_164F_8843_ADA3
TERRAIN_ZONE_TUID = 0x1020_3040_5060_7080


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
    struct.pack_into(">IIII", data, 0x30, 0x5D00, 0xD0, 1, 0x40)

    cursor = 0x140

    def add_string(value: str) -> int:
        nonlocal cursor
        encoded = value.encode("utf-8") + b"\0"
        offset = cursor
        data[offset : offset + len(encoded)] = encoded
        cursor += len(encoded)
        return offset

    lookup_path = add_string("fixture/r3_shader")
    texture_paths = tuple(
        add_string(f"fixture/r3_texture_{index}")
        for index in range(6)
    )
    struct.pack_into(
        ">IIII",
        data,
        0x40,
        0x11300,
        0x140,
        1,
        cursor - 0x140,
    )

    struct.pack_into(">6i", data, 0x50, 0, 1, 2, 3, 4, 5)
    struct.pack_into(">2I", data, 0x68, *MATERIAL_PRE_CONTROL_VALUES)
    struct.pack_into(">I", data, 0x70, MATERIAL_WORD)
    values = tuple(float(index) / 8.0 for index in range(20))
    tail = (10.25, 11.5, 12.75)
    struct.pack_into(">20f3f", data, 0x74, *values, *tail)

    struct.pack_into(">QII", data, 0xD0, SHADER_TUID, lookup_path, 0)
    struct.pack_into(">6I", data, 0xE0, *TEXTURE_IDS)
    struct.pack_into(">6I", data, 0xF8, *texture_paths)
    return bytes(data[:cursor])


def _collision_mesh() -> IghwFile:
    data = bytearray(0x120)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 1)
    struct.pack_into(">IIII", data, 0x20, 0xDA00, 0x40, 1, 0xE0)
    struct.pack_into(">6H", data, 0xC0, 8, 16, 257, 1, 0x50, 0)
    struct.pack_into(">H", data, 0xCC, 0x60)
    struct.pack_into(">3i", data, 0xD0, 0, 0, 0)
    struct.pack_into(">8H", data, 0xE0, *range(8))
    struct.pack_into(">8H", data, 0xF0, *range(8))
    struct.pack_into(">8H", data, 0x100, *range(8))
    struct.pack_into(">I", data, 0x110, (0 << 18) | (1 << 9) | 2)
    return IghwFile(data, label="Resistance 3 collision fixture")


def _terrain_zone() -> IghwFile:
    data = bytearray(0x1C0)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 5)
    struct.pack_into(">IIII", data, 0x20, 0x6000, 0x80, 1, 0x48)
    struct.pack_into(">IIII", data, 0x30, 0x6100, 0xD0, 1, 0x06)
    struct.pack_into(">IIII", data, 0x40, 0x6200, 0x100, 1, 0x80)
    struct.pack_into(">IIII", data, 0x50, 0x5400, 0x180, 1, 0x20)
    struct.pack_into(">IIII", data, 0x60, 0x5410, 0x1A0, 1, 0x20)
    purposes = (-4096, 8193, 32767)
    secondary_uvs = ((0.0, 0.25), (0.5, 0.75), (1.0, 1.0))
    for index, position in enumerate(
        ((256, 512, 768), (512, 512, 768), (256, 768, 768))
    ):
        vertex = 0x80 + index * 0x18
        struct.pack_into(">3h", data, vertex, *position)
        struct.pack_into(">h", data, vertex + 6, purposes[index])
        struct.pack_into(">2e", data, vertex + 8, 0.0, 0.0)
        struct.pack_into(">2e", data, vertex + 0x0C, *secondary_uvs[index])
    struct.pack_into(">3H", data, 0xD0, 0, 1, 2)
    struct.pack_into(">IIHH", data, 0x140, 0, 0, 3, 3)
    struct.pack_into(">H", data, 0x14E, 0)
    struct.pack_into(">3f", data, 0x160, 25_600.0, 51_200.0, 76_800.0)
    struct.pack_into(">3f", data, 0x170, 2_560.0, 5_120.0, 7_680.0)
    struct.pack_into(">IHB", data, 0x180, 0, 1, 0x86)
    struct.pack_into(">HH", data, 0x198, 4, 4)
    struct.pack_into(">IHB", data, 0x1A0, 8, 1, 0x86)
    struct.pack_into(">HH", data, 0x1B8, 4, 4)
    return IghwFile(data, label="Resistance 3 terrain fixture")


def _terrain_assetlookup(zone_length: int, lighting_length: int) -> bytes:
    data = bytearray(0x60)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 2)
    struct.pack_into(
        ">IIII",
        data,
        0x20,
        AssetKind.ZONE.section_id,
        0x40,
        1,
        0x10,
    )
    struct.pack_into(
        ">IIII",
        data,
        0x30,
        AssetKind.LIGHTING.section_id,
        0x50,
        1,
        0x10,
    )
    struct.pack_into(">QII", data, 0x40, TERRAIN_ZONE_TUID, 0, zone_length)
    struct.pack_into(
        ">QII",
        data,
        0x50,
        TERRAIN_ZONE_TUID,
        0,
        lighting_length,
    )
    return bytes(data)


def _extended_assetlookup() -> bytes:
    data = bytearray(0x60)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 2)
    struct.pack_into(">IIII", data, 0x20, 0x1D400, 0x40, 1, 0x10)
    struct.pack_into(">IIII", data, 0x30, 0x1DB80, 0x50, 1, 0x10)
    struct.pack_into(">QII", data, 0x40, FOLIAGE_TUID, 1, 3)
    struct.pack_into(">QII", data, 0x50, HIGHLIGHT_TUID, 2, 4)
    return bytes(data)


def _embedded_texture_assetlookup() -> bytes:
    data = bytearray(0x80)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 3)
    struct.pack_into(">IIII", data, 0x20, 0x1D140, 0x50, 1, 4)
    struct.pack_into(">IIII", data, 0x30, 0x1D180, 0x60, 1, 0x10)
    struct.pack_into(">IIII", data, 0x40, 0x1D1C0, 0x70, 1, 0x10)
    struct.pack_into(">4B", data, 0x50, 0x86, 1, 2, 2)
    struct.pack_into(
        ">QII",
        data,
        0x60,
        EMBEDDED_TEXTURE_TUID,
        0,
        8,
    )
    struct.pack_into(
        ">QII",
        data,
        0x70,
        EMBEDDED_TEXTURE_TUID,
        0,
        0,
    )
    return bytes(data)


class Resistance3Tests(unittest.TestCase):
    def test_scopes_luminance_alpha_to_goliath_wet_overlay(self):
        shader_tuid = 0x5966_249F_D0D6_149B

        self.assertEqual(
            Resistance3Reader.material_luminance_alpha_shader_tuids,
            frozenset({shader_tuid}),
        )
        self.assertNotIn(
            shader_tuid,
            Resistance2Reader.material_luminance_alpha_shader_tuids,
        )

    def test_reads_six_slot_shader_and_shifted_material_record(self):
        shader_data = _shader()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(
                _assetlookup(len(shader_data))
            )
            (root / "shaders.dat").write_bytes(shader_data)

            with GameSource.open(root) as source:
                reader = Resistance3Reader(source)
                shader = reader.read_shader(SHADER_TUID)

        self.assertEqual(reader.game_id, "RESISTANCE_3")
        self.assertEqual(shader.albedo_texture_id, TEXTURE_IDS[0])
        self.assertEqual(shader.normal_texture_id, TEXTURE_IDS[1])
        self.assertEqual(shader.expensive_texture_id, TEXTURE_IDS[2])
        self.assertEqual(shader.detail_texture_id, TEXTURE_IDS[3])
        self.assertEqual(shader.extra_texture_ids, TEXTURE_IDS[4:])
        self.assertEqual(
            shader.texture_lookup_paths,
            tuple(
                f"fixture/r3_texture_{index}"
                for index in range(4)
            ),
        )
        self.assertEqual(
            shader.extra_texture_lookup_paths,
            ("fixture/r3_texture_4", "fixture/r3_texture_5"),
        )
        self.assertEqual(shader.texture_map_indices, (0, 1, 2, 3, 4, 5))
        self.assertEqual(
            shader.material_pre_control_values,
            MATERIAL_PRE_CONTROL_VALUES,
        )
        self.assertTrue(shader.is_texture_slot_bound(4))
        self.assertTrue(shader.is_texture_slot_bound(5))
        self.assertEqual(shader.material_word, MATERIAL_WORD)
        self.assertEqual(len(shader.material_values), 5)
        self.assertEqual(
            shader.material_values[0],
            (0.0, 0.125, 0.25, 0.375),
        )
        self.assertEqual(
            shader.material_tail_values,
            (10.25, 11.5, 12.75),
        )

    def test_accepts_shared_collision_quantization_marker(self):
        parsed = _collision_mesh()

        for reader_type in (Resistance2Reader, Resistance3Reader):
            with self.subTest(reader=reader_type.game_id):
                collision = reader_type._read_collision_mesh(parsed, 0x40)

                self.assertEqual(collision.positions.shape, (8, 3))
                self.assertEqual(collision.indices.tolist(), [[0, 1, 2]])

    def test_uses_game_specific_ufrag_position_offsets(self):
        parsed = _terrain_zone()

        r2_terrain = object.__new__(Resistance2Reader)._parse_terrain(parsed)
        r3_terrain = object.__new__(Resistance3Reader)._parse_terrain(parsed)

        np.testing.assert_allclose(
            r2_terrain[0].positions[0],
            (101.0, 202.0, 303.0),
        )
        np.testing.assert_allclose(
            r3_terrain[0].positions[0],
            (11.0, 22.0, 33.0),
        )
        self.assertIsNone(r2_terrain[0].secondary_uvs)
        self.assertIsNone(r2_terrain[0].colors)
        self.assertEqual(r3_terrain[0].lightmap_index, 0)
        np.testing.assert_allclose(
            r3_terrain[0].secondary_uvs,
            ((0.0, 0.25), (0.5, 0.75), (1.0, 1.0)),
        )
        np.testing.assert_allclose(
            r3_terrain[0].colors[:, 0],
            (0.25, 8193 / 0x4000, 1.0),
        )
        np.testing.assert_allclose(
            r3_terrain[0].colors[:, 3],
            (0.0, 1 / 0x80, 127 / 0x80),
        )

    def test_reads_zone_lightmap_from_matching_lighting_asset(self):
        parsed = _terrain_zone()
        zone_data = bytes(parsed.data)
        texels = (
            struct.pack("<HHI", 0xF800, 0x001F, 0)
            + struct.pack("<HHI", 0x07E0, 0x001F, 0)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(
                _terrain_assetlookup(len(zone_data), len(texels))
            )
            (root / "zones.dat").write_bytes(zone_data)
            (root / "lighting.dat").write_bytes(texels)

            with GameSource.open(root) as source:
                zone = Resistance3Reader(source).read_zones()[0]

        self.assertEqual(zone.tuid, TERRAIN_ZONE_TUID)
        self.assertEqual(set(zone.lightmaps), {0})
        self.assertEqual(set(zone.directional_lightmaps), {0})
        lightmap = zone.lightmaps[0]
        directional = zone.directional_lightmaps[0]
        self.assertEqual(lightmap.info.tuid, TERRAIN_ZONE_TUID)
        self.assertEqual(lightmap.info.format_code, 0x86)
        self.assertEqual(lightmap.rgba.shape, (4, 4, 4))
        self.assertEqual(directional.info.offset, 8)
        self.assertEqual(directional.info.format_code, 0x86)
        self.assertEqual(directional.rgba.shape, (4, 4, 4))
        self.assertFalse(np.array_equal(lightmap.rgba, directional.rgba))

    def test_rejects_unpaired_directional_lightmap_table(self):
        zone_data = bytearray(_terrain_zone().data)
        struct.pack_into(">I", zone_data, 8, 4)
        texels = struct.pack("<HHI", 0xF800, 0x001F, 0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(
                _terrain_assetlookup(len(zone_data), len(texels))
            )
            (root / "zones.dat").write_bytes(zone_data)
            (root / "lighting.dat").write_bytes(texels)

            with GameSource.open(root) as source:
                with self.assertRaisesRegex(
                    FormatError,
                    "parallel 0x5400 and 0x5410",
                ):
                    Resistance3Reader(source).read_zones()

    def test_uses_v3_tie_shader_group_byte(self):
        data = bytearray(0x80)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        mesh = 0x40
        data[mesh + 0x26] = 2
        struct.pack_into(">H", data, mesh + 0x28, 0)
        struct.pack_into(">H", data, mesh + 0x0C, 1)
        parsed = IghwFile(data, label="Tie shader fixture")

        r2_index = Resistance2Reader._tie_shader_index(parsed, mesh, 3)
        r3_index = Resistance3Reader._tie_shader_index(parsed, mesh, 3)

        self.assertEqual(r2_index, 0)
        self.assertEqual(r3_index, 2)

    def test_decodes_tie_vertex_lighting_and_secondary_uvs(self):
        vertex_data = bytearray(3 * 0x14)
        purposes = (-4096, 8193, 32767)
        for index, purpose in enumerate(purposes):
            vertex = index * 0x14
            struct.pack_into(">3h", vertex_data, vertex, index, index + 1, 2)
            struct.pack_into(">h", vertex_data, vertex + 6, purpose)
            struct.pack_into(">2e", vertex_data, vertex + 8, index / 2, 0.5)
        secondary_uvs = ((0.0, 0.25), (0.5, 0.75), (1.0, 1.0))
        secondary_data = b"".join(
            struct.pack(">2e", *uv) for uv in secondary_uvs
        )
        index_data = struct.pack(">3H", 0, 1, 2)

        mesh = Resistance3Reader._decode_tie_mesh(
            memoryview(vertex_data),
            memoryview(index_data),
            0,
            3,
            0,
            3,
            np.ones(3, dtype=np.float32),
            0,
            ">",
            "R3 Tie fixture",
            secondary_vertex_buffer=memoryview(secondary_data),
            use_secondary_uvs=True,
            decode_vertex_lighting=True,
        )

        np.testing.assert_allclose(mesh.secondary_uvs, secondary_uvs)
        np.testing.assert_allclose(
            mesh.colors[:, 0],
            (0.25, 8193 / 0x4000, 1.0),
        )
        np.testing.assert_allclose(
            mesh.colors[:, 3],
            (0.0, 1 / 0x80, 127 / 0x80),
        )

    def test_reads_r3_tie_instance_lightmap_index(self):
        data = bytearray(0x80)
        struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, 0)
        struct.pack_into(">h", data, 0x5A, 23)
        parsed = IghwFile(data, label="Tie instance fixture")
        r2 = object.__new__(Resistance2Reader)
        r3 = object.__new__(Resistance3Reader)

        self.assertIsNone(r2._tie_instance_lightmap_index(parsed, 0))
        self.assertEqual(r3._tie_instance_lightmap_index(parsed, 0), 23)

        struct.pack_into(">h", data, 0x5A, -1)
        parsed = IghwFile(data, label="Unlit Tie instance fixture")
        self.assertIsNone(r3._tie_instance_lightmap_index(parsed, 0))

    def test_decodes_embedded_texture_when_high_mip_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(
                _embedded_texture_assetlookup()
            )
            (root / "textures.dat").write_bytes(
                struct.pack("<HHI", 0xF800, 0x001F, 0)
            )
            (root / "highmips.dat").write_bytes(b"")

            with GameSource.open(root) as source:
                texture = Resistance3Reader(source).read_texture(
                    EMBEDDED_TEXTURE_TUID & 0xFFFF_FFFF
                )

        self.assertEqual(texture.info.tuid, EMBEDDED_TEXTURE_TUID)
        self.assertEqual(texture.info.length, 8)
        self.assertEqual(texture.rgba.shape, (4, 4, 4))

    def test_reads_recomp_verified_resource_companions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assetlookup.dat").write_bytes(_extended_assetlookup())
            (root / "foliages.dat").write_bytes(b"_fol_")
            (root / "highlight.dat").write_bytes(b"__high__")

            with GameSource.open(root) as source:
                reader = Resistance3Reader(source)
                foliage = reader._asset_bytes(
                    AssetKind.FOLIAGE,
                    FOLIAGE_TUID,
                )
                highlight = reader._asset_bytes(
                    AssetKind.HIGHLIGHT,
                    HIGHLIGHT_TUID,
                )

        self.assertEqual(AssetKind.FOLIAGE.filename, "foliages.dat")
        self.assertEqual(foliage, b"fol")
        self.assertEqual(AssetKind.HIGHLIGHT.section_id, 0x1DB80)
        self.assertEqual(highlight, b"high")


if __name__ == "__main__":
    unittest.main()
