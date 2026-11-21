from __future__ import annotations

from ...core.binary import IghwFile, Section
from ...core.errors import FormatError
from ..resistance_2.animations import AnimationProfile
from ..resistance_2.formats import ShaderInfo
from ..resistance_2.reader import Resistance2Reader

FROZEN_EFFECT_FAMILY = "R3_FROZEN"
FROZEN_EFFECT_PROGRAM_TUID = 0x5C86_77F5_D69F_F338
FROZEN_EFFECT_TEXTURE_PATH = (
    "artdepartment/environment/decals/effects/ig_frozen_customa"
)


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
    shader_material_program_tuid_offset = 0x48
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
    def _matches_embedded_program_descriptors(
        ighw: IghwFile,
        program: Section | None,
        descriptors: Section | None,
        program_tuid: int,
    ) -> bool:
        if (
            program is None
            or program.length <= 0
            or descriptors is None
            or descriptors.length != 0x30
        ):
            return False

        previous_end = 0
        for index in range(3):
            descriptor = descriptors.offset + index * 0x10
            chunk_tuid = ighw.u64(descriptor)
            chunk_length = ighw.u32(descriptor + 0x08)
            chunk_offset = ighw.u32(descriptor + 0x0C)
            chunk_end = chunk_offset + chunk_length
            if (
                chunk_tuid != program_tuid + index + 1
                or chunk_length == 0
                or chunk_offset < previous_end
                or chunk_end > program.length
            ):
                return False
            previous_end = chunk_end
        return ighw.u32(descriptors.offset + 0x0C) == 0 and (
            previous_end == program.length
        )

    def _shader_effect_family(
        self,
        ighw: IghwFile,
        shader: ShaderInfo,
    ) -> str | None:
        shader_path = (shader.lookup_path or "").replace("\\", "/").casefold()
        effect_path = (
            shader.extra_texture_lookup_paths[0]
            if shader.extra_texture_lookup_paths
            else None
        )
        normalized_effect_path = (
            (effect_path or "")
            .replace(
                "\\",
                "/",
            )
            .casefold()
        )
        program = ighw.section(0x33001)
        descriptors = ighw.section(0x33002)
        selectors = ighw.section(0x33003)
        if (
            shader_path.rsplit("/", 1)[-1].endswith("frozen")
            and shader.material_program_tuid == FROZEN_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and len(shader.extra_texture_ids) >= 1
            and shader.extra_texture_ids[0] is not None
            and shader.is_texture_slot_bound(4)
            and normalized_effect_path == FROZEN_EFFECT_TEXTURE_PATH
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                FROZEN_EFFECT_PROGRAM_TUID,
            )
            and selectors is not None
            and selectors.length > 0
        ):
            return FROZEN_EFFECT_FAMILY
        return super()._shader_effect_family(ighw, shader)

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
