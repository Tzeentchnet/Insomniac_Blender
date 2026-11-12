from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import bpy
except ModuleNotFoundError:
    bpy = None


if bpy is not None:
    from insomniac_asset_io.blender.importer import _MaterialFactory
    from insomniac_asset_io.games.resistance_2.formats import ShaderInfo


@unittest.skipUnless(bpy is not None, "Blender Python is not available")
class BlenderMaterialTests(unittest.TestCase):
    def tearDown(self) -> None:
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in tuple(bpy.data.images):
            bpy.data.images.remove(image)

    def test_authored_alpha_mode_controls_diffuse_alpha(self) -> None:
        image = bpy.data.images.new(
            "Transparent_Albedo",
            width=1,
            height=1,
            alpha=True,
        )
        image.pixels.foreach_set((1.0, 1.0, 1.0, 0.0))
        image.update()
        factory = _MaterialFactory(None, True, [])
        factory.image = lambda texture_id, **kwargs: image

        opaque, opaque_principled = self._material("Opaque")
        opaque_render_method = opaque.surface_render_method
        factory._add_diffuse(
            opaque,
            opaque_principled,
            ShaderInfo(1, 0x1234, None, None, None, material_word=0),
        )

        self.assertFalse(opaque_principled.inputs["Alpha"].is_linked)
        self.assertEqual(opaque_principled.inputs["Alpha"].default_value, 1.0)
        self.assertEqual(opaque.surface_render_method, opaque_render_method)

        masked, masked_principled = self._material("Masked")
        factory._add_diffuse(
            masked,
            masked_principled,
            ShaderInfo(
                2,
                0x1234,
                None,
                None,
                None,
                material_word=0x0004_0000,
            ),
        )

        self.assertTrue(masked_principled.inputs["Alpha"].is_linked)
        alpha_link = masked_principled.inputs["Alpha"].links[0]
        self.assertEqual(alpha_link.from_node.name, "Alpha_Clip_0000000000000002")
        self.assertEqual(alpha_link.from_node.operation, "GREATER_THAN")
        self.assertEqual(masked.surface_render_method, "DITHERED")

    def test_color_textures_keep_rgb_independent_from_alpha(self) -> None:
        image = bpy.data.images.new(
            "Packed_Albedo",
            width=1,
            height=1,
            alpha=True,
        )
        self.assertEqual(image.alpha_mode, "STRAIGHT")

        _MaterialFactory._apply_colorspace(image, "color")

        self.assertEqual(image.alpha_mode, "CHANNEL_PACKED")

    @staticmethod
    def _material(name: str):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        return material, principled


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
