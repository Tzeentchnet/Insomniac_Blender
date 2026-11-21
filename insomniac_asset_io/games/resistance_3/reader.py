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
DISSOLVE_EFFECT_FAMILY = "R3_GRADIENT_DISSOLVE"
DISSOLVE_EFFECT_PROGRAM_TUID = 0x13C6_0114_0542_FB72
DISSOLVE_EFFECT_TEXTURE_PATH = "ssharit/gradientdissolve"
DISSOLVE_EFFECT_PROGRAM_LENGTH = 0x2E30
FIRE_EFFECT_FAMILY = "R3_ON_FIRE"
FIRE_EFFECT_PROGRAM_TUID = 0xB2DA_FFA9_D7AF_976A
FIRE_EFFECT_TEXTURE_PATH = (
    "artdepartment/environment/decals/effects/onfire/fire_customa"
)
FIRE_EFFECT_TEXTURE_PATHS = frozenset(
    {
        FIRE_EFFECT_TEXTURE_PATH,
        "graphshaders/iang/onfire_01.graphshader",
    }
)
FIRE_EFFECT_PROGRAM_LENGTHS = frozenset({0x2DB0, 0x2E30})
FLASH_OVERLAY_EFFECT_FAMILY = "R3_FLASH_OVERLAY"
FLASH_OVERLAY_EFFECT_PROGRAM_TUID = 0x22A1_46AA_E58E_49A4
FLASH_OVERLAY_EFFECT_TEXTURE_PATH = "d_action_pickup/textures/flash_002"
FLASH_OVERLAY_EFFECT_PROGRAM_LENGTHS = frozenset({0x2A10, 0x2A50})
FLASH_OVERLAY_EFFECT_SELECTOR_LENGTHS = frozenset({0x09, 0x0B})
WEAPON_UPGRADE_EFFECT_FAMILY = "R3_WEAPON_UPGRADE"
WEAPON_UPGRADE_EFFECT_PROGRAM_TUID = 0x747A_316C_D67F_BD5D
WEAPON_UPGRADE_EFFECT_TEXTURE_PATH = (
    "artdepartment/environment/decals/effects/weaponupgrade/weaponupgrade_c"
)
WEAPON_UPGRADE_EFFECT_PROGRAM_LENGTHS = frozenset({0x2A00, 0x2A80})
R3_DEFAULT_SHRUB_POSITION_EXTENT = 32.0


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
        *,
        first_chunk_tuid: int,
        descriptor_count: int,
        expected_program_length: int | None = None,
    ) -> bool:
        if (
            program is None
            or program.length <= 0
            or (
                expected_program_length is not None
                and program.length != expected_program_length
            )
            or descriptors is None
            or descriptors.length != descriptor_count * 0x10
        ):
            return False

        previous_end = 0
        for index in range(descriptor_count):
            descriptor = descriptors.offset + index * 0x10
            chunk_tuid = ighw.u64(descriptor)
            chunk_length = ighw.u32(descriptor + 0x08)
            chunk_offset = ighw.u32(descriptor + 0x0C)
            chunk_end = chunk_offset + chunk_length
            if (
                chunk_tuid != first_chunk_tuid + index
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
        shader_name = shader_path.rsplit("/", 1)[-1]
        has_effect_texture = (
            len(shader.extra_texture_ids) >= 1
            and shader.extra_texture_ids[0] is not None
            and shader.is_texture_slot_bound(4)
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
            shader_name.endswith("frozen")
            and shader.material_program_tuid == FROZEN_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and has_effect_texture
            and normalized_effect_path == FROZEN_EFFECT_TEXTURE_PATH
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                first_chunk_tuid=FROZEN_EFFECT_PROGRAM_TUID + 1,
                descriptor_count=3,
            )
            and selectors is not None
            and selectors.length > 0
        ):
            return FROZEN_EFFECT_FAMILY
        if (
            shader_name.endswith(("_dissolve", "_dissovle"))
            and shader.material_program_tuid == DISSOLVE_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and has_effect_texture
            and normalized_effect_path == DISSOLVE_EFFECT_TEXTURE_PATH
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                first_chunk_tuid=DISSOLVE_EFFECT_PROGRAM_TUID,
                descriptor_count=4,
                expected_program_length=DISSOLVE_EFFECT_PROGRAM_LENGTH,
            )
            and selectors is not None
            and selectors.length == 0x0F
        ):
            return DISSOLVE_EFFECT_FAMILY
        if (
            shader_name.endswith("_onfire")
            and shader.material_program_tuid == FIRE_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and has_effect_texture
            and normalized_effect_path in FIRE_EFFECT_TEXTURE_PATHS
            and program is not None
            and program.length in FIRE_EFFECT_PROGRAM_LENGTHS
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                first_chunk_tuid=FIRE_EFFECT_PROGRAM_TUID + 1,
                descriptor_count=3,
            )
            and selectors is not None
            and selectors.length == 0x15
        ):
            return FIRE_EFFECT_FAMILY
        if (
            shader.material_program_tuid == FLASH_OVERLAY_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and has_effect_texture
            and normalized_effect_path == FLASH_OVERLAY_EFFECT_TEXTURE_PATH
            and program is not None
            and program.length in FLASH_OVERLAY_EFFECT_PROGRAM_LENGTHS
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                first_chunk_tuid=FLASH_OVERLAY_EFFECT_PROGRAM_TUID + 1,
                descriptor_count=3,
            )
            and selectors is not None
            and selectors.length in FLASH_OVERLAY_EFFECT_SELECTOR_LENGTHS
        ):
            return FLASH_OVERLAY_EFFECT_FAMILY
        if (
            shader_name.endswith(("_upgrading", "_weaponupgrading"))
            and shader.material_program_tuid == WEAPON_UPGRADE_EFFECT_PROGRAM_TUID
            and shader.material_unknown_0 == 0x13
            and has_effect_texture
            and normalized_effect_path == WEAPON_UPGRADE_EFFECT_TEXTURE_PATH
            and program is not None
            and program.length in WEAPON_UPGRADE_EFFECT_PROGRAM_LENGTHS
            and self._matches_embedded_program_descriptors(
                ighw,
                program,
                descriptors,
                first_chunk_tuid=WEAPON_UPGRADE_EFFECT_PROGRAM_TUID + 1,
                descriptor_count=3,
            )
            and selectors is not None
            and selectors.length == 0x0B
        ):
            return WEAPON_UPGRADE_EFFECT_FAMILY
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

    @staticmethod
    def _shrub_position_extent(
        ighw: IghwFile,
        base: int,
    ) -> float:
        extent = ighw.f32(base + 0x18)
        if extent == 0.0:
            return R3_DEFAULT_SHRUB_POSITION_EXTENT
        return extent
