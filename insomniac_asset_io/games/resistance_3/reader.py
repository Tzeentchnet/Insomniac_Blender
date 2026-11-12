from __future__ import annotations

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
