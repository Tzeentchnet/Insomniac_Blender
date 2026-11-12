from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender import register, unregister


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertFalse(hasattr(bpy.types.Scene, "insomniac_asset_io"))
        register()

    def tearDown(self) -> None:
        if hasattr(bpy.types.Scene, "insomniac_asset_io"):
            unregister()

    def test_selected_imported_action_updates_playback_range(self) -> None:
        scene = bpy.context.scene
        armature_data = bpy.data.armatures.new("Range_Rig")
        armature = bpy.data.objects.new("Range_Rig", armature_data)
        scene.collection.objects.link(armature)
        action = bpy.data.actions.new("Range_Action")
        action["insomniac_frame_rate"] = 60.0
        action.frame_range = (5.25, 42.2)
        action.slots.new("OBJECT", armature.name)
        armature.animation_data_create()

        try:
            bpy.context.view_layer.objects.active = armature
            armature.select_set(True)
            scene.frame_start = 1
            scene.frame_end = 250
            scene.use_preview_range = True
            scene.frame_preview_start = 1
            scene.frame_preview_end = 250
            scene.frame_set(100)

            armature.animation_data.action = action
            bpy.context.view_layer.update()

            self.assertEqual(scene.frame_start, 5)
            self.assertEqual(scene.frame_end, 43)
            self.assertEqual(scene.frame_preview_start, 5)
            self.assertEqual(scene.frame_preview_end, 43)
            self.assertEqual(scene.frame_current, 43)
        finally:
            armature.animation_data.action = None
            scene.use_preview_range = False
            scene.frame_start = 1
            scene.frame_end = 250
            scene.frame_set(1)
            bpy.data.objects.remove(armature, do_unlink=True)
            bpy.data.armatures.remove(armature_data)
            bpy.data.actions.remove(action)

    def test_user_choices_survive_new_file(self) -> None:
        original_scene = bpy.context.scene
        original_scene_pointer = original_scene.as_pointer()
        settings = original_scene.insomniac_asset_io
        settings.source_path = r"E:\Tests\resistance_2.iso"
        settings.asset_filter = "vtol approach"
        settings.asset_kind_filter = "MOBY"
        settings.asset_level_filter = "badlands"
        settings.asset_tuid = "MOBY:E083951C9DF29723"
        settings.level_id = "badlands"
        settings.source_summary = "Transient source summary"
        settings.source_inspection_busy = True
        settings.last_message = "Transient result"

        self.assertEqual(
            bpy.ops.wm.read_homefile(use_empty=True),
            {"FINISHED"},
        )

        self.assertNotEqual(
            original_scene_pointer,
            bpy.context.scene.as_pointer(),
        )
        restored = bpy.context.scene.insomniac_asset_io
        self.assertEqual(
            restored.source_path,
            r"E:\Tests\resistance_2.iso",
        )
        self.assertEqual(restored.asset_filter, "vtol approach")
        self.assertEqual(restored.asset_kind_filter, "MOBY")
        self.assertEqual(restored.asset_level_filter, "badlands")
        self.assertEqual(
            restored.asset_tuid,
            "MOBY:E083951C9DF29723",
        )
        self.assertEqual(restored.level_id, "badlands")
        self.assertEqual(restored.source_summary, "")
        self.assertFalse(restored.source_inspection_busy)
        self.assertEqual(restored.last_message, "")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
