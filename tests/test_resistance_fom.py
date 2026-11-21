from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_fom.reader import (
    FOLIAGE_SHADER_TUID_BASE,
    ResistanceFomReader,
)


def _track_mask(bone: int, component: int, kind: int = 0) -> int:
    return (bone << 6) | (kind << 4) | (component << 2) | 0b10


def _ighw(
    size: int,
    sections: tuple[tuple[int, int, int, int], ...],
    *,
    version: tuple[int, int] = (1, 1),
) -> bytearray:
    data = bytearray(size)
    struct.pack_into(
        ">4sHHI",
        data,
        0,
        b"IGHW",
        version[0],
        version[1],
        len(sections),
    )
    table_offset = 0x20 if version == (1, 1) else 0x10
    for index, section in enumerate(sections):
        struct.pack_into(
            ">4I",
            data,
            table_offset + index * 16,
            *section,
        )
    return data


def _matrix(
    data: bytearray,
    offset: int,
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
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
        *translation,
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


def _vegetation_vertex(
    data: bytearray,
    offset: int,
    xyz: tuple[float, float, float],
    uv: tuple[float, float],
    *,
    stride: int,
) -> None:
    struct.pack_into(">4e", data, offset, *xyz, 1.0)
    if stride == 0x10:
        struct.pack_into(">4B2e", data, offset + 8, 255, 255, 255, 255, *uv)
    else:
        struct.pack_into(">2e", data, offset + 8, *uv)
        data[offset + 12 : offset + stride] = bytes(stride - 12)


def _write_fixture(root: Path) -> None:
    main = _ighw(
        0xE00,
        (
            (0xD100, 0x200, 1, 0xC0),
            (0x3400, 0x2C0, 1, 0x40),
            (0x5001, 0x300, 2, 0x80),
            (0x5300, 0x400, 1, 0x20),
            (0x6200, 0x420, 1, 0x80),
            (0x9300, 0x4A0, 1, 0xC0),
            (0xC700, 0xA00, 1, 0x30),
            (0xC200, 0xA30, 1, 0x120),
            (0x9700, 0xB50, 1, 0xE0),
            (0xC650, 0xC30, 1, 0xF0),
        ),
    )

    struct.pack_into(">4f", main, 0x200, 0.0, 0.0, 0.0, 2.0)
    struct.pack_into(">3H", main, 0x216, 1, 1, 0x123)
    struct.pack_into(">3I", main, 0x220, 0x680, 0x730, 0x600)
    struct.pack_into(">2If", main, 0x234, 0, 0x20, 1.0)
    struct.pack_into(">2I", main, 0x600, 0x608, 1)
    struct.pack_into(
        ">HHHBBIII",
        main,
        0x608,
        1,
        3,
        3,
        1,
        0,
        0,
        0,
        0x640,
    )
    struct.pack_into(">H", main, 0x640, 0)

    struct.pack_into(
        ">HHIIIHH",
        main,
        0x680,
        1,
        0,
        0x6A0,
        0x6B0,
        0x6F0,
        0,
        0,
    )
    struct.pack_into(">HhHH", main, 0x6A0, 0, -1, 0xFFFF, 0xFFFF)
    _matrix(main, 0x6B0)
    _matrix(main, 0x6F0)

    struct.pack_into(">I", main, 0x730, 0x740)
    struct.pack_into(">HHHH", main, 0x740, 0, 4, 1, 1)
    struct.pack_into(">I", main, 0x748, 0x7E0)
    struct.pack_into(">ff", main, 0x754, 0.0, 30.0)
    struct.pack_into(">II", main, 0x760, 0x800, 0x880)
    struct.pack_into(">4H", main, 0x772, 0x10, 0, 4, 0)
    main[0x7E0 : 0x7E5] = b"idle\0"
    struct.pack_into(">4h", main, 0x800, 0, 0, 0, 32767)
    struct.pack_into(
        ">4H",
        main,
        0x810,
        *(_track_mask(0, component) for component in range(4)),
    )
    main[0x820] = 0xFF
    struct.pack_into(">4h", main, 0x880, 0, 0, 0, 32767)

    struct.pack_into(">I", main, 0x2C0, 0x900)
    struct.pack_into(">H", main, 0x2C8, 1)
    struct.pack_into(">I", main, 0x2D0, 0)
    struct.pack_into(">H", main, 0x2D8, 7)
    struct.pack_into(">3f", main, 0x2E0, 1.0, 1.0, 1.0)
    struct.pack_into(">HHIHHHH", main, 0x900, 0, 0, 0, 3, 3, 0, 0)

    material = 0x380
    struct.pack_into(">I", main, material + 0x1C, 0x1C040000)
    struct.pack_into(">4I", main, material + 0x20, *(0x400,) * 4)
    struct.pack_into(
        ">20f",
        main,
        material + 0x30,
        *(float(index) / 8.0 for index in range(20)),
    )
    struct.pack_into(">IHBB", main, 0x400, 0x80, 1, 0x86, 0)
    struct.pack_into(">HH", main, 0x418, 4, 4)

    struct.pack_into(">3f", main, 0x460, 256.0, 512.0, 768.0)
    struct.pack_into(">HH", main, 0x46C, 1, 0)
    struct.pack_into(">IIHH", main, 0x474, 3, 0x3C, 3, 3)

    _matrix(main, 0x4A0, (1.0, 2.0, 3.0))
    struct.pack_into(">I", main, 0x52C, 0x2C0)

    struct.pack_into(">3I", main, 0xA00, 0xA0, 6, 3)
    struct.pack_into(">H", main, 0xA20, 1)

    struct.pack_into(">H", main, 0xA34, 7)
    struct.pack_into(">I", main, 0xA38, 0)
    struct.pack_into(">I", main, 0xA40, 9)
    struct.pack_into(">I", main, 0xA48, 0xD0)
    struct.pack_into(">IHH", main, 0xA50, 0, 3, 0)
    struct.pack_into(">II", main, 0xA70, 0x10C, 1)
    struct.pack_into(">III f", main, 0xA78, 3, 9, 0, 0.0)
    struct.pack_into(">II", main, 0xAE8, 0xD20, 1)
    struct.pack_into(">4H", main, 0xAF0, 3, 9, 0, 1)

    _matrix(main, 0xB50, (4.0, 5.0, 6.0))
    struct.pack_into(">I", main, 0xC14, 0xA30)

    struct.pack_into(">4I", main, 0xC30, 1, 0xB0, 1, 0x60)
    main[0xCD0 : 0xCD8] = bytes((1, 0, 0, 0, 0, 0, 0, 0))
    struct.pack_into(">IH", main, 0xCD8, 0, 1)
    struct.pack_into(">4f", main, 0xCE0, 10.0, 20.0, 30.0, 2.0)
    struct.pack_into(">3f", main, 0xCF0, 1.0, 0.0, 0.0)
    struct.pack_into(">3f", main, 0xD00, 0.0, 1.0, 0.0)
    struct.pack_into(">4f", main, 0xD20, 7.0, 8.0, 9.0, 1.0)
    (root / "ps3levelmain.dat").write_bytes(main)

    vertices = _ighw(
        0x450,
        (
            (0x9000, 0x100, 1, 0x13C),
            (0x9100, 0x400, 1, 0x24),
        ),
    )
    for index, xyz in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        _vertex(vertices, 0x100 + index * 0x14, xyz, stride=0x14)
    for index, xyz in enumerate(((0, 0, 0), (256, 0, 0), (0, 256, 0))):
        _vertex(vertices, 0x13C + index * 0x1A, xyz, stride=0x1A)
    for index, xyz in enumerate(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    ):
        _vegetation_vertex(
            vertices,
            0x1A0 + index * 0x10,
            xyz,
            (float(index == 1), float(index == 2)),
            stride=0x10,
        )
        _vegetation_vertex(
            vertices,
            0x1D0 + index * 0x14,
            xyz,
            (float(index == 1), float(index == 2)),
            stride=0x14,
        )
    for index, (size, uv) in enumerate(
        (
            ((-1.0, -1.0), (0.0, 0.0)),
            ((1.0, -1.0), (1.0, 0.0)),
            ((1.0, 1.0), (1.0, 1.0)),
            ((-1.0, 1.0), (0.0, 1.0)),
        )
    ):
        struct.pack_into(
            ">2e2eI",
            vertices,
            0x20C + index * 0x0C,
            *size,
            *uv,
            0,
        )
    struct.pack_into(
        ">18H",
        vertices,
        0x400,
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
        0,
        2,
        3,
        0,
        0,
        0,
    )
    (root / "ps3levelverts.dat").write_bytes(vertices)

    textures = bytearray(0x88)
    struct.pack_into(">3H", textures, 0, 0, 1, 2)
    for index, xyz in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        _vertex(textures, 0x20 + index * 0x14, xyz, stride=0x14)
    (root / "ps3leveltexs.dat").write_bytes(textures)

    gameplay = _ighw(
        0x240,
        ((0x25000, 0x100, 1, 0x30),),
        version=(0, 2),
    )
    struct.pack_into(">I", gameplay, 0x120, 0x140)
    gameplay[0x140 : 0x148] = b"fixture\0"
    struct.pack_into(">II", gameplay, 0x180, 0x200, 1)
    struct.pack_into(">3f", gameplay, 0x210, 1.0, 2.0, 3.0)
    struct.pack_into(">3f", gameplay, 0x21C, 0.0, 0.0, 0.0)
    struct.pack_into(">H", gameplay, 0x23C, 0x123)
    (root / "ps3gameplay.dat").write_bytes(gameplay)


class ResistanceFomReaderTests(unittest.TestCase):
    def test_decodes_v1_level_asset_chains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            with GameSource.open(root) as source:
                reader = ResistanceFomReader(source)
                moby_pointers = reader.asset_pointers(AssetKind.MOBY)
                tie_pointers = reader.asset_pointers(AssetKind.TIE)
                shrub_pointers = reader.asset_pointers(AssetKind.SHRUB)
                foliage_pointers = reader.asset_pointers(
                    AssetKind.FOLIAGE
                )
                self.assertEqual(moby_pointers[0].tuid, 0x123)
                self.assertEqual(tie_pointers[0].tuid, 0x2C0)
                self.assertEqual(shrub_pointers[0].tuid, 0xA00)
                self.assertEqual(foliage_pointers[0].tuid, 0xA30)

                moby = reader.read_moby(0x123)
                self.assertEqual(len(moby.meshes), 1)
                self.assertEqual(moby.shader_tuids, (1,))
                self.assertIsNotNone(moby.skeleton)
                self.assertEqual(reader.read_animations(moby)[0].name, "idle")

                shader = reader.read_shader(1)
                self.assertEqual(shader.alpha_mode, "MASK")
                self.assertEqual(len(shader.material_values), 5)
                texture = reader.read_texture(shader.albedo_texture_id)
                self.assertEqual(texture.rgba.shape, (4, 4, 4))

                tie = reader.read_tie(0x2C0)
                self.assertEqual(len(tie.meshes), 1)
                self.assertEqual(tie.shader_tuids, (0,))

                shrub = reader.read_shrub(0xA00)
                self.assertEqual(shrub.shader_tuids, (1,))
                np.testing.assert_allclose(
                    shrub.meshes[0].positions[1],
                    (0.9144, 0.0, 0.0),
                )

                foliage = reader.read_foliage(0xA30)
                self.assertEqual(
                    foliage.shader_tuids,
                    (FOLIAGE_SHADER_TUID_BASE,),
                )
                self.assertEqual(len(foliage.meshes), 2)
                self.assertEqual(len(foliage.meshes[1].positions), 6)
                foliage_shader = reader.read_shader(
                    foliage.shader_tuids[0]
                )
                self.assertEqual(foliage_shader.albedo_texture_id, 0x400)
                self.assertEqual(foliage_shader.alpha_mode, "MASK")
                np.testing.assert_allclose(
                    foliage.meshes[1].positions[0],
                    np.asarray((6.0, 7.0, 9.0)) * 0.9144,
                    rtol=0,
                    atol=1e-4,
                )

                zone = reader.read_zones()[0]
                self.assertEqual(zone.shader_tuids, (1,))
                self.assertEqual(len(zone.terrain_meshes), 1)
                self.assertEqual(len(zone.shrub_instances), 1)
                self.assertEqual(len(zone.foliage_instances), 1)
                np.testing.assert_allclose(
                    zone.terrain_meshes[0].positions[0],
                    (0.9144, 1.8288, 2.7432),
                )
                np.testing.assert_allclose(
                    zone.tie_instances[0].matrix[:3, 3],
                    np.asarray((1.0, 2.0, 3.0)) * 0.9144,
                )
                np.testing.assert_allclose(
                    zone.shrub_instances[0].matrix[:3, 3],
                    np.asarray((10.0, 20.0, 30.0)) * 0.9144,
                )
                np.testing.assert_allclose(
                    np.diag(zone.shrub_instances[0].matrix)[:3],
                    (2.0, 2.0, 2.0),
                )
                np.testing.assert_allclose(
                    zone.foliage_instances[0].matrix[:3, 3],
                    np.asarray((4.0, 5.0, 6.0)) * 0.9144,
                )

                gameplay = reader.read_gameplay_regions()[0]
                self.assertEqual(gameplay.name, "fixture")
                self.assertEqual(
                    gameplay.moby_instances[0].asset_tuid,
                    0x123,
                )
                np.testing.assert_allclose(
                    gameplay.moby_instances[0].matrix[:3, 3],
                    np.asarray((1.0, 2.0, 3.0)) * 0.9144,
                )


if __name__ == "__main__":
    unittest.main()
