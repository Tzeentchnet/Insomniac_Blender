from __future__ import annotations

import logging
from pathlib import PurePosixPath

import numpy as np

from ...core.errors import (
    AmbiguousResourceError,
    FormatError,
    MissingResourceError,
)
from ...core.source import GameSource
from ..resistance_2.formats import (
    AssetKind,
    AssetPointer,
    CollisionMesh,
    MobyAsset,
    ShaderInfo,
    SourceSummary,
)
from .ids import stable_resource_id
from .materials import MaterialTexturePaths, decode_material_texture_paths
from .models import decode_smf
from .textures import decode_p2t

LOGGER = logging.getLogger(__name__)

_MODEL_MATERIAL_NAME_OVERRIDES = {
    "models/ap_base_amship.smf": ("E1A653EE_5D4C7E4E_8E0B6D8A_A677550B",),
}


class BurningSkiesReader:
    """Experimental reader for Nihilistic's Vita NSIRSRC resources."""

    game_id = "RESISTANCE_BURNING_SKIES"
    game_label = "Resistance: Burning Skies (Experimental)"
    engine_id = "NIHILISTIC_VITA"
    asset_prefix = "RBS"
    resource_prefix = "archive"
    material_surface_channels_by_default = True
    material_uses_base_color_factor_by_default = True
    material_emission_uses_albedo = False
    material_emission_disabled_control_mask = 0
    material_luminance_alpha_shader_tuids: frozenset[int] = frozenset()
    axis_conversion_x_degrees = 0.0

    def __init__(self, source: GameSource) -> None:
        self.source = source
        self.warnings: list[str] = []
        self._paths_by_normalized = {
            self._normalize(path): path for path in source.paths
        }
        self._model_paths = tuple(
            path
            for path in source.paths
            if path.casefold().endswith(".smf")
            and path.casefold().startswith("models/")
        )
        if not self._model_paths:
            raise MissingResourceError(
                "Burning Skies source contains no named models/*.smf "
                "resources. Select the decrypted PSAR build.psa archive."
            )
        self._material_paths = tuple(
            path
            for path in source.paths
            if path.casefold().endswith(".bmx")
            and path.casefold().startswith("materials/")
        )
        self._texture_paths = tuple(
            path
            for path in source.paths
            if path.casefold().endswith(".p2t")
            and path.casefold().startswith("textures/")
        )
        self._model_paths_by_id = self._build_id_index(
            "RBSModel",
            self._model_paths,
        )
        self._material_paths_by_id = self._build_id_index(
            "RBSMaterial",
            self._material_paths,
        )
        self._texture_paths_by_id = self._build_id_index(
            "RBSTexture",
            self._texture_paths,
        )
        self._material_paths_by_name = {
            PurePosixPath(path).stem.casefold(): path for path in self._material_paths
        }
        self._texture_paths_by_stem = self._unique_stem_index(self._texture_paths)
        self._material_cache: dict[str, MaterialTexturePaths] = {}
        self._texture_cache = {}

    @staticmethod
    def _normalize(path: str) -> str:
        return path.replace("\\", "/").lstrip("/").casefold()

    @staticmethod
    def _resource_id(namespace: str, path: str) -> int:
        return stable_resource_id(namespace, path)

    @classmethod
    def model_id(cls, path: str) -> int:
        return cls._resource_id("RBSModel", path)

    @classmethod
    def material_id(cls, name_or_path: str) -> int:
        return cls._resource_id("RBSMaterial", name_or_path)

    @classmethod
    def texture_id(cls, path: str) -> int:
        return cls._resource_id("RBSTexture", path)

    @classmethod
    def _build_id_index(
        cls,
        namespace: str,
        paths: tuple[str, ...],
    ) -> dict[int, str]:
        result: dict[int, str] = {}
        for path in paths:
            resource_id = cls._resource_id(namespace, path)
            previous = result.setdefault(resource_id, path)
            if previous != path:
                raise FormatError(
                    f"Stable ID collision between {previous!r} and {path!r}"
                )
        return result

    @staticmethod
    def _unique_stem_index(paths: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in paths:
            stem = PurePosixPath(path).stem.casefold()
            previous = result.setdefault(stem, path)
            if previous != path:
                raise AmbiguousResourceError(
                    f"Burning Skies texture name {stem!r} resolves to "
                    f"both {previous!r} and {path!r}"
                )
        return result

    def inspect(self) -> SourceSummary:
        counts = {kind: 0 for kind in AssetKind}
        counts[AssetKind.MOBY] = len(self._model_paths)
        counts[AssetKind.SHADER] = len(self._material_paths)
        counts[AssetKind.HIGH_MIP] = len(self._texture_paths)
        return SourceSummary(counts=counts, resource_prefix="archive")

    def summary_text(self) -> str:
        return (
            f"{len(self._model_paths):,} SMF models, "
            f"{len(self._texture_paths):,} P2T textures, "
            f"{len(self._material_paths):,} BMX materials"
        )

    def asset_pointers(
        self,
        kind: AssetKind,
    ) -> tuple[AssetPointer, ...]:
        if kind is not AssetKind.MOBY:
            return ()
        return tuple(
            AssetPointer(tuid=tuid, offset=0, length=0)
            for tuid in sorted(self._model_paths_by_id)
        )

    def read_asset_name(
        self,
        kind: AssetKind,
        tuid: int,
    ) -> str | None:
        if kind is not AssetKind.MOBY:
            return None
        return self._model_paths_by_id.get(tuid)

    def read_moby(self, tuid: int) -> MobyAsset:
        try:
            path = self._model_paths_by_id[tuid]
        except KeyError as exc:
            raise MissingResourceError(
                f"Burning Skies model 0x{tuid:016X} is unavailable"
            ) from exc
        decoded = decode_smf(self.source.read(path), label=path)
        material_names = _MODEL_MATERIAL_NAME_OVERRIDES.get(
            self._normalize(path),
            decoded.material_names,
        )
        material_paths: list[str] = []
        for name in material_names:
            material_path = self._material_paths_by_name.get(name.casefold())
            if material_path is None:
                self._warn(f"{path}: referenced material {name} is unavailable")
                continue
            material_paths.append(material_path)
        if len(material_paths) != len(material_names):
            material_paths = []
            for mesh in decoded.meshes:
                mesh.shader_index = 0
            self._warn(
                f"{path}: skipped materials because one or more BMX "
                "dependencies are unavailable"
            )
        if (
            self._normalize(path) not in _MODEL_MATERIAL_NAME_OVERRIDES
            and not decoded.material_binding_supported
        ):
            self._warn(
                f"{path}: skipped materials because "
                f"{len(decoded.meshes)} mesh packets cannot be mapped "
                "unambiguously to "
                f"{decoded.material_dependency_count} unique BMX "
                "dependencies"
            )
        shader_tuids = tuple(
            self.material_id(material_path) for material_path in material_paths
        )
        collision_meshes: list[CollisionMesh] = []
        render_meshes = []
        material_infos = [
            self._read_material(material_path)
            for material_path in material_paths
        ]
        for mesh in decoded.meshes:
            if (
                material_infos
                and material_infos[mesh.shader_index].is_auxiliary
            ):
                collision_meshes.append(
                    CollisionMesh(
                        positions=mesh.positions,
                        indices=mesh.indices,
                        face_types=np.zeros(
                            len(mesh.indices) // 3,
                            dtype=np.uint8,
                        ),
                    )
                )
            else:
                render_meshes.append(mesh)
        return MobyAsset(
            tuid=tuid,
            name=decoded.name or PurePosixPath(path).stem,
            meshes=render_meshes,
            bounding_sphere=decoded.bounding_sphere,
            shader_tuids=shader_tuids,
            skeleton=decoded.skeleton,
            animset_hash=None,
            bind_pose_inverse_offset=0,
            collision_meshes=tuple(collision_meshes),
        )

    def read_shader(self, tuid: int) -> ShaderInfo:
        try:
            path = self._material_paths_by_id[tuid]
        except KeyError as exc:
            raise MissingResourceError(
                f"Burning Skies material 0x{tuid:016X} is unavailable"
            ) from exc
        textures = self._read_material(path)
        texture_paths = (
            textures.albedo,
            textures.normal,
            textures.specular,
            textures.detail,
        )
        texture_ids = tuple(
            self.texture_id(texture_path) if texture_path is not None else None
            for texture_path in texture_paths
        )
        emissive_path = (
            textures.emissive
            if textures.use_emissive_texture is not False
            else None
        )
        emissive_texture_id = (
            self.texture_id(emissive_path)
            if emissive_path is not None
            else None
        )
        alpha_texture_id = (
            self.texture_id(textures.alpha)
            if textures.alpha is not None
            else None
        )
        return ShaderInfo(
            tuid=tuid,
            albedo_texture_id=texture_ids[0],
            normal_texture_id=texture_ids[1],
            specular_texture_id=texture_ids[2],
            detail_texture_id=texture_ids[3],
            lookup_path=path,
            texture_lookup_paths=texture_paths,
            material_values=(textures.diffuse_tint,),
            normal_texture_encoding="DIRECT",
            specular_texture_semantics="GLOSS",
            emissive_texture_id=emissive_texture_id,
            emissive_texture_lookup_path=emissive_path,
            alpha_texture_id=alpha_texture_id,
            alpha_texture_lookup_path=textures.alpha,
            emissive_color=textures.emissive_color,
            emissive_strength=textures.emissive_factor,
            emissive_enabled=textures.emissive_enabled,
            use_emissive_texture=textures.use_emissive_texture,
            specular_power=textures.specular_power,
            alpha_mode_override=textures.alpha_mode_override,
        )

    def _read_material(self, path: str) -> MaterialTexturePaths:
        cached = self._material_cache.get(path)
        if cached is not None:
            return cached
        material = decode_material_texture_paths(
            self.source.read(path),
            label=path,
            texture_paths_by_stem=self._texture_paths_by_stem,
        )
        self._material_cache[path] = material
        return material

    def read_texture(
        self,
        texture_id: int,
        *,
        lookup_path: str | None = None,
    ):
        path = None
        if lookup_path:
            path = self._paths_by_normalized.get(self._normalize(lookup_path))
        if path is None:
            path = self._texture_paths_by_id.get(texture_id)
        if path is None:
            raise MissingResourceError(
                f"Burning Skies texture 0x{texture_id:016X} is unavailable"
            )
        actual_id = self.texture_id(path)
        cached = self._texture_cache.get(actual_id)
        if cached is not None:
            return cached
        texture = decode_p2t(
            self.source.read(path),
            label=path,
            texture_id=actual_id,
        )
        self._texture_cache[actual_id] = texture
        return texture

    def clear_texture_cache(self) -> None:
        self._texture_cache.clear()

    def consume_import_warnings(self) -> tuple[str, ...]:
        warnings = tuple(self.warnings)
        self.warnings.clear()
        return warnings

    def _warn(self, message: str) -> None:
        LOGGER.warning(message)
        if message not in self.warnings:
            self.warnings.append(message)

    def read_animations(self, _asset: MobyAsset):
        return ()
