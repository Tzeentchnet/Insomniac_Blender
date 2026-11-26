from __future__ import annotations

import os
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.registry import get_game_adapter
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_fom.reader import (
    ResistanceFomReader,
)

REAL_SOURCE = os.environ.get("RFOM_LEVEL_SOURCE")
REAL_ISO = os.environ.get("RFOM_ISO_PATH")


@unittest.skipUnless(REAL_ISO, "RFOM_ISO_PATH is not set")
class RealResistanceFomIsoTests(unittest.TestCase):
    def test_validates_disc_identity_and_level_inventory(self) -> None:
        adapter = get_game_adapter("RESISTANCE_FOM")
        values = adapter.validate_iso(Path(REAL_ISO))

        self.assertEqual(values["TITLE_ID"], "BCUS98107")
        self.assertEqual(values["VERSION"], "01.00")
        levels = adapter.list_levels(Path(REAL_ISO))
        self.assertEqual(len(levels), 41)
        self.assertIn("level20", levels)


@unittest.skipUnless(REAL_ISO, "RFOM_ISO_PATH is not set")
class RealResistanceFomDebugNameTests(unittest.TestCase):
    def test_debug_names_match_single_group_gameplay(self) -> None:
        from insomniac_asset_io.core.binary import IghwFile
        from insomniac_asset_io.core.iso import _Iso9660Image
        from insomniac_asset_io.core.psarc import PsarcArchive
        from insomniac_asset_io.games.resistance_fom.debug_names import (
            parse_debug_names,
        )

        descriptors = {
            "moby": 0x40,
            "path": 0x50,
            "trigger": 0x60,
            "effect": 0x70,
            "marker": 0x80,
            "moby_group": 0x90,
            "area": 0xA0,
        }
        adapter = get_game_adapter("RESISTANCE_FOM")
        single = multi = named = 0
        with _Iso9660Image(Path(REAL_ISO)) as image:
            with PsarcArchive.from_stream(
                image.open_member(adapter.iso_container_member),
                label="GAME.PSARC",
            ) as archive:
                levels = sorted(
                    {
                        entry.name.split("/")[2]
                        for entry in archive.entries
                        if entry.name.casefold().endswith(
                            "/ps3levelmain.dat"
                        )
                    }
                )
                for level in levels:
                    with self.subTest(level=level):
                        names = parse_debug_names(
                            archive.read(
                                f"packed/levels/{level}/ps3debug.dat"
                            ),
                            label=level,
                        )
                        gameplay = IghwFile(
                            archive.read(
                                f"packed/levels/{level}/ps3gameplay.dat"
                            ),
                            label=level,
                        )
                        section = gameplay.require_section(0x25000)
                        if gameplay.u32(section.offset + 0x24) != 1:
                            multi += 1
                            continue
                        single += 1
                        instances = gameplay.u32(section.offset + 0x20)
                        for family, offset in descriptors.items():
                            count = gameplay.u32(instances + offset + 4)
                            self.assertEqual(
                                len(names.get(family, ())),
                                count,
                                family,
                            )
                            named += count
        self.assertEqual(len(levels), 41)
        self.assertEqual(multi, 4)
        self.assertEqual(single, 37)
        self.assertGreater(named, 0)

    def test_level20_names_gameplay_records(self) -> None:
        import tempfile

        adapter = get_game_adapter("RESISTANCE_FOM")
        with tempfile.TemporaryDirectory() as temporary:
            prepared = adapter.prepare_level(
                Path(REAL_ISO),
                "level20",
                temporary,
            )
            self.assertTrue((prepared / "ps3debug.dat").is_file())
            with GameSource.open(prepared) as source:
                reader = ResistanceFomReader(source)
                region = reader.read_gameplay_regions()[0]
                self.assertEqual(
                    region.moby_instances[0].name,
                    "1A_Hyb_FirstOnslaught_1",
                )
                self.assertEqual(
                    region.moby_instances[0].source_guid,
                    "2ba161ab-87cb-41e5-a591-103772389c54",
                )
                self.assertEqual(
                    region.paths[0].source_name,
                    "1A_FlyAwayVTOL_PATH_1",
                )
                # Areas carry their own authored name: an independent
                # check that the debug table is aligned.
                self.assertTrue(
                    all(area.source_name == area.name for area in region.areas)
                )
                self.assertEqual(len(region.areas), 93)
                self.assertEqual(reader.consume_import_warnings(), ())


