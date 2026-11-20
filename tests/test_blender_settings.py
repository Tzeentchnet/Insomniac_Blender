from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender import operators, register, unregister
    from insomniac_asset_io.blender import properties as scene_properties


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertFalse(hasattr(bpy.types.Scene, "insomniac_asset_io"))
        register()
        self.preferences = SimpleNamespace(
            last_game="RESISTANCE_2",
            source_paths_initialized=True,
            resistance_fom_last_source_path="",
            resistance_2_last_source_path="",
            resistance_3_last_source_path="",
            resistance_burning_skies_last_source_path="",
            ratchet_tod_last_source_path="",
        )
        self.preferences_patch = mock.patch.object(
            scene_properties,
            "_addon_preferences",
            return_value=self.preferences,
        )
        self.preferences_patch.start()
        scene_properties._RESTORING_PERSISTED_SELECTION = True
        try:
            settings = bpy.context.scene.insomniac_asset_io
            settings.game = "RESISTANCE_2"
            settings.source_path = ""
        finally:
            scene_properties._RESTORING_PERSISTED_SELECTION = False
        scene_properties._SESSION_SETTINGS.clear()

    def tearDown(self) -> None:
        if hasattr(bpy.types.Scene, "insomniac_asset_io"):
            unregister()
        self.preferences_patch.stop()

    def test_selected_imported_action_updates_playback_range(self) -> None:
        scene = bpy.context.scene
        armature_data = bpy.data.armatures.new("Range_Rig")
        armature = bpy.data.objects.new("Range_Rig", armature_data)
        scene.collection.objects.link(armature)
        action = bpy.data.actions.new("Range_Action")
        action["insomniac_frame_rate"] = 60.0
        action.frame_range = (5.25, 42.2)
        slot = action.slots.new("OBJECT", armature.name)
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

            armature.animation_data.action_slot = None
            bpy.context.view_layer.update()

            self.assertEqual(armature.animation_data.action_slot, slot)
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
        settings.game = "RESISTANCE_2"
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
        self.assertEqual(restored.game, "RESISTANCE_2")
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

    def test_switching_games_resets_game_specific_scene_choices(self) -> None:
        settings = bpy.context.scene.insomniac_asset_io

        self.preferences.resistance_3_last_source_path = r"E:\Tests\resistance_3.iso"
        settings.source_path = r"E:\Tests\resistance_2.iso"
        settings.asset_filter = "vtol"
        settings.asset_kind_filter = "MOBY"

        settings.game = "RESISTANCE_3"

        self.assertEqual(
            settings.source_path,
            r"E:\Tests\resistance_3.iso",
        )
        self.assertEqual(settings.asset_filter, "")
        self.assertEqual(settings.asset_kind_filter, "ALL")
        self.assertTrue(settings.asset_tuid.startswith(("MOBY:", "TIE:")))
        self.assertEqual(settings.level_id, "lobby")

        settings.game = "RESISTANCE_2"
        self.assertEqual(
            settings.source_path,
            r"E:\Tests\resistance_2.iso",
        )

    def test_selecting_iso_switches_to_detected_game(self) -> None:
        settings = bpy.context.scene.insomniac_asset_io
        settings.game = "RESISTANCE_2"
        settings.source_path = r"E:\Tests\resistance_2.iso"
        detected = scene_properties.GAME_ADAPTERS["RESISTANCE_3"]

        with (
            mock.patch.object(
                operators,
                "detect_game_iso",
                return_value=detected,
            ),
            mock.patch.object(
                operators,
                "addon_preferences",
                return_value=self.preferences,
            ),
        ):
            adapter, switched = operators._synchronize_iso_game(
                bpy.context,
                r"E:\Tests\resistance_3.iso",
            )

        self.assertIs(adapter, detected)
        self.assertTrue(switched)
        self.assertEqual(settings.game, "RESISTANCE_3")
        self.assertEqual(
            settings.source_path,
            r"E:\Tests\resistance_3.iso",
        )
        self.assertEqual(
            self.preferences.resistance_2_last_source_path,
            r"E:\Tests\resistance_2.iso",
        )
        self.assertEqual(
            self.preferences.resistance_3_last_source_path,
            r"E:\Tests\resistance_3.iso",
        )
        self.assertEqual(
            self.preferences.resistance_3_iso_path,
            r"E:\Tests\resistance_3.iso",
        )

    def test_game_and_source_restore_from_preferences(self) -> None:
        self.preferences.last_game = "RESISTANCE_3"
        self.preferences.resistance_3_last_source_path = (
            r"E:\Tests\persisted_resistance_3.iso"
        )
        scene_properties._SESSION_SETTINGS.clear()
        scene_properties._RESTORING_PERSISTED_SELECTION = True
        try:
            settings = bpy.context.scene.insomniac_asset_io
            settings.game = "RESISTANCE_2"
            settings.source_path = ""
        finally:
            scene_properties._RESTORING_PERSISTED_SELECTION = False

        scene_properties._restore_scene_settings()

        restored = bpy.context.scene.insomniac_asset_io
        self.assertEqual(restored.game, "RESISTANCE_3")
        self.assertEqual(
            restored.source_path,
            r"E:\Tests\persisted_resistance_3.iso",
        )

    def test_switching_to_tod_uses_level_scoped_catalog_selection(self) -> None:
        settings = bpy.context.scene.insomniac_asset_io
        self.preferences.ratchet_tod_last_source_path = (
            r"E:\Tests\ratchet_tod.iso"
        )

        settings.game = "RATCHET_TOD"

        self.assertEqual(
            settings.source_path,
            r"E:\Tests\ratchet_tod.iso",
        )
        self.assertEqual(settings.level_id, "apogee_space_station")
        self.assertEqual(len(settings.asset_tuid.split(":")), 3)

    def test_switching_to_rfom_uses_level_scoped_catalog_selection(
        self,
    ) -> None:
        settings = bpy.context.scene.insomniac_asset_io
        self.preferences.resistance_fom_last_source_path = (
            r"E:\Tests\resistance_fom.iso"
        )

        settings.game = "RESISTANCE_FOM"

        self.assertEqual(
            settings.source_path,
            r"E:\Tests\resistance_fom.iso",
        )
        self.assertEqual(settings.level_id, "level100")
        self.assertEqual(len(settings.asset_tuid.split(":")), 3)

    def test_rfom_material_filter_lists_level_scoped_shaders(self) -> None:
        settings = bpy.context.scene.insomniac_asset_io
        settings.game = "RESISTANCE_FOM"
        settings.asset_kind_filter = "SHADER"
        settings.asset_level_filter = "level20"

        state = scene_properties.asset_selector_state(
            settings,
            bpy.context,
        )

        self.assertEqual(state.match_count, 633)
        self.assertTrue(
            all(
                identifier.startswith("SHADER:level20:")
                for identifier, *_rest in state.items
            )
        )

    def test_tod_material_filter_lists_level_scoped_shaders(self) -> None:
        settings = bpy.context.scene.insomniac_asset_io
        settings.game = "RATCHET_TOD"
        settings.asset_kind_filter = "SHADER"
        settings.asset_level_filter = "metropolis"

        state = scene_properties.asset_selector_state(
            settings,
            bpy.context,
        )

        self.assertGreater(state.match_count, 600)
        self.assertTrue(
            all(
                identifier.startswith("SHADER:metropolis:")
                for identifier, *_rest in state.items
            )
        )

    def test_animation_search_selects_first_matching_clip(self) -> None:
        picker = SimpleNamespace(
            clips=[
                SimpleNamespace(search_text="idle 30 frames"),
                SimpleNamespace(search_text="crumbling 151 frames"),
            ],
            clip_index=0,
            asset_search="crumbling",
        )

        operators._update_animation_picker_search(picker, None)

        self.assertEqual(picker.clip_index, 1)

        picker.asset_search = "missing"
        operators._update_animation_picker_search(picker, None)

        self.assertEqual(picker.clip_index, -1)

    def test_import_completion_selects_new_asset_collection(self) -> None:
        scene = bpy.context.scene
        collection = bpy.data.collections.new("Focused_Import")
        scene.collection.children.link(collection)
        root = bpy.data.objects.new("Focused_Import_Root", None)
        collection.objects.link(root)
        mesh = bpy.data.meshes.new("Focused_Import_Mesh")
        mesh.from_pydata(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            [],
            [(0, 1, 2)],
        )
        mesh_object = bpy.data.objects.new("Focused_Import", mesh)
        collection.objects.link(mesh_object)
        unrelated = bpy.data.objects.new("Previously_Selected", None)
        scene.collection.objects.link(unrelated)

        try:
            bpy.context.view_layer.objects.active = unrelated
            unrelated.select_set(True)

            operators._focus_imported_asset(bpy.context, root)

            self.assertEqual(
                set(bpy.context.selected_objects),
                {root, mesh_object},
            )
            self.assertIs(bpy.context.view_layer.objects.active, mesh_object)
            self.assertFalse(unrelated.select_get())
        finally:
            bpy.data.objects.remove(unrelated, do_unlink=True)
            bpy.data.objects.remove(mesh_object, do_unlink=True)
            bpy.data.objects.remove(root, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.data.collections.remove(collection)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
