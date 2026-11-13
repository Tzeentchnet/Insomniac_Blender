from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.errors import FormatError, MissingResourceError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_burning_skies.catalog import (
    build_source_catalog,
)
from insomniac_asset_io.games.resistance_burning_skies.formats import (
    NSIRSRC_MAGIC,
    NSIRSRC_VERSION,
)
from insomniac_asset_io.games.resistance_burning_skies.materials import (
    decode_material_texture_paths,
)
from insomniac_asset_io.games.resistance_burning_skies.models import (
    decode_smf,
)
from insomniac_asset_io.games.resistance_burning_skies.reader import (
    BurningSkiesReader,
)
from insomniac_asset_io.games.resistance_burning_skies.textures import (
    FORMAT_PVRTCII4,
    FORMAT_RGBA8_ABGR,
    FORMAT_UBC1,
    FORMAT_UBC2,
    FORMAT_UBC3,
    GXT_MAGIC,
    GXT_VERSION,
    TEXTURE_TYPE_LINEAR,
    TEXTURE_TYPE_SWIZZLED,
    decode_p2t,
)

DATA_BASE = 0x1C0


def _resource(root_type: int, size: int, name: str) -> bytearray:
    data = bytearray(size)
    data[:8] = NSIRSRC_MAGIC
    struct.pack_into("<II", data, 0x08, NSIRSRC_VERSION, DATA_BASE)
    struct.pack_into("<I", data, 0x28, 0x2C)
    encoded_name = name.encode("ascii") + b"\0"
    data[0x2C : 0x2C + len(encoded_name)] = encoded_name
    struct.pack_into("<I", data, 0x5C, root_type)
    return data


def _stream_descriptor(
    data: bytearray,
    *,
    offset: int,
    stream_offset: int,
    stream_size: int,
    scale: float = 1.0,
) -> None:
    struct.pack_into(
        "<f7I",
        data,
        offset,
        scale,
        stream_size,
        stream_offset,
        0,
        0,
        0,
        0,
        0,
    )


