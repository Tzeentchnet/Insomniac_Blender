import logging
from pathlib import Path
import traceback

import bpy
from bpy_extras.io_utils import ImportHelper

from ..core.errors import InsomniacError
from ..core.iso import (
    find_resistance_2_iso,
    prepare_resistance_2_level,
    resistance_2_cached_level_path,
)
from ..core.source import GameSource
from ..games.resistance_2.catalog import CatalogAsset, load_catalog
from ..games.resistance_2.formats import AssetKind
from ..games.resistance_2.reader import Resistance2Reader
from .asset_cache import (
    AssetCacheOptions,
    ensure_asset_library_registered,
    run_asset_cache_worker,
)
from .catalog_cache import run_catalog_worker
from .importer import (
    AssetImportOptions,
    LevelImportOptions,
    import_level,
    import_moby,
    import_tie,
)
from .preferences import addon_preferences, cycles_materials_enabled
from .properties import (
    InsomniacAssetPickerItem,
    asset_search_items,
    clear_asset_filters,
    ensure_asset_selection,
    set_asset_selection,
)


LOGGER = logging.getLogger(__name__)
SOURCE_INSPECTION_STATUS = (
    "Inspecting Resistance 2 source; ISO extraction may take several minutes"
)


def _settings(context: bpy.types.Context):
    return context.scene.insomniac_asset_io


def _preferences(context: bpy.types.Context):
    preferences = addon_preferences(context)
    if preferences is None:
        raise InsomniacError(
            "Insomniac Asset IO preferences are unavailable"
        )
    return preferences


def _cache_directory(context: bpy.types.Context) -> str:
    return bpy.path.abspath(_preferences(context).asset_cache_directory)


def _configured_iso(context: bpy.types.Context) -> str:
    settings = _settings(context)
    if settings.source_path:
        source = bpy.path.abspath(settings.source_path)
        iso = find_resistance_2_iso(source)
        if iso is not None:
            return str(iso)
    configured = bpy.path.abspath(
        _preferences(context).resistance_2_iso_path
    )
    if not configured:
        return ""
    iso = find_resistance_2_iso(configured)
    return str(iso) if iso is not None else configured


def _catalog(context: bpy.types.Context):
    return load_catalog(_cache_directory(context))


def _source_contains_asset(
    source_path: str,
    asset: CatalogAsset,
) -> bool:
    with GameSource.open(source_path) as source:
        reader = Resistance2Reader(source)
        return any(
            pointer.tuid == asset.tuid
            for pointer in reader.asset_pointers(asset.kind)
        )


def _resolved_asset_source(
    context: bpy.types.Context,
    asset: CatalogAsset,
) -> tuple[str, str]:
    settings = _settings(context)
    cache_directory = _cache_directory(context)
    manual = (
        bpy.path.abspath(settings.source_path)
        if settings.source_path
        else ""
    )
    manual_iso = find_resistance_2_iso(manual) if manual else None
    if manual_iso is not None:
        cached = prepare_resistance_2_level(
            manual_iso,
            asset.levels[0],
            cache_directory,
        )
        return str(cached), str(manual_iso)
    if manual and _source_contains_asset(manual, asset):
        return manual, manual

    for level in asset.levels:
        cached = resistance_2_cached_level_path(cache_directory, level)
        if cached.is_file():
            return str(cached), str(cached)

    iso = _configured_iso(context)
    if iso:
        cached = prepare_resistance_2_level(
            iso,
            asset.levels[0],
            cache_directory,
        )
        return str(cached), iso
    raise InsomniacError(
        f"{asset.label} is not extracted. Configure a Resistance 2 ISO in "
        "Add-on Preferences or select an ISO as the source."
    )


def _source_matches_level(source_path: str, level_name: str) -> bool:
    with GameSource.open(source_path) as source:
        reader = Resistance2Reader(source)
        normalized = reader.resource_prefix.replace("\\", "/").casefold()
        return normalized.endswith(f"/levels/{level_name.casefold()}")


