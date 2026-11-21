from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from .errors import FormatError

CollisionMetadataValue: TypeAlias = bool | int | float | str
CollisionMetadata: TypeAlias = dict[str, CollisionMetadataValue]
FloatArray: TypeAlias = NDArray[np.float32]
IntArray: TypeAlias = NDArray[np.int32]


@dataclass(slots=True)
class CollisionMesh:
    mesh_id: str
    name: str
    positions: FloatArray
    indices: IntArray
    surface_ids: IntArray | None = None
    material_ids: IntArray | None = None
    source_kind: str = ""
    source_id: str | None = None
    metadata: CollisionMetadata = field(default_factory=dict)


@dataclass(slots=True)
class CollisionInstance:
    mesh_id: str
    name: str
    matrix: FloatArray
    source_group: str
    source_id: str | None = None
    metadata: CollisionMetadata = field(default_factory=dict)


@dataclass(slots=True)
class CollisionScene:
    meshes: tuple[CollisionMesh, ...] = ()
    instances: tuple[CollisionInstance, ...] = ()


def validate_collision_scene(
    scene: CollisionScene,
    *,
    label: str = "level collision",
) -> None:
    meshes: dict[str, CollisionMesh] = {}
    for mesh in scene.meshes:
        if not mesh.mesh_id:
            raise FormatError(f"{label}: collision mesh ID is empty")
        if mesh.mesh_id in meshes:
            raise FormatError(
                f"{label}: duplicate collision mesh ID {mesh.mesh_id!r}"
            )
        meshes[mesh.mesh_id] = mesh
        _validate_mesh(mesh, label=label)

    for instance in scene.instances:
        if instance.mesh_id not in meshes:
            raise FormatError(
                f"{label}: collision instance {instance.name!r} references "
                f"unknown mesh {instance.mesh_id!r}"
            )
        if not instance.name:
            raise FormatError(f"{label}: collision instance name is empty")
        if not instance.source_group:
            raise FormatError(
                f"{label}: collision instance {instance.name!r} has no "
                "source group"
            )
        matrix = np.asarray(instance.matrix)
        if matrix.shape != (4, 4):
            raise FormatError(
                f"{label}: collision instance {instance.name!r} matrix has "
                f"shape {matrix.shape}, expected (4, 4)"
            )
        if not np.all(np.isfinite(matrix)):
            raise FormatError(
                f"{label}: collision instance {instance.name!r} matrix "
                "contains non-finite values"
            )
        if not np.allclose(
            matrix[3],
            (0.0, 0.0, 0.0, 1.0),
            rtol=0.0,
            atol=1e-6,
        ):
            raise FormatError(
                f"{label}: collision instance {instance.name!r} matrix is "
                "not affine"
            )
        _validate_metadata(
            instance.metadata,
            label=f"{label} instance {instance.name!r}",
        )


def _validate_mesh(mesh: CollisionMesh, *, label: str) -> None:
    if not mesh.name:
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} has no name"
        )

    positions = np.asarray(mesh.positions)
    if positions.ndim != 2 or positions.shape[1:] != (3,):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} positions have "
            f"shape {positions.shape}, expected (vertex_count, 3)"
        )
    if not len(positions):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} has no vertices"
        )
    if not np.issubdtype(positions.dtype, np.floating):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} positions must be "
            "floating-point"
        )
    if not np.all(np.isfinite(positions)):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} contains non-finite "
            "positions"
        )

    indices = np.asarray(mesh.indices)
    if indices.ndim != 2 or indices.shape[1:] != (3,):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} indices have shape "
            f"{indices.shape}, expected (face_count, 3)"
        )
    if not len(indices):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} has no faces"
        )
    if not np.issubdtype(indices.dtype, np.integer):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} indices must be "
            "integers"
        )
    minimum = int(indices.min())
    maximum = int(indices.max())
    if minimum < 0 or maximum >= len(positions):
        raise FormatError(
            f"{label}: collision mesh {mesh.mesh_id!r} index range "
            f"[{minimum}, {maximum}] exceeds {len(positions)} vertices"
        )

    _validate_face_ids(
        mesh.surface_ids,
        len(indices),
        label=f"{label} collision mesh {mesh.mesh_id!r} surface IDs",
    )
    _validate_face_ids(
        mesh.material_ids,
        len(indices),
        label=f"{label} collision mesh {mesh.mesh_id!r} material IDs",
    )
    _validate_metadata(
        mesh.metadata,
        label=f"{label} collision mesh {mesh.mesh_id!r}",
    )


def _validate_face_ids(
    values: IntArray | None,
    face_count: int,
    *,
    label: str,
) -> None:
    if values is None:
        return
    ids = np.asarray(values)
    if ids.shape != (face_count,):
        raise FormatError(
            f"{label} have shape {ids.shape}, expected ({face_count},)"
        )
    if not np.issubdtype(ids.dtype, np.integer):
        raise FormatError(f"{label} must be integers")


def _validate_metadata(
    metadata: CollisionMetadata,
    *,
    label: str,
) -> None:
    for key, value in metadata.items():
        if not isinstance(key, str) or not key:
            raise FormatError(f"{label} has an empty metadata key")
        if isinstance(value, bool | int | str):
            continue
        if isinstance(value, float) and isfinite(value):
            continue
        raise FormatError(
            f"{label} metadata {key!r} has unsupported value {value!r}"
        )
