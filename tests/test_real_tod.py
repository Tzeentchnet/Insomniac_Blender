from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.iso import list_game_levels
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.ratchet_tod.reader import RatchetTodReader
from insomniac_asset_io.games.registry import get_game_adapter
from insomniac_asset_io.games.resistance_2.formats import AssetKind

REAL_SOURCE = os.environ.get("TOD_LEVEL_SOURCE")
REAL_ISO = os.environ.get("TOD_ISO_PATH")


@unittest.skipUnless(REAL_ISO, "TOD_ISO_PATH is not set")
class RealRatchetTodIsoTests(unittest.TestCase):
    def test_validates_disc_identity_and_level_inventory(self) -> None:
        adapter = get_game_adapter("RATCHET_TOD")
        values = adapter.validate_iso(Path(REAL_ISO))

        self.assertEqual(values["TITLE_ID"], "BCUS98127")
        self.assertEqual(values["VERSION"], "02.00")
        levels = list_game_levels(Path(REAL_ISO), game_label=adapter.label)
        self.assertEqual(len(levels), 20)
        self.assertIn("metropolis", levels)


@unittest.skipUnless(REAL_SOURCE, "TOD_LEVEL_SOURCE is not set")
class RealRatchetTodTests(unittest.TestCase):
    def test_decodes_representative_metropolis_chains(self) -> None:
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = RatchetTodReader(source)

            self.assertGreaterEqual(
                len(reader.asset_pointers(AssetKind.MOBY)),
                0x17,
            )
            complex_moby = reader.read_moby(0x16)
            self.assertTrue(complex_moby.meshes)
            self.assertIsNotNone(complex_moby.skeleton)
            complex_clip = reader.read_animations(complex_moby)[0]
            self.assertEqual(complex_clip.name, "crumbling")
            self.assertEqual(complex_clip.frame_count, 151)
            self.assertEqual(complex_clip.bone_count, 38)
            self.assertEqual(complex_clip.frame_rate, 30.0)
            self.assertTrue(np.isfinite(complex_clip.translations).all())
            self.assertTrue(np.isfinite(complex_clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(complex_clip.scales).all())

            pair_moby = reader.read_moby(0x02)
            pair_clip = reader.read_animations(pair_moby)[0]
            self.assertEqual(pair_clip.name, "animate_spin")
            self.assertEqual(pair_clip.frame_count, 4000)
            self.assertEqual(pair_clip.bone_count, 4)
            self.assertEqual(pair_clip.frame_rate, 15.0)
            self.assertTrue(np.isfinite(pair_clip.translations).all())
            self.assertTrue(np.isfinite(pair_clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(pair_clip.scales).all())

            shader = reader.read_shader(complex_moby.shader_tuids[0])
            self.assertIsNotNone(shader.albedo_texture_id)
            texture = reader.read_texture(shader.albedo_texture_id)
            self.assertEqual(texture.rgba.shape, (512, 256, 4))

            tie = reader.read_tie(0x00FC7A00)
            self.assertTrue(tie.meshes)
            self.assertTrue(tie.shader_tuids)

            foliage_pointers = reader.asset_pointers(AssetKind.FOLIAGE)
            self.assertEqual(len(foliage_pointers), 2)
            for pointer in foliage_pointers:
                foliage = reader.read_foliage(pointer.tuid)
                self.assertEqual(len(foliage.meshes), 2)
                self.assertTrue(
                    all(
                        np.isfinite(mesh.positions).all()
                        for mesh in foliage.meshes
                    )
                )
                foliage_shader = reader.read_shader(
                    foliage.shader_tuids[0]
                )
                self.assertEqual(foliage_shader.alpha_mode, "MASK")
                self.assertIsNotNone(foliage_shader.albedo_texture_id)

            zone = reader.read_zones()[0]
            self.assertTrue(zone.terrain_meshes)
            self.assertTrue(zone.tie_instances)
            self.assertEqual(len(zone.foliage_instances), 757)
            self.assertEqual(zone.unsupported_foliage_instances, 0)

            gameplay = reader.read_gameplay_regions()[0]
            self.assertEqual(gameplay.name, "art")
            self.assertTrue(gameplay.moby_instances)


if __name__ == "__main__":
    unittest.main()