@unittest.skipUnless(REAL_SOURCE, "RFOM_LEVEL_SOURCE is not set")
class RealResistanceFomTests(unittest.TestCase):
    def test_decodes_representative_level20_chains(self) -> None:
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = ResistanceFomReader(source)

            self.assertEqual(
                len(reader.asset_pointers(AssetKind.MOBY)),
                155,
            )
            self.assertEqual(
                len(reader.asset_pointers(AssetKind.TIE)),
                222,
            )
            self.assertEqual(
                len(reader.asset_pointers(AssetKind.SHADER)),
                633,
            )
            self.assertEqual(len(reader.texture_infos), 1384)

            moby = reader.read_moby(0x03C5)
            self.assertEqual(len(moby.meshes), 3)
            self.assertIsNotNone(moby.skeleton)
            self.assertEqual(moby.skeleton.bone_count, 76)
            clip = reader.read_animations(moby)[0]
            self.assertEqual(
                clip.name,
                "vtol_L20_crashed_building_roof",
            )
            self.assertEqual(clip.frame_count, 136)
            self.assertEqual(clip.frame_rate, 30.0)
            self.assertTrue(np.isfinite(clip.translations).all())
            self.assertTrue(np.isfinite(clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(clip.scales).all())

            shader = reader.read_shader(moby.shader_tuids[0])
            self.assertIsNotNone(shader.albedo_texture_id)
            texture = reader.read_texture(shader.albedo_texture_id)
            self.assertEqual(texture.rgba.shape, (512, 512, 4))

            tie = reader.read_tie(0x014E4A00)
            self.assertEqual(len(tie.meshes), 7)
            self.assertTrue(tie.shader_tuids)

            zone = reader.read_zones()[0]
            self.assertEqual(len(zone.terrain_meshes), 1675)
            self.assertEqual(len(zone.tie_instances), 4247)

            gameplay = reader.read_gameplay_regions()[0]
            self.assertEqual(gameplay.name, "default")
            self.assertEqual(len(gameplay.moby_instances), 533)
            self.assertEqual(len(gameplay.paths), 9)
            self.assertEqual(len(gameplay.triggers), 480)
            self.assertEqual(len(gameplay.effects), 43)
            self.assertEqual(len(gameplay.markers), 1123)
            self.assertEqual(len(gameplay.moby_groups), 65)
            self.assertEqual(len(gameplay.areas), 93)
            self.assertEqual(
                sum(len(path.points) for path in gameplay.paths),
                53,
            )
            self.assertEqual(
                Counter(trigger.shape_kind for trigger in gameplay.triggers),
                {0: 466, 1: 2, 2: 12},
            )
            self.assertEqual(gameplay.effects[0].effect_class, 1)
            self.assertEqual(gameplay.effects[0].payload_size, 64)
            self.assertEqual(gameplay.markers[0].marker_class, 0x43E)
            self.assertEqual(gameplay.markers[0].payload_size, 464)
            self.assertEqual(
                gameplay.moby_groups[0].moby_indices,
                (105, 104),
            )
            self.assertEqual(
                gameplay.areas[0].name,
                "00_AllyTriggersBottomOfHill",
            )
            self.assertEqual(
                gameplay.areas[0].trigger_indices,
                (45, 41, 42, 47, 43, 0),
            )

            collision = reader.read_level_collision()
            validate_collision_scene(
                collision,
                label="RFoM level20 collision",
            )
            self.assertEqual(len(collision.instances), 13291)
            self.assertEqual(len(collision.meshes), 2491)
            self.assertEqual(
                sum(len(mesh.positions) for mesh in collision.meshes),
                252515,
            )
            self.assertEqual(
                sum(len(mesh.indices) for mesh in collision.meshes),
                363870,
            )
            self.assertEqual(
                {
                    kind: sum(
                        mesh.source_kind == kind
                        for mesh in collision.meshes
                    )
                    for kind in ("TERRAIN", "TIE", "PLACED")
                },
                {"TERRAIN": 995, "TIE": 1491, "PLACED": 5},
            )


if __name__ == "__main__":
    unittest.main()
