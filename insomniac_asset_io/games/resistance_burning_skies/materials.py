from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

from ...core.errors import FormatError
from .formats import NsirsrcFile

MATERIAL_ROOT_TYPE = 0x09
MATERIAL_PARAMETER_RECORD_SIZE = 0x18
MAX_MATERIAL_PARAMETERS = 4096
_ASCII_STRING = re.compile(rb"[ -~]{3,}")


@dataclass(frozen=True, slots=True)
class MaterialTexturePaths:
    albedo: str | None = None
    normal: str | None = None
    specular: str | None = None
    specular_semantics: str = "GLOSS"
    detail: str | None = None
    emissive: str | None = None
    alpha: str | None = None
    diffuse_tint: tuple[float, float, float, float] = (
        1.0,
        1.0,
        1.0,
        1.0,
    )
    specular_power: float | None = None
    emissive_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    emissive_factor: float = 1.0
    use_emissive_texture: bool | None = None
    emissive_enabled: bool = False
    alpha_mode_override: str | None = None
    renderable: bool | None = None
    world_collision: bool = False
    only_cast_shadows: bool = False

    @property
    def is_auxiliary(self) -> bool:
        return (
            self.world_collision
            or self.only_cast_shadows
            or self.renderable is False
        )


@dataclass(frozen=True, slots=True)
class _Parameter:
    type_id: int
    current_value_offset: int


_PARAMETER_ROLES = {
    "g_diffusemaptexture": "albedo",
    "g_albedomaptexture": "albedo",
    "g_normalmaptexture": "normal",
    "g_glossmaptexture": "gloss",
    "g_specularmaptexture": "specular",
    "g_detailmaptexture": "detail",
    "g_emissivemaptexture": "emissive",
    "g_alphamaptexture": "alpha",
}


def decode_material_texture_paths(
    data: bytes,
    *,
    label: str,
    texture_paths_by_stem: dict[str, str],
) -> MaterialTexturePaths:
    resource = NsirsrcFile(data, label=label)
    if resource.root_type != MATERIAL_ROOT_TYPE:
        raise FormatError(
            f"{label}: root type 0x{resource.root_type:X} is not Material"
        )

    strings = [
        match.group().decode("ascii")
        for match in _ASCII_STRING.finditer(data)
    ]
    folded_strings = {value.casefold() for value in strings}
    parameters = _decode_parameters(resource)
    roles = _decode_texture_roles(
        resource,
        parameters,
        texture_paths_by_stem=texture_paths_by_stem,
    )
    _add_ascii_texture_role_fallbacks(
        roles,
        strings,
        texture_paths_by_stem=texture_paths_by_stem,
    )

    diffuse_tint = _vector_parameter(
        resource,
        parameters.get("diffuse_tint")
        or parameters.get("diffuse_color"),
        default=(1.0, 1.0, 1.0),
    )
    emissive_color = _vector_parameter(
        resource,
        parameters.get("emissive_color"),
        default=(1.0, 1.0, 1.0),
    )
    specular_power = _scalar_parameter(
        resource,
        parameters.get("specular_power"),
    )
    emissive_factor = _scalar_parameter(
        resource,
        parameters.get("emissive_factor"),
    )
    use_emissive_texture = _boolean_parameter(
        resource,
        parameters.get("use_emissive_texture"),
    )
    renderable = _boolean_parameter(
        resource,
        parameters.get("renderable"),
    )

    alpha_mode_override = None
    if "sortedtranslucent" in folded_strings:
        alpha_mode_override = "BLEND"
    elif {"alphatest", "masked"} & folded_strings:
        alpha_mode_override = "MASK"

    only_cast_shadows = (
        "onlycastshadows" in folded_strings
        or any(
            value.startswith("ge_shadowcaster_")
            for value in folded_strings
        )
    )
    return MaterialTexturePaths(
        albedo=roles.get("albedo"),
        normal=roles.get("normal"),
        specular=roles.get("gloss") or roles.get("specular"),
        specular_semantics=(
            "SPECULAR"
            if roles.get("gloss") is None
            and roles.get("specular") is not None
            else "GLOSS"
        ),
        detail=roles.get("detail"),
        emissive=roles.get("emissive"),
        alpha=roles.get("alpha"),
        diffuse_tint=(*diffuse_tint, 1.0),
        specular_power=specular_power,
        emissive_color=emissive_color,
        emissive_factor=(
            emissive_factor
            if emissive_factor is not None and math.isfinite(emissive_factor)
            else 1.0
        ),
        use_emissive_texture=use_emissive_texture,
        emissive_enabled=bool(
            {"opaqueemissive", "gunemissive"} & folded_strings
        ),
        alpha_mode_override=alpha_mode_override,
        renderable=renderable,
        world_collision="worldcollision" in folded_strings,
        only_cast_shadows=only_cast_shadows,
    )


