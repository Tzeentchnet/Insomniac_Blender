from __future__ import annotations

import os
import unittest
from collections import Counter
from pathlib import Path, PurePosixPath

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_burning_skies.animations import (
    ANIMATION_FRAME_RATE,
    ANIMATION_STREAM_CLASS,
    PackedTrackKind,
    animation_set_clip_names,
    inspect_packed_animation,
    packed_animation_descriptor,
)
from insomniac_asset_io.games.resistance_burning_skies.containers import (
    ResourceInfoPack,
    StreamPack,
)
from insomniac_asset_io.games.resistance_burning_skies.models import decode_smf
from insomniac_asset_io.games.resistance_burning_skies.reader import (
    BurningSkiesReader,
)

REAL_SOURCE = os.environ.get("BURNING_SKIES_ARCHIVE")


@unittest.skipUnless(
    REAL_SOURCE,
    "BURNING_SKIES_ARCHIVE is not set",
)
class RealBurningSkiesTests(unittest.TestCase):
    def test_maps_ellie_animation_metadata_to_stream_payload(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            resource_pack = ResourceInfoPack(
                source.read("levels/Bayonne.rip"),
                label="levels/Bayonne.rip",
            )
            root_counts: Counter[int] = Counter()
            ellie_set = None
            camp_alert = None
            for resource in resource_pack.iter_resources():
                root_counts[resource.root_type] += 1
                if resource.resource_name == "Ellie":
                    ellie_set = resource
                elif resource.resource_name == "Ellie_CampAlert":
                    camp_alert = resource

            self.assertEqual(resource_pack.resource_count, 2_724)
            self.assertEqual(root_counts[0x05], 26)
            self.assertEqual(root_counts[0x08], 690)
            self.assertIsNotNone(ellie_set)
            self.assertIsNotNone(camp_alert)
            assert ellie_set is not None
            assert camp_alert is not None
            clip_names = animation_set_clip_names(ellie_set)
            self.assertEqual(len(clip_names), 64)
            self.assertEqual(clip_names[0], "Ellie_CampAlert")
            self.assertEqual(clip_names[-1], "Ellie_Walk_Start")

            descriptor = packed_animation_descriptor(camp_alert)
            stream_pack = StreamPack(
                source.read("levels/Bayonne_global.spk"),
                label="levels/Bayonne_global.spk",
            )
            matches = [
                entry
                for entry in stream_pack.entries_named(
                    camp_alert.resource_name
                )
                if entry.stream_class == ANIMATION_STREAM_CLASS
                and entry.primary_size == descriptor.primary_size
                and entry.secondary_size == descriptor.secondary_size
            ]
            self.assertEqual(len(matches), 1)
            entry = matches[0]
            self.assertEqual(entry.name_hash, 0x0178DEBF)
            self.assertEqual(entry.primary_size, 0x24A8)
            self.assertEqual(entry.secondary_size, 0x280)
            clip = inspect_packed_animation(
                stream_pack.primary_data(entry),
                label=entry.name,
            )
            ellie = decode_smf(
                source.read("models/CH_BASE_Ellie.smf"),
                label="models/CH_BASE_Ellie.smf",
            )

        self.assertEqual(clip.frame_rate, ANIMATION_FRAME_RATE)
        self.assertEqual(clip.duration, 51.0)
        self.assertEqual(len(clip.tracks), 66)
        self.assertEqual(sum(track.key_count for track in clip.tracks), 690)
        self.assertEqual(
            Counter(track.kind for track in clip.tracks),
            Counter(
                {
                    PackedTrackKind.RAW_0: 65,
                    PackedTrackKind.RAW_1: 1,
                }
            ),
        )
        self.assertEqual(max(track.bone_index for track in clip.tracks), 120)
        self.assertIsNotNone(ellie.skeleton)
        assert ellie.skeleton is not None
        self.assertEqual(ellie.skeleton.bone_count, 121)

    def test_validates_complete_animation_container_corpus(self):
        packed_resource_count = 0
        animation_count = 0
        stream_resource_count = 0
        track_count = 0
        key_count = 0
        max_bone_index = 0
        frame_rates: Counter[int] = Counter()
        track_kinds: Counter[PackedTrackKind] = Counter()
        parsed_stream_packs: set[str] = set()

        with GameSource.open(Path(REAL_SOURCE)) as source:
            rip_paths = sorted(
                path for path in source.paths if path.casefold().endswith(".rip")
            )
            spk_paths = sorted(
                path for path in source.paths if path.casefold().endswith(".spk")
            )
            for rip_path in rip_paths:
                rip_posix = PurePosixPath(rip_path)
                related_stream_packs = [
                    path
                    for path in spk_paths
                    if PurePosixPath(path).parent == rip_posix.parent
                    and (
                        PurePosixPath(path).stem == rip_posix.stem
                        or PurePosixPath(path).stem.startswith(
                            f"{rip_posix.stem}_"
                        )
                    )
                ]
                resource_pack = ResourceInfoPack(
                    source.read(rip_path),
                    label=rip_path,
                )
                packed_resource_count += resource_pack.resource_count
                expected: dict[tuple[str, int, int], None] = {}
                for resource in resource_pack.iter_resources():
                    if resource.root_type != 0x08:
                        continue
                    descriptor = packed_animation_descriptor(resource)
                    identity = (
                        resource.resource_name,
                        descriptor.primary_size,
                        descriptor.secondary_size,
                    )
                    self.assertNotIn(identity, expected)
                    expected[identity] = None
                animation_count += len(expected)

                matched: Counter[tuple[str, int, int]] = Counter()
                for spk_path in related_stream_packs:
                    self.assertNotIn(spk_path, parsed_stream_packs)
                    parsed_stream_packs.add(spk_path)
                    stream_pack = StreamPack(
                        source.read(spk_path),
                        label=spk_path,
                    )
                    stream_resource_count += len(stream_pack.entries)
                    for entry in stream_pack.entries:
                        identity = (
                            entry.name,
                            entry.primary_size,
                            entry.secondary_size,
                        )
                        if (
                            entry.stream_class != ANIMATION_STREAM_CLASS
                            or identity not in expected
                        ):
                            continue
                        matched[identity] += 1
                        clip = inspect_packed_animation(
                            stream_pack.primary_data(entry),
                            label=f"{spk_path}:{entry.name}",
                        )
                        frame_rates[clip.frame_rate] += 1
                        track_count += len(clip.tracks)
                        for track in clip.tracks:
                            track_kinds[track.kind] += 1
                            key_count += track.key_count
                            max_bone_index = max(
                                max_bone_index,
                                track.bone_index,
                            )
                self.assertEqual(
                    matched,
                    Counter({identity: 1 for identity in expected}),
                )

        self.assertEqual(len(rip_paths), 18)
        self.assertEqual(len(spk_paths), 59)
        self.assertEqual(parsed_stream_packs, set(spk_paths))
        self.assertEqual(packed_resource_count, 24_237)
        self.assertEqual(stream_resource_count, 75_746)
        self.assertEqual(animation_count, 6_458)
        self.assertEqual(
            frame_rates,
            Counter({ANIMATION_FRAME_RATE: 6_433, 0: 25}),
        )
        self.assertEqual(track_count, 384_720)
        self.assertEqual(key_count, 11_423_760)
        self.assertEqual(max_bone_index, 120)
        self.assertEqual(
            track_kinds,
            Counter(
                {
                    PackedTrackKind.RAW_0: 361_733,
                    PackedTrackKind.RAW_1: 22_888,
                    PackedTrackKind.RAW_2: 99,
                }
            ),
        )

    def test_decodes_known_model_material_and_texture(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            self.assertGreaterEqual(
                len(reader.asset_pointers(AssetKind.MOBY)),
                800,
            )
            path = next(
                path
                for path in source.paths
                if path.casefold().endswith("/ap_base_twoby4.smf")
            )
            model = reader.read_moby(reader.model_id(path))
            self.assertEqual(len(model.meshes), 1)
            self.assertEqual(model.meshes[0].positions.shape, (24, 3))
            self.assertEqual(model.meshes[0].uvs.shape, (24, 2))
            self.assertAlmostEqual(
                float(model.meshes[0].uvs[0, 0]),
                0.5428,
                places=4,
            )
            self.assertAlmostEqual(
                float(model.meshes[0].uvs[0, 1]),
                0.00048828125,
                places=6,
            )
            self.assertEqual(model.meshes[0].indices.shape, (36,))
            self.assertEqual(len(model.shader_tuids), 1)

            shader = reader.read_shader(model.shader_tuids[0])
            self.assertIsNotNone(shader.albedo_texture_id)
            texture = reader.read_texture(
                shader.albedo_texture_id,
                lookup_path=shader.texture_lookup_paths[0],
            )
            self.assertEqual(texture.rgba.shape, (512, 512, 4))

            amship = reader.read_moby(reader.model_id("models/AP_BASE_AMShip.smf"))
            self.assertEqual(len(amship.shader_tuids), 1)
            amship_shader = reader.read_shader(amship.shader_tuids[0])
            self.assertEqual(
                amship_shader.texture_lookup_paths[0],
                "textures/Models/RS_CLR_alienmothership2.p2t",
            )

    def test_decodes_serialized_packet_material_maps(self):
        expected_maps = {
            "models/AP_BASE_AMShipA.smf": [0, 1, 2, 3, 4, 5],
            "models/CH_BASE_Hybrid.smf": [
                1,
                0,
                0,
                2,
                2,
                2,
                2,
                2,
                3,
            ],
            "models/CH_PHYS_Hybrid.smf": [
                1,
                2,
                4,
                4,
                4,
                4,
                4,
                4,
                4,
                4,
                4,
                5,
                6,
                1,
                1,
                2,
                2,
                2,
                2,
                2,
                3,
                3,
            ],
            "models/IT_BASE_FPHero_Axe.smf": [1],
        }

        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            for path, expected in expected_maps.items():
                with self.subTest(path=path):
                    model = reader.read_moby(reader.model_id(path))
                    actual = [mesh.shader_index for mesh in model.meshes]
                    self.assertEqual(actual, expected)
                    self.assertTrue(model.shader_tuids)
                    self.assertLess(max(actual), len(model.shader_tuids))
                    self.assertEqual(
                        len(model.collision_meshes),
                        4 if path == "models/CH_BASE_Hybrid.smf" else 0,
                    )

    def test_filters_car_shadow_hull_and_decodes_authored_lods(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            car = reader.read_moby(
                reader.model_id("models/RS_OB_Car_C.smf")
            )

            self.assertEqual(len(car.meshes), 3)
            self.assertTrue(all(mesh.lod_level == 0 for mesh in car.meshes))
            self.assertEqual(len(car.collision_meshes), 1)
            shadow_hull = car.collision_meshes[0]
            self.assertEqual(shadow_hull.positions.shape, (76, 3))
            self.assertEqual(shadow_hull.indices.shape, (120,))

            lod_test = reader.read_moby(
                reader.model_id("models/GE_OB_LOD_TEST.smf")
            )
            self.assertEqual(
                [mesh.lod_level for mesh in lod_test.meshes],
                [0, 0, 1, 1],
            )
            self.assertEqual(
                [mesh.shader_index for mesh in lod_test.meshes],
                [2, 3, 0, 1],
            )

    def test_classifies_hybrid_gib_packets_as_damage(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            hybrid = reader.read_moby(
                reader.model_id("models/CH_PHYS_Hybrid.smf")
            )
            damage_meshes = [
                mesh
                for mesh in hybrid.meshes
                if mesh.is_damage_permutation
            ]
            base_meshes = [
                mesh
                for mesh in hybrid.meshes
                if not mesh.is_damage_permutation
            ]

            self.assertEqual(len(damage_meshes), 13)
            self.assertEqual(len(base_meshes), 9)
            self.assertEqual(
                [mesh.segment_index for mesh in damage_meshes],
                [47, 46, 35, 36, 37, 38, 39, 41, 42, 43, 44, 40, 45],
            )

            base_hybrid = reader.read_moby(
                reader.model_id("models/CH_BASE_Hybrid.smf")
            )
            self.assertFalse(
                any(
                    mesh.is_damage_permutation
                    for mesh in base_hybrid.meshes
                )
            )

    def test_decodes_normal_gloss_and_emissive_material_channels(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            car = reader.read_moby(
                reader.model_id("models/RS_OB_Car_C.smf")
            )
            car_shader = reader.read_shader(car.shader_tuids[2])
            self.assertEqual(car_shader.normal_texture_encoding, "DIRECT")
            self.assertIsNotNone(car_shader.normal_texture_id)
            normal = reader.read_texture(
                car_shader.normal_texture_id,
                lookup_path=car_shader.texture_lookup_paths[1],
            )
            self.assertEqual(normal.info.format_code, 0x83000000)
            self.assertEqual(normal.rgba.shape, (256, 256, 4))
            self.assertGreaterEqual(int(normal.rgba[..., 2].min()), 150)

            gloss_path = (
                "materials/00E91E98_A386A043_B9ADA612_14CFB27C.bmx"
            )
            gloss = reader.read_shader(reader.material_id(gloss_path))
            self.assertEqual(gloss.specular_texture_semantics, "GLOSS")
            self.assertEqual(
                gloss.texture_lookup_paths[2],
                "textures/Models/RS_GLS_FilingCabinetMet_1.p2t",
            )
            self.assertEqual(gloss.specular_power, 80.0)

            emissive_path = (
                "materials/01A7906C_831FF643_BA99B529_D63E1D1A.bmx"
            )
            emissive = reader.read_shader(
                reader.material_id(emissive_path)
            )
            self.assertEqual(
                emissive.emissive_texture_lookup_path,
                "textures/Models/CB_GLO_Light_Pillar_01.p2t",
            )
            self.assertEqual(emissive.emissive_strength, 2.0)
            self.assertAlmostEqual(emissive.emissive_color[0], 0.5019608)

    def test_decodes_dropship_color_only_emissive_materials(self):
        expected = {
            0x6C1660E1224715A1: (
                (1.0, 0.5019608, 0.2509804, 1.0),
                (1.0, 0.5019608, 0.0),
            ),
            0x98FE2F2A92B89201: (
                (1.0, 1.0, 0.0, 1.0),
                (1.0, 0.5019608, 0.2509804),
            ),
        }
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            for shader_id, (base_color, emission_color) in expected.items():
                with self.subTest(shader_id=f"0x{shader_id:016X}"):
                    shader = reader.read_shader(shader_id)
                    self.assertTrue(shader.emissive_enabled)
                    self.assertIs(shader.use_emissive_texture, False)
                    self.assertIsNone(shader.emissive_texture_id)
                    for actual, expected_value in zip(
                        shader.base_color_factor,
                        base_color,
                        strict=True,
                    ):
                        self.assertAlmostEqual(actual, expected_value)
                    for actual, expected_value in zip(
                        shader.emissive_color,
                        emission_color,
                        strict=True,
                    ):
                        self.assertAlmostEqual(actual, expected_value)
                    self.assertEqual(shader.emissive_strength, 1.0)

    def test_faxe_head_uses_upper_texture_region(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            model = reader.read_moby(reader.model_id("models/AP_BASE_FAxe.smf"))
            mesh = model.meshes[0]
            head_vertices = (abs(mesh.positions[:, 0]) > 0.05) & (
                mesh.positions[:, 1] > 0.3
            )
            self.assertGreater(int(head_vertices.sum()), 200)
            blender_v = 1.0 - mesh.uvs[head_vertices, 1]
            self.assertGreater(float(blender_v.min()), 0.5)

    def test_decodes_adrone_skeleton_only_model(self):
        with GameSource.open(Path(REAL_SOURCE)) as source:
            reader = BurningSkiesReader(source)
            model = reader.read_moby(reader.model_id("models/CH_PHYS_ADrone.smf"))

            self.assertEqual(model.meshes, [])
            self.assertEqual(model.shader_tuids, ())
            self.assertIsNotNone(model.skeleton)
            assert model.skeleton is not None
            self.assertEqual(model.skeleton.bone_count, 37)
            self.assertEqual(model.skeleton.root_bone, 0)
            self.assertEqual(model.skeleton.bones[1].parent_index, 0)
            self.assertEqual(model.skeleton.bones[2].parent_index, 1)

    def test_decodes_complete_weighted_gbuffer_corpus(self):
        renderable_models = 0
        weighted_packets = 0
        weighted_vertices = 0
        bone_records = 0

        with GameSource.open(Path(REAL_SOURCE)) as source:
            for path in source.paths:
                if not path.casefold().endswith(".smf"):
                    continue
                model = decode_smf(source.read(path), label=path)
                self.assertIsNotNone(model.skeleton)
                assert model.skeleton is not None
                bone_records += model.skeleton.bone_count

                renderable_models += bool(model.meshes)
                for mesh in model.meshes:
                    self.assertIsNotNone(mesh.bone_indices)
                    self.assertIsNotNone(mesh.bone_weights)
                    assert mesh.bone_indices is not None
                    assert mesh.bone_weights is not None
                    weighted_packets += 1
                    weighted_vertices += len(mesh.positions)
                    self.assertEqual(mesh.bone_indices.dtype, np.uint16)
                    self.assertEqual(mesh.bone_weights.dtype, np.float32)
                    self.assertEqual(
                        mesh.bone_indices.shape,
                        (len(mesh.positions), 4),
                    )
                    self.assertEqual(
                        mesh.bone_weights.shape,
                        (len(mesh.positions), 4),
                    )
                    self.assertTrue(np.all(mesh.bone_weights >= 0.0))
                    np.testing.assert_allclose(
                        mesh.bone_weights.sum(axis=1),
                        1.0,
                        atol=2e-7,
                        rtol=0.0,
                    )

            adrone = decode_smf(
                source.read("models/CH_BASE_ADrone.smf"),
                label="models/CH_BASE_ADrone.smf",
            )
            aescort = decode_smf(
                source.read("models/AP_BASE_AEscort.smf"),
                label="models/AP_BASE_AEscort.smf",
            )

        self.assertEqual(renderable_models, 798)
        self.assertEqual(weighted_packets, 3_549)
        self.assertEqual(weighted_vertices, 2_142_432)
        self.assertEqual(bone_records, 10_202)
        np.testing.assert_array_equal(
            adrone.meshes[0].bone_indices[870],
            np.array((30, 7, 12, 0), dtype=np.uint16),
        )
        np.testing.assert_allclose(
            adrone.meshes[0].bone_weights[870],
            np.array(
                (
                    0.6999740600585938,
                    0.27525147795677185,
                    0.024774465709924698,
                    0.0,
                ),
                dtype=np.float32,
            ),
            atol=1e-7,
        )
        np.testing.assert_array_equal(
            aescort.meshes[0].bone_indices[:, 0],
            np.ones(len(aescort.meshes[0].positions), dtype=np.uint16),
        )
        np.testing.assert_array_equal(
            aescort.meshes[0].bone_weights,
            np.tile(
                np.array((1.0, 0.0, 0.0, 0.0), dtype=np.float32),
                (len(aescort.meshes[0].positions), 1),
            ),
        )


if __name__ == "__main__":
    unittest.main()
