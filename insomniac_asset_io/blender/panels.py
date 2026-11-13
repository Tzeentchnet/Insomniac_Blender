from __future__ import annotations

import bpy

from ..games.registry import get_game_adapter
from ..games.resistance_2.formats import AssetKind
from .properties import asset_selector_state, asset_selector_status


class INSOMNIAC_PT_asset_io(bpy.types.Panel):
    bl_idname = "INSOMNIAC_PT_asset_io"
    bl_label = "Insomniac Asset IO"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Insomniac"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.insomniac_asset_io
        adapter = get_game_adapter(settings.game)

        layout.prop(settings, "game")
        source = layout.box()
        source.label(text="Source", icon="FILE_FOLDER")
        row = source.row(align=True)
        row.prop(settings, "source_path", text="")
        row.operator("insomniac.select_source", text="", icon="FILEBROWSER")
        inspect = source.row()
        inspect.enabled = not settings.source_inspection_busy
        inspect.operator(
            "insomniac.inspect_source",
            text=(
                "Inspecting Source..."
                if settings.source_inspection_busy
                else "Inspect Source"
            ),
            icon="TIME" if settings.source_inspection_busy else "VIEWZOOM",
        )
        if settings.source_inspection_busy:
            source.label(text="Preparing and inspecting source...", icon="TIME")
            source.label(text="ISO extraction may take several minutes")
        elif settings.source_summary:
            source.label(text=settings.source_summary, icon="INFO")

        asset = layout.box()
        asset.label(text="Model / Static Mesh", icon="MESH_DATA")
        state = asset_selector_state(settings, context)

        # Filters only narrow what the Asset search lists; they never change
        # or hide the current selection.
        filters = asset.column(align=True)
        if adapter.supports_levels:
            filters.row(align=True).prop(
                settings,
                "asset_kind_filter",
                expand=True,
            )
            filters.prop(settings, "asset_level_filter", text="")
        filters.prop(settings, "asset_filter", text="", icon="VIEWZOOM")
        status = asset.row(align=True)
        status.label(
            text=asset_selector_status(state),
            icon="INFO" if state.match_count else "ERROR",
        )
        if state.filters_active:
            status.operator(
                "insomniac.clear_asset_filters",
                text="",
                icon="X",
            )

        selected = state.selected
        chooser = asset.row()
        chooser.enabled = state.match_count > 0
        chooser.operator(
            "insomniac.choose_asset",
            text=state.selected_label or "Choose Asset...",
            icon=(
                "DOWNARROW_HLT"
                if selected is None
                else "MESH_CUBE"
                if adapter.experimental
                else "ARMATURE_DATA"
                if selected.kind is AssetKind.MOBY
                else "MESH_CUBE"
            ),
        )
        if selected is not None:
            details = asset.column(align=True)
            details.scale_y = 0.8
            details.label(
                text=f"{selected.identity_label} - {selected.owner_label}"
            )
            if not state.selection_matches_filter:
                details.label(
                    text="Selection is outside the current filters",
                    icon="FILTER",
                )
        importer = asset.row()
        importer.enabled = selected is not None
        importer.operator("insomniac.import_asset", icon="IMPORT")

        if adapter.supports_levels:
            level = layout.box()
            level.label(text="Level", icon="WORLD_DATA")
            level.prop(settings, "level_id")
            level.operator("insomniac.import_level", icon="IMPORT")
            level.operator(
                "insomniac.prepare_iso",
                text="Extract Selected Level Now",
                icon="PACKAGE",
            )
            layout.label(
                text=(
                    "Import options and default ISO are in Add-on Preferences"
                ),
                icon="INFO",
            )
        else:
            layout.label(
                text="Experimental static model import from decrypted PSAR",
                icon="EXPERIMENTAL",
            )

        if settings.last_message:
            result = layout.box()
            result.label(text="Last result", icon="CHECKMARK")
            result.label(text=settings.last_message)


CLASSES = (INSOMNIAC_PT_asset_io,)
