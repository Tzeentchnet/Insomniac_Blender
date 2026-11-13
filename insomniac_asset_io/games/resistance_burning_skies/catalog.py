from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ...core.source import GameSource
from ..catalog import CatalogAsset, GameCatalog
from ..resistance_2.formats import AssetKind
from .reader import BurningSkiesReader

GAME_ID = "RESISTANCE_BURNING_SKIES"
GAME_LABEL = "Resistance: Burning Skies (Experimental)"
ARCHIVE_LEVEL = "archive"


def build_source_catalog(
    source_path: str | Path | None,
) -> GameCatalog:
    if not source_path:
        return _empty_catalog()
    path = Path(source_path).expanduser()
    if not path.exists():
        return _empty_catalog(path)
    resolved = path.resolve()
    stat = resolved.stat()
    return _build_source_catalog_cached(
        str(resolved),
        stat.st_mtime_ns,
        stat.st_size,
    )


@lru_cache(maxsize=4)
def _build_source_catalog_cached(
    path_text: str,
    _modified_ns: int,
    _size: int,
) -> GameCatalog:
    path = Path(path_text)
    with GameSource.open(path) as source:
        reader = BurningSkiesReader(source)
        assets = tuple(
            CatalogAsset(
                identifier=f"MOBY:{pointer.tuid:016X}",
                kind=AssetKind.MOBY,
                tuid=pointer.tuid,
                levels=(ARCHIVE_LEVEL,),
                source_name=reader.read_asset_name(
                    AssetKind.MOBY,
                    pointer.tuid,
                ),
                game_label=GAME_LABEL,
                kind_label="Model",
            )
            for pointer in reader.asset_pointers(AssetKind.MOBY)
        )
    return GameCatalog(
        game_id=GAME_ID,
        game_label=GAME_LABEL,
        path=path,
        region="Experimental Vita archive",
        levels=(ARCHIVE_LEVEL,),
        assets=assets,
    )


def _empty_catalog(path: Path | None = None) -> GameCatalog:
    return GameCatalog(
        game_id=GAME_ID,
        game_label=GAME_LABEL,
        path=path or Path(),
        region="Experimental Vita archive",
        levels=(ARCHIVE_LEVEL,),
        assets=(),
    )
