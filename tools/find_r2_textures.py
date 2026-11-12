from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


def _texture_id(value: str) -> int:
    try:
        parsed = int(value.replace("_", ""), 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid texture ID {value!r}"
        ) from exc
    if not 0 <= parsed <= 0xFFFF_FFFF:
        raise argparse.ArgumentTypeError(
            f"Texture ID {value!r} is outside the u32 range"
        )
    return parsed


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Resistance 2 texture IDs across level PSARCs"
    )
    parser.add_argument("iso", type=Path)
    parser.add_argument("texture_ids", nargs="+", type=_texture_id)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def _level_archives(tar: str, iso: Path) -> list[str]:
    result = subprocess.run(
        [tar, "-tf", str(iso)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60 * 60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Could not list ISO contents: {detail}")
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("PS3_GAME/USRDIR/packed/levels/")
        and line.endswith("/level_cached.psarc")
    )


def _extract_member(
    tar: str,
    iso: Path,
    member: str,
    destination: Path,
) -> None:
    with destination.open("wb") as stream:
        result = subprocess.run(
            [tar, "-xOf", str(iso), member],
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=60 * 60,
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not extract {member}: {detail}")


def main() -> int:
    args = _arguments()
    iso = args.iso.expanduser().resolve()
    if not iso.is_file():
        raise FileNotFoundError(f"ISO not found: {iso}")
    tar = shutil.which("tar")
    if tar is None:
        raise RuntimeError("No libarchive-compatible tar executable was found")

    targets = set(args.texture_ids)
    archives = _level_archives(tar, iso)
    matches: list[dict[str, object]] = []
    report: dict[str, object] = {
        "iso": str(iso),
        "texture_ids": [f"0x{value:08X}" for value in sorted(targets)],
        "archives_scanned": 0,
        "matches": matches,
    }

    work_parent = (
        args.work_directory.expanduser().resolve()
        if args.work_directory is not None
        else None
    )
    if work_parent is not None:
        work_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="r2_texture_scan_",
        dir=work_parent,
    ) as temporary:
        archive_path = Path(temporary) / "level_cached.psarc"
        for index, member in enumerate(archives, start=1):
            print(f"[{index}/{len(archives)}] {member}", flush=True)
            _extract_member(tar, iso, member, archive_path)
            try:
                with GameSource.open(archive_path) as source:
                    reader = Resistance2Reader(source)
                    for texture_id in sorted(
                        targets.intersection(reader.texture_infos)
                    ):
                        info = reader.texture_infos[texture_id]
                        match = {
                            "texture_id": f"0x{texture_id:08X}",
                            "archive": member,
                            "resource_prefix": reader.resource_prefix,
                            "tuid": f"0x{info.tuid:016X}",
                            "format": f"0x{info.format_code:02X}",
                            "width": info.width,
                            "height": info.height,
                            "offset": info.offset,
                            "length": info.length,
                        }
                        matches.append(match)
                        print(f"  found {match}", flush=True)
            finally:
                archive_path.unlink(missing_ok=True)
            report["archives_scanned"] = index

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.report is not None:
        output = args.report.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
