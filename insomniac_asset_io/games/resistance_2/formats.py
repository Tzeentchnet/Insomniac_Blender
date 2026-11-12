from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float32]
IntArray = NDArray[np.int32]
UInt16Array = NDArray[np.uint16]
UInt8Array = NDArray[np.uint8]


class AssetKind(Enum):
    SHADER = (0x1D100, "shaders.dat")
    TEXTURE = (0x1D180, "textures.dat")
    HIGH_MIP = (0x1D1C0, "highmips.dat")
    CUBEMAP = (0x1D200, "cubemaps.dat")
    TIE = (0x1D300, "ties.dat")
    FOLIAGE = (0x1D400, "foliage.dat")
    SHRUB = (0x1D500, "shrubs.dat")
    MOBY = (0x1D600, "mobys.dat")
    ANIMSET = (0x1D700, "animsets.dat")
    CINEMATIC = (0x1D800, "cinematics.dat")
    ZONE = (0x1DA00, "zones.dat")
    LIGHTING = (0x1DB00, "lighting.dat")

    @property
    def section_id(self) -> int:
        return self.value[0]

    @property
    def filename(self) -> str:
        return self.value[1]


@dataclass(frozen=True, slots=True)
class AssetPointer:
    tuid: int
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class SourceSummary:
    counts: dict[AssetKind, int]
    resource_prefix: str


@dataclass(frozen=True, slots=True)
class Bone:
    flags: int
    parent_index: int
    child_index: int
    sibling_index: int

    @property
    def inherits_scale(self) -> bool:
        return not bool(self.flags & 0x01)

    def has_parent(self, own_index: int, bone_count: int) -> bool:
        return (
            self.parent_index >= 0
            and self.parent_index != own_index
            and self.parent_index < bone_count
        )

    def has_child(self, own_index: int, bone_count: int) -> bool:
        return (
            self.child_index > 0
            and self.child_index != own_index
            and self.child_index < bone_count
        )


@dataclass(slots=True)
class Skeleton:
    root_bone: int
    bones: tuple[Bone, ...]
    bind_local: FloatArray
    bind_world: FloatArray
    bind_world_inverse: FloatArray
    scale_shift: int
    translation_shift: int

    @property
    def bone_count(self) -> int:
        return len(self.bones)


@dataclass(slots=True)
class MeshGeometry:
    shader_index: int
    positions: FloatArray
    uvs: FloatArray
    indices: IntArray
    bone_indices: UInt16Array | None = None
    bone_weights: UInt8Array | None = None
    segment_index: int = 0
    is_damage_permutation: bool = False


@dataclass(slots=True)
class CollisionPrimitive:
    kind: int
    bone_index: int | None
    data: FloatArray


@dataclass(slots=True)
class CollisionMesh:
    positions: FloatArray
    indices: IntArray
    face_types: UInt8Array


@dataclass(slots=True)
class MobyAsset:
    tuid: int
    name: str
    meshes: list[MeshGeometry]
    bounding_sphere: tuple[float, float, float, float]
    shader_tuids: tuple[int, ...]
    skeleton: Skeleton | None
    animset_hash: int | None
    bind_pose_inverse_offset: int
    collision_primitives: tuple[CollisionPrimitive, ...] = ()
    collision_meshes: tuple[CollisionMesh, ...] = ()


