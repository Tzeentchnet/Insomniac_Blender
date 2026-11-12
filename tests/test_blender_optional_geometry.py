from __future__ import annotations

from pathlib import Path
import sys
import unittest

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
        import_moby,
    )
    from insomniac_asset_io.blender.preferences import (
        InsomniacAddonPreferences,
    )
    from insomniac_asset_io.games.resistance_2.formats import (
        CollisionMesh,
        CollisionPrimitive,
        MeshGeometry,
        MobyAsset,
    )


class _Reader:
    warnings: list[str] = []

    def __init__(self, asset):
        self.asset = asset

    def read_moby(self, _tuid):
        return self.asset

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

        self.assertEqual(len(asset_collection.children), 0)
        self.assertEqual(len(base_object.data.polygons), 1)
        self.assertFalse(
            any(
                obj.get("insomniac_damage_permutation")
                or obj.get("insomniac_collision_geometry")
                for obj in bpy.data.objects
            )
        )

    def test_addon_preferences_default_optional_geometry_off(self) -> None:
        bpy.utils.register_class(InsomniacAddonPreferences)
        try:
            properties = InsomniacAddonPreferences.bl_rna.properties
            self.assertFalse(
                properties["asset_import_damage_permutations"].default
            )
            self.assertFalse(
                properties["asset_import_collision_geometry"].default
            )
        finally:
            bpy.utils.unregister_class(InsomniacAddonPreferences)

    def test_optional_geometry_uses_dedicated_collections(self) -> None:
        result = import_moby(
            bpy.context,
            _Reader(self._asset()),
            1,
            AssetImportOptions(
                materials=False,
                animations=False,
                damage_permutations=True,
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
        collision_objects = tuple(children["COLLISION"].objects)

        self.assertEqual(len(damage_objects), 2)
        self.assertEqual(
            {obj["insomniac_segment_index"] for obj in damage_objects},
            {1, 2},
        )
        self.assertTrue(
            all(len(obj.data.polygons) == 1 for obj in damage_objects)
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
        self.assertEqual(result.meshes, 7)

    @staticmethod
    def _asset():
        def geometry(segment_index: int, damage: bool) -> MeshGeometry:
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
        )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
