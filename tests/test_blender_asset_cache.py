from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender.asset_cache import (
        _mark_action_asset,
        _mark_material_asset,
    )


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderAssetCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        for action in tuple(bpy.data.actions):
            bpy.data.actions.remove(action)
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)

    def test_marks_material_and_action_assets_in_separate_catalogs(self) -> None:
        adapter = SimpleNamespace(
            id="RATCHET_TOD",
            label="Ratchet & Clank Future: Tools of Destruction",
        )
        material_catalog = uuid.uuid4()
        animation_catalog = uuid.uuid4()
        material = bpy.data.materials.new("TOD_Material")
        action = bpy.data.actions.new("TOD_Action")

        _mark_material_asset(
            material,
            shader_tuid=7,
            level_name="metropolis",
            catalog_id=material_catalog,
            generate_preview=False,
            adapter=adapter,
        )
        _mark_action_asset(
            action,
            moby_tuid=2,
            moby_name="Moby_0002",
            level_name="metropolis",
            catalog_id=animation_catalog,
            adapter=adapter,
        )

        self.assertEqual(
            material.asset_data.catalog_id,
            str(material_catalog),
        )
        self.assertEqual(
            material["insomniac_asset_kind"],
            "SHADER",
        )
        self.assertIn("Material", material.asset_data.tags)
        self.assertEqual(
            action.asset_data.catalog_id,
            str(animation_catalog),
        )
        self.assertEqual(
            action["insomniac_asset_kind"],
            "ANIMATION",
        )
        self.assertEqual(
            action["insomniac_source_moby_tuid"],
            "0x0000000000000002",
        )
        self.assertIn("Animation", action.asset_data.tags)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
