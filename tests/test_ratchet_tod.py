from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.ratchet_tod.reader import RatchetTodReader
from insomniac_asset_io.games.resistance_2.formats import AssetKind


def _track_mask(bone: int, component: int, kind: int = 0) -> int:
    return (bone << 6) | (kind << 4) | (component << 2) | 0b10


def _ighw(
    size: int,
    sections: tuple[tuple[int, int, int, int], ...],
) -> bytearray:
    data = bytearray(size)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, section in enumerate(sections):
        struct.pack_into(">4I", data, 0x20 + index * 16, *section)
    return data


def _matrix(data: bytearray, offset: int) -> None:
    struct.pack_into(
        ">16f",
        data,
        offset,
        1,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        1,
    )


def _vertex(
    data: bytearray,
    offset: int,
    xyz: tuple[int, int, int],
    *,
    stride: int,
) -> None:
    struct.pack_into(">3hh2e", data, offset, *xyz, -1, 0.0, 0.0)
    data[offset + 12 : offset + stride] = bytes(stride - 12)


def _write_fixture(root: Path) -> None:
    sections = (
        (0xD100, 0x200, 1, 0xC0),
        (0x3400, 0x2C0, 1, 0x80),
        (0x5000, 0x340, 2, 0x80),
        (0x5200, 0x440, 1, 0x20),
        (0x6200, 0x460, 1, 0x80),
        (0x9240, 0x4E0, 1, 0x80),
        (0xA200, 0xA00, 1, 0xB0),
        (0x9340, 0xB00, 1, 0xE0),
        (0xB100, 0xC00, 1, 0x30),
        (0x9540, 0xC40, 1, 0x160),
    )
    main = _ighw(0xDA0, sections)

    struct.pack_into(">4f", main, 0x200, 0.0, 0.0, 0.0, 2.0)
    struct.pack_into(">H", main, 0x216, 1)
    struct.pack_into(">H", main, 0x218, 1)
    struct.pack_into(">3I", main, 0x220, 0x600, 0x5F0, 0x5E0)
    struct.pack_into(">2If", main, 0x234, 0x80000000, 0x80000000, 1.0)
    struct.pack_into(">2I", main, 0x5E0, 0x700, 1)
    struct.pack_into(">I", main, 0x5F0, 0x800)
    struct.pack_into(">HHIIIHH", main, 0x600, 1, 0, 0x620, 0x630, 0x670, 0, 0)
    struct.pack_into(">HhHH", main, 0x620, 0, -1, 0xFFFF, 0xFFFF)
    _matrix(main, 0x630)
    _matrix(main, 0x670)

    struct.pack_into(">IIHH", main, 0x700, 0, 0, 1, 3)
    main[0x70C] = 1
    main[0x70D] = 0
    struct.pack_into(">H", main, 0x712, 3)
    struct.pack_into(">I", main, 0x720, 0x750)
    struct.pack_into(">H", main, 0x750, 0)

    struct.pack_into(">HHHH", main, 0x800, 0, 4, 1, 1)
    struct.pack_into(">I", main, 0x808, 0x8A0)
    struct.pack_into(">ff", main, 0x814, 0.0, 30.0)
    struct.pack_into(">II", main, 0x820, 0x900, 0x980)
    struct.pack_into(">4H", main, 0x832, 0x10, 0, 4, 0)
    main[0x8A0 : 0x8A5] = b"idle\0"
    struct.pack_into(">4h", main, 0x900, 0, 0, 0, 32767)
    struct.pack_into(
        ">4H",
        main,
        0x910,
        *(_track_mask(0, component) for component in range(4)),
    )
    main[0x920] = 0xFF
    struct.pack_into(">4h", main, 0x980, 0, 0, 0, 32767)

    struct.pack_into(">I", main, 0x2C0, 0x780)
    main[0x2CF] = 1
    struct.pack_into(">II", main, 0x2D4, 0x40, 0x3C)
    struct.pack_into(">3f", main, 0x2E0, 1.0, 1.0, 1.0)
    struct.pack_into(">I", main, 0x780, 3)
    struct.pack_into(">H", main, 0x784, 0)
    struct.pack_into(">H", main, 0x788, 3)
    struct.pack_into(">H", main, 0x792, 3)
    struct.pack_into(">H", main, 0x7A8, 0)

    struct.pack_into(">4I", main, 0x340, *(0x440,) * 4)
    main[0x350] = 0x1C
    main[0x351] = 0
    struct.pack_into(
        ">24f",
        main,
        0x360,
        *(float(index) / 8.0 for index in range(24)),
    )
    struct.pack_into(">I", main, 0x3C0, 0x440)
    main[0x3D1] = 4
    struct.pack_into(">f", main, 0x3E0, 0.5)
    struct.pack_into(">IHH", main, 0x440, 0, 0, 0x0600)
    struct.pack_into(">HH", main, 0x458, 4, 4)

    struct.pack_into(">Q", main, 0x460, 0x1234)
    struct.pack_into(">IIHH", main, 0x4A0, 6, 0x80, 3, 3)
    struct.pack_into(">H", main, 0x4B0, 1)
    struct.pack_into(">3f", main, 0x4C0, 0.0, 0.0, 0.0)

    tie_matrix = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        1.0,
        2.0,
        3.0,
        1.0,
    )
    struct.pack_into(">16f", main, 0x4E0, *tie_matrix)
    struct.pack_into(">I", main, 0x530, 0x2C0)

    struct.pack_into(">3I", main, 0xA00, 0xF, 0, 0)
    struct.pack_into(">2I", main, 0xA18, 0xC8, 4)
    struct.pack_into(">IHH", main, 0xA20, 9, 3, 3)
    struct.pack_into(">2I", main, 0xA40, 0x104, 0x124)
    struct.pack_into(">I", main, 0xA48, 1)
    struct.pack_into(">HHf", main, 0xA50, 0, 4, 0.5)
    foliage_matrix = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        4.0,
        5.0,
        6.0,
        1.0,
    )
    struct.pack_into(">16f", main, 0xB00, *foliage_matrix)
    struct.pack_into(">I", main, 0xB00 + 0xC4, 0xA00)

    struct.pack_into(">3I", main, 0xC00, 0x140, 3, 3)
    struct.pack_into(">4f", main, 0xC10, 1.0, 0.0, 0.0, 0.5)
    struct.pack_into(">H", main, 0xC20, 1)

    struct.pack_into(">4I", main, 0xC40, 2, 0xE0, 1, 0x80)
    main[0xD00] = 2
    struct.pack_into(">I", main, 0xD0C, 1)
    struct.pack_into(">H", main, 0xD14, 0)
    struct.pack_into(
        ">16f",
        main,
        0xD20,
        7.0,
        8.0,
        9.0,
        1.5,
        1.0,
        0.0,
        0.0,
        2.0,
        0.0,
        1.0,
        0.0,
        0.25,
        0.5,
        0.5,
        0.5,
        1.0,
    )
    struct.pack_into(
        ">16f",
        main,
        0xD60,
        -1.0,
        -2.0,
        -3.0,
        0.5,
        0.0,
        1.0,
        0.0,
        3.0,
        -1.0,
        0.0,
        0.0,
        0.5,
        0.25,
        0.25,
        0.25,
        1.0,
    )
    (root / "main.dat").write_bytes(main)

    vertex_sections = (
        (0x9000, 0x100, 1, 0x180),
        (0x9100, 0x300, 1, 0x18),
    )
    vertices = _ighw(0x320, vertex_sections)
    for base, stride in ((0x100, 0x14), (0x140, 0x14), (0x180, 0x18)):
        _vertex(vertices, base, (0, 0, 0), stride=stride)
        _vertex(vertices, base + stride, (256, 0, 0), stride=stride)
        _vertex(vertices, base + stride * 2, (0, 256, 0), stride=stride)
    for index, (x, y, z, u, v) in enumerate(
        (
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0, 1.0, 0.0),
            (0.0, 3.0, 0.0, 0.0, 1.0),
        )
    ):
        struct.pack_into(
            ">4e2e",
            vertices,
            0x1C8 + index * 0x14,
            x,
            y,
            z,
            1.0,
            u,
            v,
        )
    struct.pack_into(
        ">16e",
        vertices,
        0x204,
        -1.0,
        -2.0,
        0.0,
        0.0,
        1.0,
        -2.0,
        1.0,
        0.0,
        1.0,
        2.0,
        1.0,
        1.0,
        -1.0,
        2.0,
        0.0,
        1.0,
    )
    struct.pack_into(">3eH", vertices, 0x224, 10.0, 20.0, 30.0, 0)
    for index, (x, y, z, u, v) in enumerate(
        (
            (0, 0, 0, 0.0, 0.0),
            (256, 0, 0, 1.0, 0.0),
            (0, 512, 0, 0.0, 1.0),
        )
    ):
        struct.pack_into(
            ">4h2eH",
            vertices,
            0x240 + index * 0x0E,
            x,
            y,
            z,
            0x2000,
            u,
            v,
            0x3555,
        )
    struct.pack_into(
        ">12H",
        vertices,
        0x300,
        0,
        1,
        2,
        0,
        1,
        2,
        0,
        1,
        2,
        0,
        1,
        2,
    )
    (root / "vertices.dat").write_bytes(vertices)
    (root / "textures.dat").write_bytes(bytes(8))

    gameplay = _ighw(
        0x148,
        ((0x7340, 0x100, 1, 0x48),),
    )
    struct.pack_into(">3f", gameplay, 0x118, 1.0, 2.0, 3.0)
    struct.pack_into(">3f", gameplay, 0x124, 0.0, 0.0, 0.0)
    struct.pack_into(">f", gameplay, 0x130, 1.0)
    struct.pack_into(">H", gameplay, 0x13C, 0)
    (root / "gameplay.dat").write_bytes(gameplay)