def _model_resource(
    *,
    renderable: bool = True,
    material_names: tuple[str, ...] = ("00000000_11111111_22222222_33333333",),
    material_indices: tuple[int, ...] = (0,),
    lod_material_indices: tuple[tuple[int, ...], ...] | None = None,
    bone_parents: tuple[int, ...] = (),
    gib_mesh_indices: tuple[int, ...] = (),
    gib_bone_index: int = 0,
) -> bytes:
    if not renderable:
        lod_material_indices = ()
    elif lod_material_indices is None:
        lod_material_indices = (material_indices,)
    lod_count = len(lod_material_indices)
    mesh_count = sum(len(indices) for indices in lod_material_indices)
    lod_records_pointer = 0x140
    entries_pointer = lod_records_pointer + lod_count * 0x10
    mesh_entry_offsets: list[int] = []
    lod_entry_offsets: list[int] = []
    next_entry_offset = entries_pointer
    for lod_indices in lod_material_indices:
        lod_entry_offsets.append(next_entry_offset)
        mesh_entry_offsets.extend(
            next_entry_offset + index * 0x90
            for index in range(len(lod_indices))
        )
        next_entry_offset += len(lod_indices) * 0x90
    flattened_material_indices = tuple(
        index
        for lod_indices in lod_material_indices
        for index in lod_indices
    )
    entries_end = next_entry_offset
    summary_pointer = (entries_end + 0x0F) & ~0x0F
    descriptor_pointer = summary_pointer + 0x20
    reference_count_pointer = descriptor_pointer + 0x20
    reference_table_end = reference_count_pointer + 12 + mesh_count * 8
    physics_pointer = (
        (reference_table_end + 3) & ~3 if gib_mesh_indices else 0
    )
    gib_property_pointer = (
        physics_pointer + 0x18 if gib_mesh_indices else 0
    )
    gib_property_name = b"prp_traumaBase_small_gib\0"
    physics_end = (
        gib_property_pointer + 4 + len(gib_property_name)
        if gib_mesh_indices
        else 0
    )
    bone_records_pointer = 0x150
    bone_records_end = bone_records_pointer + len(bone_parents) * 0x70
    serialized_end = max(reference_table_end, bone_records_end, physics_end)
    stream_offset = (serialized_end + 0xFF) & ~0xFF
    stream_offsets = tuple(
        stream_offset + lod_level * 0x100
        for lod_level in range(max(lod_count, 1))
    )
    buffer_origins = tuple(
        DATA_BASE + offset + 0x10
        for offset in stream_offsets
    )
    data = _resource(0x1C, buffer_origins[-1] + 0xA0, "TestCube")
    for lod_level, lod_stream_offset in enumerate(stream_offsets):
        _stream_descriptor(
            data,
            offset=0x100 + lod_level * 0x20,
            stream_offset=lod_stream_offset,
            stream_size=0xB0,
            scale=(lod_level + 1) / len(stream_offsets),
        )
    if len(material_names) > 2:
        raise ValueError("Synthetic fixture supports at most two materials")
    record_offsets = (0xD0, 0x180)
    struct.pack_into("<II", data, 0x7C, len(material_names), 0xC0)
    for index, material_name in enumerate(material_names):
        record_offset = record_offsets[index]
        struct.pack_into("<I", data, 0xC0 + index * 4, record_offset)
        struct.pack_into("<I", data, record_offset, 4)
        encoded_name = material_name.encode("ascii") + b"\0"
        data[record_offset + 4 : record_offset + 4 + len(encoded_name)] = encoded_name

    struct.pack_into("<I", data, DATA_BASE, len(material_names))
    declared_bone_count = max(
        len(bone_parents),
        gib_bone_index + 1 if gib_mesh_indices else 0,
    )
    struct.pack_into(
        "<II",
        data,
        DATA_BASE + 0x0C,
        declared_bone_count,
        declared_bone_count,
    )
    struct.pack_into(
        "<I",
        data,
        DATA_BASE + 0x14,
        bone_records_pointer if bone_parents else 0,
    )
    struct.pack_into(
        "<II",
        data,
        DATA_BASE + 0x18,
        lod_count,
        lod_records_pointer if lod_count else 0,
    )
    if gib_mesh_indices:
        struct.pack_into("<I", data, DATA_BASE + 0x04, physics_end)
        struct.pack_into("<I", data, DATA_BASE + 0x28, physics_pointer)
    for bone_index, parent_index in enumerate(bone_parents):
        record_offset = DATA_BASE + bone_records_pointer + bone_index * 0x70
        inverse_world = np.eye(4, dtype=np.float32)
        inverse_world[2, 3] = -float(bone_index)
        struct.pack_into(
            "<16f",
            data,
            record_offset,
            *inverse_world.T.reshape(-1),
        )
        struct.pack_into(
            "<4f",
            data,
            record_offset + 0x40,
            0.0,
            0.0,
            float(bone_index if parent_index < 0 else 1),
            1.0,
        )
        struct.pack_into(
            "<4f",
            data,
            record_offset + 0x50,
            1.0,
            0.0,
            0.0,
            0.0,
        )
        struct.pack_into(
            "<i",
            data,
            record_offset + 0x60,
            parent_index,
        )
    if not renderable:
        return bytes(data)

    for lod_level, lod_indices in enumerate(lod_material_indices):
        struct.pack_into(
            "<2I",
            data,
            DATA_BASE + lod_records_pointer + lod_level * 0x10,
            len(lod_indices),
            lod_entry_offsets[lod_level],
        )
    gib_mesh_index_set = set(gib_mesh_indices)
    for mesh_index, entry_offset in enumerate(mesh_entry_offsets):
        record_offset = DATA_BASE + entry_offset + 0x08
        struct.pack_into(
            "<I",
            data,
            record_offset + 0x0C,
            summary_pointer,
        )
        if mesh_index in gib_mesh_index_set:
            struct.pack_into(
                "<I",
                data,
                DATA_BASE + entry_offset + 0x44,
                (1 << 16) | gib_bone_index,
            )
    summary_offset = DATA_BASE + summary_pointer
    descriptor_offset = DATA_BASE + descriptor_pointer
    struct.pack_into(
        "<8I",
        data,
        summary_offset,
        1,
        0xA0,
        6,
        4,
        0x15,
        0,
        descriptor_pointer,
        0,
    )
    struct.pack_into(
        "<8I",
        data,
        descriptor_offset,
        0xA0,
        0xA0,
        4,
        0,
        2,
        6,
        0x90,
        0,
    )
    reference_count_offset = DATA_BASE + reference_count_pointer
    struct.pack_into(
        "<3I",
        data,
        reference_count_offset,
        mesh_count,
        0x00020500,
        4,
    )
    for reference_index, mesh_index in enumerate(reversed(range(mesh_count))):
        struct.pack_into(
            "<2I",
            data,
            reference_count_offset + 12 + reference_index * 8,
            mesh_entry_offsets[mesh_index],
            flattened_material_indices[mesh_index],
        )
    if gib_mesh_indices:
        struct.pack_into(
            "<6I",
            data,
            DATA_BASE + physics_pointer,
            gib_bone_index,
            gib_property_pointer,
            1,
            0xFFFFFFFF,
            0xFFFFFFFF,
            0,
        )
        struct.pack_into(
            "<I",
            data,
            DATA_BASE + gib_property_pointer,
            0x1388,
        )
        property_name_offset = DATA_BASE + gib_property_pointer + 4
        data[
            property_name_offset : property_name_offset
            + len(gib_property_name)
        ] = gib_property_name

    vertices = (
        ((-1.0, -1.0, 0.0), (0.0, 0.0)),
        ((1.0, -1.0, 0.0), (1.0, 0.0)),
        ((-1.0, 1.0, 0.0), (0.0, 1.0)),
        ((1.0, 1.0, 0.0), (1.0, 1.0)),
    )
    for buffer_origin in buffer_origins:
        for index, (position, uv) in enumerate(vertices):
            offset = buffer_origin + index * 36
            struct.pack_into("<3f", data, offset, *position)
            struct.pack_into("<f", data, offset + 12, uv[0])
            struct.pack_into(
                "<4e",
                data,
                offset + 16,
                0.0,
                0.0,
                1.0,
                uv[1] - 1.0,
            )
            struct.pack_into(
                "<4e",
                data,
                offset + 24,
                1.0,
                0.0,
                0.0,
                1.0,
            )
            struct.pack_into("<I", data, offset + 32, 0x80000000)
        struct.pack_into(
            "<6H",
            data,
            buffer_origin + 0x90,
            0,
            1,
            2,
            2,
            1,
            3,
        )
    return bytes(data)


