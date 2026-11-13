from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.iso import (
    game_cached_level_path,
    prepare_game_level,
)
from ..core.source import GameSource


@dataclass(frozen=True, slots=True)
class GameAdapter:
    id: str
    label: str
    engine: str
    engine_id: str
    cache_key: str
    asset_prefix: str
    bundled_catalog_path: Path | None
    create_reader: Callable[[GameSource], object]
    create_source_catalog: Callable[[str | Path | None], object] | None = None
    supports_iso: bool = True
    supports_levels: bool = True
    experimental: bool = False

    @property
    def iso_preference_name(self) -> str:
        return f"{self.cache_key}_iso_path"

    @property
    def source_preference_name(self) -> str:
        return f"{self.cache_key}_last_source_path"

    def prepare_level(
        self,
        iso_path: str | Path,
        level_name: str,
        cache_directory: str | Path,
    ) -> Path:
        if not self.supports_iso or not self.supports_levels:
            raise ValueError(f"{self.label} does not use ISO level extraction")
        return prepare_game_level(
            iso_path,
            level_name,
            cache_directory,
            cache_key=self.cache_key,
            game_label=self.label,
        )

    def cached_level_path(
        self,
        cache_directory: str | Path,
        level_name: str,
    ) -> Path:
        if not self.supports_iso or not self.supports_levels:
            raise ValueError(f"{self.label} does not use cached levels")
        return game_cached_level_path(
            cache_directory,
            level_name,
            cache_key=self.cache_key,
            game_label=self.label,
        )


def _create_resistance_2_reader(source: GameSource):
    from .resistance_2.reader import Resistance2Reader

    return Resistance2Reader(source)


def _create_resistance_3_reader(source: GameSource):
    from .resistance_3.reader import Resistance3Reader

    return Resistance3Reader(source)


def _create_burning_skies_reader(source: GameSource):
    from .resistance_burning_skies.reader import BurningSkiesReader

    return BurningSkiesReader(source)


def _create_burning_skies_catalog(path: str | Path | None):
    from .resistance_burning_skies.catalog import build_source_catalog

    return build_source_catalog(path)


_GAMES_ROOT = Path(__file__).parent


GAME_ADAPTERS = {
    "RESISTANCE_2": GameAdapter(
        id="RESISTANCE_2",
        label="Resistance 2",
        engine="Insomniac Engine v2 (PS3)",
        engine_id="V2",
        cache_key="resistance_2",
        asset_prefix="R2",
        bundled_catalog_path=(
            _GAMES_ROOT / "resistance_2" / "data" / "resistance_2_us_catalog.json"
        ),
        create_reader=_create_resistance_2_reader,
    ),
    "RESISTANCE_3": GameAdapter(
        id="RESISTANCE_3",
        label="Resistance 3",
        engine="Insomniac Engine v3 (PS3)",
        engine_id="V3",
        cache_key="resistance_3",
        asset_prefix="R3",
        bundled_catalog_path=(
            _GAMES_ROOT / "resistance_3" / "data" / "resistance_3_us_catalog.json"
        ),
        create_reader=_create_resistance_3_reader,
    ),
    "RESISTANCE_BURNING_SKIES": GameAdapter(
        id="RESISTANCE_BURNING_SKIES",
        label="Resistance: Burning Skies (Experimental)",
        engine="Nihilistic NSIRSRC (PS Vita)",
        engine_id="NIHILISTIC_VITA",
        cache_key="resistance_burning_skies",
        asset_prefix="RBS",
        bundled_catalog_path=None,
        create_reader=_create_burning_skies_reader,
        create_source_catalog=_create_burning_skies_catalog,
        supports_iso=False,
        supports_levels=False,
        experimental=True,
    ),
}


def get_game_adapter(game_id: str) -> GameAdapter:
    return GAME_ADAPTERS[game_id]