class RatchetTodReaderTests(unittest.TestCase):
    def test_decodes_old_level_asset_chains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            with GameSource.open(root) as source:
                reader = RatchetTodReader(source)
                self.assertEqual(
                    len(reader.asset_pointers(AssetKind.MOBY)),
                    1,
                )
                self.assertEqual(
                    reader.read_asset_name(AssetKind.MOBY, 0),
                    "Moby_0000",
                )

                moby = reader.read_moby(0)
                self.assertEqual(len(moby.meshes), 1)
                self.assertEqual(moby.shader_tuids, (1,))
                self.assertIsNotNone(moby.skeleton)
                self.assertEqual(reader.read_animations(moby)[0].name, "idle")

                shader = reader.read_shader(1)
                self.assertEqual(shader.alpha_mode, "MASK")
                texture = reader.read_texture(shader.albedo_texture_id)
                self.assertEqual(texture.rgba.shape, (4, 4, 4))

                surface_shader = reader.read_shader(0)
                self.assertEqual(surface_shader.detail_texture_id, 0x440)
                self.assertEqual(surface_shader.material_flags, 0x1C)
                self.assertTrue(surface_shader.use_glossiness)
                self.assertTrue(surface_shader.use_normal_map)
                self.assertTrue(surface_shader.use_detail_map)
                self.assertEqual(len(surface_shader.material_values), 6)
                self.assertEqual(
                    surface_shader.material_values[-1],
                    (2.5, 2.625, 2.75, 2.875),
                )

                tie_id = reader.asset_pointers(AssetKind.TIE)[0].tuid
                tie = reader.read_tie(tie_id)
                self.assertEqual(len(tie.meshes), 1)
                self.assertEqual(tie.shader_tuids, (0,))

                shrub_id = reader.asset_pointers(AssetKind.SHRUB)[0].tuid
                shrub = reader.read_shrub(shrub_id)
                self.assertEqual(shrub_id, 0xC00)
                self.assertEqual(
                    reader.read_asset_name(AssetKind.SHRUB, shrub_id),
                    "Shrub_00000C00",
                )
                self.assertEqual(shrub.shader_tuids, (1,))
                np.testing.assert_allclose(
                    shrub.meshes[0].positions,
                    np.asarray(
                        (
                            (0.0, 0.0, 0.0),
                            (1.0, 0.0, 0.0),
                            (0.0, 2.0, 0.0),
                        )
                    )
                    * 0.9144,
                )
                np.testing.assert_allclose(
                    shrub.meshes[0].uvs,
                    ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
                )
                np.testing.assert_array_equal(
                    shrub.meshes[0].indices,
                    (0, 1, 2),
                )

                foliage_id = reader.asset_pointers(
                    AssetKind.FOLIAGE
                )[0].tuid
                foliage = reader.read_foliage(foliage_id)
                self.assertEqual(foliage_id, 0xA00)
                self.assertEqual(len(foliage.meshes), 2)
                np.testing.assert_allclose(
                    foliage.meshes[0].positions,
                    np.asarray(
                        (
                            (0.0, 0.0, 0.0),
                            (2.0, 0.0, 0.0),
                            (0.0, 3.0, 0.0),
                        )
                    )
                    * 0.9144,
                )
                np.testing.assert_array_equal(
                    foliage.meshes[0].indices,
                    (0, 1, 2),
                )
                np.testing.assert_allclose(
                    foliage.meshes[1].positions,
                    np.asarray(
                        (
                            (9.0, 18.0, 30.0),
                            (11.0, 18.0, 30.0),
                            (11.0, 22.0, 30.0),
                            (9.0, 22.0, 30.0),
                        )
                    )
                    * 0.9144,
                )
                np.testing.assert_array_equal(
                    foliage.meshes[1].indices,
                    (0, 1, 2, 3, 0, 2),
                )
                foliage_shader = reader.read_shader(
                    foliage.shader_tuids[0]
                )
                self.assertEqual(foliage_shader.alpha_mode, "MASK")
                self.assertEqual(foliage_shader.albedo_texture_id, 0x440)

                zone = reader.read_zones()[0]
                self.assertEqual(len(zone.terrain_meshes), 1)
                self.assertEqual(len(zone.tie_instances), 1)
                self.assertEqual(len(zone.shrub_instances), 2)
                self.assertEqual(len(zone.foliage_instances), 1)
                self.assertTrue(
                    all(
                        instance.asset_tuid == shrub_id
                        for instance in zone.shrub_instances
                    )
                )
                self.assertEqual(
                    zone.foliage_instances[0].asset_tuid,
                    foliage_id,
                )
                np.testing.assert_allclose(
                    zone.tie_instances[0].matrix[:3, 3],
                    np.asarray((1.0, 2.0, 3.0)) * 0.9144,
                )
                np.testing.assert_allclose(
                    zone.foliage_instances[0].matrix[:3, 3],
                    np.asarray((4.0, 5.0, 6.0)) * 0.9144,
                )
                np.testing.assert_allclose(
                    zone.shrub_instances[0].matrix[:3, 3],
                    np.asarray((7.0, 8.0, 9.0)) * 0.9144,
                )
                np.testing.assert_allclose(
                    zone.shrub_instances[0].matrix[:3, :3],
                    np.eye(3) * 1.5,
                )
                np.testing.assert_allclose(
                    zone.shrub_instances[1].matrix[:3, 3],
                    np.asarray((-1.0, -2.0, -3.0)) * 0.9144,
                )

                gameplay = reader.read_gameplay_regions()[0]
                self.assertEqual(gameplay.name, "art")
                self.assertEqual(gameplay.moby_instances[0].asset_tuid, 0)

    def test_rejects_invalid_shrub_cluster_references(self) -> None:
        mutations = (
            (
                "missing shrub",
                lambda data: struct.pack_into(">I", data, 0xD0C, 2),
                "references missing shrub 1",
            ),
            (
                "out of bounds range",
                lambda data: data.__setitem__(0xD00, 3),
                "instance range is out of bounds",
            ),
        )
        for label, mutate, expected in mutations:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _write_fixture(root)
                main = bytearray((root / "main.dat").read_bytes())
                mutate(main)
                (root / "main.dat").write_bytes(main)
                with GameSource.open(root) as source:
                    reader = RatchetTodReader(source)
                    with self.assertRaisesRegex(
                        FormatError,
                        expected,
                    ):
                        reader.read_zones()


if __name__ == "__main__":
    unittest.main()