def _texture_resource(
    format_code: int = FORMAT_UBC1,
    *,
    payload: bytes | None = None,
    width: int = 4,
    height: int = 4,
    texture_type: int = TEXTURE_TYPE_LINEAR,
) -> bytes:
    stream_offset = 0x200
    gxt_offset = DATA_BASE + stream_offset
    if payload is None:
        if format_code == FORMAT_UBC1:
            payload = struct.pack("<HHI", 0xF800, 0x07E0, 0)
        else:
            payload = bytes(16)
    data = _resource(0x0E, gxt_offset + 0x40 + len(payload), "TestTexture")
    _stream_descriptor(
        data,
        offset=0x100,
        stream_offset=stream_offset,
        stream_size=0x40 + len(payload),
    )
    struct.pack_into(
        "<4s7I",
        data,
        gxt_offset,
        GXT_MAGIC,
        GXT_VERSION,
        1,
        0x40,
        len(payload),
        0,
        0,
        0,
    )
    struct.pack_into(
        "<8I",
        data,
        gxt_offset + 0x20,
        0x40,
        len(payload),
        0xFFFFFFFF,
        0,
        texture_type,
        format_code,
        width | (height << 16),
        1,
    )
    data[gxt_offset + 0x40 :] = payload
    return bytes(data)


