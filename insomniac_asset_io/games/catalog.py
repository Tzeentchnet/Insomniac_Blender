from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from ..core.errors import InsomniacError, MissingResourceError
from ..core.iso import game_level_cache_root
from ..core.source import GameSource
from .registry import get_game_adapter
from .resistance_2.formats import AssetKind

CATALOG_SCHEMA_VERSION = 1
CATALOG_FILENAME = "disc_catalog.json"
_CATALOG_ASSET_FIELDS = (
    ("mobys", AssetKind.MOBY, True),
    ("ties", AssetKind.TIE, True),
    ("shrubs", AssetKind.SHRUB, False),
    ("foliage", AssetKind.FOLIAGE, False),
    ("materials", AssetKind.SHADER, False),
)


def normalize_texture_lookup_path(value: str | None) -> str:
    return (value or "").replace("\\", "/").strip("/").casefold()


def _catalog_integer(
    value: object,
    *,
    label: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InsomniacError(f"{label} must be an integer >= {minimum}")
    return value


def _kind_label(kind: AssetKind) -> str:
    return {
        AssetKind.MOBY: "Moby",
        AssetKind.TIE: "Tie",
        AssetKind.SHRUB: "Shrub",
        AssetKind.FOLIAGE: "Foliage",
        AssetKind.SHADER: "Material",
    }.get(kind, kind.name.title())


@dataclass(frozen=True, slots=True)
class CatalogAsset:
    identifier: str
    kind: AssetKind
    tuid: int
    levels: tuple[str, ...]
    source_name: str | None = None
    game_label: str = "Resistance 2"
    kind_label: str | None = None

    @property
    def display_name(self) -> str:
        if not self.source_name:
            return f"{self.kind_label or _kind_label(self.kind)} 0x{self.tuid:016X}"
        filename = self.source_name.replace("\\", "/").rsplit("/", 1)[-1]
        lower_filename = filename.lower()
        for suffix in (".entity.irb", ".irb", ".entity", ".smf"):
            if lower_filename.endswith(suffix):
                filename = filename[: -len(suffix)]
                break
        words = filename.replace("_", " ").replace("-", " ").split()
        return " ".join(word.capitalize() for word in words) or self.source_name

    @property
    def identity_label(self) -> str:
        kind = self.kind_label or _kind_label(self.kind)
        return f"{kind} 0x{self.tuid:016X}"

    @property
    def owner_label(self) -> str:
        owner = self.levels[0]
        if len(self.levels) > 1:
            owner = f"{owner} +{len(self.levels) - 1}"
        return owner

    @property
    def duplicate_key(self) -> str:
        return self.display_name.casefold()

    @property
    def search_text(self) -> str:
        source_name = (self.source_name or "").replace("\\", "/")
        expanded_path = (
            source_name.replace("/", " ")
            .replace("_", " ")
            .replace("-", " ")
        )
        return " ".join(
            (
                source_name,
                expanded_path,
                self.display_name,
                self.identity_label,
                f"0x{self.tuid:016X}",
                *self.levels,
            )
        ).casefold()

    def selector_label(
        self,
        *,
        show_tuid: bool,
        show_owner: bool,
    ) -> str:
        parts = [self.display_name]
        if show_tuid and self.identity_label != self.display_name:
            parts.append(self.identity_label)
        if show_owner:
            parts.append(self.owner_label)
        return " - ".join(parts)

    @property
    def label(self) -> str:
        return self.selector_label(show_tuid=True, show_owner=True)

    @property
    def description(self) -> str:
        owners = ", ".join(self.levels[:6])
        if len(self.levels) > 6:
            owners += f", and {len(self.levels) - 6} more"
        kind = self.kind_label or _kind_label(self.kind)
        identity = f"{self.game_label} {kind} 0x{self.tuid:016X}"
        if not self.source_name:
            return f"{identity}; available in {owners}"
        return f"{self.source_name}; {identity}; available in {owners}"


@dataclass(frozen=True, slots=True)
class CatalogDiscIdentity:
    size: int
    modified_ns: int
    title_id: str | None = None
    version: str | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "size": self.size,
            "modified_ns": self.modified_ns,
            "title_id": self.title_id,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class CatalogTextureOwner:
    level: str
    texture_id: int
    tuid: int
    format_code: int
    mip_count: int
    width: int
    height: int
    length: int
    lookup_paths: tuple[str, ...] = ()
    payload_sha256: str | None = None


def filter_catalog_assets(
    assets: Iterable[CatalogAsset],
    query: str,
    *,
    hide_duplicates: bool,
    kind: AssetKind | None = None,
    level: str | None = None,
) -> tuple[CatalogAsset, ...]:
    """Return the assets that satisfy every active filter.

    ``kind`` and ``level`` narrow the catalog structurally; ``query`` then
    requires every whitespace-separated fragment to appear in the asset's
    searchable text. Duplicate names are collapsed last, so the surviving
    entry always satisfies the other filters.
    """
    terms = tuple(
        term
        for term in query.casefold().replace("\\", "/").split()
        if term
    )
    matches: list[CatalogAsset] = []
    seen_names: set[str] = set()
    for asset in assets:
        if kind is not None and asset.kind is not kind:
            continue
        if level is not None and level not in asset.levels:
            continue
        search_text = asset.search_text
        if terms and not all(
            term in search_text
            or (
                len(term) > 3
                and term.endswith("s")
                and term[:-1] in search_text
            )
            for term in terms
        ):
            continue
        if hide_duplicates:
            if asset.duplicate_key in seen_names:
                continue
            seen_names.add(asset.duplicate_key)
        matches.append(asset)
    return tuple(matches)


class GameCatalog:
    def __init__(
        self,
        *,
        game_id: str,
        game_label: str,
        path: Path,
        region: str,
        levels: tuple[str, ...],
        assets: tuple[CatalogAsset, ...],
        disc: CatalogDiscIdentity | None = None,
        texture_owners: tuple[CatalogTextureOwner, ...] = (),
    ) -> None:
        self.game_id = game_id
        self.game_label = game_label
        self.path = path
        self.region = region
        self.levels = levels
        self.assets = assets
        self.disc = disc
        self.texture_owners = texture_owners
        self._assets_by_identifier = {
            asset.identifier: asset for asset in assets
        }
        self._asset_numbers_by_identifier = {
            asset.identifier: index
            for index, asset in enumerate(assets, start=1)
        }
        owners_by_id: dict[int, list[CatalogTextureOwner]] = {}
        owners_by_path: dict[str, list[CatalogTextureOwner]] = {}
        for owner in texture_owners:
            owners_by_id.setdefault(owner.texture_id, []).append(owner)
            for lookup_path in owner.lookup_paths:
                owners_by_path.setdefault(lookup_path, []).append(owner)
        self._texture_owners_by_id = {
            texture_id: tuple(owners)
            for texture_id, owners in owners_by_id.items()
        }
        self._texture_owners_by_path = {
            lookup_path: tuple(owners)
            for lookup_path, owners in owners_by_path.items()
        }

    def asset(self, identifier: str) -> CatalogAsset:
        try:
            return self._assets_by_identifier[identifier]
        except KeyError as exc:
            raise MissingResourceError(
                f"Asset catalog entry {identifier!r} is unavailable"
            ) from exc

    def asset_number(self, identifier: str) -> int:
        try:
            return self._asset_numbers_by_identifier[identifier]
        except KeyError as exc:
            raise MissingResourceError(
                f"Asset catalog entry {identifier!r} is unavailable"
            ) from exc

    def texture_candidates(
        self,
        texture_id: int,
        lookup_path: str | None = None,
    ) -> tuple[CatalogTextureOwner, ...]:
        exact = self.texture_owners_for_id(texture_id)
        if exact:
            return exact
        return self.texture_owners_for_path(lookup_path)

    def texture_owners_for_id(
        self,
        texture_id: int,
    ) -> tuple[CatalogTextureOwner, ...]:
        return self._texture_owners_by_id.get(texture_id, ())

    def texture_owners_for_path(
        self,
        lookup_path: str | None,
    ) -> tuple[CatalogTextureOwner, ...]:
        normalized = normalize_texture_lookup_path(lookup_path)
        if not normalized:
            return ()
        return self._texture_owners_by_path.get(normalized, ())


def bundled_catalog_path(game_id: str) -> Path:
    adapter = get_game_adapter(game_id)
    if adapter.bundled_catalog_path is None:
        raise MissingResourceError(
            f"{adapter.label} uses a source-driven catalog"
        )
    return adapter.bundled_catalog_path


def local_catalog_path(
    cache_directory: str | Path,
    game_id: str,
) -> Path:
    adapter = get_game_adapter(game_id)
    return (
        Path(cache_directory).expanduser()
        / adapter.cache_key
        / CATALOG_FILENAME
    )


def load_catalog(
    cache_directory: str | Path,
    game_id: str = "RESISTANCE_2",
    *,
    source_path: str | Path | None = None,
) -> GameCatalog:
    adapter = get_game_adapter(game_id)
    if adapter.create_source_catalog is not None:
        return adapter.create_source_catalog(source_path)
    local = local_catalog_path(cache_directory, game_id)
    path = local if local.is_file() else bundled_catalog_path(game_id)
    if not path.is_file():
        raise MissingResourceError(
            f"{adapter.label} import catalog was not found: {path}"
        )
    stat = path.stat()
    return _load_catalog_cached(
        str(path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        adapter.id,
        adapter.label,
    )


@lru_cache(maxsize=8)
def _load_catalog_cached(
    path_text: str,
    _modified_ns: int,
    _size: int,
    game_id: str,
    game_label: str,
) -> GameCatalog:
    path = Path(path_text)
    adapter = get_game_adapter(game_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InsomniacError(f"Could not read import catalog {path}: {exc}") from exc
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise InsomniacError(
            f"{path}: unsupported catalog schema "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("game") != game_id:
        raise InsomniacError(
            f"{path}: catalog is not for {game_label}"
        )

    raw_disc = payload.get("disc")
    disc: CatalogDiscIdentity | None = None
    if raw_disc is not None:
        if not isinstance(raw_disc, dict):
            raise InsomniacError(f"{path}: catalog disc must be an object")
        title_id = raw_disc.get("title_id")
        version = raw_disc.get("version")
        if title_id is not None and not isinstance(title_id, str):
            raise InsomniacError(
                f"{path}: catalog disc title_id must be a string"
            )
        if version is not None and not isinstance(version, str):
            raise InsomniacError(
                f"{path}: catalog disc version must be a string"
            )
        disc = CatalogDiscIdentity(
            size=_catalog_integer(
                raw_disc.get("size"),
                label=f"{path}: catalog disc size",
                minimum=1,
            ),
            modified_ns=_catalog_integer(
                raw_disc.get("modified_ns"),
                label=f"{path}: catalog disc modified_ns",
            ),
            title_id=title_id or None,
            version=version or None,
        )

    raw_levels = payload.get("levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        raise InsomniacError(f"{path}: catalog contains no levels")
    raw_level_entries: list[tuple[str, dict[str, object]]] = []
    level_ids: list[str] = []
    for raw_level in raw_levels:
        if not isinstance(raw_level, dict):
            raise InsomniacError(f"{path}: malformed level catalog entry")
        level_id = raw_level.get("id")
        if not isinstance(level_id, str) or not level_id:
            raise InsomniacError(f"{path}: level catalog entry has no ID")
        if level_id in level_ids:
            raise InsomniacError(f"{path}: duplicate level {level_id!r}")
        level_ids.append(level_id)
        raw_level_entries.append((level_id, raw_level))

    indexed_textures: dict[str, list[dict[str, object]]] = {}
    raw_texture_index = payload.get("texture_index", {})
    if not isinstance(raw_texture_index, dict):
        raise InsomniacError(
            f"{path}: catalog texture_index must be an object"
        )
    known_levels = set(level_ids)
    for raw_texture_id, raw_records in raw_texture_index.items():
        if not isinstance(raw_texture_id, str) or not isinstance(
            raw_records,
            list,
        ):
            raise InsomniacError(
                f"{path}: malformed catalog texture index entry"
            )
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                raise InsomniacError(
                    f"{path}: texture {raw_texture_id!r} has a "
                    "malformed index record"
                )
            raw_owners = raw_record.get("owners")
            if not isinstance(raw_owners, list) or not all(
                isinstance(owner, str) and owner
                for owner in raw_owners
            ):
                raise InsomniacError(
                    f"{path}: texture {raw_texture_id!r} has malformed "
                    "owners"
                )
            for owner in raw_owners:
                if owner not in known_levels:
                    raise InsomniacError(
                        f"{path}: texture {raw_texture_id!r} names "
                        f"unknown owner {owner!r}"
                    )
                expanded = {
                    key: value
                    for key, value in raw_record.items()
                    if key != "owners"
                }
                expanded["id"] = raw_texture_id
                indexed_textures.setdefault(owner, []).append(expanded)

    raw_names = payload.get("names", {})
    if not isinstance(raw_names, dict):
        raise InsomniacError(f"{path}: catalog names must be an object")
    names: dict[str, str] = {}
    for identifier, source_name in raw_names.items():
        if (
            not isinstance(identifier, str)
            or not isinstance(source_name, str)
            or not source_name
        ):
            raise InsomniacError(f"{path}: malformed catalog name entry")
        names[identifier] = source_name
    owners: dict[tuple[str, AssetKind, int], list[str]] = {}
    texture_owners: list[CatalogTextureOwner] = []
    texture_owner_keys: set[tuple[str, int]] = set()
    for level_id, raw_level in raw_level_entries:
        for field, kind, required in _CATALOG_ASSET_FIELDS:
            values = raw_level.get(field, [] if not required else None)
            if not isinstance(values, list):
                raise InsomniacError(
                    f"{path}: level {level_id!r} has no {field} list"
                )
            for value in values:
                try:
                    tuid = int(value, 16)
                except (TypeError, ValueError) as exc:
                    raise InsomniacError(
                        f"{path}: invalid {field} TUID {value!r}"
                    ) from exc
                identifier = adapter.catalog_identifier(
                    kind,
                    tuid,
                    level_id,
                )
                owners.setdefault(
                    (identifier, kind, tuid),
                    [],
                ).append(level_id)
        raw_level_textures = raw_level.get("textures", [])
        if not isinstance(raw_level_textures, list):
            raise InsomniacError(
                f"{path}: level {level_id!r} textures must be a list"
            )
        raw_textures = [
            *raw_level_textures,
            *indexed_textures.get(level_id, ()),
        ]
        for raw_texture in raw_textures:
            if not isinstance(raw_texture, dict):
                raise InsomniacError(
                    f"{path}: level {level_id!r} has a malformed "
                    "texture owner"
                )
            raw_texture_id = raw_texture.get("id")
            raw_tuid = raw_texture.get("tuid")
            try:
                texture_id = int(raw_texture_id, 16)
                tuid = int(raw_tuid, 16)
            except (TypeError, ValueError) as exc:
                raise InsomniacError(
                    f"{path}: level {level_id!r} has invalid texture "
                    f"identity {raw_texture_id!r}/{raw_tuid!r}"
                ) from exc
            if not 0 <= texture_id <= 0xFFFF_FFFF:
                raise InsomniacError(
                    f"{path}: level {level_id!r} texture ID "
                    f"{raw_texture_id!r} is not a u32"
                )
            if not 0 <= tuid <= 0xFFFF_FFFF_FFFF_FFFF:
                raise InsomniacError(
                    f"{path}: level {level_id!r} texture TUID "
                    f"{raw_tuid!r} is not a u64"
                )
            if tuid & 0xFFFF_FFFF != texture_id:
                raise InsomniacError(
                    f"{path}: level {level_id!r} texture "
                    f"0x{texture_id:08X} does not match TUID "
                    f"0x{tuid:016X}"
                )
            owner_key = (level_id, texture_id)
            if owner_key in texture_owner_keys:
                raise InsomniacError(
                    f"{path}: level {level_id!r} repeats texture "
                    f"0x{texture_id:08X}"
                )
            texture_owner_keys.add(owner_key)
            raw_paths = raw_texture.get("paths", [])
            if not isinstance(raw_paths, list) or not all(
                isinstance(value, str) and value
                for value in raw_paths
            ):
                raise InsomniacError(
                    f"{path}: level {level_id!r} texture "
                    f"0x{texture_id:08X} has malformed paths"
                )
            lookup_paths = tuple(
                sorted(
                    {
                        normalize_texture_lookup_path(value)
                        for value in raw_paths
                    }
                )
            )
            if any(not value for value in lookup_paths):
                raise InsomniacError(
                    f"{path}: level {level_id!r} texture "
                    f"0x{texture_id:08X} has an empty path"
                )
            payload_sha256 = raw_texture.get("sha256")
            if payload_sha256 is not None:
                if (
                    not isinstance(payload_sha256, str)
                    or len(payload_sha256) != 64
                    or any(
                        character not in "0123456789abcdefABCDEF"
                        for character in payload_sha256
                    )
                ):
                    raise InsomniacError(
                        f"{path}: level {level_id!r} texture "
                        f"0x{texture_id:08X} has an invalid SHA-256 digest"
                    )
                payload_sha256 = payload_sha256.casefold()
            texture_owners.append(
                CatalogTextureOwner(
                    level=level_id,
                    texture_id=texture_id,
                    tuid=tuid,
                    format_code=_catalog_integer(
                        raw_texture.get("format_code"),
                        label=(
                            f"{path}: level {level_id!r} texture "
                            f"0x{texture_id:08X} format_code"
                        ),
                    ),
                    mip_count=_catalog_integer(
                        raw_texture.get("mip_count"),
                        label=(
                            f"{path}: level {level_id!r} texture "
                            f"0x{texture_id:08X} mip_count"
                        ),
                        minimum=1,
                    ),
                    width=_catalog_integer(
                        raw_texture.get("width"),
                        label=(
                            f"{path}: level {level_id!r} texture "
                            f"0x{texture_id:08X} width"
                        ),
                        minimum=1,
                    ),
                    height=_catalog_integer(
                        raw_texture.get("height"),
                        label=(
                            f"{path}: level {level_id!r} texture "
                            f"0x{texture_id:08X} height"
                        ),
                        minimum=1,
                    ),
                    length=_catalog_integer(
                        raw_texture.get("length"),
                        label=(
                            f"{path}: level {level_id!r} texture "
                            f"0x{texture_id:08X} length"
                        ),
                    ),
                    lookup_paths=lookup_paths,
                    payload_sha256=payload_sha256,
                )
            )

    levels = tuple(
        sorted(
            level_ids,
            key=lambda value: (value.casefold() != "lobby", value.casefold()),
        )
    )
    assets = tuple(
        CatalogAsset(
            identifier=identifier,
            kind=kind,
            tuid=tuid,
            levels=tuple(
                sorted(
                    asset_levels,
                    key=lambda value: (
                        value.casefold() != "lobby",
                        value.casefold(),
                    ),
                )
            ),
            source_name=names.get(identifier),
            game_label=game_label,
            kind_label=_kind_label(kind),
        )
        for (identifier, kind, tuid), asset_levels in sorted(
            owners.items(),
            key=lambda item: (
                "lobby" not in {
                    level.casefold() for level in item[1]
                },
                item[0][1] is not AssetKind.MOBY,
                item[0][0].casefold(),
            ),
        )
    )
    return GameCatalog(
        game_id=game_id,
        game_label=game_label,
        path=path,
        region=str(payload.get("region") or "Custom"),
        levels=levels,
        assets=assets,
        disc=disc,
        texture_owners=tuple(
            sorted(
                texture_owners,
                key=lambda owner: (
                    owner.texture_id,
                    owner.level.casefold(),
                ),
            )
        ),
    )


def _group_texture_index(
    catalog_levels: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    payload_levels: list[dict[str, object]] = []
    grouped: dict[
        tuple[object, ...],
        dict[str, object],
    ] = {}
    for level in catalog_levels:
        level_id = str(level["id"])
        payload_levels.append(
            {
                key: value
                for key, value in level.items()
                if key != "textures"
            }
        )
        textures = level.get("textures")
        if textures is None:
            continue
        if not isinstance(textures, list):
            raise InsomniacError(
                f"Catalog level {level_id!r} textures must be a list"
            )
        for texture in textures:
            if not isinstance(texture, dict):
                raise InsomniacError(
                    f"Catalog level {level_id!r} has malformed "
                    "texture metadata"
                )
            key = (
                texture.get("id"),
                texture.get("tuid"),
                texture.get("format_code"),
                texture.get("mip_count"),
                texture.get("width"),
                texture.get("height"),
                texture.get("length"),
                texture.get("sha256"),
            )
            record = grouped.get(key)
            if record is None:
                record = {
                    "tuid": texture.get("tuid"),
                    "format_code": texture.get("format_code"),
                    "mip_count": texture.get("mip_count"),
                    "width": texture.get("width"),
                    "height": texture.get("height"),
                    "length": texture.get("length"),
                    "sha256": texture.get("sha256"),
                    "owners": set(),
                    "paths": set(),
                }
                grouped[key] = record
            owners = record["owners"]
            paths = record["paths"]
            assert isinstance(owners, set)
            assert isinstance(paths, set)
            owners.add(level_id)
            raw_paths = texture.get("paths", ())
            if not isinstance(raw_paths, list) or not all(
                isinstance(path, str) for path in raw_paths
            ):
                raise InsomniacError(
                    f"Catalog texture {texture.get('id')!r} has "
                    "malformed paths"
                )
            paths.update(raw_paths)

    texture_index: dict[str, list[dict[str, object]]] = {}
    for key, record in sorted(
        grouped.items(),
        key=lambda item: (
            str(item[0][0]),
            str(item[0][1]),
            str(item[0][7]),
        ),
    ):
        texture_id = key[0]
        if not isinstance(texture_id, str):
            raise InsomniacError(
                "Catalog texture metadata has no string ID"
            )
        owners = record["owners"]
        paths = record["paths"]
        assert isinstance(owners, set)
        assert isinstance(paths, set)
        compact = {
            field: value
            for field, value in record.items()
            if field not in {"owners", "paths"}
        }
        compact["owners"] = sorted(owners, key=str.casefold)
        compact["paths"] = sorted(paths)
        texture_index.setdefault(texture_id, []).append(compact)
    return payload_levels, texture_index


def build_catalog_from_iso(
    iso_path: str | Path,
    output_path: str | Path,
    *,
    game_id: str = "RESISTANCE_2",
    region: str = "Custom",
    work_directory: str | Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    adapter = get_game_adapter(game_id)
    iso = Path(iso_path).expanduser().resolve()
    if not iso.is_file():
        raise MissingResourceError(
            f"{adapter.label} ISO was not found: {iso}"
        )
    iso_values = adapter.validate_iso(iso)
    iso_stat = iso.stat()
    levels = adapter.list_levels(iso)
    work_parent = (
        Path(work_directory).expanduser().resolve()
        if work_directory is not None
        else None
    )
    if work_parent is not None:
        work_parent.mkdir(parents=True, exist_ok=True)

    catalog_levels: list[dict[str, object]] = []
    asset_names: dict[str, str] = {}
    with tempfile.TemporaryDirectory(
        prefix=f"{adapter.cache_key}_catalog_",
        dir=work_parent,
    ) as temporary:
        for index, level in enumerate(levels, start=1):
            if progress is not None:
                progress(index, len(levels), level)
            source_path = adapter.prepare_catalog_level(
                iso,
                level,
                temporary,
                validate_iso=False,
            )
            cache_root = game_level_cache_root(
                temporary,
                level,
                cache_key=adapter.cache_key,
                game_label=adapter.label,
            )
            try:
                with GameSource.open(source_path) as source:
                    reader_factory = (
                        adapter.create_catalog_reader
                        or adapter.create_reader
                    )
                    reader = reader_factory(source)
                    pointers_by_field = {
                        field: reader.asset_pointers(kind)
                        for field, kind, _required in _CATALOG_ASSET_FIELDS
                    }
                    for field, kind, _required in _CATALOG_ASSET_FIELDS:
                        pointers = pointers_by_field[field]
                        for pointer in pointers:
                            identifier = adapter.catalog_identifier(
                                kind,
                                pointer.tuid,
                                level,
                            )
                            if identifier in asset_names:
                                continue
                            source_name = reader.read_asset_name(
                                kind,
                                pointer.tuid,
                            )
                            if source_name:
                                asset_names[identifier] = source_name
                    level_assets = {
                        field: [
                            f"0x{pointer.tuid:016X}"
                            for pointer in pointers_by_field[field]
                        ]
                        for field, _kind, _required in _CATALOG_ASSET_FIELDS
                    }
                    level_metadata: dict[str, object] = {}
                    if adapter.catalog_level_metadata is not None:
                        level_metadata = adapter.catalog_level_metadata(
                            reader,
                            level,
                        )
                        if not isinstance(level_metadata, dict):
                            raise InsomniacError(
                                f"{adapter.label} catalog metadata hook "
                                "did not return an object"
                            )
                        reserved = {
                            "id",
                            *(
                                field
                                for field, _kind, _required
                                in _CATALOG_ASSET_FIELDS
                            ),
                        }
                        overlap = reserved.intersection(level_metadata)
                        if overlap:
                            raise InsomniacError(
                                f"{adapter.label} catalog metadata hook "
                                "replaced reserved fields: "
                                + ", ".join(sorted(overlap))
                            )
            finally:
                if cache_root.is_dir():
                    shutil.rmtree(cache_root)
            catalog_levels.append(
                {
                    "id": level,
                    **level_assets,
                    **level_metadata,
                }
            )

    disc = {
        "size": iso_stat.st_size,
        "modified_ns": iso_stat.st_mtime_ns,
    }
    title_id = iso_values.get("TITLE_ID")
    version = iso_values.get("VERSION") or iso_values.get("APP_VER")
    if title_id:
        disc["title_id"] = title_id
    if version:
        disc["version"] = version
    payload_levels, texture_index = _group_texture_index(catalog_levels)
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "game": adapter.id,
        "region": region,
        "disc": disc,
        "levels": payload_levels,
        "names": asset_names,
    }
    if texture_index:
        payload["texture_index"] = texture_index
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_output.replace(output)

    unique_assets = {
        adapter.catalog_identifier(
            kind,
            int(value, 16),
            str(level["id"]),
        )
        for level in catalog_levels
        for field, kind, _required in _CATALOG_ASSET_FIELDS
        for value in level[field]
    }
    report = {
        "catalog": str(output),
        "levels": len(catalog_levels),
        "asset_references": sum(
            sum(
                len(level[field])
                for field, _kind, _required in _CATALOG_ASSET_FIELDS
            )
            for level in catalog_levels
        ),
        "unique_assets": len(unique_assets),
        "named_assets": len(asset_names),
    }
    for field, kind, _required in _CATALOG_ASSET_FIELDS:
        prefix = f"{kind.name}:"
        report[field] = sum(
            identifier.startswith(prefix) for identifier in unique_assets
        )
        report[f"named_{field}"] = sum(
            identifier.startswith(prefix) for identifier in asset_names
        )
    texture_records = tuple(
        texture
        for level in catalog_levels
        for texture in level.get("textures", ())
        if isinstance(texture, dict)
    )
    if texture_records:
        report["texture_owners"] = len(texture_records)
        report["unique_textures"] = len(
            {
                texture.get("id")
                for texture in texture_records
            }
        )
        report["texture_paths"] = len(
            {
                lookup_path
                for texture in texture_records
                for lookup_path in texture.get("paths", ())
                if isinstance(lookup_path, str)
            }
        )
    return report
