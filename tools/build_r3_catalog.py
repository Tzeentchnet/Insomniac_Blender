from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.games.resistance_3.catalog import (
    build_catalog_from_iso,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a metadata-only Resistance 3 import catalog"
    )
    parser.add_argument("iso", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--region", default="Custom")
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()

    def progress(index: int, total: int, level: str) -> None:
        print(f"[{index}/{total}] {level}", flush=True)

    report = build_catalog_from_iso(
        args.iso,
        args.output,
        region=args.region,
        work_directory=args.work_directory,
        progress=progress,
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
