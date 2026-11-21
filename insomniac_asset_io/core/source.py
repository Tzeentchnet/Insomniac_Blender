from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import threading
from typing import Protocol

from .errors import (
    AmbiguousResourceError,
    MissingResourceError,
)
from .psarc import PsarcArchive, PsarcEntry


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("/").casefold()


class _Resource(Protocol):
    display_path: str
    size: int

    def read(self) -> bytes: ...

    def read_slice(self, offset: int, length: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _DiskResource:
    path: Path
    display_path: str
    size: int

    def read(self) -> bytes:
        return self.path.read_bytes()

    def read_slice(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise MissingResourceError(
                f"{self.path}: invalid slice [{offset}, {offset + length})"
            )
        with self.path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        if len(data) != length:
            raise MissingResourceError(
                f"{self.path}: short read ({len(data)} of {length} bytes)"
            )
        return data


@dataclass(frozen=True, slots=True)
class _PsarcResource:
    archive: PsarcArchive
    entry: PsarcEntry
    display_path: str
    size: int

    def read(self) -> bytes:
        return self.archive.read(self.entry)

    def read_slice(self, offset: int, length: int) -> bytes:
        return self.archive.read_slice(self.entry, offset, length)


class GameSource:
    """Case-insensitive overlay over extracted files and PSARC entries."""

    MAX_DISCOVERED_FILES = 200_000

    def __init__(
        self,
        root: str | Path,
        resources: list[_Resource],
        archives: list[PsarcArchive] | None = None,
        *,
        selected_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.selected_path = (
            Path(selected_path).resolve()
            if selected_path is not None
            else None
        )
        self._archives = archives or []
        self._lock = threading.RLock()
        by_path: dict[str, list[_Resource]] = {}
        for resource in resources:
            by_path.setdefault(_normalize(resource.display_path), []).append(resource)
        self._by_path = by_path
        self.paths = tuple(
            sorted(resource.display_path for resource in resources)
        )

    @classmethod
    def open(cls, path: str | Path) -> "GameSource":
        source_path = Path(path).expanduser()
        if not source_path.exists():
            raise MissingResourceError(f"Source path does not exist: {source_path}")

        if source_path.is_file() and cls._is_psarc_file(source_path):
            archive_paths = cls._paired_archives(source_path)
            return cls._from_archives(
                source_path.parent,
                archive_paths,
                selected_path=source_path,
            )

        if source_path.is_file():
            return cls._from_directory(
                source_path.parent,
                selected_path=source_path,
            )

        archive_paths = sorted(
            item
            for item in source_path.rglob("*")
            if item.is_file()
            and item.suffix.casefold() in {".psarc", ".psa"}
            and cls._is_psarc_file(item)
        )
        if archive_paths:
            return cls._from_archives(
                source_path,
                archive_paths,
                selected_path=source_path,
            )
        return cls._from_directory(
            source_path,
            selected_path=source_path,
        )

    @staticmethod
    def _is_psarc_file(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                return stream.read(4) == b"PSAR"
        except OSError as exc:
            raise MissingResourceError(
                f"Could not inspect source file {path}: {exc}"
            ) from exc

    @staticmethod
    def _paired_archives(path: Path) -> list[Path]:
        paths = [path]
        lower = path.name.casefold()
        level_names = (
            "level_cached.psarc",
            "level_uncached.psarc",
            "level_textures.psarc",
        )
        if lower in level_names:
            siblings = tuple(
                path.with_name(name)
                for name in level_names
                if name != lower
            )
        elif lower == "global_cached.psarc":
            siblings = (path.with_name("global_uncached.psarc"),)
        elif lower == "global_uncached.psarc":
            siblings = (path.with_name("global_cached.psarc"),)
        else:
            siblings = ()
        paths.extend(sibling for sibling in siblings if sibling.is_file())
        if lower in level_names:
            packed = next(
                (
                    parent
                    for parent in path.parents
                    if parent.name.casefold() == "packed"
                ),
                None,
            )
            if packed is not None:
                for name in ("global_cached.psarc", "global_uncached.psarc"):
                    global_archive = packed / "game" / name
                    if global_archive.is_file():
                        paths.append(global_archive)
        return paths

    @classmethod
    def _from_directory(
        cls,
        root: Path,
        *,
        selected_path: Path | None = None,
    ) -> "GameSource":
        resources: list[_Resource] = []
        for index, file_path in enumerate(
            item for item in root.rglob("*") if item.is_file()
        ):
            if index >= cls.MAX_DISCOVERED_FILES:
                raise MissingResourceError(
                    f"{root}: source contains more than "
                    f"{cls.MAX_DISCOVERED_FILES} files"
                )
            relative = file_path.relative_to(root).as_posix()
            resources.append(
                _DiskResource(
                    path=file_path,
                    display_path=relative,
                    size=file_path.stat().st_size,
                )
            )
        return cls(root, resources, selected_path=selected_path)

    @classmethod
    def _from_archives(
        cls,
        root: Path,
        paths: list[Path],
        *,
        selected_path: Path | None = None,
    ) -> "GameSource":
        archives: list[PsarcArchive] = []
        resources: list[_Resource] = []
        try:
            for archive_path in paths:
                archive = PsarcArchive(archive_path)
                archives.append(archive)
                for entry in archive.entries:
                    resources.append(
                        _PsarcResource(
                            archive=archive,
                            entry=entry,
                            display_path=entry.name,
                            size=entry.uncompressed_size,
                        )
                    )
        except Exception:
            for archive in archives:
                archive.close()
            raise
        return cls(
            root,
            resources,
            archives,
            selected_path=selected_path,
        )

    def sibling_level_archives(self) -> tuple[Path, ...]:
        """Return extracted sibling level cached archives, excluding this source."""

        anchors = tuple(
            path
            for path in (self.selected_path, self.root.resolve())
            if path is not None
        )
        packed: Path | None = None
        for anchor in anchors:
            start = anchor if anchor.is_dir() else anchor.parent
            packed = next(
                (
                    candidate
                    for candidate in (start, *start.parents)
                    if candidate.name.casefold() == "packed"
                ),
                None,
            )
            if packed is not None:
                break
        if packed is None:
            return ()

        levels = packed / "levels"
        if not levels.is_dir():
            return ()
        allowed: set[Path] | None = None
        provenance_path = next(
            (
                parent / ".insomniac-cache.json"
                for parent in packed.parents
                if (parent / ".insomniac-cache.json").is_file()
            ),
            None,
        )
        if provenance_path is not None:
            try:
                provenance = json.loads(
                    provenance_path.read_text(encoding="utf-8")
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                provenance = None
            resources = (
                provenance.get("resources")
                if isinstance(provenance, dict)
                else None
            )
            if isinstance(resources, list) and all(
                isinstance(resource, str) for resource in resources
            ):
                allowed = set()
                for resource in resources:
                    normalized = resource.replace("\\", "/")
                    marker = "/packed/"
                    lower = "/" + normalized.casefold().lstrip("/")
                    marker_index = lower.find(marker)
                    if marker_index < 0:
                        continue
                    relative = normalized.lstrip("/")[
                        marker_index + len(marker) - 1 :
                    ]
                    if not relative.casefold().startswith("levels/"):
                        continue
                    if not relative.casefold().endswith(
                        "/level_cached.psarc"
                    ):
                        continue
                    allowed.add(
                        (
                            packed / Path(*PurePosixPath(relative).parts)
                        ).resolve()
                    )
        opened = {archive.path.resolve() for archive in self._archives}
        if (
            self.selected_path is not None
            and self.selected_path.name.casefold() == "level_cached.psarc"
        ):
            opened.add(self.selected_path.resolve())
        return tuple(
            path
            for path in sorted(levels.rglob("level_cached.psarc"))
            if path.resolve() not in opened
            and (allowed is None or path.resolve() in allowed)
        )

    def close(self) -> None:
        with self._lock:
            for archive in self._archives:
                archive.close()
            self._archives.clear()

    def __enter__(self) -> "GameSource":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _resolve(self, path: str) -> _Resource:
        normalized = _normalize(path)
        exact = self._by_path.get(normalized)
        if exact:
            if len(exact) == 1:
                return exact[0]
            unique_sizes = {item.size for item in exact}
            if len(unique_sizes) == 1:
                return exact[0]
            raise AmbiguousResourceError(
                f"Resource {path!r} occurs multiple times with different data"
            )

        suffix = "/" + normalized
        matches = [
            resource
            for key, values in self._by_path.items()
            if key.endswith(suffix)
            for resource in values
        ]
        if not matches:
            raise MissingResourceError(
                f"Resource {path!r} was not found under {self.root}"
            )
        if len(matches) > 1:
            choices = ", ".join(item.display_path for item in matches[:8])
            raise AmbiguousResourceError(
                f"Resource {path!r} is ambiguous: {choices}"
            )
        return matches[0]

    def exists(self, path: str) -> bool:
        try:
            self._resolve(path)
        except MissingResourceError:
            return False
        return True

    def size(self, path: str) -> int:
        return self._resolve(path).size

    def read(self, path: str) -> bytes:
        return self._resolve(path).read()

    def read_slice(self, path: str, offset: int, length: int) -> bytes:
        return self._resolve(path).read_slice(offset, length)