def _decode_parameters(resource: NsirsrcFile) -> dict[str, _Parameter]:
    record_offsets: set[int] = set()
    root = resource.data_base
    if root + 0xA0 > len(resource.data):
        return {}
    if (
        resource.u32(root + 0x24) != 0xCCCCCCCC
        or resource.u32(root + 0x28) != 0xCCCCCCCC
    ):
        return {}
    for group_offset in range(0x70, 0xA0, 8):
        count = resource.u32(root + group_offset)
        records_pointer = resource.u32(root + group_offset + 4)
        if count > MAX_MATERIAL_PARAMETERS:
            raise FormatError(
                f"{resource.label}: material parameter count {count} "
                f"exceeds {MAX_MATERIAL_PARAMETERS}"
            )
        if not count:
            continue
        if not records_pointer:
            raise FormatError(
                f"{resource.label}: material parameter group has no records"
            )
        records_offset = resource.physical_offset(records_pointer)
        resource.require(
            records_offset,
            count * MATERIAL_PARAMETER_RECORD_SIZE,
            purpose="material parameter records",
        )
        record_offsets.update(
            records_offset + index * MATERIAL_PARAMETER_RECORD_SIZE
            for index in range(count)
        )

    parameters: dict[str, _Parameter] = {}
    for record_offset in sorted(record_offsets):
        (
            name_pointer,
            type_id,
            _default_value_pointer,
            current_value_pointer,
            _reserved_0,
            _reserved_1,
        ) = struct.unpack_from("<6I", resource.data, record_offset)
        name = resource.c_string(
            resource.physical_offset(name_pointer),
            limit=128,
        ).casefold()
        current_value_offset = resource.physical_offset(
            current_value_pointer
        )
        previous = parameters.setdefault(
            name,
            _Parameter(
                type_id=type_id,
                current_value_offset=current_value_offset,
            ),
        )
        if previous.type_id != type_id:
            raise FormatError(
                f"{resource.label}: material parameter {name!r} has "
                "conflicting types"
            )
    return parameters


def _decode_texture_roles(
    resource: NsirsrcFile,
    parameters: dict[str, _Parameter],
    *,
    texture_paths_by_stem: dict[str, str],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    for parameter_name, role in _PARAMETER_ROLES.items():
        parameter = parameters.get(parameter_name)
        if parameter is None or parameter.type_id != 0x0E:
            continue
        resource.require(
            parameter.current_value_offset,
            8,
            purpose=f"{parameter_name} texture value",
        )
        texture_name_pointer = resource.u32(parameter.current_value_offset)
        if not texture_name_pointer:
            continue
        texture_name = resource.c_string(
            resource.physical_offset(texture_name_pointer),
            limit=128,
        )
        if not texture_name:
            continue
        texture_path = texture_paths_by_stem.get(texture_name.casefold())
        if texture_path is not None:
            roles[role] = texture_path
    return roles


def _add_ascii_texture_role_fallbacks(
    roles: dict[str, str],
    strings: list[str],
    *,
    texture_paths_by_stem: dict[str, str],
) -> None:
    for index, value in enumerate(strings):
        role = _PARAMETER_ROLES.get(value.casefold())
        if role is None or role in roles:
            continue
        for candidate in strings[index + 1 :]:
            path = texture_paths_by_stem.get(candidate.casefold())
            if path is not None:
                roles[role] = path
                break
            if candidate.casefold() in _PARAMETER_ROLES:
                break


def _scalar_parameter(
    resource: NsirsrcFile,
    parameter: _Parameter | None,
) -> float | None:
    if parameter is None or parameter.type_id not in {0x09, 0x0D}:
        return None
    resource.require(
        parameter.current_value_offset,
        4,
        purpose="material scalar parameter",
    )
    return struct.unpack_from(
        "<f",
        resource.data,
        parameter.current_value_offset,
    )[0]


def _boolean_parameter(
    resource: NsirsrcFile,
    parameter: _Parameter | None,
) -> bool | None:
    value = _scalar_parameter(resource, parameter)
    if value is None or not math.isfinite(value):
        return None
    return value >= 0.5


def _vector_parameter(
    resource: NsirsrcFile,
    parameter: _Parameter | None,
    *,
    default: tuple[float, float, float],
) -> tuple[float, float, float]:
    if parameter is None or parameter.type_id != 0x0B:
        return default
    resource.require(
        parameter.current_value_offset,
        12,
        purpose="material vector parameter",
    )
    value = struct.unpack_from(
        "<3f",
        resource.data,
        parameter.current_value_offset,
    )
    if not all(math.isfinite(component) for component in value):
        return default
    return value
