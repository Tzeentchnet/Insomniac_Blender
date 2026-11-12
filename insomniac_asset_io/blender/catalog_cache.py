from __future__ import annotations

import json
from pathlib import Path
import subprocess
import uuid

import bpy

from ..core.errors import InsomniacError
from ..games.resistance_2.catalog import local_catalog_path


def run_catalog_worker(
    *,
    iso_path: str | Path,
    cache_directory: str | Path,
) -> dict[str, object]:
    cache_root = Path(cache_directory).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)
    report_path = cache_root / f".catalog-report-{uuid.uuid4().hex}.json"
    worker = Path(__file__).with_name("catalog_worker.py")
    command = [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(worker),
        "--",
        "--iso",
        str(Path(iso_path).expanduser()),
        "--cache-directory",
        str(cache_root),
        "--report",
        str(report_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=6 * 60 * 60,
        )
        if result.returncode != 0:
            output = "\n".join(
                line
                for line in (result.stdout + "\n" + result.stderr).splitlines()
                if line.strip()
            )
            detail = "\n".join(output.splitlines()[-20:])
            raise InsomniacError(
                "Resistance 2 import catalog generation failed"
                + (f":\n{detail}" if detail else "")
            )
        if not report_path.is_file():
            raise InsomniacError(
                "The import catalog worker finished without a report"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise InsomniacError("The import catalog report is invalid")
        catalog = local_catalog_path(cache_root)
        if not catalog.is_file():
            raise InsomniacError(
                f"The import catalog worker did not create {catalog}"
            )
        return report
    finally:
        report_path.unlink(missing_ok=True)
