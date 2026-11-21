from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader

REAL_SOURCE = os.environ.get("R2_LEVEL_SOURCE")


@unittest.skipUnless(REAL_SOURCE, "R2_LEVEL_SOURCE is not set")
class RealResistance2Tests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
