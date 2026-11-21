from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender.action_range import (
        sync_active_action_range,
    )
    from insomniac_asset_io.blender.importer import (
        AssetImportOptions,
        _assign_skin,
        _build_mesh,
        _create_actions,
        _create_armature,
        import_moby,
        import_moby_animation,
    )
    from insomniac_asset_io.core.source import GameSource
    from insomniac_asset_io.games.resistance_2.animations import (
        _compose_r3_overlays,
    )
    from insomniac_asset_io.games.resistance_2.formats import (
        Bone,
        DecodedClip,
        MeshGeometry,
        MobyAsset,
        Skeleton,
    )
    from insomniac_asset_io.games.resistance_burning_skies.reader import (
        BurningSkiesReader,
    )
    from tests.test_burning_skies import _model_resource


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderRigTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def tearDown(self) -> None:
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for action in tuple(bpy.data.actions):
            bpy.data.actions.remove(action)
        for mesh in tuple(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)
        for armature in tuple(bpy.data.armatures):
            bpy.data.armatures.remove(armature)
        for collection in tuple(bpy.data.collections):
            bpy.data.collections.remove(collection)

    def test_preserves_normalized_float_skin_weights(self) -> None:
        weights = np.array(
            (
                (0.1, 0.9, 0.0, 0.0),
                (0.25, 0.75, 0.0, 0.0),
                (1.0, 0.0, 0.0, 0.0),
            ),
            dtype=np.float32,
        )
        geometry = MeshGeometry(
            shader_index=0,
            positions=np.array(
                ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                dtype=np.float32,
            ),
            uvs=np.zeros((3, 2), dtype=np.float32),
            indices=np.array((0, 1, 2), dtype=np.int32),
            bone_indices=np.array(
                ((0, 1, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0)),
                dtype=np.uint16,
            ),
            bone_weights=weights,
        )
        combined = _build_mesh("Float_Weights", (geometry,), [])
        np.testing.assert_array_equal(combined.bone_weights, weights)

        mesh_object = bpy.data.objects.new("Float_Weights", combined.mesh)
        self._collection().objects.link(mesh_object)
        bind = np.repeat(
            np.identity(4, dtype=np.float32)[None],
            2,
            axis=0,
        )
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 1, 0), Bone(0, 0, 0, 0)),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        _assign_skin(
            mesh_object,
            skeleton,
            combined.bone_indices,
            combined.bone_weights,
        )

        self.assertAlmostEqual(mesh_object.vertex_groups["bone_000"].weight(0), 0.1)
        self.assertAlmostEqual(mesh_object.vertex_groups["bone_001"].weight(0), 0.9)
        self.assertAlmostEqual(mesh_object.vertex_groups["bone_000"].weight(1), 0.25)
        self.assertAlmostEqual(mesh_object.vertex_groups["bone_001"].weight(1), 0.75)
        self.assertAlmostEqual(mesh_object.vertex_groups["bone_000"].weight(2), 1.0)

    def test_imports_burning_skies_skin_as_working_armature(self) -> None:
        source_weights = (
            (0.5, 0.25, 0.25, 0.0),
            (0.33325, 0.33325, 0.33325, 0.0),
            (1.0, 0.0, 0.0, 0.0),
            (0.1, 0.2, 0.3, 0.4),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "models" / "TestSkinned.smf"
            model_path.parent.mkdir()
            model_path.write_bytes(
                _model_resource(
                    bone_parents=(-1, 0, 0, 0),
                    skin_palette=(3, 1, 1, 2),
                    skin_weights=source_weights,
                    skin_local_indices=(
                        (0, 1, 2, 0),
                        (0, 1, 3, 0),
                        (2, 0, 0, 0),
                        (0, 1, 2, 3),
                    ),
                )
            )
            with GameSource.open(root) as source:
                reader = BurningSkiesReader(source)
                result = import_moby(
                    bpy.context,
                    reader,
                    reader.model_id("models/TestSkinned.smf"),
                    AssetImportOptions(
                        materials=False,
                        animations=False,
                        convert_axes=False,
                    ),
                    source_path=str(root),
                )

            collection = result.root.users_collection[0]
            mesh_object = next(
                item for item in collection.objects if item.type == "MESH"
            )
            armature = next(
                item for item in collection.objects if item.type == "ARMATURE"
            )
            self.assertEqual(result.objects, 3)
            self.assertIs(mesh_object.parent, armature)
            self.assertIs(armature.parent, result.root)
            self.assertEqual(len(armature.data.bones), 4)
            self.assertEqual(len(mesh_object.vertex_groups), 4)
            self.assertEqual(len(mesh_object.modifiers), 1)
            self.assertEqual(mesh_object.modifiers[0].type, "ARMATURE")
            self.assertIs(mesh_object.modifiers[0].object, armature)
            self.assertAlmostEqual(
                mesh_object.vertex_groups["bone_003"].weight(0),
                0.5,
            )
            self.assertAlmostEqual(
                mesh_object.vertex_groups["bone_001"].weight(0),
                0.5,
            )

            bpy.context.view_layer.update()
            evaluated = mesh_object.evaluated_get(
                bpy.context.evaluated_depsgraph_get()
            )
            np.testing.assert_allclose(
                [vertex.co[:] for vertex in evaluated.data.vertices],
                [vertex.co[:] for vertex in mesh_object.data.vertices],
                atol=1e-6,
            )

            armature.pose.bones["bone_003"].location.x = 2.0
            bpy.context.view_layer.update()
            evaluated = mesh_object.evaluated_get(
                bpy.context.evaluated_depsgraph_get()
            )
            self.assertAlmostEqual(evaluated.data.vertices[0].co.x, 0.0, places=5)
            armature.pose.bones["bone_003"].location.x = 0.0
            bpy.context.view_layer.update()

            blend_path = root / "burning_skies_skin.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
            bpy.ops.wm.open_mainfile(filepath=str(blend_path))

            reopened_mesh = bpy.data.objects["TestCube"]
            reopened_armature = bpy.data.objects["TestCube_Rig"]
            self.assertIs(reopened_mesh.parent, reopened_armature)
            self.assertEqual(len(reopened_armature.data.bones), 4)
            self.assertIs(
                reopened_mesh.modifiers[0].object,
                reopened_armature,
            )
            self.assertAlmostEqual(
                reopened_mesh.vertex_groups["bone_003"].weight(0),
                0.5,
            )
            self.assertAlmostEqual(
                reopened_mesh.vertex_groups["bone_001"].weight(0),
                0.5,
            )

    def test_imports_one_animation_with_compatible_rig(self) -> None:
        bind = np.identity(4, dtype=np.float32)[None]
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        clip = DecodedClip(
            name="selected",
            frame_rate=30.0,
            looping=True,
            additive=False,
            translations=np.zeros((2, 1, 3), dtype=np.float32),
            rotations_xyzw=np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
                (2, 1, 1),
            ),
            scales=np.ones((2, 1, 3), dtype=np.float32),
            animated_bones=np.ones(1, dtype=np.bool_),
        )
        moby = MobyAsset(
            tuid=0x22,
            name="Animated_Moby",
            meshes=[],
            bounding_sphere=(0.0, 0.0, 0.0, 1.0),
            shader_tuids=(),
            skeleton=skeleton,
            animset_hash=1,
            bind_pose_inverse_offset=0,
        )

        class Reader:
            game_id = "RATCHET_TOD"
            game_label = "Ratchet & Clank Future: Tools of Destruction"
            engine_id = "V1"
            asset_prefix = "TOD"
            resource_prefix = "levels/test"

            @staticmethod
            def read_moby(_tuid):
                return moby

            @staticmethod
            def read_animations(_moby):
                return [clip]

            @staticmethod
            def consume_import_warnings():
                return ()

        result = import_moby_animation(
            bpy.context,
            Reader(),
            moby.tuid,
            0,
            AssetImportOptions(
                materials=False,
                animations=True,
                convert_axes=False,
            ),
            source_path="test",
        )

        self.assertEqual(result.objects, 2)
        self.assertEqual(result.meshes, 0)
        self.assertEqual(result.actions, 1)
        self.assertEqual(
            result.root["insomniac_asset_kind"],
            "ANIMATION",
        )
        action = bpy.data.actions["Animated_Moby_Rig_selected"]
        self.assertEqual(
            action["insomniac_source_moby_tuid"],
            "0x0000000000000022",
        )
        self.assertEqual(action["insomniac_animation_name"], "selected")
        self.assertEqual(action["insomniac_animation_index"], 0)

    def test_scaled_bind_frame_does_not_deform_mesh(self) -> None:
        collection = self._collection()
        bind = np.diag([2.0, 3.0, 4.0, 1.0]).astype(np.float32)
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind[None].copy(),
            bind_world=bind[None].copy(),
            bind_world_inverse=np.linalg.inv(bind)[None].astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Scaled_Rig",
            skeleton,
            collection,
        )
        mesh_object = self._weighted_vertex(collection, armature)
        clip = DecodedClip(
            name="bind",
            frame_rate=30.0,
            looping=False,
            additive=False,
            translations=np.zeros((1, 1, 3), dtype=np.float32),
            rotations_xyzw=np.array(
                [[[0.0, 0.0, 0.0, 1.0]]],
                dtype=np.float32,
            ),
            scales=np.array([[[2.0, 3.0, 4.0]]], dtype=np.float32),
            animated_bones=np.zeros(1, dtype=np.bool_),
        )

        self.assertEqual(
            _create_actions(bpy.context, armature, skeleton, [clip]),
            1,
        )
        self.assertIsNone(armature.animation_data.action)

        action = bpy.data.actions["Scaled_Rig_bind"]
        self.assertEqual(len(action.slots), 1)
        armature.animation_data.action = action
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()

        evaluated = mesh_object.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        )
        self.assertAlmostEqual(evaluated.data.vertices[0].co.x, 1.0, places=5)

    def test_action_exposes_keyframes_and_source_range(self) -> None:
        collection = self._collection()
        bind = np.identity(4, dtype=np.float32)[None]
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Action_Rig",
            skeleton,
            collection,
        )
        frame_count = 4
        frame_rate = 30.0
        translations = np.zeros((frame_count, 1, 3), dtype=np.float32)
        translations[:, 0, 0] = np.arange(frame_count, dtype=np.float32)
        translations[:, 0, 1] = 2.0
        clip = DecodedClip(
            name="motion",
            frame_rate=frame_rate,
            looping=False,
            additive=False,
            translations=translations,
            rotations_xyzw=np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
                (frame_count, 1, 1),
            ),
            scales=np.ones((frame_count, 1, 3), dtype=np.float32),
            animated_bones=np.ones(1, dtype=np.bool_),
        )

        self.assertEqual(
            _create_actions(bpy.context, armature, skeleton, [clip]),
            1,
        )

        action = bpy.data.actions["Action_Rig_motion"]
        expected_end = (
            1.0
            + (frame_count - 1)
            * max(bpy.context.scene.render.fps, 1)
            / frame_rate
        )
        self.assertTrue(action.use_frame_range)
        self.assertAlmostEqual(action.frame_range[0], 1.0)
        self.assertAlmostEqual(action.frame_range[1], expected_end, places=6)
        self.assertEqual(action.slots.active, action.slots[0])

        channelbag = action.layers[0].strips[0].channelbags[0]
        self.assertEqual(len(channelbag.fcurves), 2)
        self.assertEqual(
            channelbag.fcurves[0].data_path,
            'pose.bones["bone_000"].location',
        )
        self.assertEqual(channelbag.fcurves[0].array_index, 0)
        self.assertEqual(
            len(channelbag.fcurves[0].keyframe_points),
            frame_count,
        )
        self.assertEqual(channelbag.fcurves[1].array_index, 1)
        self.assertEqual(
            len(channelbag.fcurves[1].keyframe_points),
            1,
        )
        self.assertAlmostEqual(
            channelbag.fcurves[1].keyframe_points[0].co.y,
            2.0,
        )
        self.assertTrue(
            all(
                point.interpolation == "LINEAR"
                for fcurve in channelbag.fcurves
                for point in fcurve.keyframe_points
            )
        )
        self.assertTrue(all(bone.select for bone in armature.pose.bones))
        self.assertIsNone(armature.animation_data.action)

    def test_selected_action_with_missing_slot_moves_rig(self) -> None:
        collection = self._collection()
        bind = np.identity(4, dtype=np.float32)[None]
        skeleton = Skeleton(
            root_bone=0,
            bones=(Bone(0, -1, 0xFFFF, 0xFFFF),),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Slot_Rig",
            skeleton,
            collection,
        )
        clip = DecodedClip(
            name="motion",
            frame_rate=24.0,
            looping=False,
            additive=False,
            translations=np.array(
                [[[0.0, 0.0, 0.0]], [[2.0, 0.0, 0.0]]],
                dtype=np.float32,
            ),
            rotations_xyzw=np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
                (2, 1, 1),
            ),
            scales=np.ones((2, 1, 3), dtype=np.float32),
            animated_bones=np.ones(1, dtype=np.bool_),
        )

        self.assertEqual(
            _create_actions(bpy.context, armature, skeleton, [clip]),
            1,
        )
        action = bpy.data.actions["Slot_Rig_motion"]
        bpy.context.view_layer.objects.active = armature
        armature.animation_data.action = action
        armature.animation_data.action_slot = None
        bpy.context.scene.frame_set(2)
        bpy.context.view_layer.update()
        self.assertAlmostEqual(
            armature.pose.bones["bone_000"].location.x,
            0.0,
            places=5,
        )

        self.assertTrue(sync_active_action_range())
        self.assertEqual(
            armature.animation_data.action_slot,
            action.slots[0],
        )
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()
        first = armature.pose.bones["bone_000"].location.x
        bpy.context.scene.frame_set(2)
        bpy.context.view_layer.update()
        second = armature.pose.bones["bone_000"].location.x

        self.assertAlmostEqual(first, 0.0, places=5)
        self.assertAlmostEqual(second, 2.0, places=5)

    def test_baked_overlay_preserves_base_motion(self) -> None:
        collection = self._collection()
        bind = np.repeat(
            np.identity(4, dtype=np.float32)[None], 2, axis=0
        )
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 0xFFFF, 0xFFFF),
                Bone(0, -1, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind.copy(),
            bind_world=bind.copy(),
            bind_world_inverse=bind.copy(),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Overlay_Rig",
            skeleton,
            collection,
        )
        mesh_object = self._weighted_vertex(
            collection,
            armature,
            bone_name="bone_001",
        )
        identity = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        quarter_turn = np.array(
            [0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)],
            dtype=np.float32,
        )
        base_translations = np.zeros((2, 2, 3), dtype=np.float32)
        base_translations[1, 0, 0] = 1.0
        base = DecodedClip(
            name="ravager_r3_idle_sighted",
            frame_rate=float(bpy.context.scene.render.fps),
            looping=True,
            additive=False,
            translations=base_translations,
            rotations_xyzw=np.tile(identity, (2, 2, 1)),
            scales=np.ones((2, 2, 3), dtype=np.float32),
            animated_bones=np.array([True, False]),
        )
        overlay_rotations = np.tile(identity, (2, 2, 1))
        overlay_rotations[1, 1] = quarter_turn
        overlay = DecodedClip(
            name="ravager_partial_aiming_horizontal",
            frame_rate=float(bpy.context.scene.render.fps),
            looping=False,
            additive=True,
            translations=np.zeros((2, 2, 3), dtype=np.float32),
            rotations_xyzw=overlay_rotations,
            scales=np.ones((2, 2, 3), dtype=np.float32),
            animated_bones=np.array([False, True]),
            source_flags=0x0202,
            blend_weights=np.array([0, 255], dtype=np.uint8),
            reference_rotations_xyzw=np.tile(identity, (2, 1)),
            rotation_animated_bones=np.array([False, True]),
            translation_components=np.zeros((2, 3), dtype=np.bool_),
            scale_components=np.zeros((2, 3), dtype=np.bool_),
        )
        _compose_r3_overlays([base, overlay], skeleton)

        self.assertEqual(
            _create_actions(
                bpy.context, armature, skeleton, [overlay]
            ),
            1,
        )
        action = bpy.data.actions[
            "Overlay_Rig_ravager_partial_aiming_horizontal"
        ]
        self.assertTrue(action["insomniac_overlay_baked"])
        self.assertEqual(
            action["insomniac_overlay_base"],
            "ravager_r3_idle_sighted",
        )
        armature.animation_data.action = action
        armature.animation_data.action_slot = action.slots[0]
        bpy.context.scene.frame_set(2)
        bpy.context.view_layer.update()

        self.assertAlmostEqual(
            armature.pose.bones["bone_000"].location.x,
            1.0,
            places=5,
        )
        evaluated = mesh_object.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        )
        self.assertAlmostEqual(
            evaluated.data.vertices[0].co.x, 0.0, places=5
        )
        self.assertAlmostEqual(
            evaluated.data.vertices[0].co.y, 1.0, places=5
        )

    def test_bone_relations_preserve_length_and_scale_flags(self) -> None:
        collection = self._collection()
        bind_world = np.repeat(
            np.identity(4, dtype=np.float32)[None],
            2,
            axis=0,
        )
        bind_world[1, 1, 3] = 0.25
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 1, 0xFFFF),
                Bone(0x01, 0, 0, 0),
            ),
            bind_local=bind_world.copy(),
            bind_world=bind_world,
            bind_world_inverse=np.linalg.inv(bind_world).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )

        armature = _create_armature(
            bpy.context,
            "Relations_Rig",
            skeleton,
            collection,
        )

        root = armature.data.bones["bone_000"]
        child = armature.data.bones["bone_001"]
        self.assertAlmostEqual(root.length, 0.25, places=5)
        self.assertAlmostEqual(child.length, 0.05, places=5)
        self.assertEqual(root.inherit_scale, "FULL")
        self.assertEqual(child.inherit_scale, "NONE")

    def test_parent_motion_is_not_applied_twice_to_child(self) -> None:
        collection = self._collection()
        bind_local = np.repeat(
            np.identity(4, dtype=np.float32)[None],
            2,
            axis=0,
        )
        bind_local[0, 1, 3] = 2.0
        bind_world = bind_local.copy()
        bind_world[1] = bind_world[0]
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0x01, -1, 1, 0xFFFF),
                Bone(0x01, 0, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind_local,
            bind_world=bind_world,
            bind_world_inverse=np.linalg.inv(bind_world).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Hierarchy_Rig",
            skeleton,
            collection,
        )
        mesh_object = self._weighted_vertex(
            collection,
            armature,
            coordinate=(1.0, 2.0, 0.0),
            bone_name="bone_001",
        )
        clip = DecodedClip(
            name="parent_motion",
            frame_rate=30.0,
            looping=False,
            additive=False,
            translations=np.zeros((1, 2, 3), dtype=np.float32),
            rotations_xyzw=np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
                (1, 2, 1),
            ),
            scales=np.ones((1, 2, 3), dtype=np.float32),
            animated_bones=np.array([True, False], dtype=np.bool_),
        )

        self.assertEqual(
            _create_actions(bpy.context, armature, skeleton, [clip]),
            1,
        )
        armature.animation_data.action = bpy.data.actions[
            "Hierarchy_Rig_parent_motion"
        ]
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()

        self.assertAlmostEqual(
            armature.pose.bones["bone_000"].matrix.translation.y,
            0.0,
            places=5,
        )
        self.assertAlmostEqual(
            armature.pose.bones["bone_001"].matrix.translation.y,
            0.0,
            places=5,
        )
        evaluated = mesh_object.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        )
        self.assertAlmostEqual(evaluated.data.vertices[0].co.y, 0.0, places=5)

    def test_non_inheriting_bone_ignores_parent_scale(self) -> None:
        collection = self._collection()
        bind_local = np.repeat(
            np.identity(4, dtype=np.float32)[None],
            2,
            axis=0,
        )
        bind_local[1, 1, 3] = 1.0
        bind_world = bind_local.copy()
        skeleton = Skeleton(
            root_bone=0,
            bones=(
                Bone(0, -1, 1, 0xFFFF),
                Bone(0x01, 0, 0xFFFF, 0xFFFF),
            ),
            bind_local=bind_local,
            bind_world=bind_world,
            bind_world_inverse=np.linalg.inv(bind_world).astype(np.float32),
            scale_shift=0,
            translation_shift=0,
        )
        armature = _create_armature(
            bpy.context,
            "Scale_Hierarchy_Rig",
            skeleton,
            collection,
        )
        clip = DecodedClip(
            name="parent_scale",
            frame_rate=30.0,
            looping=False,
            additive=False,
            translations=np.array(
                [[[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]],
                dtype=np.float32,
            ),
            rotations_xyzw=np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
                (1, 2, 1),
            ),
            scales=np.array(
                [[[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]]],
                dtype=np.float32,
            ),
            animated_bones=np.array([True, False], dtype=np.bool_),
        )

        self.assertEqual(
            _create_actions(bpy.context, armature, skeleton, [clip]),
            1,
        )
        armature.animation_data.action = bpy.data.actions[
            "Scale_Hierarchy_Rig_parent_scale"
        ]
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()

        root = armature.pose.bones["bone_000"]
        child = armature.pose.bones["bone_001"]
        self.assertAlmostEqual(root.matrix.to_scale().x, 2.0, places=5)
        self.assertAlmostEqual(child.matrix.translation.y, 1.0, places=5)
        self.assertAlmostEqual(child.matrix.to_scale().x, 1.0, places=5)

    @staticmethod
    def _collection():
        collection = bpy.data.collections.new("Rig_Test")
        bpy.context.scene.collection.children.link(collection)
        return collection

    @staticmethod
    def _weighted_vertex(
        collection,
        armature,
        *,
        coordinate=(1.0, 0.0, 0.0),
        bone_name="bone_000",
    ):
        mesh = bpy.data.meshes.new("Mesh")
        mesh.from_pydata([coordinate], [], [])
        mesh_object = bpy.data.objects.new("Mesh", mesh)
        collection.objects.link(mesh_object)
        mesh_object.parent = armature
        group = mesh_object.vertex_groups.new(name=bone_name)
        group.add([0], 1.0, "REPLACE")
        modifier = mesh_object.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature
        return mesh_object


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