class BurningSkiesFormatTests(unittest.TestCase):
    def test_decodes_rigid_gbuffer_geometry_and_uvs(self):
        model = decode_smf(
            _model_resource(),
            label="models/TestCube.smf",
        )

        self.assertEqual(model.name, "TestCube")
        self.assertEqual(
            model.material_names,
            ("00000000_11111111_22222222_33333333",),
        )
        self.assertTrue(model.material_binding_supported)
        self.assertEqual(len(model.meshes), 1)
        mesh = model.meshes[0]
        self.assertEqual(mesh.lod_level, 0)
        np.testing.assert_array_equal(
            mesh.indices,
            np.array([0, 1, 2, 2, 1, 3], dtype=np.int32),
        )
        np.testing.assert_allclose(
            mesh.uvs,
            np.array(
                ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)),
                dtype=np.float32,
            ),
        )

    def test_rejects_model_without_render_geometry(self):
        with self.assertRaisesRegex(
            MissingResourceError,
            "neither renderable GBuffer geometry nor a recoverable skeleton",
        ):
            decode_smf(
                _model_resource(renderable=False),
                label="models/TestPhysics.smf",
            )

    def test_decodes_skeleton_only_model(self):
        model = decode_smf(
            _model_resource(
                renderable=False,
                bone_parents=(-1, 0),
            ),
            label="models/TestSkeleton.smf",
        )

        self.assertEqual(model.meshes, [])
        self.assertEqual(model.material_names, ())
        self.assertIsNotNone(model.skeleton)
        assert model.skeleton is not None
        self.assertEqual(model.skeleton.bone_count, 2)
        self.assertEqual(model.skeleton.root_bone, 0)
        self.assertEqual(model.skeleton.bones[1].parent_index, 0)
        np.testing.assert_allclose(
            model.skeleton.bind_world[:, :3, 3],
            np.array(((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
            atol=1e-6,
        )

    def test_uses_serialized_packet_material_map(self):
        model = decode_smf(
            _model_resource(
                material_names=(
                    "00000000_11111111_22222222_33333333",
                    "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
                ),
                material_indices=(1, 0, 1),
            ),
            label="models/TestMappedMaterials.smf",
        )

        self.assertTrue(model.material_binding_supported)
        self.assertEqual(model.material_dependency_count, 2)
        self.assertEqual(
            model.material_names,
            (
                "00000000_11111111_22222222_33333333",
                "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
            ),
        )
        self.assertEqual(
            [mesh.shader_index for mesh in model.meshes],
            [1, 0, 1],
        )

    def test_classifies_authored_gib_packets_as_damage(self):
        model = decode_smf(
            _model_resource(
                material_indices=(0, 0),
                gib_mesh_indices=(1,),
                gib_bone_index=5,
            ),
            label="models/TestDamage.smf",
        )

        self.assertFalse(model.meshes[0].is_damage_permutation)
        self.assertTrue(model.meshes[1].is_damage_permutation)
        self.assertEqual(model.meshes[1].segment_index, 5)

    def test_decodes_all_lod_groups_and_exact_material_offsets(self):
        model = decode_smf(
            _model_resource(
                material_names=(
                    "00000000_11111111_22222222_33333333",
                    "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
                ),
                lod_material_indices=((1, 0), (0, 1)),
            ),
            label="models/TestLods.smf",
        )

        self.assertEqual(
            [mesh.lod_level for mesh in model.meshes],
            [0, 0, 1, 1],
        )
        self.assertEqual(
            [mesh.shader_index for mesh in model.meshes],
            [0, 1, 1, 0],
        )

    def test_rejects_out_of_range_model_index(self):
        data = bytearray(_model_resource())
        index_offset = DATA_BASE + 0x300 + 0x10 + 0x90
        struct.pack_into("<H", data, index_offset, 10)

        with self.assertRaisesRegex(FormatError, "index exceeds"):
            decode_smf(bytes(data), label="models/Invalid.smf")

    def test_decodes_linear_ubc1_gxt(self):
        texture = decode_p2t(
            _texture_resource(),
            label="textures/Test.p2t",
            texture_id=123,
        )

        self.assertEqual(texture.rgba.shape, (4, 4, 4))
        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_pvrtc2_hard_transition_and_punchthrough(self):
        blocks = (
            (0xE4E4E4E4, 0xFC1FFC01 | 0x8000),
            (0x1B1B1B1B, 0x83E0FFFF),
            (0xAAAAAAAA, 0xFFFF801F),
            (0x55555555, 0xFC007C01),
        )
        payload = b"".join(
            struct.pack("<2I", *block)
            for block in blocks
        )
        texture = decode_p2t(
            _texture_resource(
                FORMAT_PVRTCII4,
                payload=payload,
                width=8,
                height=8,
                texture_type=TEXTURE_TYPE_SWIZZLED,
            ),
            label="textures/TestNormal.p2t",
            texture_id=456,
        )

        coordinates = (
            (0, 0),
            (0, 3),
            (1, 6),
            (2, 2),
            (3, 5),
            (4, 1),
            (5, 4),
            (7, 7),
        )
        actual = [
            tuple(int(channel) for channel in texture.rgba[y, x])
            for y, x in coordinates
        ]
        self.assertEqual(
            actual,
            [
                (191, 63, 127, 255),
                (255, 0, 0, 255),
                (159, 95, 191, 0),
                (255, 0, 159, 255),
                (255, 255, 255, 255),
                (255, 0, 0, 255),
                (255, 0, 0, 255),
                (207, 71, 79, 255),
            ],
        )

    def test_decodes_ubc2_explicit_alpha(self):
        payload = bytes([0xFF] * 8) + struct.pack(
            "<HHI",
            0xF800,
            0x07E0,
            0,
        )
        texture = decode_p2t(
            _texture_resource(FORMAT_UBC2, payload=payload),
            label="textures/TestUbc2.p2t",
            texture_id=124,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_ubc3_interpolated_alpha(self):
        payload = (
            bytes((255, 0))
            + bytes(6)
            + struct.pack(
                "<HHI",
                0xF800,
                0x07E0,
                0,
            )
        )
        texture = decode_p2t(
            _texture_resource(FORMAT_UBC3, payload=payload),
            label="textures/TestUbc3.p2t",
            texture_id=125,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_32_bit_bgra_channel_order(self):
        texture = decode_p2t(
            _texture_resource(
                FORMAT_RGBA8_ABGR,
                payload=bytes((255, 0, 0, 128)) * 16,
            ),
            label="textures/TestColor.p2t",
            texture_id=126,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (0, 0, 255, 128), dtype=np.uint8),
        )

    def test_unswizzles_vita_rectangular_morton_block_order(self):
        colors = (
            0xF800,
            0x07E0,
            0x001F,
            0xFFFF,
            0xFFE0,
            0x07FF,
            0xF81F,
            0x0000,
        )
        payload = b"".join(struct.pack("<HHI", color, 0, 0) for color in colors)
        texture = decode_p2t(
            _texture_resource(
                FORMAT_UBC1,
                payload=payload,
                width=16,
                height=8,
                texture_type=TEXTURE_TYPE_SWIZZLED,
            ),
            label="textures/TestSwizzled.p2t",
            texture_id=127,
        )

        expected = (
            (255, 0, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 0),
            (255, 255, 255),
            (0, 255, 255),
            (0, 0, 0),
        )
        actual = tuple(
            tuple(int(channel) for channel in texture.rgba[y, x, :3])
            for y in (1, 5)
            for x in (1, 5, 9, 13)
        )
        self.assertEqual(actual, expected)

    def test_resolves_bmx_texture_parameter_names(self):
        data = _resource(0x09, DATA_BASE, "TestMaterial")
        strings = (
            b"g_DiffuseMapTexture\0Test_CLR\0"
            b"g_NormalMapTexture\0Test_NRM\0"
            b"g_GlossMapTexture\0Test_GLS\0"
            b"g_EmissiveMapTexture\0Test_GLO\0"
            b"g_AlphaMapTexture\0Test_APH\0"
        )
        data[0x100 : 0x100 + len(strings)] = strings

        textures = decode_material_texture_paths(
            bytes(data),
            label="materials/Test.bmx",
            texture_paths_by_stem={
                "test_clr": "textures/Test_CLR.p2t",
                "test_nrm": "textures/Test_NRM.p2t",
                "test_gls": "textures/Test_GLS.p2t",
                "test_glo": "textures/Test_GLO.p2t",
                "test_aph": "textures/Test_APH.p2t",
            },
        )

        self.assertEqual(textures.albedo, "textures/Test_CLR.p2t")
        self.assertEqual(textures.normal, "textures/Test_NRM.p2t")
        self.assertEqual(textures.specular, "textures/Test_GLS.p2t")
        self.assertEqual(textures.emissive, "textures/Test_GLO.p2t")
        self.assertEqual(textures.alpha, "textures/Test_APH.p2t")

    def test_classifies_auxiliary_bmx_materials(self):
        data = _resource(0x09, DATA_BASE, "TestMaterial")
        strings = b"WorldCollision\0OnlyCastShadows\0"
        data[0x100 : 0x100 + len(strings)] = strings

        material = decode_material_texture_paths(
            bytes(data),
            label="materials/Test.bmx",
            texture_paths_by_stem={},
        )

        self.assertTrue(material.world_collision)
        self.assertTrue(material.only_cast_shadows)
        self.assertTrue(material.is_auxiliary)

    def test_builds_source_driven_catalog_from_smf_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "models" / "Test_Cube.smf"
            model_path.parent.mkdir()
            model_path.write_bytes(_model_resource())

            catalog = build_source_catalog(root)

        self.assertEqual(catalog.game_id, "RESISTANCE_BURNING_SKIES")
        self.assertEqual(catalog.region, "Experimental Vita archive")
        self.assertEqual(len(catalog.assets), 1)
        asset = catalog.assets[0]
        self.assertEqual(asset.display_name, "Test Cube")
        self.assertEqual(asset.identity_label.split()[0], "Model")
        self.assertEqual(asset.source_name, "models/Test_Cube.smf")

    def test_existing_invalid_catalog_source_reports_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build.psa"
            path.write_bytes(b"not a PSARC")

            with self.assertRaisesRegex(
                MissingResourceError,
                "contains no named models",
            ):
                build_source_catalog(path)

    def test_reports_material_fallback_for_missing_bmx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "models" / "Test_Cube.smf"
            model_path.parent.mkdir()
            model_path.write_bytes(_model_resource())
            with GameSource.open(root) as source:
                reader = BurningSkiesReader(source)
                model = reader.read_moby(reader.model_id("models/Test_Cube.smf"))
                warnings = reader.consume_import_warnings()

        self.assertEqual(model.shader_tuids, ())
        self.assertTrue(
            any("BMX dependencies are unavailable" in item for item in warnings)
        )
        self.assertEqual(reader.consume_import_warnings(), ())


if __name__ == "__main__":
    unittest.main()
