from __future__ import annotations

import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.iso import list_game_levels
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.ratchet_tod.reader import RatchetTodReader
from insomniac_asset_io.games.registry import get_game_adapter
from insomniac_asset_io.games.resistance_2.formats import AssetKind

REAL_SOURCE = os.environ.get("TOD_LEVEL_SOURCE")
REAL_ISO = os.environ.get("TOD_ISO_PATH")


@unittest.skipUnless(REAL_ISO, "TOD_ISO_PATH is not set")
class RealRatchetTodIsoTests(unittest.TestCase):
    def test_validates_disc_identity_and_level_inventory(self) -> None:
        adapter = get_game_adapter("RATCHET_TOD")
        values = adapter.validate_iso(Path(REAL_ISO))

        self.assertEqual(values["TITLE_ID"], "BCUS98127")
        self.assertEqual(values["VERSION"], "02.00")
        levels = list_game_levels(Path(REAL_ISO), game_label=adapter.label)
        self.assertEqual(len(levels), 20)
        self.assertIn("metropolis", levels)

    def test_debug_names_cover_every_level(self) -> None:
        from insomniac_asset_io.core.binary import IghwFile
        from insomniac_asset_io.core.iso import _Iso9660Image
        from insomniac_asset_io.core.psarc import PsarcArchive
        from insomniac_asset_io.games.ratchet_tod.debug_names import (
            INSTANCE_SECTIONS,
            PROTOTYPE_SECTIONS,
            debug_dat_iso_member,
            parse_debug_names,
        )

        main_sections = {
            AssetKind.MOBY: 0xD100,
            AssetKind.TIE: 0x3400,
            AssetKind.FOLIAGE: 0xA200,
            AssetKind.SHRUB: 0xB100,
        }
        gameplay_sections = {
            "moby": 0x7340,
            "path": 0x7700,
            "volume": 0x7740,
            "clue": 0x7780,
            "controller": 0x77C0,
            "pod": 0x7900,
            "area": 0x7940,
            "scent": 0x7980,
        }
        from insomniac_asset_io.games.class_names import (
            class_name,
            parse_tod_class_names,
            read_class_id,
        )
        from insomniac_asset_io.games.ratchet_tod.debug_names import (
            GAME_DEBUG_DAT_MEMBER,
        )

        adapter = get_game_adapter("RATCHET_TOD")
        levels = list_game_levels(Path(REAL_ISO), game_label=adapter.label)
        totals: Counter[str] = Counter()
        with _Iso9660Image(Path(REAL_ISO)) as image:
            class_names = parse_tod_class_names(
                image.read(GAME_DEBUG_DAT_MEMBER, max_size=64 * 1024 * 1024),
                label="game DEBUG.DAT",
            )
            self.assertEqual(len(class_names), 1_754)
            for level in levels:
                with self.subTest(level=level):
                    names = parse_debug_names(
                        image.read(
                            debug_dat_iso_member(level),
                            max_size=64 * 1024 * 1024,
                        ),
                        label=level,
                    )
                    with PsarcArchive.from_stream(
                        image.open_member(
                            "PS3_GAME/USRDIR/PACKED/LEVELS/"
                            f"{level}/LEVEL_CACHED.PSARC"
                        ),
                        label=level,
                    ) as archive:
                        # Archive paths spell level folders with spaces.
                        entries = {
                            entry.name.casefold().rsplit("/", 1)[-1]: entry
                            for entry in archive.entries
                            if entry.name.casefold().startswith("built/levels/")
                        }
                        main = IghwFile(
                            archive.read(entries["main.dat"]),
                            label=f"{level}/main.dat",
                        )
                        gameplay = IghwFile(
                            archive.read(entries["gameplay.dat"]),
                            label=f"{level}/gameplay.dat",
                        )
                    for kind in PROTOTYPE_SECTIONS:
                        section = main.section(main_sections[kind])
                        self.assertEqual(
                            len(names.prototypes.get(kind, ())),
                            section.count if section else 0,
                        )
                        totals[kind.name] += section.count if section else 0
                    for family, section_id in gameplay_sections.items():
                        section = gameplay.section(section_id)
                        self.assertEqual(
                            len(names.instances.get(family, ())),
                            section.count if section else 0,
                        )
                        totals[family] += section.count if section else 0
                    self.assertEqual(
                        len(names.shaders),
                        main.require_section(0x5000).count,
                    )
                    self.assertEqual(
                        len(names.instances["tie"]),
                        main.require_section(0x9240).count,
                    )
                    mobys = gameplay.section(0x7340)
                    for index in range(mobys.count if mobys else 0):
                        class_id = read_class_id(
                            gameplay,
                            gameplay.u32(mobys.offset + index * 0x48),
                            label=level,
                        )
                        if class_id is None:
                            totals["unclassed"] += 1
                            continue
                        self.assertIsNotNone(
                            class_name(class_names, class_id)
                        )
                        totals["classed"] += 1
                    self.assertLessEqual(
                        set(names.instances),
                        set(INSTANCE_SECTIONS),
                    )
        self.assertEqual(totals["moby"], 18_251)
        self.assertEqual(totals["MOBY"], 3_012)
        self.assertEqual(totals["TIE"], 2_048)
        self.assertEqual(totals["classed"], 17_798)
        self.assertEqual(totals["unclassed"], 453)

    def test_names_kerchu_city_from_debug_dat(self) -> None:
        from insomniac_asset_io.games.ratchet_tod.debug_names import (
            prepare_debug_dat,
            prepare_game_debug_dat,
        )

        adapter = get_game_adapter("RATCHET_TOD")
        with tempfile.TemporaryDirectory() as temporary:
            prepared = adapter.prepare_catalog_level(
                Path(REAL_ISO),
                "kerchu_city",
                temporary,
                validate_iso=False,
            )
            debug_dat = prepare_debug_dat(
                Path(REAL_ISO),
                "kerchu_city",
                prepared,
                game_label=adapter.label,
            )
            self.assertIsNotNone(debug_dat)
            self.assertIsNotNone(
                prepare_game_debug_dat(
                    Path(REAL_ISO),
                    prepared,
                    game_label=adapter.label,
                )
            )
            with GameSource.open(prepared) as source:
                reader = RatchetTodReader(source)
                tie = reader.asset_pointers(AssetKind.TIE)[0].tuid
                self.assertEqual(
                    reader.read_asset_name(AssetKind.TIE, tie),
                    "object/plant/tree/palmtree",
                )
                self.assertEqual(
                    reader.read_asset_name(AssetKind.MOBY, 0),
                    "object/celestial/galactic_map/gmp_12",
                )
                zone = reader.read_zones()[0]
                self.assertEqual(
                    zone.shrub_instances[0].name,
                    "rockgrass_3_SHRUB8",
                )
                self.assertEqual(
                    zone.tie_instances[0].source_guid,
                    "48577f73-3e0a-43e8-b5be-5608b1c28779",
                )
                gameplay = reader.read_gameplay_regions()[0]
                self.assertEqual(
                    gameplay.moby_instances[0].name,
                    "gmap_zordoom",
                )
                self.assertEqual(gameplay.moby_instances[0].class_id, 1082)
                self.assertEqual(
                    gameplay.moby_instances[0].class_name,
                    "GMapPlanet_CLASS",
                )
                self.assertEqual(reader.read_shader(0).lookup_path, "gmp_12_surface")
                self.assertEqual(reader.consume_import_warnings(), ())

    def test_decodes_kerchu_city_shrubs(self) -> None:
        adapter = get_game_adapter("RATCHET_TOD")
        with tempfile.TemporaryDirectory() as temporary:
            prepared = adapter.prepare_catalog_level(
                Path(REAL_ISO),
                "kerchu_city",
                temporary,
                validate_iso=False,
            )
            # Level imports also copy the game-wide DEBUG.DAT, which holds
            # the collision material names; catalog preparation does not.
            from insomniac_asset_io.games.ratchet_tod.debug_names import (
                prepare_game_debug_dat,
            )

            prepare_game_debug_dat(
                Path(REAL_ISO),
                prepared,
                game_label=adapter.label,
            )
            with GameSource.open(prepared) as source:
                reader = RatchetTodReader(source)
                pointers = reader.asset_pointers(AssetKind.SHRUB)
                self.assertEqual(len(pointers), 15)

                vertex_count = 0
                triangle_count = 0
                alpha_modes: Counter[str] = Counter()
                for pointer in pointers:
                    shrub = reader.read_shrub(pointer.tuid)
                    self.assertEqual(len(shrub.meshes), 1)
                    mesh = shrub.meshes[0]
                    self.assertTrue(np.isfinite(mesh.positions).all())
                    self.assertTrue(np.isfinite(mesh.uvs).all())
                    self.assertLess(int(mesh.indices.max()), len(mesh.positions))
                    vertex_count += len(mesh.positions)
                    triangle_count += len(mesh.indices) // 3

                    shader = reader.read_shader(shrub.shader_tuids[0])
                    alpha_modes[shader.alpha_mode] += 1
                    self.assertIsNotNone(shader.albedo_texture_id)

                self.assertEqual(vertex_count, 4387)
                self.assertEqual(triangle_count, 5998)
                self.assertEqual(
                    alpha_modes,
                    {"MASK": 10, "OPAQUE": 4, "BLEND": 1},
                )

                vine = reader.read_shrub(0x017EB070).meshes[0]
                np.testing.assert_allclose(vine.uvs.min(axis=0), (0.0, -1.0))
                np.testing.assert_allclose(vine.uvs.max(axis=0), (1.0, 0.0))

                zone = reader.read_zones()[0]
                self.assertEqual(len(zone.shrub_instances), 11857)
                self.assertEqual(zone.unsupported_shrub_instances, 0)
                self.assertTrue(
                    all(
                        np.isfinite(instance.matrix).all()
                        for instance in zone.shrub_instances
                    )
                )
                counts = Counter(
                    instance.asset_tuid
                    for instance in zone.shrub_instances
                )
                self.assertEqual(
                    [counts[pointer.tuid] for pointer in pointers],
                    [
                        559,
                        108,
                        187,
                        318,
                        37,
                        9329,
                        69,
                        312,
                        83,
                        6,
                        4,
                        133,
                        8,
                        696,
                        8,
                    ],
                )

                collision = reader.read_level_collision()
                validate_collision_scene(
                    collision,
                    label="Kerchu City collision",
                )
                self.assertEqual(len(collision.instances), 9913)
                self.assertEqual(len(collision.meshes), 1058)
                self.assertEqual(
                    Counter(
                        instance.metadata["flags"]
                        for instance in collision.instances
                    ),
                    {0x20: 9224, 0x00: 689},
                )
                self.assertEqual(
                    Counter(
                        mesh.source_kind for mesh in collision.meshes
                    ),
                    {"TERRAIN": 689, "TIE": 369},
                )
                self.assertEqual(
                    sorted(collision.material_names),
                    [0, 1, 8, 10, 12, 16, 20, 29, 30, 31, 36, 44, 46, 47,
                     58, 59],
                )
                self.assertEqual(
                    collision.material_names[8],
                    "COLL_MATERIAL_QUICKSAND_GELATONIUM",
                )
                self.assertEqual(
                    collision.material_names[44],
                    "COLL_MATERIAL_GRASS_DEFAULT",
                )