def _resolved_level_source(
    context: bpy.types.Context,
    level_name: str,
) -> tuple[str, str]:
    settings = _settings(context)
    cache_directory = _cache_directory(context)
    manual = (
        bpy.path.abspath(settings.source_path)
        if settings.source_path
        else ""
    )
    manual_iso = find_resistance_2_iso(manual) if manual else None
    if manual_iso is not None:
        cached = prepare_resistance_2_level(
            manual_iso,
            level_name,
            cache_directory,
        )
        return str(cached), str(manual_iso)
    if manual and _source_matches_level(manual, level_name):
        return manual, manual

    cached = resistance_2_cached_level_path(cache_directory, level_name)
    if cached.is_file():
        return str(cached), str(cached)

    iso = _configured_iso(context)
    if iso:
        cached = prepare_resistance_2_level(
            iso,
            level_name,
            cache_directory,
        )
        return str(cached), iso
    raise InsomniacError(
        f"Level {level_name!r} is not extracted. Configure a Resistance 2 "
        "ISO in Add-on Preferences or select an ISO as the source."
    )


def _resolved_inspection_source(
    context: bpy.types.Context,
) -> tuple[str, str]:
    settings = _settings(context)
    original = bpy.path.abspath(settings.source_path)
    iso = find_resistance_2_iso(original)
    if iso is None:
        return original, original
    cached = prepare_resistance_2_level(
        iso,
        settings.level_id,
        _cache_directory(context),
    )
    return str(cached), str(iso)


def _summary(reader: Resistance2Reader) -> str:
    summary = reader.inspect()
    counts = summary.counts
    return (
        f"{counts[AssetKind.MOBY]} mobys, {counts[AssetKind.TIE]} ties, "
        f"{counts[AssetKind.ANIMSET]} animsets, "
        f"{counts[AssetKind.HIGH_MIP]} textures, "
        f"{counts[AssetKind.ZONE]} zones"
    )


def _report_warnings(operator: bpy.types.Operator, warnings) -> None:
    messages = warnings or ()
    for message in messages[:5]:
        operator.report({"WARNING"}, message)
    if len(messages) > 5:
        operator.report(
            {"WARNING"},
            f"{len(messages) - 5} additional warnings were logged",
        )


class INSOMNIAC_OT_select_source(bpy.types.Operator, ImportHelper):
    bl_idname = "insomniac.select_source"
    bl_label = "Select Insomniac Source"
    bl_description = (
        "Select a Resistance 2 ISO, assetlookup.dat, or level PSARC"
    )
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".psarc"
    filter_glob: bpy.props.StringProperty(
        default="*.iso;*.psarc;*.dat",
        options={"HIDDEN"},
    )

    def execute(self, context):
        settings = _settings(context)
        iso = find_resistance_2_iso(self.filepath)
        settings.source_path = (
            str(iso) if iso is not None else self.filepath
        )
        if iso is not None:
            preferences = addon_preferences(context)
            if preferences is not None:
                preferences.resistance_2_iso_path = str(iso)
        result = bpy.ops.insomniac.inspect_source()
        return {"CANCELLED"} if "CANCELLED" in result else {"FINISHED"}


