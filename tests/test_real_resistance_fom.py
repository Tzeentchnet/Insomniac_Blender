from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.registry import get_game_adapter
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_fom.reader import (
    ResistanceFomReader,
)

REAL_SOURCE = os.environ.get("RFOM_LEVEL_SOURCE")
REAL_ISO = os.environ.get("RFOM_ISO_PATH")


@unittest.skipUnless(REAL_ISO, "RFOM_ISO_PATH is not set")
class RealResistanceFomIsoTests(unittest.TestCase):
    def test_validates_disc_identity_and_level_inventory(self) -> None:
        adapter = get_game_adapter("RESISTANCE_FOM")
        values = adapter.validate_iso(Path(REAL_ISO))

        self.assertEqual(values["TITLE_ID"], "BCUS98107")
        self.assertEqual(values["VERSION"], "01.00")
        levels = adapter.list_levels(Path(REAL_ISO))
        self.assertEqual(len(levels), 41)
        self.assertIn("level20", levels)


@unittest.skipUnless(REAL_SOURCE, "RFOM_LEVEL_SOURCE is not set")
class RealResistanceFomTests(unittest.TestCase):
    def test_decodes_representative_level20_chains(self) -> None:
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = ResistanceFomReader(source)

            self.assertEqual(
                len(reader.asset_pointers(AssetKind.MOBY)),
                155,
            )
            self.assertEqual(
                len(reader.asset_pointers(AssetKind.TIE)),
                222,
            )
            self.assertEqual(
                len(reader.asset_pointers(AssetKind.SHADER)),
                633,
            )
            self.assertEqual(len(reader.texture_infos), 1384)

            moby = reader.read_moby(0x03C5)
            self.assertEqual(len(moby.meshes), 3)
            self.assertIsNotNone(moby.skeleton)
            self.assertEqual(moby.skeleton.bone_count, 76)
            clip = reader.read_animations(moby)[0]
            self.assertEqual(
                clip.name,
                "vtol_L20_crashed_building_roof",
            )
            self.assertEqual(clip.frame_count, 136)
            self.assertEqual(clip.frame_rate, 30.0)
            self.assertTrue(np.isfinite(clip.translations).all())
            self.assertTrue(np.isfinite(clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(clip.scales).all())

            shader = reader.read_shader(moby.shader_tuids[0])
            self.assertIsNotNone(shader.albedo_texture_id)
            texture = reader.read_texture(shader.albedo_texture_id)
            self.assertEqual(texture.rgba.shape, (512, 512, 4))

            tie = reader.read_tie(0x014E4A00)
            self.assertEqual(len(tie.meshes), 7)
            self.assertTrue(tie.shader_tuids)

            zone = reader.read_zones()[0]
            self.assertEqual(len(zone.terrain_meshes), 1675)
            self.assertEqual(len(zone.tie_instances), 4247)

            gameplay = reader.read_gameplay_regions()[0]
            self.assertEqual(gameplay.name, "default")
            self.assertEqual(len(gameplay.moby_instances), 533)


if __name__ == "__main__":
    unittest.main()
