from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..core.errors import InsomniacError
from ..core.iso import (
    game_cached_level_path,
    game_level_cache_is_verified,
    list_game_levels,
    prepare_game_level,
    validate_game_iso,
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
    create_catalog_reader: Callable[[GameSource], object] | None = None
    create_source_catalog: Callable[[str | Path | None], object] | None = None
    catalog_level_metadata: (
        Callable[[object, str], dict[str, object]] | None
    ) = None
    prepare_level_dependencies: (
        Callable[
            ["GameAdapter", str | Path, str, str | Path, Path],
            None,
        ]
        | None
    ) = None
    supports_iso: bool = True
    supports_levels: bool = True
    experimental: bool = False
    level_archives: tuple[str, ...] = (
        "level_cached.psarc",
        "level_uncached.psarc",
    )
    global_archives: tuple[str, ...] = (
        "global_cached.psarc",
        "global_uncached.psarc",
    )
    catalog_level_archives: tuple[str, ...] = ("level_cached.psarc",)
    catalog_global_archives: tuple[str, ...] = ()
    iso_container_member: str | None = None
    level_marker: str = "level_cached.psarc"
    level_primary_resource: str | None = "level_cached.psarc"
    iso_title_id: str | None = None
    iso_version: str | None = None
    level_local_asset_ids: bool = False

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
        cached_level = prepare_game_level(
            iso_path,
            level_name,
            cache_directory,
            cache_key=self.cache_key,
            game_label=self.label,
            level_archives=self.level_archives,
            global_archives=self.global_archives,
            primary_resource=self.level_primary_resource,
            container_member=self.iso_container_member,
            expected_title_id=self.iso_title_id,
            expected_version=self.iso_version,
        )
        if self.prepare_level_dependencies is not None:
            self.prepare_level_dependencies(
                self,
                iso_path,
                level_name,
                cache_directory,
                cached_level,
            )
        return cached_level

    def prepare_catalog_level(
        self,
        iso_path: str | Path,
        level_name: str,
        cache_directory: str | Path,
        *,
        validate_iso: bool = True,
    ) -> Path:
        if not self.supports_iso or not self.supports_levels:
            raise ValueError(f"{self.label} does not use ISO level extraction")
        primary_resource = (
            self.level_primary_resource
            if self.level_primary_resource in self.catalog_level_archives
            else None
        )
        return prepare_game_level(
            iso_path,
            level_name,
            cache_directory,
            cache_key=self.cache_key,
            game_label=self.label,
            level_archives=self.catalog_level_archives,
            global_archives=self.catalog_global_archives,
            primary_resource=primary_resource,
            container_member=self.iso_container_member,
            expected_title_id=self.iso_title_id,
            expected_version=self.iso_version,
            validate_iso_identity=validate_iso,
        )

    def validate_iso(self, iso_path: str | Path) -> dict[str, str]:
        if not self.supports_iso:
            raise ValueError(f"{self.label} does not use ISO sources")
        if self.iso_title_id is None and self.iso_version is None:
            return {}
        return validate_game_iso(
            iso_path,
            game_label=self.label,
            expected_title_id=self.iso_title_id,
            expected_version=self.iso_version,
        )

    def catalog_identifier(
        self,
        kind: Enum,
        tuid: int,
        level: str,
    ) -> str:
        kind_name = kind.name
        if self.level_local_asset_ids:
            return f"{kind_name}:{level}:{tuid:016X}"
        return f"{kind_name}:{tuid:016X}"

    def list_levels(
        self,
        iso_path: str | Path,
    ) -> tuple[str, ...]:
        if not self.supports_iso or not self.supports_levels:
            raise ValueError(f"{self.label} does not use ISO level extraction")
        return list_game_levels(
            iso_path,
            game_label=self.label,
            level_marker=self.level_marker,
            container_member=self.iso_container_member,
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
            primary_resource=self.level_primary_resource,
        )

    def is_level_cached(
        self,
        cache_directory: str | Path,
        level_name: str,
    ) -> bool:
        return game_level_cache_is_verified(
            cache_directory,
            level_name,
            cache_key=self.cache_key,
            game_label=self.label,
            level_archives=self.level_archives,
            global_archives=self.global_archives,
            container_member=self.iso_container_member,
            expected_title_id=self.iso_title_id,
            expected_version=self.iso_version,
        )


def _create_resistance_2_reader(source: GameSource):
    from .resistance_2.reader import Resistance2Reader

    return Resistance2Reader(source)


def _resistance_2_catalog_level_metadata(
    reader: object,
    _level_name: str,
) -> dict[str, object]:
    from .resistance_2.dependencies import build_texture_catalog_metadata

    return build_texture_catalog_metadata(reader)


def _prepare_resistance_2_level_dependencies(
    adapter: GameAdapter,
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    cached_level: Path,
) -> None:
    from .resistance_2.dependencies import prepare_texture_dependencies

    prepare_texture_dependencies(
        adapter,
        iso_path,
        level_name,
        cache_directory,
        cached_level,
    )


def _create_resistance_fom_reader(source: GameSource):
    from .resistance_fom.reader import ResistanceFomReader

    return ResistanceFomReader(source)


def _create_resistance_fom_catalog_reader(source: GameSource):
    from .resistance_fom.reader import ResistanceFomReader

    return ResistanceFomReader(source, metadata_only=True)


def _create_resistance_3_reader(source: GameSource):
    from .resistance_3.reader import Resistance3Reader

    return Resistance3Reader(source)


def _create_burning_skies_reader(source: GameSource):
    from .resistance_burning_skies.reader import BurningSkiesReader

    return BurningSkiesReader(source)


def _create_burning_skies_catalog(path: str | Path | None):
    from .resistance_burning_skies.catalog import build_source_catalog

    return build_source_catalog(path)


def _create_ratchet_tod_reader(source: GameSource):
    from .ratchet_tod.reader import RatchetTodReader

    return RatchetTodReader(source)


_GAMES_ROOT = Path(__file__).parent


GAME_ADAPTERS = {
    "RESISTANCE_FOM": GameAdapter(
        id="RESISTANCE_FOM",
        label="Resistance: Fall of Man",
        engine="Insomniac Engine v1 (PS3)",
        engine_id="V1",
        cache_key="resistance_fom",
        asset_prefix="RFOM",
        bundled_catalog_path=(
            _GAMES_ROOT
            / "resistance_fom"
            / "data"
            / "resistance_fom_us_v0100_catalog.json"
        ),
        create_reader=_create_resistance_fom_reader,
        create_catalog_reader=_create_resistance_fom_catalog_reader,
        level_archives=(
            "ps3levelmain.dat",
            "ps3levelverts.dat",
            "ps3leveltexs.dat",
            "ps3gameplay.dat",
        ),
        global_archives=(),
        catalog_level_archives=("ps3levelmain.dat",),
        iso_container_member="PS3_GAME/USRDIR/GAME.PSARC",
        level_marker="ps3levelmain.dat",
        level_primary_resource=None,
        iso_title_id="BCUS98107",
        iso_version="01.00",
        level_local_asset_ids=True,
    ),
    "RATCHET_TOD": GameAdapter(
        id="RATCHET_TOD",
        label="Ratchet & Clank Future: Tools of Destruction",
        engine="Insomniac Engine v1 (PS3)",
        engine_id="V1",
        cache_key="ratchet_tod",
        asset_prefix="TOD",
        bundled_catalog_path=(
            _GAMES_ROOT
            / "ratchet_tod"
            / "data"
            / "ratchet_tod_us_v0200_catalog.json"
        ),
        create_reader=_create_ratchet_tod_reader,
        level_archives=(
            "level_cached.psarc",
            "level_uncached.psarc",
            "level_textures.psarc",
        ),
        global_archives=(),
        iso_title_id="BCUS98127",
        iso_version="02.00",
        level_local_asset_ids=True,
    ),
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
        catalog_level_metadata=_resistance_2_catalog_level_metadata,
        prepare_level_dependencies=_prepare_resistance_2_level_dependencies,
        catalog_level_archives=(
            "level_cached.psarc",
            "level_uncached.psarc",
        ),
        iso_title_id="BCUS98120",
        iso_version="01.00",
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
        iso_title_id="BCUS98176",
        iso_version="01.00",
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


def detect_game_iso(iso_path: str | Path) -> GameAdapter:
    values = validate_game_iso(
        iso_path,
        game_label="Insomniac game",
    )
    title_id = values.get("TITLE_ID")
    version = values.get("VERSION") or values.get("APP_VER")
    matches = tuple(
        adapter
        for adapter in GAME_ADAPTERS.values()
        if adapter.supports_iso
        and adapter.iso_title_id is not None
        and adapter.iso_title_id == title_id
    )
    if not matches:
        supported = ", ".join(
            sorted(
                adapter.iso_title_id
                for adapter in GAME_ADAPTERS.values()
                if adapter.supports_iso
                and adapter.iso_title_id is not None
            )
        )
        raise InsomniacError(
            f"The selected ISO reports TITLE_ID "
            f"{title_id or 'missing'}, which is not supported. "
            f"Supported TITLE_ID values: {supported}"
        )
    adapter = matches[0]
    if adapter.iso_version is not None and version != adapter.iso_version:
        raise InsomniacError(
            f"{adapter.label} requires version {adapter.iso_version}, "
            f"but the selected ISO reports {version or 'no version'}"
        )
    return adapter
