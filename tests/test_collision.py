from __future__ import annotations

import unittest

import numpy as np

from insomniac_asset_io.core.collision import (
    CollisionInstance,
    CollisionMesh,
    CollisionScene,
    validate_collision_scene,
)
from insomniac_asset_io.core.errors import FormatError


class CollisionModelTests(unittest.TestCase):
    def test_accepts_shared_mesh_instances_and_face_metadata(self) -> None:
        scene = CollisionScene(
            meshes=(
                self._mesh(
                    surface_ids=np.asarray((7,), dtype=np.int32),
                    material_ids=np.asarray((3,), dtype=np.int32),
                    metadata={"flags": 5, "walkable": True},
                ),
            ),
            instances=(
                self._instance("first"),
                self._instance("second"),
            ),
        )

        validate_collision_scene(scene, label="fixture")

    def test_rejects_duplicate_mesh_ids(self) -> None:
        scene = CollisionScene(
            meshes=(self._mesh(), self._mesh()),
            instances=(self._instance(),),
        )

        with self.assertRaisesRegex(
            FormatError,
            "duplicate collision mesh ID",
        ):
            validate_collision_scene(scene)

    def test_rejects_malformed_positions_and_indices(self) -> None:
        cases = (
            (
                self._mesh(
                    positions=np.zeros((3, 2), dtype=np.float32),
                ),
                "positions have shape",
            ),
            (
                self._mesh(
                    positions=np.asarray(
                        ((0, 0, 0), (1, 0, 0), (0, np.nan, 0)),
                        dtype=np.float32,
                    ),
                ),
                "non-finite positions",
            ),
            (
                self._mesh(
                    indices=np.asarray(((0, 1, 3),), dtype=np.int32),
                ),
                "index range",
            ),
            (
                self._mesh(
                    indices=np.asarray((0, 1, 2), dtype=np.int32),
                ),
                "indices have shape",
            ),
        )
        for mesh, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(FormatError, message),
            ):
                validate_collision_scene(
                    CollisionScene(
                        meshes=(mesh,),
                        instances=(self._instance(),),
                    )
                )

    def test_rejects_mismatched_face_metadata(self) -> None:
        mesh = self._mesh(
            surface_ids=np.asarray((1, 2), dtype=np.int32),
        )

        with self.assertRaisesRegex(FormatError, "surface IDs have shape"):
            validate_collision_scene(
                CollisionScene(
                    meshes=(mesh,),
                    instances=(self._instance(),),
                )
            )

    def test_rejects_unknown_instance_mesh(self) -> None:
        instance = self._instance()
        instance.mesh_id = "missing"

        with self.assertRaisesRegex(FormatError, "unknown mesh"):
            validate_collision_scene(
                CollisionScene(
                    meshes=(self._mesh(),),
                    instances=(instance,),
                )
            )

    def test_rejects_non_affine_instance_transform(self) -> None:
        instance = self._instance()
        instance.matrix[3, 0] = 1.0

        with self.assertRaisesRegex(FormatError, "not affine"):
            validate_collision_scene(
                CollisionScene(
                    meshes=(self._mesh(),),
                    instances=(instance,),
                )
            )

    def test_rejects_unsupported_metadata_values(self) -> None:
        mesh = self._mesh(metadata={"nested": {"value": 1}})

        with self.assertRaisesRegex(FormatError, "unsupported value"):
            validate_collision_scene(
                CollisionScene(
                    meshes=(mesh,),
                    instances=(self._instance(),),
                )
            )

    @staticmethod
    def _mesh(**changes) -> CollisionMesh:
        values = {
            "mesh_id": "fixture",
            "name": "Fixture Mesh",
            "positions": np.asarray(
                ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                dtype=np.float32,
            ),
            "indices": np.asarray(((0, 1, 2),), dtype=np.int32),
            "source_kind": "STATIC_PROP",
            "source_id": "0x1234",
        }
        values.update(changes)
        return CollisionMesh(**values)

    @staticmethod
    def _instance(name: str = "instance") -> CollisionInstance:
        return CollisionInstance(
            mesh_id="fixture",
            name=name,
            matrix=np.eye(4, dtype=np.float32),
            source_group="Static Props",
            source_id=f"{name}-id",
            metadata={"group_index": 2},
        )


if __name__ == "__main__":
    unittest.main()
