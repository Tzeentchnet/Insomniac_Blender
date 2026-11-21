from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from insomniac_asset_io.core.errors import AmbiguousResourceError
from insomniac_asset_io.games.catalog import (
    CatalogDiscIdentity,
    CatalogTextureOwner,
    GameCatalog,
)
from insomniac_asset_io.games.resistance_2.dependencies import (
    build_texture_catalog_metadata,
    plan_texture_dependencies,
)
from insomniac_asset_io.games.resistance_2.formats import TextureInfo
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


def _owner(
    level: str,
    texture_id: int,
    *,
    tuid: int | None = None,
    path: str = "fixture/texture",
    width: int = 64,
    digest: str = "a" * 64,
) -> CatalogTextureOwner:
    return CatalogTextureOwner(
        level=level,
        texture_id=texture_id,
        tuid=tuid if tuid is not None else 0x1234_0000_0000 | texture_id,
        format_code=0x88,
        mip_count=7,
        width=width,
        height=64,
        length=4096,
        lookup_paths=(path,),
        payload_sha256=digest,
    )


def _catalog(
    path: Path,
    owners: tuple[CatalogTextureOwner, ...],
) -> GameCatalog:
    return GameCatalog(
        game_id="RESISTANCE_2",
        game_label="Resistance 2",
        path=path,
        region="Test",
        levels=("selected", "owner_a", "owner_b"),
        assets=(),
        disc=CatalogDiscIdentity(
            size=123,
            modified_ns=456,
            title_id="BCUS98120",
            version="01.00",
        ),
        texture_owners=owners,
    )


class Resistance2TextureDependencyTests(unittest.TestCase):
    def test_builds_metadata_only_texture_owner_records(self):
        reader = Resistance2Reader.__new__(Resistance2Reader)
        reader._texture_paths_by_id = {
            0x1234_5678: (
                "art/fixture_c",
                "art/shared_c",
            )
        }
        reader.texture_infos = {
            0x1234_5678: TextureInfo(
                id=0x1234_5678,
                tuid=0xABCD_EF01_1234_5678,
                format_code=0x88,
                mip_count=9,
                width=256,
                height=128,
                offset=0x2000,
                length=0x8000,
            )
        }
        reader.texture_payload_digest = mock.Mock(
            return_value="f" * 64,
        )

        metadata = build_texture_catalog_metadata(reader)

        self.assertEqual(
            metadata,
            {
                "textures": [
                    {
                        "id": "0x12345678",
                        "tuid": "0xABCDEF0112345678",
                        "format_code": 0x88,
                        "mip_count": 9,
                        "width": 256,
                        "height": 128,
                        "length": 0x8000,
                        "sha256": "f" * 64,
                        "paths": [
                            "art/fixture_c",
                            "art/shared_c",
                        ],
                    }
                ]
            },
        )

    def test_selects_one_owner_for_identical_verified_payloads(self):
        requested = 0x1234_5678
        shared_tuid = 0xABCD_EF01_1234_5678
        owners = (
            _owner("owner_a", requested, tuid=shared_tuid),
            _owner("owner_b", requested, tuid=shared_tuid),
        )
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        reader.has_available_texture_for_path.return_value = True
        with tempfile.TemporaryDirectory() as temporary:
            dependencies = plan_texture_dependencies(
                reader,
                _catalog(Path(temporary) / "catalog.json", owners),
                "selected",
            )

        self.assertEqual(len(dependencies), 1)
        self.assertEqual(
            dependencies[0].levels,
            ("owner_a",),
        )
        reader.has_available_texture_for_path.assert_not_called()

    def test_rejects_conflicting_exact_owner_metadata(self):
        requested = 0x1234_5678
        owners = (
            _owner("owner_a", requested),
            _owner(
                "owner_b",
                requested,
                width=128,
            ),
        )
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                AmbiguousResourceError,
                "conflicting catalog payloads",
            ):
                plan_texture_dependencies(
                    reader,
                    _catalog(Path(temporary) / "catalog.json", owners),
                    "selected",
                )

    def test_uses_authored_path_when_requested_id_has_no_owner(self):
        requested = 0x1111_1111
        owners = (
            _owner("owner_a", 0x2222_2222),
            _owner("owner_b", 0x3333_3333),
        )
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        reader.has_available_texture_for_path.return_value = False
        with tempfile.TemporaryDirectory() as temporary:
            dependencies = plan_texture_dependencies(
                reader,
                _catalog(Path(temporary) / "catalog.json", owners),
                "selected",
            )

        self.assertEqual(
            dependencies[0].levels,
            ("owner_a",),
        )

    def test_rejects_same_metadata_with_conflicting_payload_digests(self):
        requested = 0x1234_5678
        shared_tuid = 0xABCD_EF01_1234_5678
        owners = (
            _owner(
                "owner_a",
                requested,
                tuid=shared_tuid,
                digest="a" * 64,
            ),
            _owner(
                "owner_b",
                requested,
                tuid=shared_tuid,
                digest="b" * 64,
            ),
        )
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                AmbiguousResourceError,
                "conflicting catalog payloads",
            ):
                plan_texture_dependencies(
                    reader,
                    _catalog(Path(temporary) / "catalog.json", owners),
                    "selected",
                )

    def test_keeps_available_path_alias_when_no_exact_owner_exists(self):
        requested = 0x1111_1111
        owners = (_owner("owner_a", 0x2222_2222),)
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        reader.has_available_texture_for_path.return_value = True
        with tempfile.TemporaryDirectory() as temporary:
            dependencies = plan_texture_dependencies(
                reader,
                _catalog(Path(temporary) / "catalog.json", owners),
                "selected",
            )

        self.assertEqual(dependencies, ())

    def test_skips_exact_owner_when_local_alias_has_same_payload(self):
        requested = 0x1111_1111
        alias = 0x2222_2222
        owners = (
            _owner("owner_a", requested),
            _owner("selected", alias),
        )
        reader = mock.create_autospec(
            Resistance2Reader,
            instance=True,
        )
        reader.missing_exact_texture_references.return_value = (
            (requested, "fixture/texture"),
        )
        reader.available_texture_ids_for_path.return_value = (alias,)
        with tempfile.TemporaryDirectory() as temporary:
            dependencies = plan_texture_dependencies(
                reader,
                _catalog(Path(temporary) / "catalog.json", owners),
                "selected",
            )

        self.assertEqual(dependencies, ())


if __name__ == "__main__":
    unittest.main()
