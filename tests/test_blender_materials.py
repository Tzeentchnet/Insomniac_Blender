from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender.importer import (
        _MaterialFactory,
        _static_asset_geometry_and_materials,
        import_materials,
    )
    from insomniac_asset_io.games.resistance_2.formats import (
        DecodedTexture,
        MeshGeometry,
        PlacedInstance,
        ShaderInfo,
        TieAsset,
        TextureInfo,
        Zone,
    )


    class _MaterialReader:
        def __init__(
            self,
            game_id: str,
            shader: ShaderInfo,
            textures: dict[int, DecodedTexture],
        ) -> None:
            self.game_id = game_id
            self.game_label = (
                "Resistance 3"
                if game_id == "RESISTANCE_3"
                else (
                    "Ratchet & Clank Future: Tools of Destruction"
                    if game_id == "RATCHET_TOD"
                    else (
                        "Resistance: Burning Skies"
                        if game_id == "RESISTANCE_BURNING_SKIES"
                        else "Resistance 2"
                    )
                )
            )
            self.asset_prefix = (
                "R3"
                if game_id == "RESISTANCE_3"
                else (
                    "TOD"
                    if game_id == "RATCHET_TOD"
                    else (
                        "RBS"
                        if game_id == "RESISTANCE_BURNING_SKIES"
                        else "R2"
                    )
                )
            )
            self.resource_prefix = ""
            self.material_surface_channels_by_default = True
            self.material_base_color_factor_supported = (
                game_id != "RATCHET_TOD"
            )
            self.material_uses_base_color_factor_by_default = (
                game_id == "RESISTANCE_BURNING_SKIES"
            )
            self.material_emission_uses_albedo = True
            self.material_emission_disabled_control_mask = (
                0x08 if game_id == "RESISTANCE_3" else 0
            )
            self.material_luminance_alpha_shader_tuids = (
                frozenset({0x5966_249F_D0D6_149B})
                if game_id == "RESISTANCE_3"
                else frozenset()
            )
            self.shader = shader
            self.textures = textures
            self.texture_reads: list[tuple[int, str | None]] = []
            self.clear_texture_cache_calls = 0

        def read_shader(self, _tuid: int) -> ShaderInfo:
            return self.shader

        def read_texture(
            self,
            texture_id: int,
            *,
            lookup_path: str | None = None,
        ) -> DecodedTexture:
            self.texture_reads.append((texture_id, lookup_path))
            return self.textures[texture_id]

        def clear_texture_cache(self) -> None:
            self.clear_texture_cache_calls += 1


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderMaterialTests(unittest.TestCase):
    def tearDown(self) -> None:
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in tuple(bpy.data.images):
            bpy.data.images.remove(image)

    def test_tod_standalone_material_names_are_level_scoped(self) -> None:
        shader = ShaderInfo(7, None, None, None, None)
        metropolis = _MaterialReader("RATCHET_TOD", shader, {})
        metropolis.resource_prefix = (
            "PS3_GAME/USRDIR/packed/levels/metropolis"
        )
        apogee = _MaterialReader("RATCHET_TOD", shader, {})
        apogee.resource_prefix = (
            "PS3_GAME/USRDIR/packed/levels/apogee_space_station"
        )

        metropolis_result = import_materials(metropolis, (7,))
        apogee_result = import_materials(apogee, (7,))

        metropolis_material = metropolis_result.materials[0]
        apogee_material = apogee_result.materials[0]
        self.assertNotEqual(
            metropolis_material.name,
            apogee_material.name,
        )
        self.assertEqual(
            metropolis_material["insomniac_asset_scope"],
            "metropolis",
        )
        self.assertEqual(
            apogee_material["insomniac_asset_scope"],
            "apogee_space_station",
        )
        self.assertTrue(metropolis_material.use_fake_user)
        self.assertEqual(metropolis_result.images, ())
        self.assertEqual(metropolis_result.warnings, [])

    def test_authored_alpha_mode_controls_diffuse_alpha(self) -> None:
        image = bpy.data.images.new(
            "Transparent_Albedo",
            width=1,
            height=1,
            alpha=True,
        )
        image.pixels.foreach_set((1.0, 1.0, 1.0, 0.0))
        image.update()
        factory = _MaterialFactory(None, True, [])
        factory.image = lambda texture_id, **kwargs: image

        opaque, opaque_principled = self._material("Opaque")
        opaque_render_method = opaque.surface_render_method
        factory._add_diffuse(
            opaque,
            opaque_principled,
            ShaderInfo(1, 0x1234, None, None, None, material_word=0),
        )

        self.assertFalse(opaque_principled.inputs["Alpha"].is_linked)
        self.assertEqual(opaque_principled.inputs["Alpha"].default_value, 1.0)
        self.assertEqual(opaque.surface_render_method, opaque_render_method)

        masked, masked_principled = self._material("Masked")
        factory._add_diffuse(
            masked,
            masked_principled,
            ShaderInfo(
                2,
                0x1234,
                None,
                None,
                None,
                material_word=0x0004_0000,
            ),
        )

        self.assertTrue(masked_principled.inputs["Alpha"].is_linked)
        alpha_link = masked_principled.inputs["Alpha"].links[0]
        self.assertEqual(alpha_link.from_node.name, "Alpha_Clip_0000000000000002")
        self.assertEqual(alpha_link.from_node.operation, "GREATER_THAN")
        self.assertEqual(masked.surface_render_method, "DITHERED")

    def test_color_textures_keep_rgb_independent_from_alpha(self) -> None:
        image = bpy.data.images.new(
            "Packed_Albedo",
            width=1,
            height=1,
            alpha=True,
        )
        self.assertEqual(image.alpha_mode, "STRAIGHT")

        _MaterialFactory._apply_colorspace(image, "color")

        self.assertEqual(image.alpha_mode, "CHANNEL_PACKED")

    def test_r3_terrain_material_multiplies_vertex_and_zone_lighting(
        self,
    ) -> None:
        shader_tuid = 0x1234
        albedo_id = 0x200
        zone_tuid = 0x1020_3040_5060_7080
        shader = ShaderInfo(
            shader_tuid,
            albedo_id,
            None,
            None,
            None,
            material_word=0,
        )
        albedo = self._decoded_texture(albedo_id)
        lightmap = self._decoded_texture(
            5,
            tuid=zone_tuid,
            format_code=0x88,
            value=192,
        )
        reader = _MaterialReader(
            "RESISTANCE_3",
            shader,
            {albedo_id: albedo},
        )
        factory = _MaterialFactory(reader, True, [])

        material = factory.terrain_material(
            shader_tuid,
            zone_tuid,
            5,
            lightmap,
            vertex_colors=True,
        )
        cached = factory.terrain_material(
            shader_tuid,
            zone_tuid,
            5,
            lightmap,
            vertex_colors=True,
        )

        self.assertIs(material, cached)
        self.assertTrue(material["insomniac_terrain_material"])
        self.assertTrue(material["insomniac_vertex_lighting"])
        self.assertEqual(material["insomniac_lightmap_index"], 5)
        nodes = material.node_tree.nodes
        principled = nodes["Principled BSDF"]
        vertex = nodes["R3_Terrain_Vertex_Color"]
        vertex_multiply = nodes["R3_Terrain_Vertex_Color_Multiply"]
        lightmap_texture = nodes["R3_Terrain_Lightmap_0005"]
        lightmap_multiply = nodes["R3_Terrain_Lightmap_Multiply"]
        self.assertEqual(vertex.layer_name, "InsomniacVertexColor")
        self.assertEqual(
            nodes["R3_Terrain_Lightmap_UV"].uv_map,
            "LightmapUV",
        )
        self.assertEqual(
            lightmap_texture.image.colorspace_settings.name,
            "sRGB",
        )
        self.assertEqual(
            principled.inputs["Base Color"].links[0].from_node,
            lightmap_multiply,
        )
        self.assertEqual(
            lightmap_multiply.inputs[1].links[0].from_node,
            vertex_multiply,
        )
        self.assertEqual(
            lightmap_multiply.inputs[2].links[0].from_node,
            lightmap_texture,
        )
        self.assertEqual(
            vertex_multiply.inputs[2].links[0].from_node,
            vertex,
        )
        self.assertEqual(
            lightmap_texture.image["insomniac_zone_tuid"],
            f"0x{zone_tuid:016X}",
        )

    def test_r3_tie_material_multiplies_vertex_lighting(self) -> None:
        shader_tuid = 0x1234
        albedo_id = 0x200
        shader = ShaderInfo(
            shader_tuid,
            albedo_id,
            None,
            None,
            None,
            material_word=0,
        )
        reader = _MaterialReader(
            "RESISTANCE_3",
            shader,
            {albedo_id: self._decoded_texture(albedo_id)},
        )
        zone_tuid = 0x1020_3040_5060_7080
        lightmap_index = 7
        lightmap = self._decoded_texture(
            lightmap_index,
            tuid=zone_tuid,
            format_code=0x88,
            value=192,
        )
        reader.tie_instance_lightmaps = True
        reader.zone_lightmap_infos = lambda _zone_tuid: {
            lightmap_index: lightmap.info
        }
        reader.read_zone_lightmap = (
            lambda _zone_tuid, _lightmap_index: lightmap
        )
        factory = _MaterialFactory(reader, True, [])
        factory.prepare_tie_lightmap_atlas(
            (
                Zone(
                    tuid=zone_tuid,
                    tie_instances=[
                        PlacedInstance(
                            asset_tuid=1,
                            instance_tuid=2,
                            name="Tie",
                            matrix=np.eye(4, dtype=np.float32),
                            lightmap_index=lightmap_index,
                            lightmap_zone_tuid=zone_tuid,
                        )
                    ],
                    terrain_meshes=[],
                    shader_tuids=(),
                ),
            )
        )

        material = factory.vertex_lit_material(
            shader_tuid,
            variant_kind="TIE",
            lightmapped=True,
        )
        cached = factory.vertex_lit_material(
            shader_tuid,
            variant_kind="TIE",
            lightmapped=True,
        )

        self.assertIs(material, cached)
        self.assertTrue(material["insomniac_vertex_lit_material"])
        self.assertEqual(material["insomniac_vertex_lit_kind"], "TIE")
        self.assertEqual(
            factory.tie_lightmap_atlas["insomniac_lightmap_count"],
            1,
        )
        nodes = material.node_tree.nodes
        vertex = nodes["R3_TIE_Vertex_Color"]
        multiply = nodes["R3_TIE_Vertex_Color_Multiply"]
        principled = nodes["Principled BSDF"]
        self.assertEqual(vertex.layer_name, "InsomniacVertexColor")
        lightmap_multiply = nodes["R3_TIE_Lightmap_Multiply"]
        self.assertEqual(
            principled.inputs["Base Color"].links[0].from_node,
            lightmap_multiply,
        )
        self.assertEqual(
            lightmap_multiply.inputs[1].links[0].from_node,
            multiply,
        )
        self.assertEqual(
            multiply.inputs[2].links[0].from_node,
            vertex,
        )
        atlas = nodes["R3_TIE_Lightmap_Atlas"]
        self.assertIs(atlas.image, factory.tie_lightmap_atlas)
        self.assertEqual(
            nodes["R3_TIE_Lightmap_Scale"].attribute_name,
            "insomniac_lightmap_uv_scale",
        )
        self.assertEqual(
            nodes["R3_TIE_Lightmap_Offset"].attribute_name,
            "insomniac_lightmap_uv_offset",
        )

        positions = np.asarray(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            dtype=np.float32,
        )
        uvs = np.zeros((3, 2), dtype=np.float32)
        indices = np.asarray((0, 1, 2), dtype=np.int32)
        colors = np.ones((3, 4), dtype=np.float32)
        geometries, routed_materials = (
            _static_asset_geometry_and_materials(
                factory,
                TieAsset(
                    tuid=1,
                    meshes=[
                        MeshGeometry(
                            shader_index=0,
                            positions=positions,
                            uvs=uvs,
                            indices=indices,
                            colors=colors,
                        ),
                        MeshGeometry(
                            shader_index=0,
                            positions=positions,
                            uvs=uvs,
                            indices=indices,
                            secondary_uvs=uvs,
                            colors=colors,
                        ),
                    ],
                    shader_tuids=(shader_tuid,),
                ),
                variant_kind="TIE",
            )
        )
        self.assertNotEqual(
            geometries[0].shader_index,
            geometries[1].shader_index,
        )
        self.assertEqual(len(routed_materials), 2)
        self.assertIsNone(
            routed_materials[geometries[0].shader_index]
            .node_tree.nodes.get("R3_TIE_Lightmap_Atlas")
        )
        self.assertIsNotNone(
            routed_materials[geometries[1].shader_index]
            .node_tree.nodes.get("R3_TIE_Lightmap_Atlas")
        )

    def test_all_supported_games_import_verified_data_maps(self) -> None:
        texture_ids = {
            "albedo": 0x710,
            "normal": 0x720,
            "surface": 0x730,
            "detail": 0x740,
        }
        textures = {
            role: self._decoded_texture(
                texture_id,
                format_code=0x08 if role == "normal" else 0x06,
                value=128 if role == "normal" else 255,
            )
            for role, texture_id in texture_ids.items()
        }
        by_id = {
            texture.info.id: texture for texture in textures.values()
        }

        for index, game_id in enumerate(
            (
                "RATCHET_TOD",
                "RESISTANCE_2",
                "RESISTANCE_3",
                "RESISTANCE_BURNING_SKIES",
            )
        ):
            with self.subTest(game_id=game_id):
                is_vita = game_id == "RESISTANCE_BURNING_SKIES"
                shader = ShaderInfo(
                    tuid=0x200 + index,
                    albedo_texture_id=texture_ids["albedo"],
                    normal_texture_id=texture_ids["normal"],
                    specular_texture_id=texture_ids["surface"],
                    detail_texture_id=texture_ids["detail"],
                    texture_lookup_paths=(
                        "fixture/albedo",
                        "fixture/normal",
                        "fixture/surface",
                        "fixture/detail",
                    ),
                    texture_map_indices=(0, 1, 2, 3),
                    material_word=0x1C00_0000,
                    normal_texture_encoding=(
                        "DIRECT" if is_vita else "RECONSTRUCTED"
                    ),
                    specular_texture_semantics=(
                        "GLOSS" if is_vita else "PACKED"
                    ),
                )
                reader = _MaterialReader(game_id, shader, by_id)
                material = _MaterialFactory(
                    reader,
                    True,
                    [],
                ).material(shader.tuid)
                data_nodes = (
                    material.node_tree.nodes[
                        f"Normal_{texture_ids['normal']:08X}"
                    ],
                    material.node_tree.nodes[
                        (
                            "Gloss"
                            if is_vita
                            else "Packed"
                        )
                        + f"_{texture_ids['surface']:08X}"
                    ],
                    material.node_tree.nodes[
                        f"Detail_{texture_ids['detail']:08X}"
                    ],
                )
                diffuse = material.node_tree.nodes[
                    f"Diffuse_{texture_ids['albedo']:08X}"
                ]
                detail = data_nodes[-1]

                self.assertEqual(
                    diffuse.image.colorspace_settings.name,
                    "sRGB",
                )
                for node in data_nodes:
                    self.assertEqual(
                        node.image.colorspace_settings.name,
                        "Non-Color",
                    )
                    self.assertEqual(
                        node.image.alpha_mode,
                        "CHANNEL_PACKED",
                    )
                self.assertFalse(detail.outputs["Color"].is_linked)
                self.assertTrue(material["insomniac_detail_data_only"])
                self.assertEqual(reader.clear_texture_cache_calls, 1)

    def test_burning_skies_connects_dedicated_specular_data(self) -> None:
        texture_id = 0x750
        shader = ShaderInfo(
            tuid=0x250,
            albedo_texture_id=None,
            normal_texture_id=None,
            specular_texture_id=texture_id,
            detail_texture_id=None,
            texture_lookup_paths=(None, None, "fixture/specular", None),
            specular_texture_semantics="SPECULAR",
        )
        reader = _MaterialReader(
            "RESISTANCE_BURNING_SKIES",
            shader,
            {
                texture_id: self._decoded_texture(
                    texture_id,
                    format_code=0x85000000,
                )
            },
        )
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        specular = material.node_tree.nodes[f"Specular_{texture_id:08X}"]

        self.assertEqual(
            specular.image.colorspace_settings.name,
            "Non-Color",
        )
        self.assertEqual(
            material["insomniac_specular_texture_id"],
            f"0x{texture_id:08X}",
        )
        self.assertTrue(
            self._has_link(
                material,
                specular,
                principled,
                "Specular IOR Level",
                from_socket="Color",
            )
        )
        self.assertIsNone(
            material.node_tree.nodes.get(f"Gloss_{texture_id:08X}")
        )

    def test_tod_texture_images_are_level_scoped(self) -> None:
        texture_id = 0x760
        shader = ShaderInfo(texture_id, texture_id, None, None, None)
        metropolis = _MaterialReader(
            "RATCHET_TOD",
            shader,
            {texture_id: self._decoded_texture(texture_id, value=64)},
        )
        metropolis.resource_prefix = (
            "PS3_GAME/USRDIR/packed/levels/metropolis"
        )
        apogee = _MaterialReader(
            "RATCHET_TOD",
            shader,
            {texture_id: self._decoded_texture(texture_id, value=192)},
        )
        apogee.resource_prefix = (
            "PS3_GAME/USRDIR/packed/levels/apogee_space_station"
        )

        metropolis_material = _MaterialFactory(
            metropolis,
            True,
            [],
        ).material(shader.tuid)
        apogee_material = _MaterialFactory(
            apogee,
            True,
            [],
        ).material(shader.tuid)
        metropolis_image = metropolis_material.node_tree.nodes[
            f"Diffuse_{texture_id:08X}"
        ].image
        apogee_image = apogee_material.node_tree.nodes[
            f"Diffuse_{texture_id:08X}"
        ].image

        self.assertIsNot(metropolis_image, apogee_image)
        self.assertIn("metropolis", metropolis_image.name)
        self.assertIn("apogee_space_station", apogee_image.name)
        self.assertEqual(
            metropolis_image["insomniac_asset_scope"],
            "metropolis",
        )
        self.assertEqual(
            apogee_image["insomniac_asset_scope"],
            "apogee_space_station",
        )

    def test_tod_raw_vectors_are_not_applied_as_base_color(self) -> None:
        shader = ShaderInfo(
            0x265,
            None,
            None,
            None,
            None,
            material_values=((0.25, 0.5, 0.75, 1.0),),
        )
        reader = _MaterialReader("RATCHET_TOD", shader, {})

        material = _MaterialFactory(
            reader,
            True,
            [],
            cycles_materials=True,
        ).material(shader.tuid)

        self.assertFalse(
            material["insomniac_base_color_factor_valid"]
        )
        self.assertEqual(
            tuple(material["insomniac_base_color_factor"]),
            (1.0, 1.0, 1.0, 1.0),
        )
        self.assertNotEqual(
            tuple(
                material.node_tree.nodes["Principled BSDF"]
                .inputs["Base Color"]
                .default_value
            ),
            (0.25, 0.5, 0.75, 1.0),
        )

    def test_lookup_paths_disambiguate_colliding_texture_ids(self) -> None:
        texture_id = 0x770
        shader = ShaderInfo(0x270, None, None, None, None)
        reader = _MaterialReader("RESISTANCE_2", shader, {})
        textures = {
            "textures/first": self._decoded_texture(
                texture_id,
                tuid=0x1111_1111_0000_0770,
                value=32,
            ),
            "textures/second": self._decoded_texture(
                texture_id,
                tuid=0x2222_2222_0000_0770,
                value=224,
            ),
        }

        def read_texture(
            requested_id: int,
            *,
            lookup_path: str | None = None,
        ) -> DecodedTexture:
            reader.texture_reads.append((requested_id, lookup_path))
            return textures[
                (lookup_path or "").replace("\\", "/").casefold()
            ]

        reader.read_texture = read_texture
        factory = _MaterialFactory(reader, True, [])
        first = factory.image(
            texture_id,
            lookup_path="textures/first",
        )
        second = factory.image(
            texture_id,
            lookup_path="textures/second",
        )
        first_again = factory.image(
            texture_id,
            lookup_path=r"Textures\First",
        )

        self.assertIs(first, first_again)
        self.assertIsNot(first, second)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(
            first["insomniac_texture_tuid"],
            "0x1111111100000770",
        )
        self.assertEqual(
            second["insomniac_texture_tuid"],
            "0x2222222200000770",
        )
        self.assertEqual(len(reader.texture_reads), 2)

    def test_reuses_map_analysis_after_releasing_reader_cache(self) -> None:
        shader, textures = self._surface_fixture(0x280)
        reader = _MaterialReader("RESISTANCE_2", shader, textures)
        factory = _MaterialFactory(reader, True, [])

        factory.material(0x280)
        reads_after_first = len(reader.texture_reads)
        factory.material(0x281)

        self.assertEqual(len(reader.texture_reads), reads_after_first)
        self.assertEqual(reader.clear_texture_cache_calls, 2)

    def test_r3_standard_material_builds_normal_and_gloss_nodes(self) -> None:
        shader, textures = self._surface_fixture(0x100)
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        normal_texture = material.node_tree.nodes["Normal_00000200"]
        normal_map = material.node_tree.nodes["Normal_Map_00000200"]
        separate = material.node_tree.nodes["Packed_Channels_00000300"]
        roughness = material.node_tree.nodes["Roughness_00000300"]

        self.assertFalse(material["insomniac_cycles_material"])
        self.assertTrue(material["insomniac_surface_material_channels"])
        self.assertEqual(material["insomniac_material_mode"], "R3_STANDARD")
        self.assertEqual(
            normal_texture.image.colorspace_settings.name,
            "Non-Color",
        )
        self.assertTrue(
            self._has_link(
                material,
                normal_map,
                principled,
                "Normal",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                separate,
                roughness,
                1,
                from_socket="Red",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                roughness,
                principled,
                "Roughness",
            )
        )
        self.assertFalse(principled.inputs["Metallic"].is_linked)
        self.assertEqual(principled.inputs["Metallic"].default_value, 0.0)

    def test_r2_standard_material_builds_normal_and_packed_gloss(self) -> None:
        shader, textures = self._surface_fixture(0x101)
        reader = _MaterialReader("RESISTANCE_2", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        normal_texture = material.node_tree.nodes["Normal_00000200"]
        normal_map = material.node_tree.nodes["Normal_Map_00000200"]
        packed = material.node_tree.nodes["Packed_00000300"]
        separate = material.node_tree.nodes["Packed_Channels_00000300"]
        roughness = material.node_tree.nodes["Roughness_00000300"]

        self.assertFalse(material["insomniac_cycles_material"])
        self.assertTrue(material["insomniac_surface_material_channels"])
        self.assertEqual(material["insomniac_material_mode"], "R2_STANDARD")
        self.assertEqual(
            normal_texture.image.colorspace_settings.name,
            "Non-Color",
        )
        self.assertEqual(separate.label, "R=gloss G=specular B=emission")
        self.assertTrue(
            self._has_link(
                material,
                normal_map,
                principled,
                "Normal",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                separate,
                roughness,
                1,
                from_socket="Red",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                roughness,
                principled,
                "Roughness",
            )
        )
        self.assertFalse(packed.outputs["Alpha"].is_linked)

    def test_r2_standard_material_connects_flagged_packed_specular(self) -> None:
        shader, textures = self._surface_fixture(
            0x102,
            material_word=0x0E00_0008,
        )
        reader = _MaterialReader("RESISTANCE_2", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        separate = material.node_tree.nodes["Packed_Channels_00000300"]

        self.assertTrue(
            self._has_link(
                material,
                separate,
                principled,
                "Specular IOR Level",
                from_socket="Green",
            )
        )

    def test_burning_skies_uses_direct_normal_and_dedicated_maps(self) -> None:
        texture_ids = {
            "albedo": 0x610,
            "normal": 0x620,
            "gloss": 0x630,
            "emissive": 0x640,
            "alpha": 0x650,
        }
        textures = {
            texture_id: DecodedTexture(
                TextureInfo(
                    id=texture_id,
                    tuid=texture_id,
                    format_code=0x83000000,
                    mip_count=1,
                    width=2,
                    height=2,
                    offset=0,
                    length=0,
                ),
                np.full((2, 2, 4), 255, dtype=np.uint8),
            )
            for texture_id in texture_ids.values()
        }
        shader = ShaderInfo(
            tuid=0x106,
            albedo_texture_id=texture_ids["albedo"],
            normal_texture_id=texture_ids["normal"],
            specular_texture_id=texture_ids["gloss"],
            detail_texture_id=None,
            texture_lookup_paths=(
                "albedo",
                "normal",
                "gloss",
                None,
            ),
            material_values=((0.5, 0.75, 1.0, 1.0),),
            normal_texture_encoding="DIRECT",
            specular_texture_semantics="GLOSS",
            emissive_texture_id=texture_ids["emissive"],
            emissive_texture_lookup_path="emissive",
            alpha_texture_id=texture_ids["alpha"],
            alpha_texture_lookup_path="alpha",
            emissive_color=(0.25, 0.5, 1.0),
            emissive_strength=2.5,
            emissive_enabled=True,
            use_emissive_texture=True,
            specular_power=100.0,
            alpha_mode_override="BLEND",
        )
        reader = _MaterialReader(
            "RESISTANCE_BURNING_SKIES",
            shader,
            textures,
        )
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        normal_texture = material.node_tree.nodes["Normal_00000620"]
        normal_map = material.node_tree.nodes["Normal_Map_00000620"]
        gloss_channel = material.node_tree.nodes["Gloss_Channel_00000630"]
        roughness = material.node_tree.nodes["Roughness_00000630"]
        emissive = material.node_tree.nodes["Emissive_00000640"]
        emissive_tint = material.node_tree.nodes[
            "Emissive_Tint_0000000000000106"
        ]
        alpha_channel = material.node_tree.nodes["Alpha_Channel_00000650"]

        self.assertEqual(material["insomniac_material_mode"], "RBS_STANDARD")
        self.assertIn("direct RGB normal", normal_texture.label)
        self.assertEqual(
            normal_texture.image.colorspace_settings.name,
            "Non-Color",
        )
        self.assertTrue(
            self._has_link(material, normal_map, principled, "Normal")
        )
        self.assertTrue(
            self._has_link(
                material,
                gloss_channel,
                roughness,
                1,
                from_socket="Red",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                roughness,
                principled,
                "Roughness",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                emissive,
                emissive_tint,
                0,
                from_socket="Color",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                emissive_tint,
                principled,
                "Emission Color",
            )
        )
        self.assertEqual(
            principled.inputs["Emission Strength"].default_value,
            2.5,
        )
        self.assertTrue(
            self._has_link(
                material,
                alpha_channel,
                principled,
                "Alpha",
                from_socket="Red",
            )
        )
        self.assertEqual(material.surface_render_method, "DITHERED")

    def test_burning_skies_uses_color_only_emissive_parameters(self) -> None:
        shader = ShaderInfo(
            tuid=0x107,
            albedo_texture_id=None,
            normal_texture_id=None,
            specular_texture_id=None,
            detail_texture_id=None,
            material_values=((0.25, 0.5, 0.75, 1.0),),
            specular_texture_semantics="GLOSS",
            emissive_color=(1.0, 0.5, 0.25),
            emissive_strength=2.0,
            emissive_enabled=True,
            use_emissive_texture=False,
        )
        reader = _MaterialReader(
            "RESISTANCE_BURNING_SKIES",
            shader,
            {},
        )
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]

        self.assertEqual(
            tuple(principled.inputs["Base Color"].default_value),
            (0.25, 0.5, 0.75, 1.0),
        )
        self.assertEqual(
            tuple(principled.inputs["Emission Color"].default_value),
            (1.0, 0.5, 0.25, 1.0),
        )
        self.assertEqual(
            principled.inputs["Emission Strength"].default_value,
            2.0,
        )
        self.assertTrue(material["insomniac_emissive_enabled"])
        self.assertFalse(material["insomniac_use_emissive_texture"])
        self.assertTrue(material["insomniac_emissive"])
        self.assertEqual(
            material["insomniac_emissive_color_source"],
            "AUTHORED_COLOR",
        )

    def test_burning_skies_reports_missing_enabled_emissive_map(self) -> None:
        shader = ShaderInfo(
            tuid=0x108,
            albedo_texture_id=None,
            normal_texture_id=None,
            specular_texture_id=None,
            detail_texture_id=None,
            emissive_color=(1.0, 0.5, 0.25),
            emissive_enabled=True,
            use_emissive_texture=True,
        )
        reader = _MaterialReader("RESISTANCE_BURNING_SKIES", shader, {})
        warnings: list[str] = []
        material = _MaterialFactory(reader, True, warnings).material(
            shader.tuid
        )
        principled = material.node_tree.nodes["Principled BSDF"]

        self.assertTrue(material["insomniac_use_emissive_texture"])
        self.assertFalse(principled.inputs["Emission Color"].is_linked)
        self.assertEqual(
            principled.inputs["Emission Strength"].default_value,
            0.0,
        )
        self.assertNotIn("insomniac_emissive_color_source", material)
        self.assertEqual(len(warnings), 1)
        self.assertIn("texture is enabled but no map was resolved", warnings[0])

    @unittest.skipUnless(
        os.environ.get("BURNING_SKIES_ARCHIVE"),
        "BURNING_SKIES_ARCHIVE is not set",
    )
    def test_burning_skies_real_color_only_emissive_materials(self) -> None:
        from insomniac_asset_io.core.source import GameSource
        from insomniac_asset_io.games.resistance_burning_skies.reader import (
            BurningSkiesReader,
        )

        expected = {
            0x6C1660E1224715A1: (
                (1.0, 0.5019608, 0.2509804, 1.0),
                (1.0, 0.5019608, 0.0, 1.0),
            ),
            0x98FE2F2A92B89201: (
                (1.0, 1.0, 0.0, 1.0),
                (1.0, 0.5019608, 0.2509804, 1.0),
            ),
        }
        with GameSource.open(Path(os.environ["BURNING_SKIES_ARCHIVE"])) as source:
            reader = BurningSkiesReader(source)
            warnings: list[str] = []
            factory = _MaterialFactory(reader, True, warnings)
            for shader_tuid, (base_color, emission_color) in expected.items():
                with self.subTest(shader_tuid=f"0x{shader_tuid:016X}"):
                    material = factory.material(shader_tuid)
                    principled = material.node_tree.nodes["Principled BSDF"]
                    for name, expected_color in (
                        ("Base Color", base_color),
                        ("Emission Color", emission_color),
                    ):
                        socket = principled.inputs[name]
                        self.assertFalse(socket.is_linked)
                        for actual, component in zip(
                            socket.default_value,
                            expected_color,
                            strict=True,
                        ):
                            self.assertAlmostEqual(actual, component)
                    for actual, component in zip(
                        material.diffuse_color,
                        base_color,
                        strict=True,
                    ):
                        self.assertAlmostEqual(actual, component)
                    self.assertEqual(
                        principled.inputs["Emission Strength"].default_value,
                        1.0,
                    )
                    self.assertTrue(material["insomniac_emissive"])
                    self.assertEqual(
                        material["insomniac_emissive_color_source"],
                        "AUTHORED_COLOR",
                    )
            self.assertEqual(warnings, [])

    def test_r3_goliath_wet_overlay_uses_luminance_coverage(self) -> None:
        shader, textures = self._alpha_fixture()
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        diffuse = material.node_tree.nodes["Diffuse_00000100"]
        coverage = material.node_tree.nodes[
            "Alpha_Coverage_5966249FD0D6149B"
        ]
        strength = material.node_tree.nodes[
            "Alpha_Strength_5966249FD0D6149B"
        ]

        self.assertEqual(
            material["insomniac_alpha_source"],
            "ALBEDO_LUMINANCE_X_MATERIAL_ALPHA",
        )
        self.assertEqual(
            material["insomniac_alpha_approximation"],
            "R3_MODE_1_BC1_LUMINANCE",
        )
        self.assertAlmostEqual(
            material["insomniac_alpha_factor"],
            1.2744,
            places=4,
        )
        self.assertFalse(material["insomniac_base_color_factor_valid"])
        self.assertEqual(strength.operation, "MULTIPLY")
        self.assertTrue(strength.use_clamp)
        self.assertAlmostEqual(
            strength.inputs[1].default_value,
            1.2744,
            places=4,
        )
        self.assertTrue(
            self._has_link(
                material,
                diffuse,
                coverage,
                "Color",
                from_socket="Color",
            )
        )
        self.assertTrue(self._has_link(material, coverage, strength, 0))
        self.assertTrue(
            self._has_link(material, strength, principled, "Alpha")
        )

        r2_reader = _MaterialReader("RESISTANCE_2", shader, textures)
        r2_material = _MaterialFactory(r2_reader, True, []).material(
            shader.tuid
        )
        r2_principled = r2_material.node_tree.nodes["Principled BSDF"]
        r2_diffuse = r2_material.node_tree.nodes["Diffuse_00000100"]

        self.assertIsNone(
            r2_material.node_tree.nodes.get(
                "Alpha_Coverage_5966249FD0D6149B"
            )
        )
        self.assertTrue(
            self._has_link(
                r2_material,
                r2_diffuse,
                r2_principled,
                "Alpha",
                from_socket="Alpha",
            )
        )

    def test_unbound_r3_texture_references_remain_metadata_only(self) -> None:
        shader, textures = self._surface_fixture(
            0x102,
            texture_map_indices=(0, -1, -1, -1, -1, -1),
        )
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)

        self.assertEqual(material["insomniac_normal_map_index"], -1)
        self.assertFalse(material["insomniac_normal_texture_bound"])
        self.assertEqual(material["insomniac_packed_map_index"], -1)
        self.assertFalse(material["insomniac_packed_texture_bound"])
        self.assertIsNone(material.node_tree.nodes.get("Normal_00000200"))
        self.assertIsNone(material.node_tree.nodes.get("Packed_00000300"))

    def test_unsupported_normal_format_is_reported_and_skipped(self) -> None:
        shader, textures = self._surface_fixture(
            0x103,
            normal_format=0x06,
        )
        warnings: list[str] = []
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, warnings).material(
            shader.tuid
        )

        self.assertFalse(material["insomniac_normal_map_supported"])
        self.assertIsNone(material.node_tree.nodes.get("Normal_00000200"))
        self.assertTrue(
            any("unsupported texture format 0x06" in item for item in warnings)
        )

    def test_r3_emission_uses_tinted_albedo_and_source_control(self) -> None:
        shader, textures = self._emission_fixture(
            0x104,
            material_word=0x0400_0020,
            base_color_factor=(1.0, 0.5, 0.25, 1.0),
        )
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        diffuse = material.node_tree.nodes["Diffuse_00000100"]
        separate = material.node_tree.nodes["Packed_Channels_00000300"]
        tint = material.node_tree.nodes[
            "Emission_Tint_0000000000000104"
        ]
        emission = material.node_tree.nodes[
            "Emission_Color_0000000000000104"
        ]

        self.assertTrue(material["insomniac_emissive_mask"])
        self.assertTrue(material["insomniac_emissive_control_enabled"])
        self.assertTrue(material["insomniac_emissive"])
        self.assertEqual(
            material["insomniac_emissive_color_source"],
            "ALBEDO_X_BASE_COLOR_FACTOR_X_PACKED_BLUE",
        )
        self.assertEqual(tuple(tint.inputs[1].default_value), (1.0, 0.5, 0.25))
        self.assertTrue(
            self._has_link(
                material,
                diffuse,
                principled,
                "Base Color",
                from_socket="Color",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                diffuse,
                tint,
                0,
                from_socket="Color",
            )
        )
        self.assertTrue(self._has_link(material, tint, emission, 0))
        self.assertTrue(
            self._has_link(
                material,
                separate,
                emission,
                1,
                from_socket="Blue",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                emission,
                principled,
                "Emission Color",
            )
        )
        self.assertEqual(
            principled.inputs["Emission Strength"].default_value,
            1.0,
        )

    def test_r3_emission_control_disables_stale_blue_mask(self) -> None:
        shader, textures = self._emission_fixture(
            0x105,
            material_word=0x0400_0028,
        )
        reader = _MaterialReader("RESISTANCE_3", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]

        self.assertTrue(material["insomniac_emissive_mask"])
        self.assertFalse(material["insomniac_emissive_control_enabled"])
        self.assertFalse(material["insomniac_emissive"])
        self.assertIsNotNone(
            material.node_tree.nodes.get("Packed_00000300")
        )
        self.assertIsNone(
            material.node_tree.nodes.get(
                "Emission_Color_0000000000000105"
            )
        )
        self.assertFalse(principled.inputs["Emission Color"].is_linked)

    def test_r2_emission_uses_albedo_color(self) -> None:
        shader, textures = self._emission_fixture(
            0x106,
            material_word=0x0400_0008,
        )
        reader = _MaterialReader("RESISTANCE_2", shader, textures)
        material = _MaterialFactory(reader, True, []).material(shader.tuid)
        principled = material.node_tree.nodes["Principled BSDF"]
        diffuse = material.node_tree.nodes["Diffuse_00000100"]
        separate = material.node_tree.nodes["Packed_Channels_00000300"]
        emission = material.node_tree.nodes[
            "Emission_Color_0000000000000106"
        ]

        self.assertTrue(material["insomniac_emissive"])
        self.assertEqual(
            material["insomniac_emissive_color_source"],
            "ALBEDO_X_BASE_COLOR_FACTOR_X_PACKED_BLUE",
        )
        self.assertTrue(
            self._has_link(
                material,
                diffuse,
                emission,
                0,
                from_socket="Color",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                separate,
                emission,
                1,
                from_socket="Blue",
            )
        )
        self.assertTrue(
            self._has_link(
                material,
                emission,
                principled,
                "Emission Color",
            )
        )

    @staticmethod
    def _surface_fixture(
        tuid: int,
        *,
        texture_map_indices: tuple[int, ...] = (0, 1, 2, -1, -1, -1),
        normal_format: int = 0x08,
        material_word: int = 0x0C00_0008,
    ) -> tuple[ShaderInfo, dict[int, DecodedTexture]]:
        normal_id = 0x200
        packed_id = 0x300
        normal_rgba = np.full((2, 2, 4), 128, dtype=np.uint8)
        packed_rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        packed_rgba[..., 0] = np.array(((0, 64), (128, 255)))
        packed_rgba[..., 3] = 255
        textures = {
            normal_id: DecodedTexture(
                TextureInfo(
                    id=normal_id,
                    tuid=normal_id,
                    format_code=normal_format,
                    mip_count=1,
                    width=2,
                    height=2,
                    offset=0,
                    length=0,
                ),
                normal_rgba,
            ),
            packed_id: DecodedTexture(
                TextureInfo(
                    id=packed_id,
                    tuid=packed_id,
                    format_code=0x06,
                    mip_count=1,
                    width=2,
                    height=2,
                    offset=0,
                    length=0,
                ),
                packed_rgba,
            ),
        }
        shader = ShaderInfo(
            tuid=tuid,
            albedo_texture_id=None,
            normal_texture_id=normal_id,
            specular_texture_id=packed_id,
            detail_texture_id=None,
            texture_lookup_paths=(None, None, None, None),
            extra_texture_ids=(0x400, 0x500),
            extra_texture_lookup_paths=(None, None),
            texture_map_indices=texture_map_indices,
            material_pre_control_values=(0x1111, 0x2222),
            material_word=material_word,
        )
        return shader, textures

    @staticmethod
    def _decoded_texture(
        texture_id: int,
        *,
        tuid: int | None = None,
        format_code: int = 0x06,
        value: int = 255,
    ) -> DecodedTexture:
        return DecodedTexture(
            TextureInfo(
                id=texture_id,
                tuid=texture_id if tuid is None else tuid,
                format_code=format_code,
                mip_count=1,
                width=2,
                height=2,
                offset=0,
                length=0,
            ),
            np.full((2, 2, 4), value, dtype=np.uint8),
        )

    @staticmethod
    def _emission_fixture(
        tuid: int,
        *,
        material_word: int,
        base_color_factor: tuple[float, float, float, float] = (
            1.0,
            1.0,
            1.0,
            1.0,
        ),
    ) -> tuple[ShaderInfo, dict[int, DecodedTexture]]:
        albedo_id = 0x100
        packed_id = 0x300
        albedo_rgba = np.full((2, 2, 4), 255, dtype=np.uint8)
        albedo_rgba[..., :3] = (192, 128, 32)
        packed_rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        packed_rgba[..., 0] = 128
        packed_rgba[..., 2] = 255
        packed_rgba[..., 3] = 255
        textures = {
            albedo_id: DecodedTexture(
                TextureInfo(
                    id=albedo_id,
                    tuid=albedo_id,
                    format_code=0x06,
                    mip_count=1,
                    width=2,
                    height=2,
                    offset=0,
                    length=0,
                ),
                albedo_rgba,
            ),
            packed_id: DecodedTexture(
                TextureInfo(
                    id=packed_id,
                    tuid=packed_id,
                    format_code=0x06,
                    mip_count=1,
                    width=2,
                    height=2,
                    offset=0,
                    length=0,
                ),
                packed_rgba,
            ),
        }
        shader = ShaderInfo(
            tuid=tuid,
            albedo_texture_id=albedo_id,
            normal_texture_id=None,
            specular_texture_id=packed_id,
            detail_texture_id=None,
            texture_lookup_paths=(None, None, None, None),
            texture_map_indices=(0, -1, 1, -1, -1, -1),
            material_word=material_word,
            material_values=(base_color_factor,),
        )
        return shader, textures

    @staticmethod
    def _alpha_fixture() -> tuple[
        ShaderInfo,
        dict[int, DecodedTexture],
    ]:
        albedo_id = 0x100
        albedo_rgba = np.full((2, 2, 4), 255, dtype=np.uint8)
        albedo_rgba[..., :3] = np.array(
            (
                ((0, 0, 0), (64, 64, 64)),
                ((128, 128, 128), (192, 192, 192)),
            ),
            dtype=np.uint8,
        )
        texture = DecodedTexture(
            TextureInfo(
                id=albedo_id,
                tuid=albedo_id,
                format_code=0x06,
                mip_count=1,
                width=2,
                height=2,
                offset=0,
                length=0,
            ),
            albedo_rgba,
        )
        shader = ShaderInfo(
            tuid=0x5966_249F_D0D6_149B,
            albedo_texture_id=albedo_id,
            normal_texture_id=None,
            specular_texture_id=None,
            detail_texture_id=None,
            texture_lookup_paths=(None, None, None, None),
            texture_map_indices=(0, -1, -1, -1, -1, -1),
            material_word=0x1C01_0008,
            material_values=((1.0, 1.0, 1.0, 1.2744),),
        )
        return shader, {albedo_id: texture}

    @staticmethod
    def _has_link(
        material,
        from_node,
        to_node,
        to_socket: str | int,
        *,
        from_socket: str | None = None,
    ) -> bool:
        target_socket = to_node.inputs[to_socket]
        return any(
            link.from_node == from_node
            and link.to_node == to_node
            and link.to_socket == target_socket
            and (
                from_socket is None
                or link.from_socket == from_node.outputs[from_socket]
            )
            for link in material.node_tree.links
        )

    @staticmethod
    def _material(name: str):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        return material, principled


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
