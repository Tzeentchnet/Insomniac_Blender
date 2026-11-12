from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import logging

import bpy
from bpy.app.handlers import persistent

from ..core.errors import MissingResourceError
from ..core.iso import DEFAULT_CACHE_DIRECTORY
from ..games.resistance_2.catalog import (
    CatalogAsset,
    Resistance2Catalog,
    filter_catalog_assets,
    load_catalog,
)
from ..games.resistance_2.formats import AssetKind


AssetEnumItem = tuple[str, str, str, int, int]


_ASSET_ITEMS: list[AssetEnumItem] = []
_LEVEL_ITEMS: list[tuple[str, str, str]] = []
_LEVEL_FILTER_ITEMS: list[tuple[str, str, str]] = []
DEFAULT_ASSET_IDENTIFIER = "MOBY:00897CA26A876DEF"
ALL_KINDS = "ALL"
ALL_LEVELS = "__ALL__"
LOGGER = logging.getLogger(__name__)
_PERSISTENT_SETTING_NAMES = (
    "game",
    "source_path",
    "asset_filter",
    "asset_kind_filter",
    "asset_level_filter",
    "asset_tuid",
    "level_id",
)
_SESSION_SETTINGS: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class AssetSelectorState:
    """Everything the sidebar needs to draw the asset selector."""

    items: tuple[AssetEnumItem, ...]
    total_count: int
    match_count: int
    filters_active: bool
    selected: CatalogAsset | None
    selected_label: str
    selection_matches_filter: bool


def _current_scene_settings() -> bpy.types.PropertyGroup | None:
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not hasattr(scene, "insomniac_asset_io"):
        return None
    return scene.insomniac_asset_io


@persistent
def _remember_scene_settings(_unused: object = None) -> None:
    settings = _current_scene_settings()
    if settings is None:
        return
    for name in _PERSISTENT_SETTING_NAMES:
        _SESSION_SETTINGS[name] = getattr(settings, name)


@persistent
def _restore_scene_settings(_unused: object = None) -> None:
    settings = _current_scene_settings()
    if settings is None:
        return
    for name, value in _SESSION_SETTINGS.items():
        try:
            setattr(settings, name, value)
        except (TypeError, ValueError) as exc:
            LOGGER.warning(
                "Could not restore Insomniac setting %s=%r: %s",
                name,
                value,
                exc,
            )


def register_scene_settings_persistence() -> None:
    if _SESSION_SETTINGS:
        _restore_scene_settings()
    else:
        _remember_scene_settings()
    if _remember_scene_settings not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_remember_scene_settings)
    if _restore_scene_settings not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_scene_settings)


def unregister_scene_settings_persistence() -> None:
    _remember_scene_settings()
    if _remember_scene_settings in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_remember_scene_settings)
    if _restore_scene_settings in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_scene_settings)


def _cache_directory(context: bpy.types.Context | None) -> str:
    if context is None:
        return str(DEFAULT_CACHE_DIRECTORY)
    from .preferences import addon_preferences

    preferences = addon_preferences(context)
    if preferences is None:
        return str(DEFAULT_CACHE_DIRECTORY)
    return bpy.path.abspath(preferences.asset_cache_directory)


def _selector_preferences(
    context: bpy.types.Context | None,
) -> tuple[bool, bool, bool]:
    if context is None:
        return True, False, False
    from .preferences import addon_preferences

    preferences = addon_preferences(context)
    if preferences is None:
        return True, False, False
    return (
        bool(preferences.asset_selector_hide_duplicates),
        bool(preferences.asset_selector_show_tuids),
        bool(preferences.asset_selector_show_owner_levels),
    )


def _kind_filter(settings: bpy.types.PropertyGroup) -> AssetKind | None:
    value = settings.asset_kind_filter
    if value == "MOBY":
        return AssetKind.MOBY
    if value == "TIE":
        return AssetKind.TIE
    return None


