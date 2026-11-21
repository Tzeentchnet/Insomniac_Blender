import json
import tempfile
import unittest
from pathlib import Path

from insomniac_asset_io.games.catalog import (
    load_catalog as load_game_catalog,
)
from insomniac_asset_io.games.resistance_2.catalog import (
    filter_catalog_assets,
    load_catalog,
)
from insomniac_asset_io.games.resistance_2.formats import AssetKind


class Resistance2CatalogTests(unittest.TestCase):
    def test_loads_bundled_resistance_fom_usa_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_game_catalog(
                Path(temporary),
                "RESISTANCE_FOM",
            )

        self.assertEqual(catalog.region, "USA BCUS98107 v1.00")
        self.assertEqual(len(catalog.levels), 41)
        self.assertIn("level20", catalog.levels)
        self.assertEqual(len(catalog.assets), 36321)
        self.assertEqual(
            (
                sum(asset.kind is AssetKind.SHRUB for asset in catalog.assets),
                sum(
                    asset.kind is AssetKind.FOLIAGE
                    for asset in catalog.assets
                ),
            ),
            (184, 103),
        )
        level20 = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            level="level20",
        )
        self.assertEqual(len(level20), 1032)
        self.assertTrue(
            all(asset.identifier.count(":") == 2 for asset in level20)
        )

    def test_loads_bundled_usa_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_catalog(Path(temporary))

        self.assertEqual(catalog.region, "USA")
        self.assertEqual(len(catalog.levels), 27)
        self.assertEqual(catalog.levels[0], "lobby")
        self.assertEqual(len(catalog.assets), 7588)
        self.assertEqual(catalog.assets[0].identifier, "MOBY:00897CA26A876DEF")
        self.assertEqual(
            catalog.asset("MOBY:00897CA26A876DEF").levels[0],
            "lobby",
        )
        asset = catalog.asset("MOBY:00897CA26A876DEF")
        self.assertEqual(asset.display_name, "Mp Scientist Head")
        self.assertTrue(asset.label.startswith("Mp Scientist Head - Moby "))
        self.assertIn("mp_scientist_head.entity.irb", asset.description)
        self.assertEqual(
            asset.selector_label(show_tuid=False, show_owner=False),
            "Mp Scientist Head",
        )
        self.assertEqual(
            asset.selector_label(show_tuid=True, show_owner=False),
            "Mp Scientist Head - Moby 0x00897CA26A876DEF",
        )

    def test_filters_source_paths_and_duplicate_display_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_catalog(Path(temporary))

        architecture = filter_catalog_assets(
            catalog.assets,
            "entities objects architecture nb_lobby_skyline_1",
            hide_duplicates=True,
        )
        self.assertEqual(
            [asset.identifier for asset in architecture],
            ["TIE:EEA01C826A7525C0"],
        )
        by_tuid = filter_catalog_assets(
            catalog.assets,
            "0xEEA01C826A7525C0",
            hide_duplicates=True,
        )
        self.assertEqual(by_tuid, architecture)

        hidden_duplicates = filter_catalog_assets(
            catalog.assets,
            "tentacle.entity.irb",
            hide_duplicates=True,
        )
        shown_duplicates = filter_catalog_assets(
            catalog.assets,
            "tentacle.entity.irb",
            hide_duplicates=False,
        )
        self.assertEqual(len(hidden_duplicates), 1)
        self.assertEqual(len(shown_duplicates), 2)
        self.assertEqual(
            len(
                filter_catalog_assets(
                    catalog.assets,
                    "",
                    hide_duplicates=True,
                )
            ),
            7585,
        )

        test_tentacle = filter_catalog_assets(
            catalog.assets,
            "entities/test/tentacle",
            hide_duplicates=True,
        )
        self.assertEqual(
            [asset.identifier for asset in test_tentacle],
            ["MOBY:C33226B6781761E1"],
        )

    def test_filters_by_kind_and_level(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_catalog(Path(temporary))

        mobys = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.MOBY,
        )
        ties = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.TIE,
        )
        shrubs = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.SHRUB,
        )
        foliage = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.FOLIAGE,
        )
        materials = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.SHADER,
        )
        self.assertEqual(
            (
                len(mobys),
                len(ties),
                len(shrubs),
                len(foliage),
                len(materials),
            ),
            (923, 3207, 77, 26, 3355),
        )
        self.assertTrue(all(asset.kind is AssetKind.MOBY for asset in mobys))
        self.assertTrue(
            all(asset.kind is AssetKind.SHRUB for asset in shrubs)
        )
        self.assertTrue(
            all(asset.kind is AssetKind.FOLIAGE for asset in foliage)
        )

        lobby = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            level="lobby",
        )
        self.assertEqual(len(lobby), 264)
        self.assertTrue(all("lobby" in asset.levels for asset in lobby))

        # Filters combine, and most model geometry lives outside the lobby.
        chicago_ties = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            kind=AssetKind.TIE,
            level="chicago",
        )
        self.assertTrue(chicago_ties)
        self.assertTrue(
            all(
                asset.kind is AssetKind.TIE and "chicago" in asset.levels
                for asset in chicago_ties
            )
        )
        self.assertEqual(
            filter_catalog_assets(
                catalog.assets,
                "nb_lobby_skyline_1",
                hide_duplicates=True,
                kind=AssetKind.MOBY,
            ),
            (),
        )

    def test_prefers_local_regenerated_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "resistance_2" / "disc_catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "game": "RESISTANCE_2",
                        "region": "Test",
                        "names": {
                            "MOBY:0000000000001234": (
                                "entities/example/test_asset/"
                                "test_asset.entity.irb"
                            ),
                            "SHRUB:0000000000002468": (
                                "props/vegetation/test_shrub.entity.irb"
                            ),
                            "SHADER:0000000000009ABC": "example_material",
                        },
                        "levels": [
                            {
                                "id": "test_level",
                                "mobys": ["0x0000000000001234"],
                                "ties": ["0x0000000000005678"],
                                "shrubs": ["0x0000000000002468"],
                                "foliage": ["0x0000000000001357"],
                                "materials": ["0x0000000000009ABC"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_catalog(root)

        self.assertEqual(catalog.region, "Test")
        self.assertEqual(catalog.levels, ("test_level",))
        self.assertEqual(
            {asset.identifier for asset in catalog.assets},
            {
                "MOBY:0000000000001234",
                "TIE:0000000000005678",
                "SHRUB:0000000000002468",
                "FOLIAGE:0000000000001357",
                "SHADER:0000000000009ABC",
            },
        )
        moby = catalog.asset("MOBY:0000000000001234")
        self.assertEqual(moby.source_name, "entities/example/test_asset/test_asset.entity.irb")
        self.assertEqual(moby.display_name, "Test Asset")
        self.assertEqual(
            moby.label,
            "Test Asset - Moby 0x0000000000001234 - test_level",
        )
        tie = catalog.asset("TIE:0000000000005678")
        self.assertIsNone(tie.source_name)
        self.assertEqual(
            tie.label,
            "Tie 0x0000000000005678 - test_level",
        )
        shrub = catalog.asset("SHRUB:0000000000002468")
        self.assertEqual(shrub.kind, AssetKind.SHRUB)
        self.assertEqual(shrub.display_name, "Test Shrub")
        foliage = catalog.asset("FOLIAGE:0000000000001357")
        self.assertEqual(foliage.kind, AssetKind.FOLIAGE)
        self.assertEqual(
            foliage.label,
            "Foliage 0x0000000000001357 - test_level",
        )
        material = catalog.asset("SHADER:0000000000009ABC")
        self.assertEqual(material.kind, AssetKind.SHADER)
        self.assertEqual(material.display_name, "Example Material")
        self.assertEqual(
            material.identity_label,
            "Material 0x0000000000009ABC",
        )

    def test_schema_one_catalog_without_names_remains_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "resistance_2" / "disc_catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "game": "RESISTANCE_2",
                        "levels": [
                            {
                                "id": "legacy",
                                "mobys": ["0x0000000000001234"],
                                "ties": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_catalog(root)

        asset = catalog.asset("MOBY:0000000000001234")
        self.assertIsNone(asset.source_name)
        self.assertEqual(
            asset.label,
            "Moby 0x0000000000001234 - legacy",
        )
        self.assertEqual(catalog.texture_owners, ())

    def test_loads_texture_owner_metadata_and_indexes_authored_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "resistance_2" / "disc_catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "game": "RESISTANCE_2",
                        "disc": {
                            "size": 1234,
                            "modified_ns": 5678,
                            "title_id": "BCUS98120",
                            "version": "01.00",
                        },
                        "levels": [
                            {
                                "id": "owner",
                                "mobys": [],
                                "ties": [],
                            }
                        ],
                        "texture_index": {
                            "0x12345678": [
                                {
                                    "tuid": "0xABCDEF0112345678",
                                    "format_code": 0x88,
                                    "mip_count": 9,
                                    "width": 256,
                                    "height": 128,
                                    "length": 32768,
                                    "sha256": "a" * 64,
                                    "owners": ["owner"],
                                    "paths": [
                                        r"Art\Fixture\Texture_C",
                                    ],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_catalog(root)

        self.assertEqual(catalog.disc.size, 1234)
        exact = catalog.texture_candidates(0x1234_5678)
        by_path = catalog.texture_candidates(
            0xFFFF_FFFF,
            "art/fixture/texture_c",
        )
        self.assertEqual(exact, by_path)
        self.assertEqual(exact[0].level, "owner")
        self.assertEqual(exact[0].tuid, 0xABCD_EF01_1234_5678)
        self.assertEqual(
            exact[0].lookup_paths,
            ("art/fixture/texture_c",),
        )
        self.assertEqual(exact[0].payload_sha256, "a" * 64)

    def test_tod_catalog_keeps_level_local_ids_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "ratchet_tod" / "disc_catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "game": "RATCHET_TOD",
                        "region": "Test",
                        "names": {},
                        "levels": [
                            {
                                "id": "level_a",
                                "mobys": ["0x0000000000000001"],
                                "ties": [],
                                "shrubs": ["0x0000000000000002"],
                                "foliage": ["0x0000000000000003"],
                                "materials": ["0x0000000000000001"],
                            },
                            {
                                "id": "level_b",
                                "mobys": ["0x0000000000000001"],
                                "ties": [],
                                "shrubs": ["0x0000000000000002"],
                                "foliage": ["0x0000000000000003"],
                                "materials": ["0x0000000000000001"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_game_catalog(
                root,
                "RATCHET_TOD",
            )

        self.assertEqual(len(catalog.assets), 8)
        self.assertEqual(
            {asset.identifier for asset in catalog.assets},
            {
                "MOBY:level_a:0000000000000001",
                "MOBY:level_b:0000000000000001",
                "SHRUB:level_a:0000000000000002",
                "SHRUB:level_b:0000000000000002",
                "FOLIAGE:level_a:0000000000000003",
                "FOLIAGE:level_b:0000000000000003",
                "SHADER:level_a:0000000000000001",
                "SHADER:level_b:0000000000000001",
            },
        )
        self.assertTrue(
            all(len(asset.levels) == 1 for asset in catalog.assets)
        )

    def test_loads_bundled_tod_usa_v0200_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_game_catalog(
                Path(temporary),
                "RATCHET_TOD",
            )

        self.assertEqual(catalog.region, "USA v02.00")
        self.assertEqual(len(catalog.levels), 20)
        self.assertEqual(len(catalog.assets), 15629)
        self.assertEqual(
            sum(asset.kind is AssetKind.MOBY for asset in catalog.assets),
            3012,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.TIE for asset in catalog.assets),
            2048,
        )
        self.assertEqual(
            sum(
                asset.kind is AssetKind.FOLIAGE
                for asset in catalog.assets
            ),
            14,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.SHRUB for asset in catalog.assets),
            34,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.SHADER for asset in catalog.assets),
            10521,
        )
        self.assertTrue(
            all(len(asset.levels) == 1 for asset in catalog.assets)
        )

    def test_loads_bundled_resistance_3_usa_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_game_catalog(
                Path(temporary),
                "RESISTANCE_3",
            )

        self.assertEqual(catalog.game_id, "RESISTANCE_3")
        self.assertEqual(catalog.game_label, "Resistance 3")
        self.assertEqual(catalog.region, "USA")
        self.assertEqual(len(catalog.levels), 27)
        self.assertEqual(catalog.levels[0], "lobby")
        self.assertEqual(len(catalog.assets), 10482)
        self.assertEqual(
            sum(asset.kind is AssetKind.MOBY for asset in catalog.assets),
            969,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.TIE for asset in catalog.assets),
            4851,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.SHRUB for asset in catalog.assets),
            70,
        )
        self.assertEqual(
            sum(
                asset.kind is AssetKind.FOLIAGE
                for asset in catalog.assets
            ),
            13,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.SHADER for asset in catalog.assets),
            4579,
        )
        self.assertIn("Resistance 3", catalog.assets[0].description)


if __name__ == "__main__":
    unittest.main()
