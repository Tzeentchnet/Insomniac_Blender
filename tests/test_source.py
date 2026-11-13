from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from insomniac_asset_io.core.source import GameSource


class GameSourceTests(unittest.TestCase):
    def test_recognizes_psarc_magic_in_psa_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build.psa"
            path.write_bytes(b"PSAR")
            expected = object()
            with mock.patch.object(
                GameSource,
                "_from_archives",
                return_value=expected,
            ) as open_archives:
                actual = GameSource.open(path)

        self.assertIs(actual, expected)
        open_archives.assert_called_once()
        self.assertEqual(open_archives.call_args.args[1], [path])

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