def _level_filter(
    settings: bpy.types.PropertyGroup,
    catalog: Resistance2Catalog,
) -> str | None:
    # A dynamic enum reads back as "" when its stored index no longer exists
    # (for example after the catalog was regenerated); treat that as no filter.
    value = settings.asset_level_filter
    if value in catalog.levels:
        return value
    return None


@lru_cache(maxsize=64)
def _build_asset_items(
    catalog: Resistance2Catalog,
    query: str,
    kind: AssetKind | None,
    level: str | None,
    hide_duplicates: bool,
    show_tuids: bool,
    show_owner_levels: bool,
) -> tuple[tuple[AssetEnumItem, ...], int]:
    """Return every matching asset, alphabetically, plus the unfiltered total.

    The list is deliberately unbounded: it feeds a search popup, which only
    draws the rows that fit, so the whole catalog stays reachable.
    """
    matches = sorted(
        filter_catalog_assets(
            catalog.assets,
            query,
            hide_duplicates=hide_duplicates,
            kind=kind,
            level=level,
        ),
        key=lambda asset: (asset.duplicate_key, asset.identifier),
    )
    duplicate_counts = Counter(asset.duplicate_key for asset in matches)
    items: list[AssetEnumItem] = []
    for asset in matches:
        label = asset.selector_label(
            show_tuid=show_tuids,
            show_owner=show_owner_levels,
        )
        if not show_tuids and duplicate_counts[asset.duplicate_key] > 1:
            label = f"{label} - {asset.identity_label}"
        items.append(
            (
                asset.identifier,
                label,
                asset.description,
                0,
                catalog.asset_number(asset.identifier),
            )
        )
    if query.strip() or kind is not None or level is not None:
        total = len(
            filter_catalog_assets(
                catalog.assets,
                "",
                hide_duplicates=hide_duplicates,
            )
        )
    else:
        total = len(matches)
    return tuple(items), total


