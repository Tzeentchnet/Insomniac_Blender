from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from insomniac_asset_io.core.iso import (
    list_resistance_2_levels,
    prepare_resistance_2_level,
    resolve_resistance_2_source,
)


class Resistance2IsoTests(unittest.TestCase):
    def test_lists_nested_and_spaced_levels(self):
        with tempfile.TemporaryDirectory() as temporary:
            iso = Path(temporary) / "game.iso"
            iso.touch()
            listing = "\n".join(
                (
                    "PS3_GAME/USRDIR/packed/levels/lobby/level_cached.psarc",
                    "PS3_GAME/USRDIR/packed/levels/bay area/level_cached.psarc",
                    (
                        "PS3_GAME/USRDIR/packed/levels/chicxulub/chicxulub/"
                        "level_cached.psarc"
                    ),
                )
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=listing,
                stderr="",
            )
            with (
                mock.patch(
                    "insomniac_asset_io.core.iso.shutil.which",
                    return_value="tar",
                ),
                mock.patch(
                    "insomniac_asset_io.core.iso.subprocess.run",
                    return_value=completed,
                ),
            ):
                levels = list_resistance_2_levels(iso)

        self.assertEqual(
            levels,
            ("bay area", "chicxulub/chicxulub", "lobby"),
        )

    def test_reuses_complete_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "game.iso"
            iso.touch()
            cache = root / "cache"
            packed = (
                cache
                / "resistance_2"
                / "lobby"
                / "PS3_GAME"
                / "USRDIR"
                / "packed"
            )
            expected = (
                packed / "game" / "global_cached.psarc",
                packed / "game" / "global_uncached.psarc",
                packed / "levels" / "lobby" / "level_cached.psarc",
                packed / "levels" / "lobby" / "level_uncached.psarc",
            )
            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            with mock.patch(
                "insomniac_asset_io.core.iso.subprocess.run"
            ) as run:
                result = prepare_resistance_2_level(iso, "lobby", cache)

        self.assertEqual(result, expected[2])
        run.assert_not_called()

    def test_resolves_only_iso_sources_through_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "game.dec.iso"
            psarc = root / "level_cached.psarc"
            prepared = root / "prepared" / "level_cached.psarc"
            with mock.patch(
                "insomniac_asset_io.core.iso.prepare_resistance_2_level",
                return_value=prepared,
            ) as prepare:
                self.assertEqual(
                    resolve_resistance_2_source(iso, "lobby", root),
                    prepared,
                )
                self.assertEqual(
                    resolve_resistance_2_source(psarc, "lobby", root),
                    psarc,
                )

        prepare.assert_called_once_with(iso, "lobby", root)


if __name__ == "__main__":
    unittest.main()