@unittest.skipUnless(REAL_SOURCE, "TOD_LEVEL_SOURCE is not set")
class RealRatchetTodTests(unittest.TestCase):
    def test_decodes_representative_metropolis_chains(self) -> None:
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = RatchetTodReader(source)

            self.assertGreaterEqual(
                len(reader.asset_pointers(AssetKind.MOBY)),
                0x17,
            )
            complex_moby = reader.read_moby(0x16)
            self.assertTrue(complex_moby.meshes)
            self.assertIsNotNone(complex_moby.skeleton)
            complex_clip = reader.read_animations(complex_moby)[0]
            self.assertEqual(complex_clip.name, "crumbling")
            self.assertEqual(complex_clip.frame_count, 151)
            self.assertEqual(complex_clip.bone_count, 38)
            self.assertEqual(complex_clip.frame_rate, 30.0)
            self.assertTrue(np.isfinite(complex_clip.translations).all())
            self.assertTrue(np.isfinite(complex_clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(complex_clip.scales).all())

            pair_moby = reader.read_moby(0x02)
            pair_clip = reader.read_animations(pair_moby)[0]
            self.assertEqual(pair_clip.name, "animate_spin")
            self.assertEqual(pair_clip.frame_count, 4000)
            self.assertEqual(pair_clip.bone_count, 4)
            self.assertEqual(pair_clip.frame_rate, 15.0)
            self.assertTrue(np.isfinite(pair_clip.translations).all())
            self.assertTrue(np.isfinite(pair_clip.rotations_xyzw).all())
            self.assertTrue(np.isfinite(pair_clip.scales).all())

            shader = reader.read_shader(complex_moby.shader_tuids[0])
            self.assertIsNotNone(shader.albedo_texture_id)
            texture = reader.read_texture(shader.albedo_texture_id)
            self.assertEqual(texture.rgba.shape, (512, 256, 4))

            tie = reader.read_tie(0x00FC7A00)
            self.assertTrue(tie.meshes)
            self.assertTrue(tie.shader_tuids)

            foliage_pointers = reader.asset_pointers(AssetKind.FOLIAGE)
            self.assertEqual(len(foliage_pointers), 2)
            for pointer in foliage_pointers:
                foliage = reader.read_foliage(pointer.tuid)
                self.assertEqual(len(foliage.meshes), 2)
                self.assertTrue(
                    all(
                        np.isfinite(mesh.positions).all()
                        for mesh in foliage.meshes
                    )
                )
                foliage_shader = reader.read_shader(
                    foliage.shader_tuids[0]
                )
                self.assertEqual(foliage_shader.alpha_mode, "MASK")
                self.assertIsNotNone(foliage_shader.albedo_texture_id)

            zone = reader.read_zones()[0]
            self.assertTrue(zone.terrain_meshes)
            self.assertTrue(zone.tie_instances)
            lit_ties = [
                instance
                for instance in zone.tie_instances
                if instance.lightmap_index is not None
            ]
            self.assertEqual(len(lit_ties), 1728)
            self.assertEqual(
                len({instance.lightmap_index for instance in lit_ties}),
                1728,
            )
            self.assertTrue(
                all(instance.lightmap_scale for instance in lit_ties)
            )
            self.assertEqual(
                sum(
                    mesh.lightmap_index is not None
                    for mesh in zone.terrain_meshes
                ),
                1377,
            )
            self.assertEqual(len(zone.lightmaps), 23)
            uv_animations = reader._uv_animations
            self.assertEqual(
                (
                    len(uv_animations.ufrags),
                    len(uv_animations.ties),
                    len(uv_animations.mobys),
                ),
                (0, 91, 48),
            )
            lit_tie = reader.read_tie(lit_ties[0].asset_tuid)
            self.assertTrue(
                any(mesh.secondary_uvs is not None for mesh in lit_tie.meshes)
            )
            self.assertEqual(len(zone.foliage_instances), 757)
            self.assertEqual(zone.unsupported_foliage_instances, 0)

            cubemaps = reader.read_environment_cubemaps()
            self.assertEqual(len(cubemaps), 1)
            self.assertEqual(cubemaps[0].faces.shape, (6, 32, 32, 4))
            self.assertEqual(cubemaps[0].format_code, 0x85)

            environment = reader.read_level_environment()
            self.assertEqual(environment.version, 2)
            self.assertEqual(environment.light_volume_count, 19)
            np.testing.assert_allclose(
                environment.ambient,
                (0.49, 0.437, 0.257),
                atol=1e-3,
            )
            self.assertEqual(len(environment.lights), 2)
            self.assertEqual(
                (environment.fog.near, environment.fog.far),
                (80.0, 360.0),
            )
            self.assertAlmostEqual(environment.fog.amount, 0.6, places=5)
            np.testing.assert_allclose(
                (
                    environment.bloom.extract_scale,
                    environment.bloom.extract_exponent,
                    environment.bloom.intensity,
                ),
                (0.6, 2.2, 0.6),
                atol=1e-5,
            )

            gameplay = reader.read_gameplay_regions()[0]
            self.assertEqual(gameplay.name, "art")
            self.assertTrue(gameplay.moby_instances)
            self.assertEqual(
                {
                    "paths": len(gameplay.paths),
                    "volumes": len(gameplay.triggers),
                    "clues": len(gameplay.clues),
                    "controllers": len(gameplay.controllers),
                    "pods": len(gameplay.moby_groups),
                    "areas": len(gameplay.areas),
                    "scents": len(gameplay.clue_groups),
                },
                {
                    "paths": 505,
                    "volumes": 902,
                    "clues": 77,
                    "controllers": 465,
                    "pods": 123,
                    "areas": 70,
                    "scents": 1,
                },
            )
            first_clue = gameplay.clues[0]
            self.assertEqual(first_clue.source_name, "gmap_data")
            self.assertEqual(first_clue.class_name, "ClueGalacticMap_CLASS")
            self.assertEqual(gameplay.clues[2].source_name, "volume197")
            self.assertEqual(
                gameplay.clues[2].class_name,
                "ClueFallToDeath_CLASS",
            )
            controller_classes = Counter(
                controller.class_name for controller in gameplay.controllers
            )
            self.assertEqual(controller_classes["CarSpawner_CLASS"], 189)
            self.assertEqual(controller_classes["FxController_CLASS"], 168)
            self.assertTrue(
                all(volume.source_name for volume in gameplay.triggers)
            )

            collision = reader.read_level_collision()
            validate_collision_scene(
                collision,
                label="Metropolis collision",
            )
            self.assertEqual(len(collision.instances), 15247)
            self.assertEqual(len(collision.meshes), 1572)
            self.assertEqual(
                sum(len(mesh.positions) for mesh in collision.meshes),
                93761,
            )
            self.assertEqual(
                sum(len(mesh.indices) for mesh in collision.meshes),
                130230,
            )
            self.assertEqual(
                Counter(
                    instance.metadata["flags"]
                    for instance in collision.instances
                ),
                {0x20: 14804, 0x00: 443},
            )
            self.assertEqual(
                Counter(mesh.source_kind for mesh in collision.meshes),
                {"TIE": 1129, "TERRAIN": 443},
            )


if __name__ == "__main__":
    unittest.main()
