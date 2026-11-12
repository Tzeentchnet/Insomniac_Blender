import json
from pathlib import Path
import tempfile
import unittest

from insomniac_asset_io.games.catalog import (
    load_catalog as load_game_catalog,
)
from insomniac_asset_io.games.resistance_2.catalog import (
    filter_catalog_assets,
    load_catalog,
)
from insomniac_asset_io.games.resistance_2.formats import AssetKind


class Resistance2CatalogTests(unittest.TestCase):
    def test_loads_bundled_usa_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = load_catalog(Path(temporary))

        self.assertEqual(catalog.region, "USA")
        self.assertEqual(len(catalog.levels), 27)
        self.assertEqual(catalog.levels[0], "lobby")
        self.assertEqual(len(catalog.assets), 4130)
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
            4127,
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
        self.assertEqual((len(mobys), len(ties)), (923, 3207))
        self.assertTrue(all(asset.kind is AssetKind.MOBY for asset in mobys))

        lobby = filter_catalog_assets(
            catalog.assets,
            "",
            hide_duplicates=False,
            level="lobby",
        )
        self.assertEqual(len(lobby), 56)
        self.assertTrue(all("lobby" in asset.levels for asset in lobby))

        # Filters combine, and most of the catalog lives outside the lobby.
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
                            )
                        },
                        "levels": [
                            {
                                "id": "test_level",
                                "mobys": ["0x0000000000001234"],
                                "ties": ["0x0000000000005678"],
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
            {"MOBY:0000000000001234", "TIE:0000000000005678"},
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
        self.assertEqual(len(catalog.assets), 5820)
        self.assertEqual(
            sum(asset.kind is AssetKind.MOBY for asset in catalog.assets),
            969,
        )
        self.assertEqual(
            sum(asset.kind is AssetKind.TIE for asset in catalog.assets),
            4851,
        )
        self.assertIn("Resistance 3", catalog.assets[0].description)


if __name__ == "__main__":
    unittest.main()
