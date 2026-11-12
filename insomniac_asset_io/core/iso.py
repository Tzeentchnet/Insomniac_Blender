from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

from .errors import InsomniacError, MissingResourceError


DEFAULT_CACHE_DIRECTORY = Path.home() / "InsomniacAssetIOCache"
_PACKED_ROOT = PurePosixPath("PS3_GAME/USRDIR/packed")
_ISO_SECTOR_SIZE = 2048
_MAX_ISO_ENTRIES = 200_000
_MAX_DIRECTORY_DEPTH = 128


@dataclass(frozen=True, slots=True)
class _IsoEntry:
    path: str
    is_directory: bool
    extents: tuple[tuple[int, int], ...]

    @property
    def size(self) -> int:
        return sum(length for _sector, length in self.extents)


class _Iso9660Image:
    """Minimal read-only ISO9660 directory and extent reader."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None
        self._size = 0
        self._block_size = _ISO_SECTOR_SIZE
        self._entries: dict[str, _IsoEntry] = {}

    def __enter__(self) -> "_Iso9660Image":
        try:
            self._stream = self.path.open("rb")
            self._size = self.path.stat().st_size
            self._load()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self._entries.values())

    def extract(self, member: str, output: Path) -> None:
        normalized = _normalize_iso_path(member)
        try:
            entry = self._entries[normalized]
        except KeyError as exc:
            raise MissingResourceError(
                f"{self.path}: ISO member was not found: {member}"
            ) from exc
        if entry.is_directory:
            raise MissingResourceError(
                f"{self.path}: ISO member is a directory: {member}"
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for sector, length in entry.extents:
                    self._copy_extent(sector, length, temporary)
            temporary_path.replace(output)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def _load(self) -> None:
        primary = None
        for sector in range(16, 80):
            descriptor = self._read_at(
                sector * _ISO_SECTOR_SIZE,
                _ISO_SECTOR_SIZE,
            )
            if descriptor[1:6] != b"CD001":
                continue
            descriptor_type = descriptor[0]
            if descriptor_type == 1 and primary is None:
                primary = descriptor
            if descriptor_type == 255:
                break
        if primary is None:
            raise InsomniacError(
                f"{self.path}: no ISO9660 primary volume descriptor was found"
            )

        block_size = int.from_bytes(primary[128:130], "little")
        if (
            block_size < 512
            or block_size > 32 * 1024
            or block_size & (block_size - 1)
        ):
            raise InsomniacError(
                f"{self.path}: unsupported ISO logical block size {block_size}"
            )
        self._block_size = block_size
        root = self._directory_record(primary, 156)
        if root is None or not root.is_directory:
            raise InsomniacError(
                f"{self.path}: invalid ISO9660 root directory record"
            )
        self._walk_directory("", root, depth=0, visited=set())

    def _walk_directory(
        self,
        parent: str,
        directory: _IsoEntry,
        *,
        depth: int,
        visited: set[tuple[tuple[int, int], ...]],
    ) -> None:
        if depth > _MAX_DIRECTORY_DEPTH:
            raise InsomniacError(
                f"{self.path}: ISO directory nesting exceeds "
                f"{_MAX_DIRECTORY_DEPTH}"
            )
        if directory.extents in visited:
            return
        visited.add(directory.extents)
        data = self._read_extents(directory.extents)
        children: list[_IsoEntry] = []
        pending_path: str | None = None
        pending_is_directory = False
        pending_extents: list[tuple[int, int]] = []
        offset = 0
        while offset < len(data):
            record_length = data[offset]
            if record_length == 0:
                offset += self._block_size - (offset % self._block_size)
                continue
            record = self._directory_record(data, offset)
            if record is None:
                raise InsomniacError(
                    f"{self.path}: malformed ISO directory record at "
                    f"{offset:#x}"
                )
            offset += record_length
            raw_name = record.path
            if raw_name in ("\x00", "\x01"):
                continue
            component = _strip_iso_version(raw_name)
            child_path = f"{parent}/{component}" if parent else component
            is_multi_extent = bool(data[offset - record_length + 25] & 0x80)

            if pending_path is not None and child_path != pending_path:
                raise InsomniacError(
                    f"{self.path}: incomplete multi-extent ISO member "
                    f"{pending_path}"
                )
            if pending_path is None and is_multi_extent:
                pending_path = child_path
                pending_is_directory = record.is_directory
                pending_extents.extend(record.extents)
                continue
            if pending_path is not None:
                pending_extents.extend(record.extents)
                if is_multi_extent:
                    continue
                child = _IsoEntry(
                    path=pending_path,
                    is_directory=pending_is_directory,
                    extents=tuple(pending_extents),
                )
                pending_path = None
                pending_extents.clear()
            else:
                child = _IsoEntry(
                    path=child_path,
                    is_directory=record.is_directory,
                    extents=record.extents,
                )

            normalized = _normalize_iso_path(child.path)
            if normalized in self._entries:
                raise InsomniacError(
                    f"{self.path}: duplicate ISO member {child.path}"
                )
            self._entries[normalized] = child
            if len(self._entries) > _MAX_ISO_ENTRIES:
                raise InsomniacError(
                    f"{self.path}: ISO contains more than "
                    f"{_MAX_ISO_ENTRIES} entries"
                )
            children.append(child)

        if pending_path is not None:
            raise InsomniacError(
                f"{self.path}: incomplete multi-extent ISO member "
                f"{pending_path}"
            )
        for child in children:
            if child.is_directory:
                self._walk_directory(
                    child.path,
                    child,
                    depth=depth + 1,
                    visited=visited,
                )

    def _directory_record(
        self,
        data: bytes,
        offset: int,
    ) -> _IsoEntry | None:
        if offset < 0 or offset >= len(data):
            return None
        record_length = data[offset]
        if record_length < 34 or offset + record_length > len(data):
            return None
        name_length = data[offset + 32]
        name_start = offset + 33
        name_end = name_start + name_length
        if name_end > offset + record_length:
            return None
        try:
            name = data[name_start:name_end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise InsomniacError(
                f"{self.path}: non-ASCII ISO9660 file identifier"
            ) from exc
        sector = int.from_bytes(data[offset + 2 : offset + 6], "little")
        length = int.from_bytes(data[offset + 10 : offset + 14], "little")
        self._require_extent(sector, length)
        return _IsoEntry(
            path=name,
            is_directory=bool(data[offset + 25] & 0x02),
            extents=((sector, length),),
        )

    def _read_extents(
        self,
        extents: tuple[tuple[int, int], ...],
    ) -> bytes:
        chunks = []
        for sector, length in extents:
            chunks.append(self._read_at(sector * self._block_size, length))
        return b"".join(chunks)

    def _copy_extent(self, sector: int, length: int, output) -> None:
        self._require_extent(sector, length)
        if self._stream is None:
            raise InsomniacError(f"{self.path}: ISO image is closed")
        self._stream.seek(sector * self._block_size)
        remaining = length
        while remaining:
            chunk = self._stream.read(min(remaining, 8 * 1024 * 1024))
            if not chunk:
                raise InsomniacError(
                    f"{self.path}: short read while extracting ISO member"
                )
            output.write(chunk)
            remaining -= len(chunk)

    def _read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self._size:
            raise InsomniacError(
                f"{self.path}: ISO read [{offset:#x}, "
                f"{offset + length:#x}) is outside the image"
            )
        if self._stream is None:
            raise InsomniacError(f"{self.path}: ISO image is closed")
        self._stream.seek(offset)
        data = self._stream.read(length)
        if len(data) != length:
            raise InsomniacError(
                f"{self.path}: short ISO read ({len(data)} of {length} bytes)"
            )
        return data

    def _require_extent(self, sector: int, length: int) -> None:
        offset = sector * self._block_size
        if sector < 0 or length < 0 or offset + length > self._size:
            raise InsomniacError(
                f"{self.path}: invalid ISO extent at sector {sector} "
                f"with length {length}"
            )


def _normalize_iso_path(path: str) -> str:
    return "/".join(
        _strip_iso_version(part).casefold()
        for part in path.replace("\\", "/").strip("/").split("/")
        if part
    )


def _strip_iso_version(component: str) -> str:
    stem, separator, version = component.rpartition(";")
    if separator and version.isdigit():
        return stem
    return component


def _iso_path(iso_path: str | Path, game_label: str) -> Path:
    iso = Path(iso_path).expanduser()
    if not iso.is_file():
        raise MissingResourceError(f"{game_label} ISO was not found: {iso}")
    return iso


def _tar_executable() -> str:
    tar = shutil.which("tar")
    if tar is None:
        raise InsomniacError(
            "No tar executable was found and the ISO9660 reader could not "
            "read this image. Extract the PSARC files manually or install a "
            "libarchive-compatible tar."
        )
    return tar


def _iso_paths_with_tar(iso: Path, game_label: str) -> tuple[str, ...]:
    result = subprocess.run(
        [_tar_executable(), "-tf", str(iso)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15 * 60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InsomniacError(
            f"Failed to list {game_label} ISO contents: {detail}"
        )
    return tuple(
        raw_path.strip().replace("\\", "/").lstrip("./")
        for raw_path in result.stdout.splitlines()
        if raw_path.strip()
    )


def _iso_paths(iso: Path, game_label: str) -> tuple[str, ...]:
    try:
        with _Iso9660Image(iso) as image:
            return image.paths
    except (InsomniacError, OSError) as iso_error:
        try:
            return _iso_paths_with_tar(iso, game_label)
        except InsomniacError as tar_error:
            raise InsomniacError(
                f"Could not read {game_label} ISO {iso}. "
                f"ISO9660 reader: {iso_error}. tar fallback: {tar_error}"
            ) from tar_error


def find_iso(source_path: str | Path) -> Path | None:
    """Resolve an ISO path or a source directory containing exactly one ISO."""

    source = Path(source_path).expanduser()
    if source.suffix.casefold() == ".iso":
        return source
    if not source.is_dir():
        return None

    candidates = sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.casefold() == ".iso"
        ),
        key=lambda path: path.name.casefold(),
    )
    if len(candidates) > 1:
        raise InsomniacError(
            f"Source folder contains multiple ISO files; select one ISO "
            f"file directly: {source}"
        )
    return candidates[0] if candidates else None


def _level_path(level_name: str, game_label: str) -> PurePosixPath:
    level = PurePosixPath(level_name.strip().replace("\\", "/"))
    if (
        not level.parts
        or level.is_absolute()
        or any(part in ("", ".", "..") for part in level.parts)
    ):
        raise InsomniacError(
            f"Invalid {game_label} level name: {level_name!r}"
        )
    return level


def list_game_levels(
    iso_path: str | Path,
    *,
    game_label: str,
) -> tuple[str, ...]:
    """Return level archive names present in a supported game ISO."""

    iso = _iso_path(iso_path, game_label)
    prefix = f"{_PACKED_ROOT.as_posix()}/levels/".casefold()
    suffix = "/level_cached.psarc"
    levels: set[str] = set()
    for raw_path in _iso_paths(iso, game_label):
        path = _normalize_iso_path(raw_path)
        if not path.startswith(prefix) or not path.endswith(suffix):
            continue
        level_name = path[len(prefix) : -len(suffix)]
        if level_name:
            levels.add(
                _level_path(level_name, game_label).as_posix()
            )
    if not levels:
        raise InsomniacError(
            f"The ISO contains no {game_label} level_cached.psarc archives"
        )
    return tuple(sorted(levels, key=str.casefold))


def _extract_members_with_tar(
    iso: Path,
    outputs: dict[str, Path],
    game_label: str,
) -> None:
    tar = _tar_executable()
    for member, output in outputs.items():
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                result = subprocess.run(
                    [tar, "-xOf", str(iso), member],
                    check=False,
                    stdout=temporary,
                    stderr=subprocess.PIPE,
                    timeout=60 * 60,
                )
            if result.returncode != 0:
                detail = result.stderr.decode(
                    "utf-8",
                    errors="replace",
                ).strip()
                raise InsomniacError(
                    f"Could not extract {member} from the {game_label} ISO: "
                    f"{detail}"
                )
            temporary_path.replace(output)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise


def extract_game_iso_members(
    iso_path: str | Path,
    outputs: dict[str, Path],
    *,
    game_label: str,
) -> None:
    """Extract selected ISO members to exact output paths."""

    iso = _iso_path(iso_path, game_label)
    try:
        with _Iso9660Image(iso) as image:
            for member, output in outputs.items():
                image.extract(member, output)
        return
    except (InsomniacError, OSError) as iso_error:
        try:
            _extract_members_with_tar(iso, outputs, game_label)
        except InsomniacError as tar_error:
            raise InsomniacError(
                f"Could not extract {game_label} archives from {iso}. "
                f"ISO9660 reader: {iso_error}. tar fallback: {tar_error}"
            ) from tar_error


def extract_game_iso_member(
    iso_path: str | Path,
    member: str,
    output_path: str | Path,
    *,
    game_label: str,
) -> Path:
    output = Path(output_path)
    extract_game_iso_members(
        iso_path,
        {member: output},
        game_label=game_label,
    )
    return output


def prepare_game_level(
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    *,
    cache_key: str,
    game_label: str,
) -> Path:
    """Extract the selected level and global PSARCs into the local cache."""

    iso = _iso_path(iso_path, game_label)
    level = _level_path(level_name, game_label)
    output = game_level_cache_root(
        cache_directory,
        level.as_posix(),
        cache_key=cache_key,
        game_label=game_label,
    )
    output.mkdir(parents=True, exist_ok=True)

    base = _PACKED_ROOT.as_posix()
    members = (
        f"{base}/game/global_cached.psarc",
        f"{base}/game/global_uncached.psarc",
        f"{base}/levels/{level.as_posix()}/level_cached.psarc",
        f"{base}/levels/{level.as_posix()}/level_uncached.psarc",
    )
    extracted_root = output / "PS3_GAME" / "USRDIR" / "packed"
    expected = tuple(
        extracted_root / Path(*PurePosixPath(member).parts[3:])
        for member in members
    )
    cached_level = expected[2]
    if all(path.is_file() for path in expected):
        return cached_level

    extract_game_iso_members(
        iso,
        dict(zip(members, expected)),
        game_label=game_label,
    )
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise InsomniacError(
            "ISO extraction completed but did not create the expected "
            "archives: " + ", ".join(missing)
        )
    return cached_level


def game_level_cache_root(
    cache_directory: str | Path,
    level_name: str,
    *,
    cache_key: str,
    game_label: str,
) -> Path:
    level = _level_path(level_name, game_label)
    return Path(cache_directory).expanduser() / cache_key / Path(*level.parts)


def game_cached_level_path(
    cache_directory: str | Path,
    level_name: str,
    *,
    cache_key: str,
    game_label: str,
) -> Path:
    level = _level_path(level_name, game_label)
    return (
        game_level_cache_root(
            cache_directory,
            level.as_posix(),
            cache_key=cache_key,
            game_label=game_label,
        )
        / "PS3_GAME"
        / "USRDIR"
        / "packed"
        / "levels"
        / Path(*level.parts)
        / "level_cached.psarc"
    )


def resolve_game_source(
    source_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    *,
    cache_key: str,
    game_label: str,
) -> Path:
    """Resolve a PSARC/directory directly or prepare a selected ISO level."""

    source = Path(source_path).expanduser()
    iso = find_iso(source)
    if iso is not None:
        return prepare_game_level(
            iso,
            level_name,
            cache_directory,
            cache_key=cache_key,
            game_label=game_label,
        )
    return source


def find_resistance_2_iso(source_path: str | Path) -> Path | None:
    return find_iso(source_path)


def list_resistance_2_levels(iso_path: str | Path) -> tuple[str, ...]:
    return list_game_levels(iso_path, game_label="Resistance 2")


def prepare_resistance_2_level(
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    return prepare_game_level(
        iso_path,
        level_name,
        cache_directory,
        cache_key="resistance_2",
        game_label="Resistance 2",
    )


def resistance_2_level_cache_root(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    return game_level_cache_root(
        cache_directory,
        level_name,
        cache_key="resistance_2",
        game_label="Resistance 2",
    )


def resistance_2_cached_level_path(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    return game_cached_level_path(
        cache_directory,
        level_name,
        cache_key="resistance_2",
        game_label="Resistance 2",
    )


def resolve_resistance_2_source(
    source_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    source = Path(source_path).expanduser()
    iso = find_resistance_2_iso(source)
    if iso is not None:
        return prepare_resistance_2_level(
            iso,
            level_name,
            cache_directory,
        )
    return source


def find_resistance_3_iso(source_path: str | Path) -> Path | None:
    return find_iso(source_path)


def list_resistance_3_levels(iso_path: str | Path) -> tuple[str, ...]:
    return list_game_levels(iso_path, game_label="Resistance 3")


def prepare_resistance_3_level(
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    return prepare_game_level(
        iso_path,
        level_name,
        cache_directory,
        cache_key="resistance_3",
        game_label="Resistance 3",
    )


def resistance_3_level_cache_root(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    return game_level_cache_root(
        cache_directory,
        level_name,
        cache_key="resistance_3",
        game_label="Resistance 3",
    )


def resistance_3_cached_level_path(
    cache_directory: str | Path,
    level_name: str,
) -> Path:
    return game_cached_level_path(
        cache_directory,
        level_name,
        cache_key="resistance_3",
        game_label="Resistance 3",
    )


def resolve_resistance_3_source(
    source_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
) -> Path:
    source = Path(source_path).expanduser()
    iso = find_resistance_3_iso(source)
    if iso is not None:
        return prepare_resistance_3_level(
            iso,
            level_name,
            cache_directory,
        )
    return source
