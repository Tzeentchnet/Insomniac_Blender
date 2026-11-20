from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import InsomniacError, MissingResourceError
from .psarc import PsarcArchive

DEFAULT_CACHE_DIRECTORY = Path.home() / "InsomniacAssetIOCache"
_PACKED_ROOT = PurePosixPath("PS3_GAME/USRDIR/packed")
_ISO_SECTOR_SIZE = 2048
_MAX_ISO_ENTRIES = 200_000
_MAX_DIRECTORY_DEPTH = 128
_MAX_PARAM_SFO_SIZE = 1024 * 1024
_MAX_PARAM_SFO_ENTRIES = 1024
_CACHE_PROVENANCE_FILENAME = ".insomniac-cache.json"
_CACHE_PROVENANCE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class _IsoEntry:
    path: str
    is_directory: bool
    extents: tuple[tuple[int, int], ...]

    @property
    def size(self) -> int:
        return sum(length for _sector, length in self.extents)


class _IsoMemberStream(io.RawIOBase):
    def __init__(self, image: "_Iso9660Image", entry: _IsoEntry) -> None:
        super().__init__()
        self._image = image
        self._entry = entry
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._checkClosed()
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._checkClosed()
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._entry.size + offset
        else:
            raise ValueError(f"Unsupported seek mode {whence}")
        if position < 0:
            raise ValueError("Negative seek position")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        self._checkClosed()
        remaining = max(0, self._entry.size - self._position)
        length = remaining if size is None or size < 0 else min(size, remaining)
        if not length:
            return b""
        data = self._image._read_entry_slice(
            self._entry,
            self._position,
            length,
        )
        self._position += len(data)
        return data


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

    def _entry(self, member: str) -> _IsoEntry:
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
        return entry

    def open_member(self, member: str) -> _IsoMemberStream:
        return _IsoMemberStream(self, self._entry(member))

    def extract(self, member: str, output: Path) -> None:
        entry = self._entry(member)

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

    def read(self, member: str, *, max_size: int) -> bytes:
        entry = self._entry(member)
        if entry.size > max_size:
            raise InsomniacError(
                f"{self.path}: ISO member {member} is {entry.size} bytes; "
                f"limit is {max_size}"
            )
        return self._read_extents(entry.extents)

    def _read_entry_slice(
        self,
        entry: _IsoEntry,
        offset: int,
        length: int,
    ) -> bytes:
        if (
            offset < 0
            or length < 0
            or offset + length > entry.size
        ):
            raise InsomniacError(
                f"{self.path}: member read [{offset:#x}, "
                f"{offset + length:#x}) exceeds {entry.path} "
                f"({entry.size:#x} bytes)"
            )
        chunks: list[bytes] = []
        logical_start = 0
        remaining = length
        cursor = offset
        for sector, extent_length in entry.extents:
            logical_end = logical_start + extent_length
            if cursor >= logical_end:
                logical_start = logical_end
                continue
            relative = max(0, cursor - logical_start)
            available = extent_length - relative
            take = min(remaining, available)
            chunks.append(
                self._read_at(
                    sector * self._block_size + relative,
                    take,
                )
            )
            cursor += take
            remaining -= take
            logical_start = logical_end
            if not remaining:
                break
        if remaining:
            raise InsomniacError(
                f"{self.path}: short read in ISO member {entry.path}"
            )
        return b"".join(chunks)

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
    level_marker: str = "level_cached.psarc",
    container_member: str | None = None,
) -> tuple[str, ...]:
    """Return level archive names present in a supported game ISO."""

    iso = _iso_path(iso_path, game_label)
    if container_member is None:
        raw_paths = _iso_paths(iso, game_label)
        normalize = _normalize_iso_path
        prefix = f"{_PACKED_ROOT.as_posix()}/levels/".casefold()
    else:
        try:
            with _Iso9660Image(iso) as image:
                with PsarcArchive.from_stream(
                    image.open_member(container_member),
                    label=f"{iso}!/{container_member}",
                ) as archive:
                    raw_paths = tuple(
                        entry.name for entry in archive.entries
                    )
        except (InsomniacError, OSError) as exc:
            raise InsomniacError(
                f"Could not read {game_label} archive "
                f"{container_member} from {iso}: {exc}"
            ) from exc
        normalize = PsarcArchive.normalize
        prefix = "packed/levels/"
    suffix = f"/{level_marker.casefold()}"
    levels: set[str] = set()
    for raw_path in raw_paths:
        path = normalize(raw_path)
        if not path.startswith(prefix) or not path.endswith(suffix):
            continue
        level_name = path[len(prefix) : -len(suffix)]
        if level_name:
            levels.add(
                _level_path(level_name, game_label).as_posix()
            )
    if not levels:
        raise InsomniacError(
            f"The ISO contains no {game_label} {level_marker} resources"
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


def _extract_psarc_members_from_iso(
    iso: Path,
    container_member: str,
    outputs: dict[str, Path],
    *,
    game_label: str,
) -> None:
    try:
        with _Iso9660Image(iso) as image:
            with PsarcArchive.from_stream(
                image.open_member(container_member),
                label=f"{iso}!/{container_member}",
            ) as archive:
                chunk_size = max(archive.block_size, 8 * 1024 * 1024)
                chunk_size -= chunk_size % archive.block_size
                for member, output in outputs.items():
                    entry = archive.get(member)
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
                            for offset in range(
                                0,
                                entry.uncompressed_size,
                                chunk_size,
                            ):
                                temporary.write(
                                    archive.read_slice(
                                        entry,
                                        offset,
                                        min(
                                            chunk_size,
                                            entry.uncompressed_size - offset,
                                        ),
                                    )
                                )
                        temporary_path.replace(output)
                    except Exception:
                        if temporary_path is not None:
                            temporary_path.unlink(missing_ok=True)
                        raise
    except (InsomniacError, OSError) as exc:
        raise InsomniacError(
            f"Could not extract {game_label} resources from "
            f"{container_member} in {iso}: {exc}"
        ) from exc


def read_game_iso_member(
    iso_path: str | Path,
    member: str,
    *,
    game_label: str,
    max_size: int,
) -> bytes:
    """Read one bounded ISO member without retaining an extracted copy."""

    iso = _iso_path(iso_path, game_label)
    try:
        with _Iso9660Image(iso) as image:
            return image.read(member, max_size=max_size)
    except (InsomniacError, OSError) as iso_error:
        try:
            with tempfile.TemporaryDirectory(
                prefix="insomniac_iso_member_"
            ) as temporary:
                output = Path(temporary) / PurePosixPath(member).name
                _extract_members_with_tar(
                    iso,
                    {member: output},
                    game_label,
                )
                size = output.stat().st_size
                if size > max_size:
                    raise InsomniacError(
                        f"{iso}: ISO member {member} is {size} bytes; "
                        f"limit is {max_size}"
                    )
                return output.read_bytes()
        except (InsomniacError, OSError) as tar_error:
            raise InsomniacError(
                f"Could not read {member} from the {game_label} ISO {iso}. "
                f"ISO9660 reader: {iso_error}. tar fallback: {tar_error}"
            ) from tar_error


def parse_param_sfo(
    data: bytes,
    *,
    label: str = "PARAM.SFO",
) -> dict[str, str]:
    """Parse bounded string fields from a PlayStation PARAM.SFO."""

    if len(data) > _MAX_PARAM_SFO_SIZE:
        raise InsomniacError(
            f"{label}: file is {len(data)} bytes; "
            f"limit is {_MAX_PARAM_SFO_SIZE}"
        )
    if len(data) < 20 or data[:4] != b"\0PSF":
        raise InsomniacError(f"{label}: invalid PARAM.SFO header")
    key_table = int.from_bytes(data[8:12], "little")
    value_table = int.from_bytes(data[12:16], "little")
    entry_count = int.from_bytes(data[16:20], "little")
    if entry_count > _MAX_PARAM_SFO_ENTRIES:
        raise InsomniacError(
            f"{label}: unreasonable entry count {entry_count}"
        )
    table_end = 20 + entry_count * 16
    if (
        table_end > len(data)
        or key_table < table_end
        or key_table > len(data)
        or value_table < key_table
        or value_table > len(data)
    ):
        raise InsomniacError(f"{label}: invalid PARAM.SFO table offsets")

    values: dict[str, str] = {}
    for index in range(entry_count):
        entry = 20 + index * 16
        key_offset = int.from_bytes(data[entry : entry + 2], "little")
        value_format = int.from_bytes(
            data[entry + 2 : entry + 4],
            "little",
        )
        value_length = int.from_bytes(
            data[entry + 4 : entry + 8],
            "little",
        )
        value_capacity = int.from_bytes(
            data[entry + 8 : entry + 12],
            "little",
        )
        value_offset = int.from_bytes(
            data[entry + 12 : entry + 16],
            "little",
        )
        key_start = key_table + key_offset
        if key_start >= value_table:
            raise InsomniacError(
                f"{label}: entry {index} has an invalid key offset"
            )
        key_end = data.find(b"\0", key_start, value_table)
        if key_end < 0:
            raise InsomniacError(
                f"{label}: entry {index} has an unterminated key"
            )
        value_start = value_table + value_offset
        if (
            value_length > value_capacity
            or value_start > len(data)
            or value_length > len(data) - value_start
            or value_capacity > len(data) - value_start
        ):
            raise InsomniacError(
                f"{label}: entry {index} has an invalid value range"
            )
        if value_format not in {0x0004, 0x0204}:
            continue
        try:
            key = data[key_start:key_end].decode("utf-8")
            value = data[
                value_start : value_start + value_length
            ].rstrip(b"\0").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InsomniacError(
                f"{label}: entry {index} is not valid UTF-8"
            ) from exc
        values[key] = value
    return values


def validate_game_iso(
    iso_path: str | Path,
    *,
    game_label: str,
    expected_title_id: str | None = None,
    expected_version: str | None = None,
) -> dict[str, str]:
    """Validate a disc build against adapter-declared SFO identity."""

    values = parse_param_sfo(
        read_game_iso_member(
            iso_path,
            "PS3_GAME/PARAM.SFO",
            game_label=game_label,
            max_size=_MAX_PARAM_SFO_SIZE,
        ),
        label=f"{iso_path}: PS3_GAME/PARAM.SFO",
    )
    actual_title_id = values.get("TITLE_ID")
    if expected_title_id is not None and actual_title_id != expected_title_id:
        raise InsomniacError(
            f"{game_label} requires TITLE_ID {expected_title_id}, "
            f"but the selected ISO reports {actual_title_id or 'no TITLE_ID'}"
        )
    actual_version = values.get("VERSION") or values.get("APP_VER")
    if expected_version is not None and actual_version != expected_version:
        raise InsomniacError(
            f"{game_label} requires version {expected_version}, "
            f"but the selected ISO reports {actual_version or 'no version'}"
        )
    return values


def _cache_source_identity(
    iso: Path,
    *,
    iso_values: dict[str, str],
    expected_title_id: str | None,
    expected_version: str | None,
) -> dict[str, int | str | None]:
    stat = iso.stat()
    return {
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "title_id": iso_values.get("TITLE_ID") or expected_title_id,
        "version": (
            iso_values.get("VERSION")
            or iso_values.get("APP_VER")
            or expected_version
        ),
    }


def _read_cache_provenance(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _cache_provenance_matches(
    path: Path,
    *,
    cache_key: str,
    level_name: str,
    source_identity: dict[str, int | str | None],
    members: tuple[str, ...],
) -> bool:
    provenance = _read_cache_provenance(path)
    if provenance is None:
        return False
    if (
        provenance.get("schema_version") != _CACHE_PROVENANCE_SCHEMA_VERSION
        or provenance.get("cache_key") != cache_key
        or provenance.get("level") != level_name
        or provenance.get("source") != source_identity
    ):
        return False
    resources = provenance.get("resources")
    if not isinstance(resources, list) or not all(
        isinstance(resource, str) for resource in resources
    ):
        return False
    return set(members).issubset(resources)


def _write_cache_provenance(
    path: Path,
    *,
    cache_key: str,
    game_label: str,
    level_name: str,
    source_identity: dict[str, int | str | None],
    members: tuple[str, ...],
) -> None:
    payload = {
        "schema_version": _CACHE_PROVENANCE_SCHEMA_VERSION,
        "cache_key": cache_key,
        "game": game_label,
        "level": level_name,
        "source": source_identity,
        "resources": list(members),
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
        temporary_path.replace(path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise InsomniacError(
            f"Could not write extraction cache provenance {path}: {exc}"
        ) from exc


def prepare_game_level(
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    *,
    cache_key: str,
    game_label: str,
    level_archives: tuple[str, ...] = (
        "level_cached.psarc",
        "level_uncached.psarc",
    ),
    global_archives: tuple[str, ...] = (
        "global_cached.psarc",
        "global_uncached.psarc",
    ),
    primary_resource: str | None = "level_cached.psarc",
    container_member: str | None = None,
    expected_title_id: str | None = None,
    expected_version: str | None = None,
    validate_iso_identity: bool = True,
) -> Path:
    """Extract selected level resources into the local cache."""

    iso = _iso_path(iso_path, game_label)
    iso_values: dict[str, str] = {}
    if expected_title_id is not None or expected_version is not None:
        if validate_iso_identity:
            iso_values = validate_game_iso(
                iso,
                game_label=game_label,
                expected_title_id=expected_title_id,
                expected_version=expected_version,
            )
    level = _level_path(level_name, game_label)
    output = game_level_cache_root(
        cache_directory,
        level.as_posix(),
        cache_key=cache_key,
        game_label=game_label,
    )
    output.mkdir(parents=True, exist_ok=True)

    base = (
        "packed"
        if container_member is not None
        else _PACKED_ROOT.as_posix()
    )
    members = tuple(
        f"{base}/game/{name}" for name in global_archives
    ) + tuple(
        f"{base}/levels/{level.as_posix()}/{name}"
        for name in level_archives
    )
    source_identity = _cache_source_identity(
        iso,
        iso_values=iso_values,
        expected_title_id=expected_title_id,
        expected_version=expected_version,
    )
    provenance_path = output / _CACHE_PROVENANCE_FILENAME
    provenance_matches = _cache_provenance_matches(
        provenance_path,
        cache_key=cache_key,
        level_name=level.as_posix(),
        source_identity=source_identity,
        members=members,
    )
    if not level_archives:
        raise InsomniacError(
            f"{game_label} adapter declares no level archives"
        )
    extracted_root = output / "PS3_GAME" / "USRDIR" / "packed"
    expected = tuple(
        extracted_root / "game" / name
        for name in global_archives
    ) + tuple(
        extracted_root / "levels" / Path(*level.parts) / name
        for name in level_archives
    )
    level_start = len(global_archives)
    level_directory = (
        extracted_root / "levels" / Path(*level.parts)
    )
    if primary_resource is None:
        cached_level = level_directory
    else:
        try:
            primary_index = level_archives.index(primary_resource)
        except ValueError as exc:
            raise InsomniacError(
                f"{game_label} primary resource {primary_resource!r} "
                "is not declared as a level archive"
            ) from exc
        cached_level = expected[level_start + primary_index]
    if provenance_matches and all(path.is_file() for path in expected):
        return cached_level

    if provenance_matches:
        pending = {
            member: path
            for member, path in zip(members, expected)
            if not path.is_file()
        }
    else:
        pending = dict(zip(members, expected))
    if container_member is None:
        extract_game_iso_members(
            iso,
            pending,
            game_label=game_label,
        )
    else:
        _extract_psarc_members_from_iso(
            iso,
            container_member,
            pending,
            game_label=game_label,
        )
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise InsomniacError(
            "ISO extraction completed but did not create the expected "
            "archives: " + ", ".join(missing)
        )
    _write_cache_provenance(
        provenance_path,
        cache_key=cache_key,
        game_label=game_label,
        level_name=level.as_posix(),
        source_identity=source_identity,
        members=members,
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


def game_level_cache_is_verified(
    cache_directory: str | Path,
    level_name: str,
    *,
    cache_key: str,
    game_label: str,
    level_archives: tuple[str, ...],
    global_archives: tuple[str, ...],
    container_member: str | None = None,
    expected_title_id: str | None = None,
    expected_version: str | None = None,
) -> bool:
    level = _level_path(level_name, game_label)
    output = game_level_cache_root(
        cache_directory,
        level.as_posix(),
        cache_key=cache_key,
        game_label=game_label,
    )
    extracted_root = output / "PS3_GAME" / "USRDIR" / "packed"
    expected = tuple(
        extracted_root / "game" / name
        for name in global_archives
    ) + tuple(
        extracted_root / "levels" / Path(*level.parts) / name
        for name in level_archives
    )
    if not expected or not all(path.is_file() for path in expected):
        return False

    provenance = _read_cache_provenance(
        output / _CACHE_PROVENANCE_FILENAME
    )
    if (
        provenance is None
        or provenance.get("schema_version")
        != _CACHE_PROVENANCE_SCHEMA_VERSION
        or provenance.get("cache_key") != cache_key
        or provenance.get("level") != level.as_posix()
    ):
        return False

    source = provenance.get("source")
    if not isinstance(source, dict):
        return False
    if (
        expected_title_id is not None
        and source.get("title_id") != expected_title_id
    ):
        return False
    if (
        expected_version is not None
        and source.get("version") != expected_version
    ):
        return False

    base = (
        "packed"
        if container_member is not None
        else _PACKED_ROOT.as_posix()
    )
    members = tuple(
        f"{base}/game/{name}" for name in global_archives
    ) + tuple(
        f"{base}/levels/{level.as_posix()}/{name}"
        for name in level_archives
    )
    resources = provenance.get("resources")
    if not isinstance(resources, list) or not all(
        isinstance(resource, str) for resource in resources
    ):
        return False
    return set(members).issubset(resources)


def game_cached_level_path(
    cache_directory: str | Path,
    level_name: str,
    *,
    cache_key: str,
    game_label: str,
    primary_resource: str | None = "level_cached.psarc",
) -> Path:
    level = _level_path(level_name, game_label)
    level_directory = (
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
    )
    if primary_resource is None:
        return level_directory
    return level_directory / primary_resource


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
