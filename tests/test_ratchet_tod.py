from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

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
    )
    main = _ighw(0xA00, sections)

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

    struct.pack_into(">I", main, 0x340, 0x440)
    main[0x351] = 0
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
    (root / "main.dat").write_bytes(main)

    vertex_sections = (
        (0x9000, 0x100, 1, 0xC8),
        (0x9100, 0x200, 1, 0x12),
    )
    vertices = _ighw(0x220, vertex_sections)
    for base, stride in ((0x100, 0x14), (0x140, 0x14), (0x180, 0x18)):
        _vertex(vertices, base, (0, 0, 0), stride=stride)
        _vertex(vertices, base + stride, (256, 0, 0), stride=stride)
        _vertex(vertices, base + stride * 2, (0, 256, 0), stride=stride)
    struct.pack_into(">9H", vertices, 0x200, 0, 1, 2, 0, 1, 2, 0, 1, 2)
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

                tie_id = reader.asset_pointers(AssetKind.TIE)[0].tuid
                tie = reader.read_tie(tie_id)
                self.assertEqual(len(tie.meshes), 1)
                self.assertEqual(tie.shader_tuids, (0,))

                zone = reader.read_zones()[0]
                self.assertEqual(len(zone.terrain_meshes), 1)
                self.assertEqual(len(zone.tie_instances), 1)
                np.testing.assert_allclose(
                    zone.tie_instances[0].matrix[:3, 3],
                    np.asarray((1.0, 2.0, 3.0)) * 0.9144,
                )

                gameplay = reader.read_gameplay_regions()[0]
                self.assertEqual(gameplay.name, "art")
                self.assertEqual(gameplay.moby_instances[0].asset_tuid, 0)


if __name__ == "__main__":
    unittest.main()
