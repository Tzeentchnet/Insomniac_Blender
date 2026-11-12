from __future__ import annotations

import fnmatch
from pathlib import Path
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = tomllib.loads((ROOT / "blender_manifest.toml").read_text("utf-8"))
OUTPUT = (
    ROOT
    / "dist"
    / f"{MANIFEST['id']}-{MANIFEST['version']}.zip"
)
EXCLUDED_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "tests",
    "tools",
}
EXCLUDED_GLOBS = (".gitignore", "*.log", "*.pyc", "*.pyo", "*.zip")


def include(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    return not any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_GLOBS)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in ROOT.rglob("*") if path.is_file() and include(path))
    with zipfile.ZipFile(
        OUTPUT,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size} bytes, {len(files)} files)")


if __name__ == "__main__":
    main()
