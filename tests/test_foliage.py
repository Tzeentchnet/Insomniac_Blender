from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader

FOLIAGE_TUID = 0x1122_3344_5566_7788
SHADER_TUID = 0x8877_6655_4433_2211
ZONE_TUID = 0x1234_5678_90AB_CDEF


def _ighw(
    size: int,
    sections: tuple[tuple[int, int, int, int], ...],
) -> bytearray:
    data = bytearray(size)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, section in enumerate(sections):
        struct.pack_into(">4I", data, 0x20 + index * 16, *section)
    return data


def _write_fixture(root: Path, *, r3: bool = False) -> None:
    foliage = _ighw(
        0x188,
        (
            (0xA000, 0x60, 1, 0x2E),
            (0xA200, 0xC0, 1, 0x80 if r3 else 0xC0),
            (0x5600, 0x180, 1, 0x08),
        ),
    )
    struct.pack_into(
        ">16e",
        foliage,
        0x60,
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
    struct.pack_into(">3e4h", foliage, 0x80, 10.0, 20.0, 30.0, 0, 0, 0, 0)
    if r3:
        struct.pack_into(">B", foliage, 0xC0 + 0x37, 1)
        struct.pack_into(">I", foliage, 0xC0 + 0x50, 0x20)
        struct.pack_into(">HHf", foliage, 0xC0 + 0x58, 0, 4, 0.5)
    else:
        struct.pack_into(">I", foliage, 0xC0 + 0x50, 1)
        struct.pack_into(">I", foliage, 0xC0 + 0x58, 0x20)
        struct.pack_into(">HHf", foliage, 0xC0 + 0x60, 0, 4, 0.5)
    struct.pack_into(">Q", foliage, 0x180, SHADER_TUID)
    (root / "foliages.dat").write_bytes(foliage)

    shader = _ighw(0x20, ())
    (root / "shaders.dat").write_bytes(shader)

    foliage_instance_stride = 0x80 if r3 else 0xB0
    foliage_lookup_offset = 0x70 if r3 else 0x94
    zone = _ighw(
        0x80 + foliage_instance_stride,
        (
            (0x7400, 0x40, 1, 0x08),
            (0x7440, 0x80, 1, foliage_instance_stride),
        ),
    )
    struct.pack_into(">Q", zone, 0x40, FOLIAGE_TUID)
    struct.pack_into(
        ">16f",
        zone,
        0x80,
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
    struct.pack_into(">I", zone, 0x80 + foliage_lookup_offset, 0)
    (root / "zones.dat").write_bytes(zone)

    assetlookup = _ighw(
        0x90,
        (
            (AssetKind.SHADER.section_id, 0x60, 1, 0x10),
            (AssetKind.FOLIAGE.section_id, 0x70, 1, 0x10),
            (AssetKind.ZONE.section_id, 0x80, 1, 0x10),
        ),
    )
    struct.pack_into(">QII", assetlookup, 0x60, SHADER_TUID, 0, len(shader))
    struct.pack_into(
        ">QII",
        assetlookup,
        0x70,
        FOLIAGE_TUID,
        0,
        len(foliage),
    )
    struct.pack_into(">QII", assetlookup, 0x80, ZONE_TUID, 0, len(zone))
    (root / "assetlookup.dat").write_bytes(assetlookup)


class FoliageTests(unittest.TestCase):
    def test_decodes_r2_and_r3_foliage_geometry_and_placement(
        self,
    ) -> None:
        for reader_type, r3 in (
            (Resistance2Reader, False),
            (Resistance3Reader, True),
        ):
            with self.subTest(reader=reader_type.game_id):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _write_fixture(root, r3=r3)
                    with GameSource.open(root) as source:
                        reader = reader_type(source)
                        asset = reader.read_foliage(FOLIAGE_TUID)
                        zone = reader.read_zones()[0]
                        shader = reader.read_shader(asset.shader_tuids[0])

                    self.assertEqual(len(asset.meshes), 1)
                    expected_positions = (
                        (
                            (9.0, 22.0, 30.0),
                            (11.0, 22.0, 30.0),
                            (11.0, 18.0, 30.0),
                            (9.0, 18.0, 30.0),
                        )
                        if r3
                        else (
                            (9.0, 18.0, 30.0),
                            (11.0, 18.0, 30.0),
                            (11.0, 22.0, 30.0),
                            (9.0, 22.0, 30.0),
                        )
                    )
                    np.testing.assert_allclose(
                        asset.meshes[0].positions,
                        np.asarray(expected_positions, dtype=np.float32)
                        * 0.9144,
                    )
                    np.testing.assert_array_equal(
                        asset.meshes[0].indices,
                        (0, 1, 2, 3, 0, 2),
                    )
                    self.assertEqual(shader.alpha_mode, "MASK")
                    self.assertEqual(len(zone.foliage_instances), 1)
                    self.assertEqual(
                        zone.foliage_instances[0].asset_tuid,
                        FOLIAGE_TUID,
                    )
                    np.testing.assert_allclose(
                        zone.foliage_instances[0].matrix[:3, 3],
                        (1.0, 2.0, 3.0),
                    )
                    self.assertEqual(zone.unsupported_foliage_instances, 0)


if __name__ == "__main__":
    unittest.main()
