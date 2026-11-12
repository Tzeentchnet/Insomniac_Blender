from __future__ import annotations

from pathlib import Path

from ..catalog import (
    CatalogAsset,
    GameCatalog,
    build_catalog_from_iso as _build_catalog_from_iso,
    filter_catalog_assets,
    load_catalog as _load_catalog,
    local_catalog_path as _local_catalog_path,
)


Resistance2Catalog = GameCatalog


def local_catalog_path(cache_directory: str | Path) -> Path:
    return _local_catalog_path(cache_directory, "RESISTANCE_2")


def load_catalog(cache_directory: str | Path) -> GameCatalog:
    return _load_catalog(cache_directory, "RESISTANCE_2")


def build_catalog_from_iso(
    iso_path: str | Path,
    output_path: str | Path,
    **kwargs,
) -> dict[str, object]:
    return _build_catalog_from_iso(
        iso_path,
        output_path,
        game_id="RESISTANCE_2",
        **kwargs,
    )


__all__ = (
    "CatalogAsset",
    "Resistance2Catalog",
    "build_catalog_from_iso",
    "filter_catalog_assets",
    "load_catalog",
    "local_catalog_path",
)
