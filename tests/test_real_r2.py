from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader

REAL_SOURCE = os.environ.get("R2_LEVEL_SOURCE")
DETAIL_SOURCE = os.environ.get("R2_DETAIL_SOURCE")


@unittest.skipUnless(REAL_SOURCE, "R2_LEVEL_SOURCE is not set")
class RealResistance2Tests(unittest.TestCase):
    def test_decodes_level_collision(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            scene = Resistance2Reader(source).read_level_collision()

        validate_collision_scene(scene, label="real R2 level collision")
        self.assertTrue(scene.meshes)
        self.assertGreater(len(scene.instances), len(scene.meshes))
        self.assertIn("TERRAIN", {mesh.source_kind for mesh in scene.meshes})
        self.assertIn("TIE", {mesh.source_kind for mesh in scene.meshes})
        self.assertIn(
            "Terrain",
            {
                instance.source_group.split(" - ")[0]
                for instance in scene.instances
            },
        )
        self.assertIn(
            "Static Props",
            {
                instance.source_group.split(" - ")[0]
                for instance in scene.instances
            },
        )

    def test_decodes_one_of_each_supported_chain(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = Resistance2Reader(source)
            moby_tuid = reader.asset_pointers(AssetKind.MOBY)[0].tuid
            moby_name = reader.read_asset_name(AssetKind.MOBY, moby_tuid)
            self.assertIsNotNone(moby_name)
            self.assertTrue(moby_name.endswith(".entity.irb"))
            moby = reader.read_moby(moby_tuid)
            self.assertTrue(moby.meshes)
            self.assertIsNotNone(moby.skeleton)
            clips = reader.read_animations(moby)
            if moby.animset_hash is not None:
                self.assertTrue(clips)

            shader = reader.read_shader(moby.shader_tuids[0])
            if shader.albedo_texture_id is not None:
                texture = reader.read_texture(shader.albedo_texture_id)
                self.assertEqual(texture.rgba.shape[-1], 4)

            global_textures = reader.global_texture_locations
            if global_textures:
                global_texture = reader.read_texture(next(iter(global_textures)))
                self.assertEqual(global_texture.rgba.shape[-1], 4)

            tie_pointers = reader.asset_pointers(AssetKind.TIE)
            if tie_pointers:
                tie_name = reader.read_asset_name(
                    AssetKind.TIE,
                    tie_pointers[0].tuid,
                )
                self.assertIsNotNone(tie_name)
                self.assertTrue(tie_name.endswith(".entity.irb"))
                self.assertTrue(reader.read_tie(tie_pointers[0].tuid).meshes)
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
                    len(shrub.meshes[0].shrub_position_w),
                    len(shrub.meshes[0].positions),
                )
                self.assertTrue(
                    np.isfinite(
                        shrub.meshes[0].shrub_height_weights
                    ).all()
                )
                self.assertEqual(
                    len(shrub.meshes[0].shrub_runtime_remap),
                    len(shrub.meshes[0].positions),
                )
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


@unittest.skipUnless(DETAIL_SOURCE, "R2_DETAIL_SOURCE is not set")
class RealResistance2DetailTests(unittest.TestCase):
    def test_decodes_shotgun_signed_detail_material(self) -> None:
        with GameSource.open(Path(DETAIL_SOURCE)) as source:
            reader = Resistance2Reader(source)
            shader = reader.read_shader(0x40A1_0458_0F82_28C8)

            self.assertEqual(shader.lookup_path, "shotgun_r2/shotgun_r2")
            self.assertTrue(shader.use_detail_map)
            self.assertTrue(shader.detail_uses_signed_remap)
            self.assertEqual(shader.detail_sampler_address, 0x0003_1101)
            self.assertAlmostEqual(shader.detail_uv_scale, 7.944, places=3)
            self.assertEqual(shader.detail_texture_id, 0x557B_8826)
            self.assertEqual(
                shader.texture_lookup_paths[3],
                "artdepartment/detailtextures/brushed1_d",
            )

            packed = reader.read_texture(
                shader.specular_texture_id,
                lookup_path=shader.texture_lookup_paths[2],
            )
            detail = reader.read_texture(
                shader.detail_texture_id,
                lookup_path=shader.texture_lookup_paths[3],
            )

        self.assertEqual((packed.info.width, packed.info.height), (512, 512))
        self.assertEqual(
            (int(packed.rgba[..., 3].min()), int(packed.rgba[..., 3].max())),
            (0, 255),
        )
        self.assertEqual((detail.info.width, detail.info.height), (256, 256))
        self.assertEqual(
            tuple(int(value) for value in detail.rgba.min(axis=(0, 1))),
            (126, 107, 0, 136),
        )
        self.assertEqual(
            tuple(int(value) for value in detail.rgba.max(axis=(0, 1))),
            (128, 149, 248, 136),
        )


if __name__ == "__main__":
    unittest.main()
