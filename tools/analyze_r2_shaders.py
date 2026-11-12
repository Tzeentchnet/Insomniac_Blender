from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind, TextureInfo
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


SLOT_NAMES = ("diffuse", "normal", "packed", "detail")
FLAG_SLOTS = {
    "specular": (0x02, 2),
    "glossiness": (0x04, 2),
    "normal_map": (0x08, 1),
    "detail_map": (0x10, 3),
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze Resistance 2 shader records in a level archive or "
            "every level_cached.psarc in an ISO"
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument(
        "--include-shaders",
        action="store_true",
        help="Include one detailed record per unique shader TUID",
    )
    return parser.parse_args()


def _counter_rows(
    values: Counter,
    formatter: Callable[[object], object] = str,
) -> list[dict[str, object]]:
    return [
        {"value": formatter(value), "count": count}
        for value, count in values.most_common()
    ]


def _hex(width: int) -> Callable[[object], str]:
    return lambda value: f"0x{int(value):0{width}X}"


def _tuple_text(value: object) -> str:
    return ",".join(str(item) for item in value)


def _json_float(value: float) -> float | str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _string(ighw: IghwFile, pointer: int) -> str | None:
    return ighw.cstring(pointer) if pointer else None


class ShaderAnalysis:
    def __init__(self, *, include_shaders: bool) -> None:
        self.include_shaders = include_shaders
        self.archive_count = 0
        self.shader_references = 0
        self.digests: dict[int, str] = {}
        self.duplicate_mismatches: list[dict[str, str]] = []
        self.section_counts: dict[int, Counter] = defaultdict(Counter)
        self.section_lengths: dict[int, Counter] = defaultdict(Counter)
        self.flags = Counter()
        self.blend_modes = Counter()
        self.unknown_0 = Counter()
        self.unknown_1 = Counter()
        self.map_patterns = Counter()
        self.slot_presence = {name: Counter() for name in SLOT_NAMES}
        self.slot_paths = {name: Counter() for name in SLOT_NAMES}
        self.slot_formats = {name: Counter() for name in SLOT_NAMES}
        self.slot_format_pairs = {name: Counter() for name in SLOT_NAMES}
        self.slot_addresses = {name: Counter() for name in SLOT_NAMES}
        self.slot_hash_matches = {name: Counter() for name in SLOT_NAMES}
        self.texture_metadata_formats = Counter()
        self.flag_correlations = {
            name: Counter() for name in FLAG_SLOTS
        }
        self.blend_examples: dict[int, list[str]] = defaultdict(list)
        self.value_components = [
            [Counter() for _ in range(4)] for _ in range(6)
        ]
        self.shader_records: list[dict[str, object]] = []

    def add_archive(self, archive: Path, label: str) -> None:
        with GameSource.open(archive) as source:
            reader = Resistance2Reader(source)
            pointers = reader.asset_pointers(AssetKind.SHADER)
            self.texture_metadata_formats.update(
                info.format_code for info in reader.texture_infos.values()
            )
            self.archive_count += 1
            self.shader_references += len(pointers)
            for pointer in pointers:
                data = reader._asset_bytes(AssetKind.SHADER, pointer.tuid)
                digest = hashlib.sha256(data).hexdigest()
                previous = self.digests.get(pointer.tuid)
                if previous is not None:
                    if previous != digest:
                        self.duplicate_mismatches.append(
                            {
                                "tuid": f"0x{pointer.tuid:016X}",
                                "archive": label,
                                "previous_sha256": previous,
                                "sha256": digest,
                            }
                        )
                    continue
                self.digests[pointer.tuid] = digest
                self._add_shader(
                    pointer.tuid,
                    data,
                    label,
                    reader.texture_infos,
                )

    def _add_shader(
        self,
        tuid: int,
        data: bytes,
        archive: str,
        texture_infos: dict[int, TextureInfo],
    ) -> None:
        ighw = IghwFile(data, label=f"shader 0x{tuid:016X}")
        for section in ighw.sections:
            self.section_counts[section.id][section.count] += 1
            self.section_lengths[section.id][section.length] += 1

        material = ighw.section(0x5000)
        references = ighw.section(0x5D00)
        if material is None or references is None:
            return

        texture_ids = tuple(
            ighw.u32(references.offset + 0x10 + index * 4)
            for index in range(4)
        )
        texture_paths = (
            tuple(
                _string(
                    ighw,
                    ighw.u32(references.offset + 0x20 + index * 4),
                )
                for index in range(4)
            )
            if references.length >= 0x30
            else (None, None, None, None)
        )
        lookup_path = _string(ighw, ighw.u32(references.offset + 0x08))
        map_ids = tuple(
            ighw.i32(material.offset + index * 4) for index in range(4)
        )
        flag_byte, blend_mode, unknown_0, unknown_1 = bytes(
            ighw.view(material.offset + 0x10, 4)
        )
        values = tuple(
            tuple(
                ighw.f32(
                    material.offset
                    + 0x20
                    + vector_index * 0x10
                    + component_index * 4
                )
                for component_index in range(4)
            )
            for vector_index in range(6)
        )

        self.flags[flag_byte] += 1
        self.blend_modes[blend_mode] += 1
        self.unknown_0[unknown_0] += 1
        self.unknown_1[unknown_1] += 1
        self.map_patterns[map_ids] += 1
        if len(self.blend_examples[blend_mode]) < 12 and lookup_path:
            self.blend_examples[blend_mode].append(lookup_path)

        for vector_index, vector in enumerate(values):
            for component_index, value in enumerate(vector):
                self.value_components[vector_index][component_index][
                    round(value, 6) if math.isfinite(value) else _json_float(value)
                ] += 1

        for slot, (name, texture_id, path) in enumerate(
            zip(SLOT_NAMES, texture_ids, texture_paths)
        ):
            present = texture_id != 0
            self.slot_presence[name][present] += 1
            if path:
                self.slot_paths[name][
                    PurePosixPath(path).name.casefold()
                ] += 1

        for name, (mask, slot) in FLAG_SLOTS.items():
            self.flag_correlations[name][
                (bool(flag_byte & mask), texture_ids[slot] != 0)
            ] += 1

        descriptors = ighw.section(0x5200)
        resources = ighw.section(0x5A00)
        texture_formats: dict[str, str | None] = {
            name: None for name in SLOT_NAMES
        }
        if descriptors is not None:
            for slot, (name, map_id, texture_id) in enumerate(
                zip(SLOT_NAMES, map_ids, texture_ids)
            ):
                if map_id < 0 or map_id >= descriptors.count:
                    continue
                offset = descriptors.offset + map_id * descriptors.length
                format_code = ighw.u8(offset + 0x06)
                texture_formats[name] = f"0x{format_code:02X}"
                self.slot_formats[name][format_code] += 1
                texture_info = texture_infos.get(texture_id)
                if texture_info is not None:
                    self.slot_format_pairs[name][
                        (texture_info.format_code, format_code)
                    ] += 1
                self.slot_addresses[name][ighw.u32(offset + 0x08)] += 1
                if resources is not None and map_id < resources.count:
                    resource_offset = (
                        resources.offset + map_id * resources.length
                    )
                    self.slot_hash_matches[name][
                        ighw.u32(resource_offset) == texture_id
                    ] += 1

        if self.include_shaders:
            self.shader_records.append(
                {
                    "tuid": f"0x{tuid:016X}",
                    "archive": archive,
                    "lookup_path": lookup_path,
                    "texture_ids": {
                        name: f"0x{texture_id:08X}" if texture_id else None
                        for name, texture_id in zip(SLOT_NAMES, texture_ids)
                    },
                    "texture_paths": dict(zip(SLOT_NAMES, texture_paths)),
                    "texture_formats": texture_formats,
                    "texture_metadata_formats": {
                        name: (
                            f"0x{texture_infos[texture_id].format_code:02X}"
                            if texture_id in texture_infos
                            else None
                        )
                        for name, texture_id in zip(
                            SLOT_NAMES,
                            texture_ids,
                        )
                    },
                    "map_ids": dict(zip(SLOT_NAMES, map_ids)),
                    "flags": f"0x{flag_byte:02X}",
                    "blend_mode": blend_mode,
                    "unknown_0": unknown_0,
                    "unknown_1": unknown_1,
                    "values": [
                        [_json_float(value) for value in vector]
                        for vector in values
                    ],
                    "sections": [
                        {
                            "id": f"0x{section.id:X}",
                            "count": section.count,
                            "length": section.length,
                        }
                        for section in ighw.sections
                    ],
                }
            )

    def report(self, source: Path) -> dict[str, object]:
        sections = {}
        for section_id in sorted(self.section_counts):
            sections[f"0x{section_id:X}"] = {
                "counts": _counter_rows(self.section_counts[section_id]),
                "record_lengths": _counter_rows(
                    self.section_lengths[section_id]
                ),
            }

        slots = {}
        for name in SLOT_NAMES:
            slots[name] = {
                "presence": _counter_rows(
                    self.slot_presence[name],
                    lambda value: "present" if value else "absent",
                ),
                "formats": _counter_rows(
                    self.slot_formats[name],
                    _hex(2),
                ),
                "metadata_descriptor_format_pairs": _counter_rows(
                    self.slot_format_pairs[name],
                    lambda value: (
                        f"0x{value[0]:02X}->0x{value[1]:02X}"
                    ),
                ),
                "addresses": _counter_rows(
                    self.slot_addresses[name],
                    _hex(8),
                ),
                "resource_hash_matches": _counter_rows(
                    self.slot_hash_matches[name],
                    lambda value: "match" if value else "mismatch",
                ),
                "common_paths": _counter_rows(
                    Counter(dict(self.slot_paths[name].most_common(40)))
                ),
            }

        value_vectors = []
        for vector_index, components in enumerate(self.value_components):
            value_vectors.append(
                {
                    "index": vector_index,
                    "components": [
                        {
                            "index": component_index,
                            "unique_values": len(values),
                            "minimum": min(
                                (
                                    value
                                    for value in values
                                    if isinstance(value, float)
                                ),
                                default=None,
                            ),
                            "maximum": max(
                                (
                                    value
                                    for value in values
                                    if isinstance(value, float)
                                ),
                                default=None,
                            ),
                            "common_values": _counter_rows(
                                Counter(dict(values.most_common(20)))
                            ),
                        }
                        for component_index, values in enumerate(components)
                    ],
                }
            )

        report: dict[str, object] = {
            "source": str(source),
            "archives_scanned": self.archive_count,
            "shader_references": self.shader_references,
            "unique_shaders": len(self.digests),
            "duplicate_payload_mismatches": self.duplicate_mismatches,
            "sections": sections,
            "material": {
                "flag_bytes": _counter_rows(self.flags, _hex(2)),
                "blend_modes": _counter_rows(self.blend_modes),
                "blend_examples": {
                    str(mode): examples
                    for mode, examples in sorted(self.blend_examples.items())
                },
                "unknown_byte_0": _counter_rows(self.unknown_0),
                "unknown_byte_1": _counter_rows(self.unknown_1),
                "map_index_patterns": _counter_rows(
                    self.map_patterns,
                    _tuple_text,
                ),
                "flag_slot_correlations": {
                    name: _counter_rows(
                        values,
                        lambda value: (
                            f"flag={'on' if value[0] else 'off'},"
                            f"slot={'present' if value[1] else 'absent'}"
                        ),
                    )
                    for name, values in self.flag_correlations.items()
                },
                "value_vectors": value_vectors,
            },
            "texture_slots": slots,
            "texture_metadata_formats": _counter_rows(
                self.texture_metadata_formats,
                _hex(2),
            ),
        }
        if self.include_shaders:
            report["shaders"] = sorted(
                self.shader_records,
                key=lambda shader: str(shader["tuid"]),
            )
        return report


def _iso_archives(tar: str, iso: Path) -> list[str]:
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


def _extract_iso_members(
    tar: str,
    iso: Path,
    members: list[str],
    destination: Path,
) -> None:
    result = subprocess.run(
        [tar, "-xf", str(iso), "-C", str(destination), *members],
        check=False,
        capture_output=True,
        timeout=60 * 60,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not extract cached level archives: {detail}")


def _analyze_iso(
    analysis: ShaderAnalysis,
    iso: Path,
    work_directory: Path | None,
) -> None:
    tar = shutil.which("tar")
    if tar is None:
        raise RuntimeError("No libarchive-compatible tar executable was found")
    archives = _iso_archives(tar, iso)
    if work_directory is not None:
        work_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="r2_shader_scan_",
        dir=work_directory,
    ) as temporary:
        temporary_path = Path(temporary)
        print(f"Extracting {len(archives)} cached level archives", flush=True)
        _extract_iso_members(tar, iso, archives, temporary_path)
        for index, member in enumerate(archives, start=1):
            print(f"[{index}/{len(archives)}] {member}", flush=True)
            archive_path = temporary_path / Path(member)
            analysis.add_archive(archive_path, member)


def main() -> int:
    args = _arguments()
    source = args.source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Source not found: {source}")
    work_directory = (
        args.work_directory.expanduser().resolve()
        if args.work_directory is not None
        else None
    )
    analysis = ShaderAnalysis(include_shaders=args.include_shaders)
    if source.suffix.casefold() == ".iso":
        _analyze_iso(analysis, source, work_directory)
    else:
        analysis.add_archive(source, str(source))

    report = analysis.report(source)
    summary = {
        "archives_scanned": report["archives_scanned"],
        "shader_references": report["shader_references"],
        "unique_shaders": report["unique_shaders"],
        "duplicate_payload_mismatches": len(
            report["duplicate_payload_mismatches"]
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.report is not None:
        output = args.report.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
