from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.animations import _bind_trs
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_3.reader import (
    DISSOLVE_EFFECT_FAMILY,
    DISSOLVE_EFFECT_PROGRAM_TUID,
    DISSOLVE_EFFECT_TEXTURE_PATH,
    FROZEN_EFFECT_FAMILY,
    FROZEN_EFFECT_PROGRAM_TUID,
    FROZEN_EFFECT_TEXTURE_PATH,
    Resistance3Reader,
)

REAL_SOURCE = os.environ.get("R3_LEVEL_SOURCE")
FOLIAGE_SOURCE = os.environ.get("R3_FOLIAGE_SOURCE")
RAVAGER_SOURCE = os.environ.get("R3_RAVAGER_SOURCE")
STALKER_SOURCE = os.environ.get("R3_STALKER_SOURCE")
CAPELLI_SOURCE = os.environ.get("R3_CAPELLI_SOURCE")
FROZEN_SOURCE = os.environ.get("R3_FROZEN_SOURCE")


@unittest.skipUnless(REAL_SOURCE, "R3_LEVEL_SOURCE is not set")
class RealResistance3Tests(unittest.TestCase):
    def test_decodes_level_collision(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            scene = Resistance3Reader(source).read_level_collision()

        validate_collision_scene(scene, label="real R3 level collision")
        self.assertTrue(scene.meshes)
        self.assertGreater(len(scene.instances), len(scene.meshes))
        self.assertIn("TERRAIN", {mesh.source_kind for mesh in scene.meshes})
        self.assertIn("TIE", {mesh.source_kind for mesh in scene.meshes})

    def test_decodes_supported_resistance_3_chains(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = Resistance3Reader(source)

            first_moby = reader.asset_pointers(AssetKind.MOBY)[0]
            moby_name = reader.read_asset_name(
                AssetKind.MOBY,
                first_moby.tuid,
            )
            self.assertIsNotNone(moby_name)
            self.assertTrue(moby_name.endswith(".entity.irb"))
            moby = reader.read_moby(first_moby.tuid)
            self.assertTrue(moby.meshes)

            shader = reader.read_shader(moby.shader_tuids[0])
            self.assertEqual(len(shader.extra_texture_ids), 2)
            if shader.albedo_texture_id is not None:
                texture = reader.read_texture(
                    shader.albedo_texture_id,
                    lookup_path=shader.texture_lookup_paths[0],
                )
                self.assertEqual(texture.rgba.shape[-1], 4)

            decoded_mobys = tuple(
                reader.read_moby(pointer.tuid)
                for pointer in reader.asset_pointers(AssetKind.MOBY)
            )
            animated = next(
                (
                    candidate
                    for candidate in decoded_mobys
                    if candidate.animset_hash is not None
                ),
                None,
            )
            self.assertIsNotNone(animated)
            self.assertTrue(reader.read_animations(animated))

            first_tie = reader.asset_pointers(AssetKind.TIE)[0]
            tie_name = reader.read_asset_name(
                AssetKind.TIE,
                first_tie.tuid,
            )
            self.assertIsNotNone(tie_name)
            self.assertTrue(tie_name.endswith(".entity.irb"))
            self.assertTrue(reader.read_tie(first_tie.tuid).meshes)
            shrub_pointers = reader.asset_pointers(AssetKind.SHRUB)
            shrub_tuids = {pointer.tuid for pointer in shrub_pointers}
            for pointer in shrub_pointers:
                shrub_name = reader.read_asset_name(
                    AssetKind.SHRUB,
                    pointer.tuid,
                )
                self.assertIsNotNone(shrub_name)
                self.assertTrue(shrub_name.endswith(".entity.irb"))
                shrub = reader.read_shrub(pointer.tuid)
                self.assertEqual(len(shrub.meshes), 1)
                self.assertTrue(
                    np.isfinite(shrub.meshes[0].positions).all()
                )
                self.assertTrue(np.isfinite(shrub.meshes[0].uvs).all())
                self.assertEqual(len(shrub.meshes[0].indices) % 3, 0)
                self.assertEqual(
                    reader.read_shader(shrub.shader_tuids[0]).alpha_mode,
                    "MASK",
                )
            foliage_pointers = reader.asset_pointers(AssetKind.FOLIAGE)
            if foliage_pointers:
                foliage = reader.read_foliage(foliage_pointers[0].tuid)
                self.assertTrue(foliage.meshes)
                self.assertTrue(
                    np.isfinite(foliage.meshes[0].positions).all()
                )
                self.assertEqual(
                    reader.read_shader(
                        foliage.shader_tuids[0]
                    ).alpha_mode,
                    "MASK",
                )
            zones = reader.read_zones()
            self.assertTrue(zones)
            shrub_instances = [
                instance
                for zone in zones
                for instance in zone.shrub_instances
            ]
            self.assertTrue(
                {
                    instance.asset_tuid
                    for instance in shrub_instances
                }.issubset(shrub_tuids)
            )
            self.assertTrue(
                all(
                    np.isfinite(instance.matrix).all()
                    for instance in shrub_instances
                )
            )
            self.assertEqual(
                sum(zone.unsupported_shrub_instances for zone in zones),
                0,
            )
            self.assertEqual(
                sum(zone.unsupported_foliage_instances for zone in zones),
                0,
            )
            self.assertTrue(reader.read_gameplay_regions())


@unittest.skipUnless(FROZEN_SOURCE, "R3_FROZEN_SOURCE is not set")
class RealResistance3EffectTests(unittest.TestCase):
    def test_classifies_new_york_terraformer_frozen_family(self):
        with GameSource.open(Path(FROZEN_SOURCE)) as source:
            reader = Resistance3Reader(source)
            shaders = tuple(
                reader.read_shader(pointer.tuid)
                for pointer in reader.asset_pointers(AssetKind.SHADER)
            )

            frozen = tuple(
                shader
                for shader in shaders
                if shader.effect_family == FROZEN_EFFECT_FAMILY
            )
            self.assertEqual(len(frozen), 38)
            self.assertEqual(
                {shader.material_program_tuid for shader in frozen},
                {FROZEN_EFFECT_PROGRAM_TUID},
            )
            self.assertTrue(
                all(shader.is_texture_slot_bound(4) for shader in frozen)
            )
            self.assertEqual(
                {
                    path.replace("\\", "/").casefold()
                    for shader in frozen
                    for path in shader.extra_texture_lookup_paths[:1]
                },
                {FROZEN_EFFECT_TEXTURE_PATH},
            )

            representative = reader.read_shader(0x030C_48D2_E3FD_8A07)
            texture = reader.read_texture(
                representative.extra_texture_ids[0],
                lookup_path=representative.extra_texture_lookup_paths[0],
            )
            self.assertEqual(texture.rgba.shape, (512, 512, 4))
            self.assertTrue(np.all(texture.rgba[..., 3] == 255))
            self.assertFalse(
                np.array_equal(
                    texture.rgba[..., 0],
                    texture.rgba[..., 1],
                )
            )

            for shader_tuid in (
                0x4E3B_B32D_D6F6_0C1A,
                0xB964_E09D_D6F6_30F2,
                0x9DB2_D445_E3C6_56F3,
            ):
                self.assertIsNone(
                    reader.read_shader(shader_tuid).effect_family
                )

    def test_classifies_new_york_terraformer_dissolve_family(self):
        with GameSource.open(Path(FROZEN_SOURCE)) as source:
            reader = Resistance3Reader(source)
            shaders = tuple(
                reader.read_shader(pointer.tuid)
                for pointer in reader.asset_pointers(AssetKind.SHADER)
            )

            dissolve = tuple(
                shader
                for shader in shaders
                if shader.effect_family == DISSOLVE_EFFECT_FAMILY
            )
            self.assertEqual(len(dissolve), 44)
            self.assertEqual(
                {shader.material_program_tuid for shader in dissolve},
                {DISSOLVE_EFFECT_PROGRAM_TUID},
            )
            self.assertTrue(
                all(shader.is_texture_slot_bound(4) for shader in dissolve)
            )
            self.assertEqual(
                {
                    path.replace("\\", "/").casefold()
                    for shader in dissolve
                    for path in shader.extra_texture_lookup_paths[:1]
                },
                {DISSOLVE_EFFECT_TEXTURE_PATH},
            )

            representative = reader.read_shader(0x036B_71E4_E3C6_5EF6)
            texture = reader.read_texture(
                representative.extra_texture_ids[0],
                lookup_path=representative.extra_texture_lookup_paths[0],
            )
            self.assertEqual(texture.info.tuid, 0x1227_8F4B_908A_4115)
            self.assertEqual(texture.info.format_code, 0x05)
            self.assertEqual(texture.rgba.shape, (512, 512, 4))
            self.assertTrue(np.all(texture.rgba[..., 3] == 255))
            self.assertTrue(
                np.array_equal(
                    texture.rgba[..., 0],
                    texture.rgba[..., 1],
                )
            )
            self.assertTrue(
                np.array_equal(
                    texture.rgba[..., 1],
                    texture.rgba[..., 2],
                )
            )

            self.assertIsNone(
                reader.read_shader(
                    0xFC71_4DBD_E3FD_89DD
                ).effect_family
            )


@unittest.skipUnless(FOLIAGE_SOURCE, "R3_FOLIAGE_SOURCE is not set")
class RealResistance3FoliageTests(unittest.TestCase):
    def test_decodes_mine_traversal_train_foliage(self):
        with GameSource.open(Path(FOLIAGE_SOURCE)) as source:
            reader = Resistance3Reader(source)
            foliage_pointers = reader.asset_pointers(AssetKind.FOLIAGE)
            self.assertEqual(len(foliage_pointers), 11)
            foliage_tuids = {pointer.tuid for pointer in foliage_pointers}

            for pointer in foliage_pointers:
                foliage = reader.read_foliage(pointer.tuid)
                self.assertEqual(len(foliage.meshes), 1)
                mesh = foliage.meshes[0]
                self.assertGreater(len(mesh.positions), 0)
                self.assertTrue(np.isfinite(mesh.positions).all())
                self.assertTrue(np.isfinite(mesh.uvs).all())
                self.assertEqual(len(mesh.positions) % 4, 0)
                self.assertEqual(len(mesh.indices) % 6, 0)
                shader = reader.read_shader(foliage.shader_tuids[0])
                self.assertEqual(shader.alpha_mode, "MASK")
                self.assertIsNotNone(shader.albedo_texture_id)
                texture = reader.read_texture(
                    shader.albedo_texture_id,
                    lookup_path=shader.texture_lookup_paths[0],
                )
                self.assertEqual(texture.rgba.shape[-1], 4)

            zones = reader.read_zones()
            instances = [
                instance
                for zone in zones
                for instance in zone.foliage_instances
            ]
            self.assertEqual(len(instances), 1_050)
            self.assertEqual(
                {instance.asset_tuid for instance in instances},
                foliage_tuids,
            )
            self.assertEqual(
                sum(zone.unsupported_foliage_instances for zone in zones),
                0,
            )

    def test_decodes_mine_traversal_train_shrubs(self):
        with GameSource.open(Path(FOLIAGE_SOURCE)) as source:
            reader = Resistance3Reader(source)
            shrub_pointers = reader.asset_pointers(AssetKind.SHRUB)
            self.assertEqual(len(shrub_pointers), 6)
            shrub_tuids = {pointer.tuid for pointer in shrub_pointers}

            vertex_count = 0
            triangle_count = 0
            for pointer in shrub_pointers:
                shrub = reader.read_shrub(pointer.tuid)
                self.assertEqual(len(shrub.meshes), 1)
                mesh = shrub.meshes[0]
                self.assertTrue(np.isfinite(mesh.positions).all())
                self.assertTrue(np.isfinite(mesh.uvs).all())
                self.assertEqual(len(mesh.indices) % 3, 0)
                shader = reader.read_shader(shrub.shader_tuids[0])
                self.assertEqual(shader.alpha_mode, "MASK")
                vertex_count += len(mesh.positions)
                triangle_count += len(mesh.indices) // 3

            zones = reader.read_zones()
            instances = [
                instance
                for zone in zones
                for instance in zone.shrub_instances
            ]
            self.assertEqual(vertex_count, 394)
            self.assertEqual(triangle_count, 356)
            self.assertEqual(len(instances), 661)
            self.assertEqual(
                {instance.asset_tuid for instance in instances},
                shrub_tuids,
            )

    def test_decodes_mine_traversal_train_tie_material_groups(self):
        with GameSource.open(Path(FOLIAGE_SOURCE)) as source:
            reader = Resistance3Reader(source)
            grass = reader.read_tie(0x398B_4420_E2AC_F15B)
            tree = reader.read_tie(0xE18B_F091_E270_42FC)
            track = reader.read_tie(0xA62D_61B7_D19B_863D)
            zones = reader.read_zones()
            target_grass = next(
                instance
                for zone in zones
                for instance in zone.tie_instances
                if instance.name == "ceg_lowpoly_grass_field_2_85"
            )
            target_checkpoint = next(
                instance
                for zone in zones
                for instance in zone.tie_instances
                if instance.name == "TRACK_CHECKPOINT_7"
            )
            target_lightmap = reader.read_zone_lightmap(
                target_grass.lightmap_zone_tuid,
                target_grass.lightmap_index,
            )
            target_directional = reader.read_zone_directional_lightmap(
                target_checkpoint.lightmap_zone_tuid,
                target_checkpoint.lightmap_index,
            )
            for zone in zones:
                self.assertEqual(
                    set(reader.zone_lightmap_infos(zone.tuid)),
                    set(reader.zone_directional_lightmap_infos(zone.tuid)),
                )

        self.assertEqual(
            [mesh.shader_index for mesh in grass.meshes],
            [0] * 5 + [1] * 18 + [2] * 2 + [3] * 2,
        )
        self.assertEqual(
            [mesh.shader_index for mesh in tree.meshes],
            [0] * 5 + [1] * 6 + [2] * 4 + [3] * 5 + [4] * 9,
        )
        self.assertEqual(
            [mesh.shader_index for mesh in track.meshes],
            [0, 0, 1, 1, 1],
        )
        self.assertTrue(all(mesh.colors is not None for mesh in grass.meshes))
        self.assertTrue(
            all(mesh.secondary_uvs is not None for mesh in grass.meshes)
        )
        self.assertTrue(
            all(mesh.secondary_uvs is None for mesh in tree.meshes)
        )
        self.assertEqual(
            sum(mesh.secondary_uvs is not None for mesh in track.meshes),
            3,
        )
        self.assertEqual(target_grass.lightmap_index, 173)
        self.assertEqual(
            target_grass.lightmap_zone_tuid,
            0x58E8_829D_E249_D884,
        )
        self.assertEqual(target_lightmap.rgba.shape, (256, 256, 4))
        self.assertLess(int(target_lightmap.rgba[..., :3].min()), 70)
        self.assertEqual(target_checkpoint.lightmap_index, 120)
        self.assertEqual(target_directional.rgba.shape, (128, 128, 4))
        self.assertEqual(int(target_directional.rgba[..., 2].max()), 255)
        self.assertLess(int(target_directional.rgba[..., 0].max()), 110)
        self.assertEqual(
            sum(
                instance.lightmap_index is not None
                for zone in zones
                for instance in zone.tie_instances
            ),
            6_730,
        )
        for asset in (grass, tree, track):
            self.assertTrue(
                all(
                    mesh.shader_index < len(asset.shader_tuids)
                    for mesh in asset.meshes
                )
            )

    def test_decodes_warden_jeep_default_parts(self):
        jeep_tuid = 0x4B9D_A07C_D744_C4B0
        with GameSource.open(Path(FOLIAGE_SOURCE)) as source:
            reader = Resistance3Reader(source)
            jeep = reader.read_moby(jeep_tuid)
            placements = [
                instance
                for region in reader.read_gameplay_regions()
                for instance in region.moby_instances
                if instance.name == "JEEP_REINFORCEMENT_6"
            ]

        default_meshes = [
            mesh
            for mesh in jeep.meshes
            if (
                not mesh.is_damage_permutation
                and mesh.lod_level == 0
                and mesh.segment_index < 64
                and jeep.default_draw_mask & (1 << mesh.segment_index)
            )
        ]
        self.assertEqual(jeep.default_draw_mask, 0x03FF_FFFF)
        self.assertEqual(jeep.customization_segments, tuple(range(1, 26)))
        self.assertEqual(len(default_meshes), 35)
        self.assertEqual(
            sum(len(mesh.indices) // 3 for mesh in default_meshes),
            16_978,
        )
        self.assertTrue(placements)
        self.assertEqual(
            {instance.asset_tuid for instance in placements},
            {jeep_tuid},
        )

    def test_decodes_mine_traversal_train_terrain_positions(self):
        with GameSource.open(Path(FOLIAGE_SOURCE)) as source:
            zones = Resistance3Reader(source).read_zones()

        terrain_zone = next(
            zone
            for zone in zones
            if zone.tuid == 0xEF2A_7A01_E249_D922
        )
        terrain_positions = np.concatenate(
            [mesh.positions for mesh in terrain_zone.terrain_meshes]
        )
        terrain_secondary_uvs = np.concatenate(
            [mesh.secondary_uvs for mesh in terrain_zone.terrain_meshes]
        )
        terrain_colors = np.concatenate(
            [mesh.colors for mesh in terrain_zone.terrain_meshes]
        )
        self.assertEqual(len(terrain_zone.terrain_meshes), 972)
        self.assertLess(float(terrain_positions[:, 0].min()), 2_500.0)
        self.assertGreater(float(terrain_positions[:, 0].max()), 3_200.0)
        self.assertLess(float(terrain_positions[:, 2].min()), -600.0)
        self.assertGreater(float(terrain_positions[:, 2].max()), 1_200.0)
        self.assertEqual(
            {mesh.lightmap_index for mesh in terrain_zone.terrain_meshes},
            {109},
        )
        self.assertEqual(set(terrain_zone.lightmaps), {109})
        self.assertEqual(set(terrain_zone.directional_lightmaps), {109})
        self.assertEqual(
            terrain_zone.lightmaps[109].rgba.shape,
            (1_024, 1_024, 4),
        )
        self.assertEqual(
            terrain_zone.directional_lightmaps[109].rgba.shape,
            (256, 256, 4),
        )
        self.assertTrue(
            np.all(terrain_zone.directional_lightmaps[109].rgba == 255)
        )
        np.testing.assert_allclose(
            terrain_secondary_uvs.min(axis=0),
            (0.0, 0.0),
        )
        np.testing.assert_allclose(
            terrain_secondary_uvs.max(axis=0),
            (1.0, 1.0),
        )
        self.assertLess(float(terrain_colors[:, 0].min()), 0.24)
        self.assertEqual(float(terrain_colors[:, 0].max()), 1.0)
        self.assertGreater(float(terrain_colors[:, 0].mean()), 0.88)
        self.assertLess(float(terrain_colors[:, 0].mean()), 0.90)
        lightmap_mean = terrain_zone.lightmaps[109].rgba.mean(axis=(0, 1))
        self.assertGreater(float(lightmap_mean[0]), 252.0)
        self.assertLess(float(lightmap_mean[1]), 240.0)
        self.assertLess(float(lightmap_mean[2]), 213.0)


@unittest.skipUnless(CAPELLI_SOURCE, "R3_CAPELLI_SOURCE is not set")
class RealCapelliTests(unittest.TestCase):
    def test_embedded_hair_material_and_facial_overlays(self):
        with GameSource.open(Path(CAPELLI_SOURCE)) as source:
            reader = Resistance3Reader(source)
            moby = reader.read_moby(0x6E7A8CE904E0DBC0)
            shader = reader.read_shader(0xC89EC8F70464D875)
            albedo = reader.read_texture(
                shader.albedo_texture_id,
                lookup_path=shader.texture_lookup_paths[0],
            )
            clips = {
                clip.name: clip for clip in reader.read_animations(moby)
            }

        self.assertEqual(albedo.rgba.shape, (256, 256, 4))
        self.assertEqual(albedo.info.length, 65_536)
        self.assertGreater(int(np.ptp(albedo.rgba[..., 3])), 0)

        bind_translations = moby.skeleton.bind_local[:, :3, 3]
        dead_intro = clips["exp_dead_intro_upper"]
        head_idle = clips["head_idle"]
        eyes_left = clips["eyes_left"]
        translation_delta = np.linalg.norm(
            dead_intro.translations - bind_translations[None],
            axis=2,
        )
        bind_rotations = _bind_trs(moby.skeleton)[1]
        untracked = ~head_idle.rotation_animated_bones
        self.assertEqual(dead_intro.overlay_base_name, "head_idle")
        self.assertLess(float(translation_delta.max()), 0.01)
        np.testing.assert_allclose(
            head_idle.rotations_xyzw[:, untracked],
            np.broadcast_to(
                bind_rotations[None, untracked],
                head_idle.rotations_xyzw[:, untracked].shape,
            ),
            atol=1e-5,
        )
        eyes_left_dot = np.abs(
            np.sum(
                eyes_left.rotations_xyzw
                * bind_rotations[None],
                axis=2,
            )
        )
        eyes_left_angle = np.degrees(
            2.0 * np.arccos(np.clip(eyes_left_dot, -1.0, 1.0))
        )
        self.assertGreater(float(eyes_left_angle.max()), 40.0)
        np.testing.assert_allclose(
            eyes_left.rotations_xyzw[:, :3],
            np.broadcast_to(
                bind_rotations[None, :3],
                eyes_left.rotations_xyzw[:, :3].shape,
            ),
            atol=1e-5,
        )
        self.assertTrue(np.isfinite(dead_intro.rotations_xyzw).all())


@unittest.skipUnless(RAVAGER_SOURCE, "R3_RAVAGER_SOURCE is not set")
class RealRavagerOverlayTests(unittest.TestCase):
    def test_partial_aiming_overlay_is_baked_over_sighted_idle(self):
        with GameSource.open(Path(RAVAGER_SOURCE)) as source:
            reader = Resistance3Reader(source)
            moby = reader.read_moby(0x06B70D22047FEB17)
            clips = {
                clip.name: clip for clip in reader.read_animations(moby)
            }

        base = clips["ravager_r3_idle_sighted"]
        overlay = clips["ravager_partial_aiming_horizontal"]
        self.assertTrue(
            all(
                clip.overlay_base_name == "ravager_r3_idle_sighted"
                for clip in clips.values()
                if clip.additive
            )
        )
        self.assertEqual(overlay.source_flags, 0x0206)
        self.assertEqual(
            overlay.overlay_base_name, "ravager_r3_idle_sighted"
        )
        np.testing.assert_array_equal(
            overlay.blend_weights[:7],
            np.zeros(7, dtype=np.uint8),
        )
        np.testing.assert_array_equal(
            overlay.blend_weights[7:],
            np.full(71, 255, dtype=np.uint8),
        )
        np.testing.assert_allclose(
            overlay.translations[0, :7],
            base.translations[0, :7],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.rotations_xyzw[0, :7],
            base.rotations_xyzw[0, :7],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.scales[0, :7],
            base.scales[0, :7],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            overlay.rotations_xyzw[[0, 4, 8], 7],
            np.array(
                [
                    [0.04715902, -0.15293865, 0.00536405, 0.98709524],
                    [-0.04764121, 0.06305477, -0.01007049, 0.99682146],
                    [0.04455698, 0.15686208, 0.00620173, 0.98659539],
                ],
                dtype=np.float32,
            ),
            atol=2e-5,
        )
        self.assertTrue(np.isfinite(overlay.translations).all())
        self.assertTrue(np.isfinite(overlay.rotations_xyzw).all())
        self.assertTrue(np.isfinite(overlay.scales).all())
        self.assertGreater(float(overlay.scales.min()), 0.25)


@unittest.skipUnless(STALKER_SOURCE, "R3_STALKER_SOURCE is not set")
class RealStalkerAnimationTests(unittest.TestCase):
    def test_existing_full_body_locomotion_remains_complete(self):
        with GameSource.open(Path(STALKER_SOURCE)) as source:
            reader = Resistance3Reader(source)
            moby = reader.read_moby(0xDC32A1679D28847B)
            clips = reader.read_animations(moby)

        self.assertEqual(moby.skeleton.bone_count, 55)
        self.assertEqual(len(clips), 15)
        walk = next(
            clip for clip in clips if clip.name == "stalker_walk_forward"
        )
        self.assertFalse(walk.additive)
        self.assertIsNone(walk.overlay_base_name)
        self.assertTrue(np.isfinite(walk.translations).all())
        self.assertTrue(np.isfinite(walk.rotations_xyzw).all())
        self.assertTrue(np.isfinite(walk.scales).all())
        self.assertGreater(
            float(np.ptp(walk.translations, axis=0).max()), 4.0
        )
        self.assertGreater(
            float(np.ptp(walk.rotations_xyzw, axis=0).max()), 0.5
        )


if __name__ == "__main__":
    unittest.main()
