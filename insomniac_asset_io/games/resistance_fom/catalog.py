from __future__ import annotations

from pathlib import Path

from ..catalog import (
    CatalogAsset,
    GameCatalog,
    filter_catalog_assets,
)
from ..catalog import (
    build_catalog_from_iso as _build_catalog_from_iso,
)
from ..catalog import (
    load_catalog as _load_catalog,
)
from ..catalog import (
    local_catalog_path as _local_catalog_path,
)

ResistanceFomCatalog = GameCatalog


def local_catalog_path(cache_directory: str | Path) -> Path:
    return _local_catalog_path(cache_directory, "RESISTANCE_FOM")


def load_catalog(cache_directory: str | Path) -> GameCatalog:
    return _load_catalog(cache_directory, "RESISTANCE_FOM")


def build_catalog_from_iso(
    iso_path: str | Path,
    output_path: str | Path,
    **kwargs,
) -> dict[str, object]:
    return _build_catalog_from_iso(
        iso_path,
        output_path,
        game_id="RESISTANCE_FOM",
        **kwargs,
    )


__all__ = (
    "CatalogAsset",
    "ResistanceFomCatalog",
    "build_catalog_from_iso",
    "filter_catalog_assets",
    "load_catalog",
    "local_catalog_path",
)