class INSOMNIAC_OT_inspect_source(bpy.types.Operator):
    bl_idname = "insomniac.inspect_source"
    bl_label = "Inspect Source"
    bl_description = (
        "Validate the source and count Resistance 2 assets; ISO extraction "
        "may take several minutes"
    )
    bl_options = {"REGISTER"}

    _inspection_timer = None

    def execute(self, context):
        settings = _settings(context)
        if not settings.source_path or context.window is None:
            return _inspect_source(self, context)

        window_manager = context.window_manager
        self._inspection_timer = window_manager.event_timer_add(
            0.2,
            window=context.window,
        )
        window_manager.modal_handler_add(self)
        _set_source_inspection_busy(context, True)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.cancel(context)
            self.report({"INFO"}, "Source inspection cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        _remove_inspection_timer(self, context)
        try:
            return _inspect_source(self, context)
        finally:
            _set_source_inspection_busy(context, False)

    def cancel(self, context):
        _remove_inspection_timer(self, context)
        _set_source_inspection_busy(context, False)


def _set_source_inspection_busy(
    context: bpy.types.Context,
    busy: bool,
) -> None:
    settings = _settings(context)
    settings.source_inspection_busy = busy
    if busy:
        settings.source_summary = ""
    if context.window is not None:
        context.window.cursor_set("WAIT" if busy else "DEFAULT")
    if context.workspace is not None:
        context.workspace.status_text_set(
            SOURCE_INSPECTION_STATUS if busy else None
        )
    if context.area is not None:
        context.area.tag_redraw()


def _remove_inspection_timer(
    operator: INSOMNIAC_OT_inspect_source,
    context: bpy.types.Context,
) -> None:
    if operator._inspection_timer is None:
        return
    context.window_manager.event_timer_remove(operator._inspection_timer)
    operator._inspection_timer = None


def _inspect_source(operator: bpy.types.Operator, context):
    settings = _settings(context)
    if not settings.source_path:
        operator.report({"ERROR"}, "Choose a source first")
        return {"CANCELLED"}
    try:
        source_path, _original_path = _resolved_inspection_source(context)
        with GameSource.open(source_path) as source:
            reader = Resistance2Reader(source)
            settings.source_summary = _summary(reader)
            settings.last_message = (
                f"Validated {reader.resource_prefix}: {settings.source_summary}"
            )
        operator.report({"INFO"}, settings.last_message)
        return {"FINISHED"}
    except InsomniacError as exc:
        settings.source_summary = ""
        settings.last_message = str(exc)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    except Exception as exc:
        LOGGER.exception("Unexpected source inspection failure")
        settings.last_message = f"Unexpected error: {exc}"
        operator.report({"ERROR"}, settings.last_message)
        return {"CANCELLED"}


def _asset_picker_matches(
    item: InsomniacAssetPickerItem,
    query: str,
) -> bool:
    tokens = query.casefold().split()
    haystack = item.search_text.casefold()
    return all(token in haystack for token in tokens)


def _update_asset_picker_search(self, _context) -> None:
    if (
        0 <= self.asset_index < len(self.assets)
        and _asset_picker_matches(
            self.assets[self.asset_index],
            self.asset_search,
        )
    ):
        return
    self.asset_index = next(
        (
            index
            for index, item in enumerate(self.assets)
            if _asset_picker_matches(item, self.asset_search)
        ),
        -1,
    )


class INSOMNIAC_UL_asset_picker(bpy.types.UIList):
    bl_idname = "INSOMNIAC_UL_asset_picker"

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _filter_flag=0,
    ):
        icon = "ARMATURE_DATA" if item.kind == "MOBY" else "MESH_CUBE"
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            layout.label(text=item.label, icon=icon)
        else:
            layout.alignment = "CENTER"
            layout.label(text="", icon=icon)

    def filter_items(self, _context, data, property_name):
        items = getattr(data, property_name)
        if not data.asset_search.strip():
            return [], []
        flags = [
            (
                self.bitflag_filter_item
                if _asset_picker_matches(item, data.asset_search)
                else 0
            )
            for item in items
        ]
        return flags, []


