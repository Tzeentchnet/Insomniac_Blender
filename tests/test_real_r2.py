from __future__ import annotations

import os
from pathlib import Path
import unittest

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
            self.assertTrue(reader.read_zones())
            self.assertTrue(reader.read_gameplay_regions())


if __name__ == "__main__":
    unittest.main()
