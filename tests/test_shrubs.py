from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.errors import FormatError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader

SHRUB_TUID = 0x1122_3344_5566_7788
SHADER_TUID = 0x8877_6655_4433_2211
ZONE_TUID = 0x1234_5678_90AB_CDEF
SHRUB_NAME = "objects/plant/test_shrub/test_shrub.entity.irb"


def _ighw(
    size: int,
    sections: tuple[tuple[int, int, int, int], ...],
) -> bytearray:
    data = bytearray(size)
    struct.pack_into(">4sHHI", data, 0, b"IGHW", 1, 1, len(sections))
    for index, section in enumerate(sections):
        struct.pack_into(">4I", data, 0x20 + index * 16, *section)
    return data


def _write_fixture(root: Path, *, shrub_index: int = 0) -> None:
    shrub = _ighw(
        0x28C,
        (
            (0xB300, 0x80, 1, 0x30),
            (0xB400, 0xC0, 1, 0x06),
            (0x5600, 0x100, 1, 0x08),
            (0xB700, 0x180, 1, len(SHRUB_NAME) + 1),
            (0xB100, 0x200, 1, 0x80),
            (0x3150, 0x280, 1, 0x0C),
        ),
    )
    vertices = (
        ((32767, 0, 0, 0), (0.0, 0.0)),
        ((0, 32767, 0, 0), (1.0, 0.0)),
        ((0, 0, 32767, 0), (0.0, 1.0)),
    )
    for index, (position, uv) in enumerate(vertices):
        struct.pack_into(
            ">4h2eI",
            shrub,
            0x80 + index * 0x10,
            *position,
            *uv,
            0,
        )
    struct.pack_into(">3H", shrub, 0xC0, 0, 1, 2)
    struct.pack_into(">Q", shrub, 0x100, SHADER_TUID)
    shrub[0x180 : 0x180 + len(SHRUB_NAME) + 1] = (
        SHRUB_NAME.encode() + b"\0"
    )
    struct.pack_into(
        ">4I4fIIQ",
        shrub,
        0x200,
        0,
        0,
        3,
        0x10000,
        0.3,
        75.0,
        10.0,
        0.2,
        0,
        0x180,
        SHRUB_TUID,
    )
    struct.pack_into(">3I", shrub, 0x280, 0, 1, 2)
    (root / "shrubs.dat").write_bytes(shrub)

    zone = _ighw(
        0xC0,
        (
            (0x7500, 0x40, 1, 0x08),
            (0x7540, 0x80, 1, 0x40),
        ),
    )
    struct.pack_into(">Q", zone, 0x40, SHRUB_TUID)
    struct.pack_into(">3f", zone, 0x80, 1.0, 2.0, 3.0)
    struct.pack_into(">f", zone, 0x8C, 2.0)
    struct.pack_into(">3f", zone, 0x90, 1.0, 0.0, 0.0)
    struct.pack_into(">3f", zone, 0xA0, 0.0, 1.0, 0.0)
    struct.pack_into(">I", zone, 0xBC, shrub_index)
    (root / "zones.dat").write_bytes(zone)

    assetlookup = _ighw(
        0x80,
        (
            (AssetKind.SHRUB.section_id, 0x60, 1, 0x10),
            (AssetKind.ZONE.section_id, 0x70, 1, 0x10),
        ),
    )
    struct.pack_into(
        ">QII",
        assetlookup,
        0x60,
        SHRUB_TUID,
        0,
        len(shrub),
    )
    struct.pack_into(">QII", assetlookup, 0x70, ZONE_TUID, 0, len(zone))
    (root / "assetlookup.dat").write_bytes(assetlookup)


class ShrubTests(unittest.TestCase):
    def test_decodes_r2_and_r3_shrub_geometry_and_placement(self) -> None:
        for reader_type in (Resistance2Reader, Resistance3Reader):
            with self.subTest(reader=reader_type.game_id):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _write_fixture(root)
                    with GameSource.open(root) as source:
                        reader = reader_type(source)
                        name = reader.read_asset_name(
                            AssetKind.SHRUB,
                            SHRUB_TUID,
                        )
                        asset = reader.read_shrub(SHRUB_TUID)
                        zone = reader.read_zones()[0]

                    self.assertEqual(name, SHRUB_NAME)
                    self.assertEqual(asset.tuid, SHRUB_TUID)
                    self.assertEqual(asset.shader_tuids, (SHADER_TUID,))
                    self.assertEqual(len(asset.meshes), 1)
                    mesh = asset.meshes[0]
                    np.testing.assert_allclose(
                        mesh.positions,
                        np.eye(3, dtype=np.float32) * 9.144,
                        rtol=1e-5,
                    )
                    np.testing.assert_allclose(
                        mesh.uvs,
                        ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
                    )
                    np.testing.assert_array_equal(mesh.indices, (0, 1, 2))

                    self.assertEqual(len(zone.shrub_instances), 1)
                    instance = zone.shrub_instances[0]
                    self.assertEqual(instance.asset_tuid, SHRUB_TUID)
                    self.assertEqual(instance.name, "ShrubInstance_0000")
                    np.testing.assert_allclose(
                        instance.matrix[:3, :3],
                        np.eye(3, dtype=np.float32) * 2.0,
                    )
                    np.testing.assert_allclose(
                        instance.matrix[:3, 3],
                        np.asarray((1.0, 2.0, 3.0)) * 0.9144,
                    )
                    self.assertEqual(zone.unsupported_shrub_instances, 0)

    def test_rejects_out_of_range_shrub_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, shrub_index=1)
            with GameSource.open(root) as source:
                reader = Resistance2Reader(source)
                with self.assertRaisesRegex(
                    FormatError,
                    "shrub instance 0 references lookup 1",
                ):
                    reader.read_zones()


if __name__ == "__main__":
    unittest.main()
