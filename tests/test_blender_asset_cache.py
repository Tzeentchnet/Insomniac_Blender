from __future__ import annotations

import sys
import tempfile
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
        AssetCacheOptions,
        _mark_action_asset,
        _mark_asset,
        _mark_material_asset,
    )
    from insomniac_asset_io.blender.asset_cache_generation import (
        _asset_catalog_path,
        _asset_relative_file,
        _transactional_replace,
        _unique_asset_pointers,
        _write_catalog_file,
    )
    from insomniac_asset_io.games.registry import get_game_adapter
    from insomniac_asset_io.games.resistance_2.formats import (
        AssetKind,
        AssetPointer,
    )


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderAssetCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            name: set(getattr(bpy.data, name))
            for name in (
                "actions",
                "collections",
                "materials",
                "meshes",
                "objects",
            )
        }

    def tearDown(self) -> None:
        created = set()
        for name, existing in self.snapshot.items():
            created.update(set(getattr(bpy.data, name)) - existing)
        if created:
            bpy.data.batch_remove(created)

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

    def test_marks_foliage_collection_asset(self) -> None:
        adapter = SimpleNamespace(
            id="RESISTANCE_3",
            label="Resistance 3",
        )
        collection = bpy.data.collections.new("R3_Foliage")
        catalog_id = uuid.uuid4()

        _mark_asset(
            collection,
            kind=AssetKind.FOLIAGE,
            tuid=19,
            level_name="mine",
            catalog_id=catalog_id,
            generate_preview=False,
            adapter=adapter,
        )

        self.assertEqual(collection.asset_data.catalog_id, str(catalog_id))
        self.assertEqual(
            collection["insomniac_asset_kind"],
            "FOLIAGE",
        )
        self.assertIn("Foliage", collection.asset_data.tags)

    def test_deduplicates_asset_pointers_by_tuid(self) -> None:
        pointers = (
            AssetPointer(tuid=3, offset=10, length=20),
            AssetPointer(tuid=3, offset=30, length=40),
            AssetPointer(tuid=4, offset=50, length=60),
        )

        unique, duplicates = _unique_asset_pointers(pointers)

        self.assertEqual([pointer.tuid for pointer in unique], [3, 4])
        self.assertEqual(unique[0].offset, 10)
        self.assertEqual(duplicates, 1)

    def test_uses_game_scope_only_for_global_asset_ids(self) -> None:
        r3 = get_game_adapter("RESISTANCE_3")
        rfom = get_game_adapter("RESISTANCE_FOM")

        self.assertEqual(
            _asset_catalog_path(r3, "mine", AssetKind.FOLIAGE),
            "Resistance 3/Foliage",
        )
        self.assertEqual(
            _asset_relative_file(
                r3,
                ("mine",),
                AssetKind.FOLIAGE,
                7,
            ).as_posix(),
            "resistance_3/_shared/foliage/"
            "foliage_0000000000000007.blend",
        )
        self.assertEqual(
            _asset_catalog_path(rfom, "level20", AssetKind.SHRUB),
            "Resistance - Fall of Man/level20/Shrubs",
        )
        self.assertEqual(
            _asset_relative_file(
                rfom,
                ("level20",),
                AssetKind.SHRUB,
                7,
            ).as_posix(),
            "resistance_fom/level20/shrub/"
            "shrub_0000000000000007.blend",
        )

    def test_asset_cache_defaults_to_complete_mobys_and_vegetation(
        self,
    ) -> None:
        options = AssetCacheOptions()

        self.assertTrue(options.source_default_parts)
        self.assertTrue(options.vegetation)

    def test_transaction_rolls_back_installed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            staged_one = staging / "one"
            staged_two = staging / "two"
            staged_one.write_text("new-one", encoding="utf-8")
            staged_two.write_text("new-two", encoding="utf-8")
            final_one = root / "final-one"
            final_one.write_text("old-one", encoding="utf-8")
            blocker = root / "blocker"
            blocker.write_text("not-a-directory", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                _transactional_replace(
                    (
                        (staged_one, final_one),
                        (staged_two, blocker / "final-two"),
                    ),
                    (),
                    staging_root=staging,
                )

            self.assertEqual(
                final_one.read_text(encoding="utf-8"),
                "old-one",
            )

    def test_catalog_rebuild_prunes_stale_insomniac_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "existing.cats.txt"
            output = root / "output.cats.txt"
            existing.write_text(
                "VERSION 1\n\n"
                f"{uuid.uuid4()}:Resistance 3/mine/Actions:Actions\n"
                f"{uuid.uuid4()}:Resistance: Fall of Man/"
                "level20/Foliage:Foliage\n"
                f"{uuid.uuid4()}:User: Library/Props:Props\n",
                encoding="utf-8",
            )
            path = "Resistance 3/Mobys"
            catalog_id = uuid.uuid4()

            _write_catalog_file(
                existing,
                output,
                {path: (catalog_id, "Mobys")},
            )

            text = output.read_text(encoding="utf-8")
            self.assertNotIn("Resistance 3/mine/Actions", text)
            self.assertNotIn(
                "Resistance: Fall of Man/level20/Foliage",
                text,
            )
            self.assertIn("User: Library/Props", text)
            self.assertIn(f"{catalog_id}:{path}:Mobys", text)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