def asset_selector_state(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> AssetSelectorState:
    catalog = load_catalog(_cache_directory(context))
    hide_duplicates, show_tuids, show_owner_levels = _selector_preferences(
        context
    )
    query = settings.asset_filter
    kind = _kind_filter(settings)
    level = _level_filter(settings, catalog)
    items, total = _build_asset_items(
        catalog,
        query,
        kind,
        level,
        hide_duplicates,
        show_tuids,
        show_owner_levels,
    )
    try:
        selected = catalog.asset(settings.asset_tuid)
    except MissingResourceError:
        selected = None
    # Checked against the selected asset itself, so a selection that is merely
    # the hidden twin of a listed duplicate still counts as matching.
    selection_matches_filter = selected is not None and bool(
        filter_catalog_assets(
            (selected,),
            query,
            hide_duplicates=False,
            kind=kind,
            level=level,
        )
    )
    return AssetSelectorState(
        items=items,
        total_count=total,
        match_count=len(items),
        filters_active=bool(
            query.strip() or kind is not None or level is not None
        ),
        selected=selected,
        selected_label=(
            selected.selector_label(
                show_tuid=show_tuids,
                show_owner=show_owner_levels,
            )
            if selected is not None
            else ""
        ),
        selection_matches_filter=selection_matches_filter,
    )


def asset_search_items(
    _owner: object,
    context: bpy.types.Context | None,
) -> list[AssetEnumItem]:
    """Enum callback listing every asset that passes the sidebar filters."""
    global _ASSET_ITEMS
    if context is None or context.scene is None:
        return _ASSET_ITEMS
    settings = context.scene.insomniac_asset_io
    # Blender keeps pointers into these strings, so the list must stay alive.
    _ASSET_ITEMS = list(asset_selector_state(settings, context).items)
    return _ASSET_ITEMS


def asset_selector_status(state: AssetSelectorState) -> str:
    if not state.filters_active:
        return f"{state.total_count:,} assets; click Asset to search by name"
    if state.match_count == 0:
        return f"No assets match the filters (0 of {state.total_count:,})"
    return f"{state.match_count:,} of {state.total_count:,} assets match"


def set_asset_selection(
    settings: bpy.types.PropertyGroup,
    identifier: str,
) -> None:
    settings.asset_tuid = identifier


def clear_asset_filters(settings: bpy.types.PropertyGroup) -> None:
    settings.asset_filter = ""
    settings.asset_kind_filter = ALL_KINDS
    settings.asset_level_filter = ALL_LEVELS


def ensure_asset_selection(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context,
) -> str:
    catalog = load_catalog(_cache_directory(context))
    try:
        catalog.asset(settings.asset_tuid)
    except MissingResourceError:
        set_asset_selection(settings, catalog.assets[0].identifier)
    return settings.asset_tuid


def _level_filter_items(
    _settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> list[tuple[str, str, str]]:
    global _LEVEL_FILTER_ITEMS
    catalog = load_catalog(_cache_directory(context))
    _LEVEL_FILTER_ITEMS = [
        (ALL_LEVELS, "All Levels", "Do not filter assets by level"),
        *(
            (level, level, f"Only list assets used by {level}")
            for level in catalog.levels
        ),
    ]
    return _LEVEL_FILTER_ITEMS


def _level_items(
    _settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> list[tuple[str, str, str]]:
    global _LEVEL_ITEMS
    catalog = load_catalog(_cache_directory(context))
    _LEVEL_ITEMS = [
        (
            level,
            level,
            f"Resistance 2 level {level} from the {catalog.region} catalog",
        )
        for level in catalog.levels
    ]
    return _LEVEL_ITEMS


class InsomniacAssetPickerItem(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    label: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    description: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    search_text: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    kind: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})


class InsomniacSceneSettings(bpy.types.PropertyGroup):
    game: bpy.props.EnumProperty(
        name="Game",
        items=[
            (
                "RESISTANCE_2",
                "Resistance 2",
                "Insomniac Engine v2, PlayStation 3",
            )
        ],
        default="RESISTANCE_2",
    )
    source_path: bpy.props.StringProperty(
        name="Source",
        description=(
            "A Resistance 2 ISO, extracted assetlookup.dat, level PSARC, or "
            "folder containing one ISO or extracted level resources"
        ),
        subtype="FILE_PATH",
    )
    source_summary: bpy.props.StringProperty(
        name="Summary",
        options={"SKIP_SAVE"},
    )
    source_inspection_busy: bpy.props.BoolProperty(
        name="Source inspection in progress",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    last_message: bpy.props.StringProperty(
        name="Last result",
        options={"SKIP_SAVE"},
    )
    asset_filter: bpy.props.StringProperty(
        name="Asset Path Filter",
        description=(
            "Only list assets whose source path, display name, kind, TUID, "
            "or level contains every space-separated fragment. Leave empty "
            "to list everything"
        ),
        options={"TEXTEDIT_UPDATE"},
    )
    asset_kind_filter: bpy.props.EnumProperty(
        name="Asset Kind",
        description="Only list assets of one kind",
        items=[
            (ALL_KINDS, "All", "List both Mobys and Ties"),
            ("MOBY", "Mobys", "Animated or dynamic models"),
            ("TIE", "Ties", "Static level meshes"),
        ],
        default=ALL_KINDS,
    )
    asset_level_filter: bpy.props.EnumProperty(
        name="Used In",
        description="Only list assets used by one level",
        items=_level_filter_items,
    )
    asset_tuid: bpy.props.StringProperty(
        name="Selected Asset TUID",
        default=DEFAULT_ASSET_IDENTIFIER,
        options={"HIDDEN"},
    )
    level_id: bpy.props.EnumProperty(
        name="Level",
        description=(
            "Importable level from the active extraction catalog"
        ),
        items=_level_items,
    )


CLASSES = (
    InsomniacAssetPickerItem,
    InsomniacSceneSettings,
)
