from __future__ import annotations

import sys
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
    from insomniac_asset_io.blender.importer import (
        AssetImportOptions,
        LevelImportOptions,
        _build_mesh,
        import_level,
        import_moby,
        import_static_asset,
    )
    from insomniac_asset_io.blender.preferences import (
        InsomniacAddonPreferences,
    )
    from insomniac_asset_io.games.resistance_2.formats import (
        AssetKind,
        CollisionMesh,
        CollisionPrimitive,
        GameplayRegion,
        MeshGeometry,
        MobyAsset,
        PlacedInstance,
        TieAsset,
        Zone,
    )


class _Reader:
    warnings: list[str] = []

    def __init__(self, asset):
        self.asset = asset

    def read_moby(self, _tuid):
        return self.asset

    def clear_texture_cache(self):
        pass


class _LevelReader:
    resource_prefix = "fixture"
    asset_prefix = "RFOM"
    game_id = "RESISTANCE_FOM"
    game_label = "Resistance: Fall of Man"

    def __init__(self):
        self.warnings: list[str] = []
        matrix = np.eye(4, dtype=np.float32)
        self.zones = [
            Zone(
                tuid=0,
                tie_instances=[],
                terrain_meshes=[],
                shader_tuids=(),
                shrub_instances=[
                    PlacedInstance(
                        asset_tuid=1,
                        instance_tuid=10,
                        name="Shrub_0",
                        matrix=matrix.copy(),
                    )
                ],
                foliage_instances=[
                    PlacedInstance(
                        asset_tuid=2,
                        instance_tuid=20,
                        name="Foliage_0",
                        matrix=matrix.copy(),
                    )
                ],
            )
        ]

    @staticmethod
    def _asset(tuid: int) -> TieAsset:
        return TieAsset(
            tuid=tuid,
            meshes=[
                MeshGeometry(
                    shader_index=0,
                    positions=np.asarray(
                        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                        dtype=np.float32,
                    ),
                    uvs=np.zeros((3, 2), dtype=np.float32),
                    indices=np.asarray((0, 1, 2), dtype=np.int32),
                )
            ],
            shader_tuids=(),
        )

    def read_zones(self):
        return self.zones

    def read_shrub(self, tuid):
        return self._asset(tuid)

    def read_tie(self, tuid):
        return self._asset(tuid)

    def read_foliage(self, tuid):
        return self._asset(tuid)

    def read_gameplay_regions(self):
        return [GameplayRegion(name="fixture", moby_instances=[])]

    def clear_texture_cache(self):
        pass


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderOptionalGeometryTests(unittest.TestCase):
    def tearDown(self) -> None:
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in tuple(bpy.data.collections):
            bpy.data.collections.remove(collection)
        for mesh in tuple(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)

    def test_optional_geometry_is_excluded_by_default(self) -> None:
        result = import_moby(
            bpy.context,
            _Reader(self._asset()),
            1,
            AssetImportOptions(materials=False, animations=False),
            source_path="fixture",
        )
        asset_collection = result.root.users_collection[0]
        base_object = next(
            obj
            for obj in asset_collection.objects
            if obj.type == "MESH"
        )
        children = {
            child.get("insomniac_geometry_kind"): child
            for child in asset_collection.children
        }
        customization_collection = children["CUSTOMIZATIONS"]
        bangle_collections = tuple(customization_collection.children)

        self.assertEqual(set(children), {"CUSTOMIZATIONS"})
        self.assertEqual(len(bangle_collections), 1)
        self.assertTrue(bangle_collections[0].hide_viewport)
        self.assertTrue(bangle_collections[0].hide_render)
        self.assertFalse(bangle_collections[0].hide_select)
        self.assertEqual(len(base_object.data.polygons), 1)
        self.assertFalse(
            any(
                obj.get("insomniac_damage_permutation")
                or obj.get("insomniac_lod_level")
                or obj.get("insomniac_collision_geometry")
                for obj in bpy.data.objects
            )
        )

    def test_imports_foliage_as_standalone_collection(self) -> None:
        result = import_static_asset(
            bpy.context,
            _LevelReader(),
            2,
            AssetImportOptions(materials=False, animations=False),
            kind=AssetKind.FOLIAGE,
            source_path="fixture",
        )

        collection = result.root.users_collection[0]
        mesh_object = next(
            obj for obj in collection.objects if obj.type == "MESH"
        )
        self.assertEqual(collection.name, "RFOM_Foliage_0000000000000002")
        self.assertEqual(
            mesh_object["insomniac_asset_kind"],
            "FOLIAGE",
        )
        self.assertEqual(
            mesh_object["insomniac_asset_tuid"],
            "0x0000000000000002",
        )

    def test_addon_preferences_default_optional_geometry_off(self) -> None:
        bpy.utils.register_class(InsomniacAddonPreferences)
        try:
            properties = InsomniacAddonPreferences.bl_rna.properties
            self.assertFalse(
                properties["asset_import_damage_permutations"].default
            )
            self.assertFalse(
                properties["asset_import_lower_lods"].default
            )
            self.assertFalse(
                properties["asset_import_collision_geometry"].default
            )
            self.assertFalse(
                properties["asset_import_source_default_parts"].default
            )
        finally:
            bpy.utils.unregister_class(InsomniacAddonPreferences)

    def test_level_vegetation_is_enabled_by_default(self) -> None:
        bpy.utils.register_class(InsomniacAddonPreferences)
        try:
            properties = InsomniacAddonPreferences.bl_rna.properties
            self.assertTrue(properties["level_import_vegetation"].default)
            self.assertTrue(properties["asset_cache_vegetation"].default)
            self.assertTrue(
                properties[
                    "asset_cache_source_default_parts"
                ].default
            )
        finally:
            bpy.utils.unregister_class(InsomniacAddonPreferences)

    def test_level_vegetation_uses_dedicated_collections(self) -> None:
        result = import_level(
            bpy.context,
            _LevelReader(),
            LevelImportOptions(
                materials=False,
                terrain=False,
                ties=False,
                vegetation=True,
                gameplay=False,
                convert_axes=False,
            ),
            source_path="fixture",
        )
        level_collection = result.root.users_collection[0]
        child_objects = {
            child.name: tuple(child.objects)
            for child in level_collection.children
        }

        self.assertEqual(set(child_objects), {"Shrubs", "Foliage"})
        self.assertEqual(
            child_objects["Shrubs"][0]["insomniac_asset_kind"],
            "SHRUB",
        )
        self.assertEqual(
            child_objects["Foliage"][0]["insomniac_asset_kind"],
            "FOLIAGE",
        )
        self.assertEqual(result.objects, 3)
        self.assertEqual(result.meshes, 2)

    def test_mesh_preserves_secondary_uvs_and_point_colors(self) -> None:
        material = bpy.data.materials.new("Terrain")
        geometry = MeshGeometry(
            shader_index=0,
            positions=np.asarray(
                ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                dtype=np.float32,
            ),
            uvs=np.asarray(
                ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
                dtype=np.float32,
            ),
            indices=np.asarray((0, 1, 2), dtype=np.int32),
            secondary_uvs=np.asarray(
                ((0.25, 0.5), (0.5, 0.75), (0.75, 1.0)),
                dtype=np.float32,
            ),
            colors=np.asarray(
                (
                    (0.25, 0.25, 0.25, 0.0),
                    (0.5, 0.5, 0.5, 0.5),
                    (1.0, 1.0, 1.0, 1.0),
                ),
                dtype=np.float32,
            ),
        )

        mesh = _build_mesh("Terrain", (geometry,), (material,)).mesh

        self.assertEqual(
            {layer.name for layer in mesh.uv_layers},
            {"UVMap", "LightmapUV"},
        )
        color = mesh.color_attributes["InsomniacVertexColor"]
        self.assertEqual(color.domain, "POINT")
        self.assertEqual(color.data_type, "BYTE_COLOR")
        self.assertEqual(len(color.data), 3)

    def test_level_tie_preserves_instance_lightmap_index(self) -> None:
        reader = _LevelReader()
        reader.zones[0].tie_instances = [
            PlacedInstance(
                asset_tuid=3,
                instance_tuid=30,
                name="Tie_0",
                matrix=np.eye(4, dtype=np.float32),
                lightmap_index=17,
            )
        ]

        result = import_level(
            bpy.context,
            reader,
            LevelImportOptions(
                materials=False,
                terrain=False,
                ties=True,
                vegetation=False,
                gameplay=False,
                convert_axes=False,
            ),
            source_path="fixture",
        )
        level_collection = result.root.users_collection[0]
        tie = tuple(level_collection.children["Static Props"].objects)[0]

        self.assertEqual(tie["insomniac_lightmap_index"], 17)

    def test_source_default_customizations_can_be_shown(self) -> None:
        result = import_moby(
            bpy.context,
            _Reader(self._asset()),
            1,
            AssetImportOptions(
                materials=False,
                animations=False,
                source_default_parts=True,
            ),
            source_path="fixture",
        )
        asset_collection = result.root.users_collection[0]
        customization_collection = next(
            child
            for child in asset_collection.children
            if child.get("insomniac_geometry_kind") == "CUSTOMIZATIONS"
        )
        bangle_collection = customization_collection.children[0]

        self.assertTrue(
            bangle_collection["insomniac_source_default_visible"]
        )
        self.assertFalse(bangle_collection.hide_viewport)
        self.assertFalse(bangle_collection.hide_render)
        self.assertFalse(bangle_collection.hide_select)

    def test_level_gameplay_uses_source_default_customizations(self) -> None:
        reader = _LevelReader()
        asset = self._asset()
        asset.meshes.append(
            MeshGeometry(
                shader_index=0,
                positions=np.asarray(
                    ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                    dtype=np.float32,
                ),
                uvs=np.zeros((3, 2), dtype=np.float32),
                indices=np.asarray((0, 1, 2), dtype=np.int32),
                segment_index=4,
            )
        )
        asset.customization_segments = (3, 4)
        reader.read_moby = lambda _tuid: asset
        reader.read_gameplay_regions = lambda: [
            GameplayRegion(
                name="fixture",
                moby_instances=[
                    PlacedInstance(
                        asset_tuid=1,
                        instance_tuid=30,
                        name="Jeep",
                        matrix=np.eye(4, dtype=np.float32),
                    )
                ],
            )
        ]

        result = import_level(
            bpy.context,
            reader,
            LevelImportOptions(
                materials=False,
                terrain=False,
                ties=False,
                vegetation=False,
                gameplay=True,
                convert_axes=False,
            ),
            source_path="fixture",
        )
        level_collection = result.root.users_collection[0]
        gameplay = level_collection.children["Gameplay Instances"]
        jeep = tuple(gameplay.children["fixture"].objects)[0]

        self.assertEqual(len(jeep.data.polygons), 2)
        self.assertEqual(
            jeep.data["insomniac_default_draw_mask"],
            "0x0000000000000009",
        )
        self.assertTrue(jeep.data["insomniac_source_default_parts"])
        self.assertEqual(
            jeep["insomniac_default_draw_mask"],
            "0x0000000000000009",
        )
        self.assertTrue(jeep["insomniac_source_default_parts"])

    def test_optional_geometry_uses_dedicated_collections(self) -> None:
        result = import_moby(
            bpy.context,
            _Reader(self._asset()),
            1,
            AssetImportOptions(
                materials=False,
                animations=False,
                damage_permutations=True,
                lower_lods=True,
                collision_geometry=True,
            ),
            source_path="fixture",
        )
        asset_collection = result.root.users_collection[0]
        children = {
            child.get("insomniac_geometry_kind"): child
            for child in asset_collection.children
        }
        damage_objects = tuple(children["DAMAGE_PERMUTATIONS"].objects)
        lod_objects = tuple(children["LOWER_LODS"].objects)
        collision_objects = tuple(children["COLLISION"].objects)
        customization_collections = tuple(
            children["CUSTOMIZATIONS"].children
        )
        customization_objects = tuple(
            obj
            for child in customization_collections
            for obj in child.objects
        )

        self.assertEqual(len(customization_collections), 1)
        self.assertTrue(customization_collections[0].hide_viewport)
        self.assertTrue(customization_collections[0].hide_render)
        self.assertFalse(customization_collections[0].hide_select)
        self.assertEqual(len(customization_objects), 1)
        self.assertEqual(
            customization_objects[0]["insomniac_segment_index"],
            3,
        )
        self.assertTrue(
            customization_objects[0]["insomniac_customization"]
        )
        self.assertEqual(len(damage_objects), 2)
        self.assertEqual(
            {obj["insomniac_segment_index"] for obj in damage_objects},
            {1, 2},
        )
        self.assertTrue(
            all(len(obj.data.polygons) == 1 for obj in damage_objects)
        )
        self.assertEqual(len(lod_objects), 2)
        self.assertEqual(
            {obj["insomniac_lod_level"] for obj in lod_objects},
            {1, 2},
        )
        self.assertTrue(
            all(len(obj.data.polygons) == 1 for obj in lod_objects)
        )
        self.assertEqual(len(collision_objects), 4)
        self.assertEqual(
            {
                obj["insomniac_collision_kind"]
                for obj in collision_objects
            },
            {"BOX", "SPHERE", "CAPSULE", "TRIANGLE_MESH"},
        )
        self.assertTrue(
            all(obj.display_type == "WIRE" for obj in collision_objects)
        )
        self.assertTrue(all(obj.show_in_front for obj in collision_objects))
        self.assertTrue(all(obj.hide_render for obj in collision_objects))
        self.assertEqual(result.meshes, 10)

    @staticmethod
    def _asset():
        def geometry(
            segment_index: int,
            damage: bool,
            lod_level: int = 0,
        ) -> MeshGeometry:
            return MeshGeometry(
                shader_index=0,
                positions=np.asarray(
                    ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                    dtype=np.float32,
                ),
                uvs=np.zeros((3, 2), dtype=np.float32),
                indices=np.asarray((0, 1, 2), dtype=np.int32),
                segment_index=segment_index,
                is_damage_permutation=damage,
                lod_level=lod_level,
            )

        box = np.asarray(
            (
                (1, 0, 0, 1),
                (0, 1, 0, 1),
                (0, 0, 1, 1),
                (0, 0, 0, 1),
            ),
            dtype=np.float32,
        )
        sphere = np.zeros((4, 4), dtype=np.float32)
        sphere[0] = (2.0, 3.0, 4.0, 1.5)
        capsule = np.zeros((4, 4), dtype=np.float32)
        capsule[0] = (0.0, 0.0, -1.0, 0.5)
        capsule[1, :3] = (0.0, 0.0, 1.0)
        return MobyAsset(
            tuid=1,
            name="Optional Geometry Fixture",
            meshes=[
                geometry(0, False),
                geometry(1, True),
                geometry(2, True),
                geometry(0, False, 1),
                geometry(0, False, 2),
                geometry(3, False),
            ],
            bounding_sphere=(0.0, 0.0, 0.0, 2.0),
            shader_tuids=(1,),
            skeleton=None,
            animset_hash=None,
            bind_pose_inverse_offset=0,
            collision_primitives=(
                CollisionPrimitive(4, None, box),
                CollisionPrimitive(5, None, sphere),
                CollisionPrimitive(6, None, capsule),
            ),
            collision_meshes=(
                CollisionMesh(
                    positions=np.asarray(
                        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                        dtype=np.float32,
                    ),
                    indices=np.asarray(((0, 1, 2),), dtype=np.int32),
                    face_types=np.asarray((7,), dtype=np.uint8),
                ),
            ),
            customization_segments=(3,),
            default_draw_mask=0x09,
        )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
