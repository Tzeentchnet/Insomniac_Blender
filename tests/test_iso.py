import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from insomniac_asset_io.core.errors import InsomniacError
from insomniac_asset_io.core.iso import (
    extract_game_iso_member,
    find_resistance_2_iso,
    list_resistance_2_levels,
    list_resistance_3_levels,
    parse_param_sfo,
    prepare_resistance_2_level,
    resolve_resistance_2_source,
    validate_game_iso,
)


SECTOR_SIZE = 2048


def _param_sfo(values: dict[str, str]) -> bytes:
    keys = bytearray()
    encoded_values = bytearray()
    entries = bytearray()
    key_offsets: dict[str, int] = {}
    value_offsets: dict[str, int] = {}
    for key, value in values.items():
        key_offsets[key] = len(keys)
        keys.extend(key.encode("utf-8") + b"\0")
        value_offsets[key] = len(encoded_values)
        encoded_values.extend(value.encode("utf-8") + b"\0")
    key_table = 20 + len(values) * 16
    value_table = (key_table + len(keys) + 3) & ~3
    for key, value in values.items():
        length = len(value.encode("utf-8")) + 1
        entries.extend(key_offsets[key].to_bytes(2, "little"))
        entries.extend((0x0204).to_bytes(2, "little"))
        entries.extend(length.to_bytes(4, "little"))
        entries.extend(length.to_bytes(4, "little"))
        entries.extend(value_offsets[key].to_bytes(4, "little"))
    payload = bytearray(value_table + len(encoded_values))
    payload[:4] = b"\0PSF"
    payload[4:8] = (0x00000101).to_bytes(4, "little")
    payload[8:12] = key_table.to_bytes(4, "little")
    payload[12:16] = value_table.to_bytes(4, "little")
    payload[16:20] = len(values).to_bytes(4, "little")
    payload[20 : 20 + len(entries)] = entries
    payload[key_table : key_table + len(keys)] = keys
    payload[value_table:] = encoded_values
    return bytes(payload)


def _directory_record(
    identifier: bytes,
    sector: int,
    length: int,
    *,
    directory: bool,
) -> bytes:
    record_length = 33 + len(identifier) + (len(identifier) % 2 == 0)
    record = bytearray(record_length)
    record[0] = record_length
    record[2:6] = sector.to_bytes(4, "little")
    record[6:10] = sector.to_bytes(4, "big")
    record[10:14] = length.to_bytes(4, "little")
    record[14:18] = length.to_bytes(4, "big")
    record[25] = 0x02 if directory else 0
    record[28:30] = (1).to_bytes(2, "little")
    record[30:32] = (1).to_bytes(2, "big")
    record[32] = len(identifier)
    record[33 : 33 + len(identifier)] = identifier
    return bytes(record)


def _write_directory(
    image: bytearray,
    sector: int,
    parent_sector: int,
    children: tuple[tuple[bytes, int, int, bool], ...],
) -> None:
    data = b"".join(
        (
            _directory_record(
                b"\0",
                sector,
                SECTOR_SIZE,
                directory=True,
            ),
            _directory_record(
                b"\1",
                parent_sector,
                SECTOR_SIZE,
                directory=True,
            ),
            *(
                _directory_record(
                    name,
                    child_sector,
                    length,
                    directory=directory,
                )
                for name, child_sector, length, directory in children
            ),
        )
    )
    offset = sector * SECTOR_SIZE
    image[offset : offset + len(data)] = data


