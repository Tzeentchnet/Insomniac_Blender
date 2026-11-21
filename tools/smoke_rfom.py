from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from insomniac_asset_io.core.collision import validate_collision_scene
    from insomniac_asset_io.core.source import GameSource
    from insomniac_asset_io.games.resistance_2.formats import AssetKind
    from insomniac_asset_io.games.resistance_fom.reader import (
        ResistanceFomReader,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Decode one complete Resistance: Fall of Man level asset chain"
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Extracted level directory containing ps3levelmain.dat",
    )
    parser.add_argument(
        "--moby",
        type=lambda value: int(value, 0),
        help="Optional Moby class ID; defaults to the first non-empty Moby",
    )
    args = parser.parse_args()

    with GameSource.open(args.source) as source:
        reader = ResistanceFomReader(source)
        summary = reader.inspect()
        print(f"Level prefix: {summary.resource_prefix}")
        for kind, count in summary.counts.items():
            if count:
                print(f"  {kind.name.lower():12s} {count}")

        if args.moby is not None:
            moby = reader.read_moby(args.moby)
        else:
            moby = next(
                candidate
                for pointer in reader.asset_pointers(AssetKind.MOBY)
                if (candidate := reader.read_moby(pointer.tuid)).meshes
            )
        print(
            f"Moby 0x{moby.tuid:04X}: {moby.name}\n"
            f"  meshes={len(moby.meshes)} "
            f"bones={moby.skeleton.bone_count if moby.skeleton else 0}"
        )
        clips = reader.read_animations(moby)
        print(f"  clips={len(clips)}")

        if moby.shader_tuids:
            shader = reader.read_shader(moby.shader_tuids[0])
            print(f"Material 0x{shader.tuid:04X}: {shader.alpha_mode}")
            if shader.albedo_texture_id is not None:
                texture = reader.read_texture(shader.albedo_texture_id)
                print(
                    f"  diffuse={texture.info.width}x{texture.info.height} "
                    f"format=0x{texture.info.format_code:02X}"
                )

        ties = reader.asset_pointers(AssetKind.TIE)
        if ties:
            tie = reader.read_tie(ties[0].tuid)
            print(f"Tie 0x{tie.tuid:08X}: meshes={len(tie.meshes)}")
        zones = reader.read_zones()
        print(
            f"Zones={len(zones)} terrain="
            f"{sum(len(zone.terrain_meshes) for zone in zones)} "
            f"tie_instances="
            f"{sum(len(zone.tie_instances) for zone in zones)} "
            f"shrub_instances="
            f"{sum(len(zone.shrub_instances) for zone in zones)} "
            f"foliage_instances="
            f"{sum(len(zone.foliage_instances) for zone in zones)}"
        )
        regions = reader.read_gameplay_regions()
        print(
            f"Gameplay regions={len(regions)} moby_instances="
            f"{sum(len(region.moby_instances) for region in regions)}"
        )
        collision = reader.read_level_collision()
        validate_collision_scene(
            collision,
            label="RFoM level collision",
        )
        print(
            f"Level collision: instances={len(collision.instances)} "
            f"meshes={len(collision.meshes)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
