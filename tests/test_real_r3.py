from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader

REAL_SOURCE = os.environ.get("R3_LEVEL_SOURCE")
RAVAGER_SOURCE = os.environ.get("R3_RAVAGER_SOURCE")
STALKER_SOURCE = os.environ.get("R3_STALKER_SOURCE")
CAPELLI_SOURCE = os.environ.get("R3_CAPELLI_SOURCE")


@unittest.skipUnless(REAL_SOURCE, "R3_LEVEL_SOURCE is not set")
class RealResistance3Tests(unittest.TestCase):
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
            self.assertTrue(reader.read_zones())
            self.assertTrue(reader.read_gameplay_regions())


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
        translation_delta = np.linalg.norm(
            dead_intro.translations - bind_translations[None],
            axis=2,
        )
        self.assertEqual(dead_intro.overlay_base_name, "head_idle")
        self.assertLess(float(translation_delta.max()), 0.01)
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