class INSOMNIAC_OT_choose_asset(bpy.types.Operator):
    bl_idname = "insomniac.choose_asset"
    bl_label = "Choose Asset"
    bl_description = (
        "Search every asset that passes the sidebar filters and select one"
    )
    bl_options = {"INTERNAL"}

    assets: bpy.props.CollectionProperty(
        name="Assets",
        type=InsomniacAssetPickerItem,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    asset_index: bpy.props.IntProperty(
        name="Selected Asset",
        default=-1,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    asset_search: bpy.props.StringProperty(
        name="Search",
        description=(
            "Filter by asset name, source path, kind, TUID, or owner level"
        ),
        options={"TEXTEDIT_UPDATE", "SKIP_SAVE"},
        update=_update_asset_picker_search,
    )

    def invoke(self, context, _event):
        source_items = asset_search_items(self, context)
        if not source_items:
            self.report(
                {"WARNING"},
                "No assets match the filters; clear or loosen them first",
            )
            return {"CANCELLED"}

        self.assets.clear()
        self.asset_index = -1
        self.asset_search = ""
        selected_identifier = _settings(context).asset_tuid
        for index, source_item in enumerate(source_items):
            identifier, label, description, _icon, _number = source_item
            item = self.assets.add()
            item.identifier = identifier
            item.label = label
            item.description = description
            item.search_text = f"{label}\n{description}\n{identifier}"
            item.kind = identifier.partition(":")[0]
            if identifier == selected_identifier:
                self.asset_index = index
        if self.asset_index < 0:
            self.asset_index = 0

        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "asset_search", text="", icon="VIEWZOOM")
        match_count = sum(
            _asset_picker_matches(item, self.asset_search)
            for item in self.assets
        )
        layout.label(
            text=f"{match_count:,} of {len(self.assets):,} assets shown",
            icon="INFO" if match_count else "ERROR",
        )
        layout.template_list(
            "INSOMNIAC_UL_asset_picker",
            "",
            self,
            "assets",
            self,
            "asset_index",
            item_dyntip_propname="description",
            rows=12,
            maxrows=12,
        )

    def execute(self, context):
        if not 0 <= self.asset_index < len(self.assets):
            self.report({"WARNING"}, "Choose an asset from the list")
            return {"CANCELLED"}
        item = self.assets[self.asset_index]
        if not _asset_picker_matches(item, self.asset_search):
            self.report({"WARNING"}, "Choose an asset shown by the search")
            return {"CANCELLED"}
        set_asset_selection(_settings(context), item.identifier)
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


class INSOMNIAC_OT_clear_asset_filters(bpy.types.Operator):
    bl_idname = "insomniac.clear_asset_filters"
    bl_label = "Clear Filters"
    bl_description = (
        "Reset the kind, level, and path filters so every asset is listed"
    )
    bl_options = {"INTERNAL"}

    def execute(self, context):
        clear_asset_filters(_settings(context))
        return {"FINISHED"}


class INSOMNIAC_OT_import_asset(bpy.types.Operator):
    bl_idname = "insomniac.import_asset"
    bl_label = "Import Asset"
    bl_description = "Import the selected Resistance 2 Moby or Tie"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        try:
            asset = _catalog(context).asset(
                ensure_asset_selection(settings, context)
            )
            source_path, original_path = _resolved_asset_source(
                context,
                asset,
            )
            preferences = _preferences(context)
            with GameSource.open(source_path) as source:
                reader = Resistance2Reader(source)
                if not any(
                    pointer.tuid == asset.tuid
                    for pointer in reader.asset_pointers(asset.kind)
                ):
                    raise InsomniacError(
                        f"{asset.label} is not present in {source_path}"
                    )
                options = AssetImportOptions(
                    materials=preferences.asset_import_materials,
                    animations=preferences.asset_import_animations,
                    animation_clip_limit=(
                        preferences.asset_animation_clip_limit
                    ),
                    convert_axes=preferences.asset_convert_axes,
                    cycles_materials=cycles_materials_enabled(context),
                )
                if asset.kind is AssetKind.MOBY:
                    result = import_moby(
                        context,
                        reader,
                        asset.tuid,
                        options,
                        source_path=original_path,
                    )
                else:
                    result = import_tie(
                        context,
                        reader,
                        asset.tuid,
                        options,
                        source_path=original_path,
                    )
            _report_warnings(self, result.warnings)
            settings.last_message = result.summary()
            self.report({"INFO"}, settings.last_message)
            return {"FINISHED"}
        except InsomniacError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            LOGGER.error(
                "Unexpected Resistance 2 asset import failure:\n%s",
                traceback.format_exc(),
            )
            settings.last_message = f"Unexpected import error: {exc}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}


class INSOMNIAC_OT_import_level(bpy.types.Operator):
    bl_idname = "insomniac.import_level"
    bl_label = "Import Level"
    bl_description = "Import Resistance 2 terrain and placed asset instances"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        try:
            level_name = settings.level_id
            if level_name not in _catalog(context).levels:
                raise InsomniacError(
                    f"Level catalog entry {level_name!r} is unavailable"
                )
            source_path, original_path = _resolved_level_source(
                context,
                level_name,
            )
            preferences = _preferences(context)
            with GameSource.open(source_path) as source:
                reader = Resistance2Reader(source)
                result = import_level(
                    context,
                    reader,
                    LevelImportOptions(
                        materials=preferences.level_import_materials,
                        terrain=preferences.level_import_terrain,
                        ties=preferences.level_import_ties,
                        gameplay=preferences.level_import_gameplay,
                        convert_axes=preferences.level_convert_axes,
                        cycles_materials=cycles_materials_enabled(context),
                    ),
                    source_path=original_path,
                )
            _report_warnings(self, result.warnings)
            settings.last_message = result.summary()
            self.report({"INFO"}, settings.last_message)
            return {"FINISHED"}
        except InsomniacError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            LOGGER.error(
                "Unexpected Resistance 2 level import failure:\n%s",
                traceback.format_exc(),
            )
            settings.last_message = f"Unexpected level import error: {exc}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}


class INSOMNIAC_OT_prepare_iso(bpy.types.Operator):
    bl_idname = "insomniac.prepare_iso"
    bl_label = "Prepare Level Archives"
    bl_description = (
        "Extract the selected Resistance 2 level and global PSARCs to a cache"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = _settings(context)
        try:
            iso = _configured_iso(context)
            if not iso:
                raise InsomniacError(
                    "Configure a Resistance 2 ISO in Add-on Preferences or "
                    "select an ISO as the source"
                )
            cached_level = prepare_resistance_2_level(
                iso,
                settings.level_id,
                _cache_directory(context),
            )
            settings.source_path = str(cached_level)
            settings.last_message = f"Prepared {cached_level}"
            self.report({"INFO"}, settings.last_message)
            return {"FINISHED"}
        except InsomniacError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            LOGGER.exception("Unexpected ISO preparation failure")
            settings.last_message = f"Unexpected ISO extraction error: {exc}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}


class INSOMNIAC_OT_rebuild_import_catalog(bpy.types.Operator):
    bl_idname = "insomniac.rebuild_import_catalog"
    bl_label = "Regenerate Resistance 2 Import Catalog"
    bl_description = (
        "Scan the configured ISO for importable Moby, Tie, and level IDs and "
        "write a local metadata catalog"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = addon_preferences(context)
        if preferences is None:
            self.report({"ERROR"}, "Insomniac Asset IO preferences are unavailable")
            return {"CANCELLED"}
        if not preferences.resistance_2_iso_path:
            self.report({"ERROR"}, "Choose a Resistance 2 ISO first")
            return {"CANCELLED"}

        window = context.window
        if window is not None:
            window.cursor_set("WAIT")
        try:
            report = run_catalog_worker(
                iso_path=bpy.path.abspath(
                    preferences.resistance_2_iso_path
                ),
                cache_directory=bpy.path.abspath(
                    preferences.asset_cache_directory
                ),
            )
            catalog = _catalog(context)
            settings = _settings(context)
            asset_identifiers = {
                asset.identifier for asset in catalog.assets
            }
            if settings.asset_tuid not in asset_identifiers:
                set_asset_selection(
                    settings,
                    catalog.assets[0].identifier,
                )
            if settings.level_id not in catalog.levels:
                settings.level_id = catalog.levels[0]
            self.report(
                {"INFO"},
                f"Cataloged {report['unique_assets']} unique assets across "
                f"{report['levels']} levels",
            )
            return {"FINISHED"}
        except InsomniacError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            LOGGER.exception("Unexpected import catalog failure")
            self.report({"ERROR"}, f"Unexpected catalog error: {exc}")
            return {"CANCELLED"}
        finally:
            if window is not None:
                window.cursor_set("DEFAULT")


class INSOMNIAC_OT_rebuild_asset_library(bpy.types.Operator):
    bl_idname = "insomniac.rebuild_asset_library"
    bl_label = "Regenerate Resistance 2 Asset Cache"
    bl_description = (
        "Generate a local Blender Asset Browser library from the selected "
        "Resistance 2 ISO and level"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = addon_preferences(context)
        if preferences is None:
            self.report({"ERROR"}, "Insomniac Asset IO preferences are unavailable")
            return {"CANCELLED"}
        if not preferences.resistance_2_iso_path:
            self.report({"ERROR"}, "Choose a Resistance 2 ISO first")
            return {"CANCELLED"}

        window = context.window
        if window is not None:
            window.cursor_set("WAIT")
        try:
            report = run_asset_cache_worker(
                iso_path=bpy.path.abspath(
                    preferences.resistance_2_iso_path
                ),
                level_name=preferences.asset_cache_level,
                cache_directory=bpy.path.abspath(
                    preferences.asset_cache_directory
                ),
                options=AssetCacheOptions(
                    materials=preferences.asset_cache_materials,
                    animations=preferences.asset_cache_animations,
                    cycles_materials=preferences.cycles_materials,
                    generate_previews=preferences.asset_cache_previews,
                ),
            )
            library = ensure_asset_library_registered(
                context,
                bpy.path.abspath(preferences.asset_cache_directory),
            )
            warning_count = int(report.get("warnings", 0))
            if warning_count:
                self.report(
                    {"WARNING"},
                    f"Asset cache completed with {warning_count} source "
                    f"warnings; see {report['manifest']}",
                )
            self.report(
                {"INFO"},
                f"Cached {report['assets']} assets for {report['level']} "
                f"in {library.name}",
            )
            return {"FINISHED"}
        except InsomniacError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            LOGGER.exception("Unexpected Asset Browser cache failure")
            self.report({"ERROR"}, f"Unexpected asset cache error: {exc}")
            return {"CANCELLED"}
        finally:
            if window is not None:
                window.cursor_set("DEFAULT")


class INSOMNIAC_OT_register_asset_library(bpy.types.Operator):
    bl_idname = "insomniac.register_asset_library"
    bl_label = "Register Insomniac Asset Library"
    bl_description = (
        "Register the generated Insomniac cache with Blender's Asset Browser"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        preferences = addon_preferences(context)
        if preferences is None:
            self.report({"ERROR"}, "Insomniac Asset IO preferences are unavailable")
            return {"CANCELLED"}
        try:
            library = ensure_asset_library_registered(
                context,
                bpy.path.abspath(preferences.asset_cache_directory),
            )
            self.report(
                {"INFO"},
                f"Registered Blender asset library {library.name}",
            )
            return {"FINISHED"}
        except Exception as exc:
            LOGGER.exception("Unexpected asset library registration failure")
            self.report({"ERROR"}, f"Could not register asset library: {exc}")
            return {"CANCELLED"}


CLASSES = (
    INSOMNIAC_OT_select_source,
    INSOMNIAC_OT_inspect_source,
    INSOMNIAC_UL_asset_picker,
    INSOMNIAC_OT_choose_asset,
    INSOMNIAC_OT_clear_asset_filters,
    INSOMNIAC_OT_import_asset,
    INSOMNIAC_OT_import_level,
    INSOMNIAC_OT_prepare_iso,
    INSOMNIAC_OT_rebuild_import_catalog,
    INSOMNIAC_OT_rebuild_asset_library,
    INSOMNIAC_OT_register_asset_library,
)
