from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from insomniac_asset_io.core.source import GameSource


class GameSourceTests(unittest.TestCase):
    def test_level_archive_discovers_pair_and_global_siblings(self):
        with tempfile.TemporaryDirectory() as temporary:
            packed = Path(temporary) / "packed"
            level = packed / "levels" / "fixture"
            game = packed / "game"
            level.mkdir(parents=True)
            game.mkdir()
            selected = level / "level_cached.psarc"
            expected = [
                selected,
                level / "level_uncached.psarc",
                game / "global_cached.psarc",
                game / "global_uncached.psarc",
            ]
            for path in expected:
                path.touch()

            actual = GameSource._paired_archives(selected)

        self.assertEqual(actual, expected)

    def test_discovers_extracted_sibling_level_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            levels = Path(temporary) / "packed" / "levels"
            selected = levels / "selected" / "level_cached.psarc"
            sibling = levels / "sibling" / "level_cached.psarc"
            nested = levels / "chapter" / "nested" / "level_cached.psarc"
            for path in (selected, sibling, nested):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            source = GameSource(
                selected.parent,
                [],
                selected_path=selected,
            )
            actual = source.sibling_level_archives()

        self.assertEqual(actual, (nested, sibling))


if __name__ == "__main__":
    unittest.main()
