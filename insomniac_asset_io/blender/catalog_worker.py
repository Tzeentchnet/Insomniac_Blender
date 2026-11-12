from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Generate an Insomniac game import metadata catalog"
    )
    parser.add_argument("--game", required=True)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--cache-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(arguments)


def _load_builder():
    extension_root = Path(__file__).resolve().parents[2]
    root_text = str(extension_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from insomniac_asset_io.games.catalog import (
        build_catalog_from_iso,
        local_catalog_path,
    )

    return build_catalog_from_iso, local_catalog_path


def main() -> int:
    args = _arguments()
    build_catalog, catalog_path = _load_builder()

    def progress(index: int, total: int, level: str) -> None:
        print(f"[{index}/{total}] {level}", flush=True)

    try:
        report = build_catalog(
            args.iso,
            catalog_path(args.cache_directory, args.game),
            game_id=args.game,
            region="Custom",
            work_directory=args.cache_directory,
            progress=progress,
        )
    except Exception as exc:
        report = {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(report["traceback"], file=sys.stderr)
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("INSOMNIAC_CATALOG_REPORT=" + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
