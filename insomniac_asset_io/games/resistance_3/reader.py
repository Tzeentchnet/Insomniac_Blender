from __future__ import annotations

from ...core.binary import IghwFile
from ...core.errors import FormatError
from ..resistance_2.animations import AnimationProfile
from ..resistance_2.reader import Resistance2Reader


class Resistance3Reader(Resistance2Reader):
    """Resistance 3 reader with its expanded shader record layouts."""

    game_id = "RESISTANCE_3"
    game_label = "Resistance 3"
    engine_id = "V3"
    asset_prefix = "R3"
    shader_texture_slot_count = 6
    shader_material_word_offset = 0x20
    shader_material_values_offset = 0x24
    shader_material_vector_count = 5
    shader_material_tail_count = 3
    material_surface_channels_by_default = True
    material_emission_uses_albedo = True
    material_emission_disabled_control_mask = 0x08
    material_luminance_alpha_shader_tuids = frozenset(
        {
            0x5966_249F_D0D6_149B,
        }
    )
    collision_quantization_markers = (256, 257)
    animation_profile = AnimationProfile.RESISTANCE_3
    foliage_descriptor_min_length = 0x68
    foliage_corner_y_sign = -1.0
    foliage_instance_stride = 0x80
    foliage_instance_lookup_offset = 0x70
    terrain_position_offset = 0x70
    terrain_vertex_lighting = True
    terrain_lightmaps = True
    zone_directional_lightmaps = True
    tie_vertex_lighting = True
    tie_secondary_uvs = True
    tie_instance_lightmaps = True

    @staticmethod
    def _tie_shader_index(
        ighw: IghwFile,
        mesh: int,
        shader_count: int,
    ) -> int:
        shader_index = ighw.u8(mesh + 0x26)
        if shader_count and shader_index >= shader_count:
            raise FormatError(
                f"{ighw.label}: Tie shader index {shader_index} exceeds "
                f"the {shader_count}-entry shader table"
            )
        return shader_index

    @staticmethod
    def _foliage_sprite_layout(
        ighw: IghwFile,
        base: int,
    ) -> tuple[int, int, int]:
        return (
            ighw.u8(base + 0x37),
            ighw.u32(base + 0x50),
            base + 0x58,
        )
