from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
import subprocess

from .errors import InsomniacError, MissingResourceError


DEFAULT_CACHE_DIRECTORY = Path.home() / "InsomniacAssetIOCache"
_PACKED_ROOT = PurePosixPath("PS3_GAME/USRDIR/packed")


def _iso_path(iso_path: str | Path) -> Path:
    iso = Path(iso_path).expanduser()
    if not iso.is_file():
        raise MissingResourceError(f"Resistance 2 ISO was not found: {iso}")
    return iso


def _tar_executable() -> str:
    tar = shutil.which("tar")
    if tar is None:
        raise InsomniacError(
            "No tar executable was found. Extract the PSARC files manually "
            "or install a libarchive-compatible tar."
        )
    return tar


def _level_path(level_name: str) -> PurePosixPath:
    level = PurePosixPath(level_name.strip().replace("\\", "/"))
    if (
        not level.parts
        or level.is_absolute()
        or any(part in ("", ".", "..") for part in level.parts)
    ):
        raise InsomniacError(f"Invalid Resistance 2 level name: {level_name!r}")
    return level


def list_resistance_2_levels(iso_path: str | Path) -> tuple[str, ...]:
    """Return level archive names present in a Resistance 2 ISO."""

    iso = _iso_path(iso_path)
    tar = _tar_executable()
    result = subprocess.run(
        [tar, "-tf", str(iso)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15 * 60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InsomniacError(
            f"Failed to list Resistance 2 levels in the ISO: {detail}"
        )

    prefix = f"{_PACKED_ROOT.as_posix()}/levels/"
    suffix = "/level_cached.psarc"
    levels: set[str] = set()
    for raw_path in result.stdout.splitlines():
        path = raw_path.strip().replace("\\", "/").lstrip("./")
        if not path.startswith(prefix) or not path.endswith(suffix):
            continue
        level_name = path[len(prefix) : -len(suffix)]
        if level_name:
            levels.add(_level_path(level_name).as_posix())
    if not levels:
        raise InsomniacError(
            "The ISO contains no Resistance 2 level_cached.psarc archives"
        )
    return tuple(sorted(levels, key=str.casefold))


def prepare_resistance_2_level(
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    """Extract the selected level and global PSARCs with the system tar tool."""

    iso = _iso_path(iso_path)
    level = _level_path(level_name)

    output = resistance_2_level_cache_root(cache_directory, level.as_posix())
    output.mkdir(parents=True, exist_ok=True)

    base = _PACKED_ROOT.as_posix()
    members = [
        f"{base}/game/global_cached.psarc",
        f"{base}/game/global_uncached.psarc",
        f"{base}/levels/{level.as_posix()}/level_cached.psarc",
        f"{base}/levels/{level.as_posix()}/level_uncached.psarc",
    ]
    extracted_root = output / "PS3_GAME" / "USRDIR" / "packed"
    expected = tuple(
        extracted_root / Path(*PurePosixPath(member).parts[3:])
        for member in members
    )
    cached_level = expected[2]
    if all(path.is_file() for path in expected):
        return cached_level

    tar = _tar_executable()
    command = [tar, "-xf", str(iso), "-C", str(output), *members]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60 * 60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InsomniacError(
            f"Failed to extract Resistance 2 archives from the ISO: {detail}"
        )
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise InsomniacError(
            "tar completed but did not create the expected archives: "
            + ", ".join(missing)
        )
    return cached_level


def resistance_2_level_cache_root(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    level = _level_path(level_name)
    return Path(cache_directory).expanduser() / "resistance_2" / Path(
        *level.parts
    )


def resistance_2_cached_level_path(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    level = _level_path(level_name)
    return (
        resistance_2_level_cache_root(cache_directory, level.as_posix())
        / "PS3_GAME"
        / "USRDIR"
        / "packed"
        / "levels"
        / Path(*level.parts)
        / "level_cached.psarc"
    )


def resolve_resistance_2_source(
    source_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    """Resolve a PSARC/directory directly or prepare a selected ISO level."""

    source = Path(source_path).expanduser()
    if source.suffix.casefold() == ".iso":
        return prepare_resistance_2_level(source, level_name, cache_directory)
    return source