@dataclass(slots=True)
class TieAsset:
    tuid: int
    meshes: list[MeshGeometry]
    shader_tuids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ShaderInfo:
    tuid: int
    albedo_texture_id: int | None
    normal_texture_id: int | None
    specular_texture_id: int | None
    detail_texture_id: int | None
    lookup_path: str | None = None
    texture_lookup_paths: tuple[
        str | None,
        str | None,
        str | None,
        str | None,
    ] = (None, None, None, None)
    material_word: int | None = None
    material_values: tuple[tuple[float, float, float, float], ...] = ()

    @property
    def expensive_texture_id(self) -> int | None:
        return self.specular_texture_id

    @property
    def material_flags(self) -> int | None:
        if self.material_word is None:
            return None
        return (self.material_word >> 24) & 0xFF

    @property
    def blend_mode(self) -> int | None:
        if self.material_word is None:
            return None
        return (self.material_word >> 16) & 0xFF

    @property
    def material_unknown_0(self) -> int | None:
        if self.material_word is None:
            return None
        return (self.material_word >> 8) & 0xFF

    @property
    def material_unknown_1(self) -> int | None:
        if self.material_word is None:
            return None
        return self.material_word & 0xFF

    @property
    def use_specular(self) -> bool:
        return bool((self.material_flags or 0) & 0x02)

    @property
    def use_glossiness(self) -> bool:
        return bool((self.material_flags or 0) & 0x04)

    @property
    def use_normal_map(self) -> bool:
        return bool((self.material_flags or 0) & 0x08)

    @property
    def use_detail_map(self) -> bool:
        return bool((self.material_flags or 0) & 0x10)

    @property
    def uses_implicit_albedo_alpha(self) -> bool:
        return self.tuid == 0x3B7E_8841_9D2A_C85A

    @property
    def alpha_mode(self) -> str:
        blend_mode = self.blend_mode or 0
        if blend_mode == 4:
            return "MASK"
        if blend_mode or self.uses_implicit_albedo_alpha:
            return "BLEND"
        return "OPAQUE"

    @property
    def source_placeholder_kind(self) -> str | None:
        path = (self.lookup_path or "").replace("\\", "/").strip("/").casefold()
        if path == "defaults/default_pink_shader/default_pink_shader":
            return "DEFAULT_PINK"
        return None

    @property
    def base_color_factor(self) -> tuple[float, float, float, float]:
        if not self.material_values:
            return (1.0, 1.0, 1.0, 1.0)
        return self.material_values[0]


@dataclass(frozen=True, slots=True)
class TextureInfo:
    id: int
    tuid: int
    format_code: int
    mip_count: int
    width: int
    height: int
    offset: int
    length: int


@dataclass(slots=True)
class DecodedTexture:
    info: TextureInfo
    rgba: UInt8Array


@dataclass(slots=True)
class PlacedInstance:
    asset_tuid: int
    instance_tuid: int
    name: str
    matrix: FloatArray
    group: int = 0


@dataclass(slots=True)
class TerrainMesh:
    tuid: int
    shader_index: int
    positions: FloatArray
    uvs: FloatArray
    indices: IntArray


@dataclass(slots=True)
class Zone:
    tuid: int
    tie_instances: list[PlacedInstance]
    terrain_meshes: list[TerrainMesh]
    shader_tuids: tuple[int, ...]
    unsupported_shrub_instances: int = 0
    unsupported_foliage_instances: int = 0


@dataclass(slots=True)
class GameplayRegion:
    name: str
    moby_instances: list[PlacedInstance]


class TrackKind(Enum):
    ROTATION = 0
    SCALE = 1
    POSITION = 2
    UNKNOWN = 3


@dataclass(frozen=True, slots=True)
class TrackMask:
    bone_index: int
    component: int
    kind: TrackKind
    marker: int

    @classmethod
    def unpack(cls, raw: int) -> "TrackMask":
        kind_bits = (raw >> 4) & 0b11
        return cls(
            bone_index=(raw >> 6) & 0x3FF,
            component=(raw >> 2) & 0b11,
            kind=TrackKind(kind_bits),
            marker=raw & 0b11,
        )


@dataclass(frozen=True, slots=True)
class AnimationHeader:
    index: int
    flags: int
    bone_count: int
    frame_count: int
    name: str
    frame_rate: float
    linear_speed: float
    frame_stride: int
    reference_value_count: int
    track16_count: int
    track8_count: int
    control_offset: int
    frames_offset: int

    @property
    def looping(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def additive(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def packed_frames(self) -> bool:
        return bool(self.flags & 0x04)


@dataclass(slots=True)
class DecodedClip:
    name: str
    frame_rate: float
    looping: bool
    additive: bool
    translations: FloatArray
    rotations_xyzw: FloatArray
    scales: FloatArray
    animated_bones: NDArray[np.bool_]

    @property
    def frame_count(self) -> int:
        return int(self.translations.shape[0])

    @property
    def bone_count(self) -> int:
        return int(self.translations.shape[1])