def _minimal_game_iso(path: Path) -> bytes:
    sectors = 32
    image = bytearray(sectors * SECTOR_SIZE)
    payload = b"synthetic level archive"

    primary = memoryview(image)[16 * SECTOR_SIZE : 17 * SECTOR_SIZE]
    primary[0] = 1
    primary[1:6] = b"CD001"
    primary[6] = 1
    primary[80:84] = sectors.to_bytes(4, "little")
    primary[84:88] = sectors.to_bytes(4, "big")
    primary[128:130] = SECTOR_SIZE.to_bytes(2, "little")
    primary[130:132] = SECTOR_SIZE.to_bytes(2, "big")
    root = _directory_record(
        b"\0",
        20,
        SECTOR_SIZE,
        directory=True,
    )
    primary[156 : 156 + len(root)] = root

    terminator = memoryview(image)[17 * SECTOR_SIZE : 18 * SECTOR_SIZE]
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1

    _write_directory(
        image,
        20,
        20,
        ((b"PS3_GAME", 21, SECTOR_SIZE, True),),
    )
    _write_directory(
        image,
        21,
        20,
        ((b"USRDIR", 22, SECTOR_SIZE, True),),
    )
    _write_directory(
        image,
        22,
        21,
        ((b"PACKED", 23, SECTOR_SIZE, True),),
    )
    _write_directory(
        image,
        23,
        22,
        ((b"LEVELS", 24, SECTOR_SIZE, True),),
    )
    _write_directory(
        image,
        24,
        23,
        ((b"LOBBY", 25, SECTOR_SIZE, True),),
    )
    _write_directory(
        image,
        25,
        24,
        ((b"LEVEL_CACHED.PSARC;1", 26, len(payload), False),),
    )
    offset = 26 * SECTOR_SIZE
    image[offset : offset + len(payload)] = payload
    path.write_bytes(image)
    return payload


class Resistance2IsoTests(unittest.TestCase):
    def test_parses_bounded_param_sfo_strings(self):
        values = parse_param_sfo(
            _param_sfo(
                {
                    "TITLE_ID": "BCUS98127",
                    "VERSION": "02.00",
                }
            )
        )

        self.assertEqual(values["TITLE_ID"], "BCUS98127")
        self.assertEqual(values["VERSION"], "02.00")

    def test_rejects_invalid_param_sfo_ranges(self):
        data = bytearray(_param_sfo({"TITLE_ID": "BCUS98127"}))
        data[8:12] = (0xFFFF).to_bytes(4, "little")

        with self.assertRaisesRegex(
            InsomniacError,
            "invalid PARAM.SFO table offsets",
        ):
            parse_param_sfo(bytes(data))

    def test_tod_iso_identity_mismatch_fails_explicitly(self):
        with (
            mock.patch(
                "insomniac_asset_io.core.iso.read_game_iso_member",
                return_value=_param_sfo(
                    {
                        "TITLE_ID": "BCES00001",
                        "VERSION": "01.00",
                    }
                ),
            ),
            self.assertRaisesRegex(
                InsomniacError,
                "requires TITLE_ID BCUS98127",
            ),
        ):
            validate_game_iso(
                "wrong.iso",
                game_label="Tools of Destruction",
                expected_title_id="BCUS98127",
                expected_version="02.00",
            )

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

    def test_resolves_single_iso_inside_source_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "disc"
            source.mkdir()
            iso = source / "Resistance 2.dec.iso"
            iso.touch()
            prepared = root / "prepared" / "level_cached.psarc"
            with mock.patch(
                "insomniac_asset_io.core.iso.prepare_resistance_2_level",
                return_value=prepared,
            ) as prepare:
                result = resolve_resistance_2_source(
                    source,
                    "lobby",
                    root,
                )

        self.assertEqual(result, prepared)
        prepare.assert_called_once_with(iso, "lobby", root)

    def test_rejects_source_directory_with_multiple_isos(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "disc-1.iso").touch()
            (source / "disc-2.iso").touch()

            with self.assertRaisesRegex(
                InsomniacError,
                "contains multiple ISO files",
            ):
                find_resistance_2_iso(source)

    def test_reads_and_extracts_iso9660_members_without_tar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "resistance_3.iso"
            expected = _minimal_game_iso(iso)
            output = root / "level_cached.psarc"

            with mock.patch(
                "insomniac_asset_io.core.iso.subprocess.run",
                side_effect=AssertionError("tar fallback was used"),
            ):
                levels = list_resistance_3_levels(iso)
                extract_game_iso_member(
                    iso,
                    (
                        "PS3_GAME/USRDIR/packed/levels/lobby/"
                        "level_cached.psarc"
                    ),
                    output,
                    game_label="Resistance 3",
                )
                actual = output.read_bytes()

        self.assertEqual(levels, ("lobby",))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
