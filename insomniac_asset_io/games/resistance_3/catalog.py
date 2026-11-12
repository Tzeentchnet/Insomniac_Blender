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


Resistance3Catalog = GameCatalog


def local_catalog_path(cache_directory: str | Path) -> Path:
    return _local_catalog_path(cache_directory, "RESISTANCE_3")


def load_catalog(cache_directory: str | Path) -> GameCatalog:
    return _load_catalog(cache_directory, "RESISTANCE_3")


def build_catalog_from_iso(
    iso_path: str | Path,
    output_path: str | Path,
    **kwargs,
) -> dict[str, object]:
    return _build_catalog_from_iso(
        iso_path,
        output_path,
        game_id="RESISTANCE_3",
        **kwargs,
    )


__all__ = (
    "CatalogAsset",
    "Resistance3Catalog",
    "build_catalog_from_iso",
    "filter_catalog_assets",
    "load_catalog",
    "local_catalog_path",
)
