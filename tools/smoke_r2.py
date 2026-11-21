from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode one complete Resistance 2 asset chain"
    )
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    with GameSource.open(args.source) as source:
        reader = Resistance2Reader(source)
        summary = reader.inspect()
        print(f"Level prefix: {summary.resource_prefix}")
        for kind, count in summary.counts.items():
            if count:
                print(f"  {kind.name.lower():12s} {count}")

        moby_pointer = reader.asset_pointers(AssetKind.MOBY)[0]
        moby = reader.read_moby(moby_pointer.tuid)
        print(
            f"Moby 0x{moby.tuid:016X}: {moby.name}\n"
            f"  meshes={len(moby.meshes)} "
            f"bones={moby.skeleton.bone_count if moby.skeleton else 0}"
        )
        clips = reader.read_animations(moby)
        print(f"  clips={len(clips)}")

        if moby.shader_tuids:
            shader = reader.read_shader(moby.shader_tuids[0])
            print(f"Shader 0x{shader.tuid:016X}: {shader}")
            if shader.albedo_texture_id is not None:
                texture = reader.read_texture(shader.albedo_texture_id)
                print(
                    f"  diffuse={texture.info.width}x{texture.info.height} "
                    f"format=0x{texture.info.format_code:02X}"
                )

        ties = reader.asset_pointers(AssetKind.TIE)
        if ties:
            tie = reader.read_tie(ties[0].tuid)
            print(f"Tie 0x{tie.tuid:016X}: meshes={len(tie.meshes)}")
        shrub_pointers = reader.asset_pointers(AssetKind.SHRUB)
        if shrub_pointers:
            shrub = reader.read_shrub(shrub_pointers[0].tuid)
            print(
                f"Shrub 0x{shrub.tuid:016X}: "
                f"meshes={len(shrub.meshes)}"
            )
        foliage_pointers = reader.asset_pointers(AssetKind.FOLIAGE)
        if foliage_pointers:
            foliage = reader.read_foliage(foliage_pointers[0].tuid)
            print(
                f"Foliage 0x{foliage.tuid:016X}: "
                f"meshes={len(foliage.meshes)}"
            )
        zones = reader.read_zones()
        print(
            f"Zones={len(zones)} tie_instances="
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
        validate_collision_scene(collision, label="Resistance 2 collision")
        print(
            f"Level collision meshes={len(collision.meshes)} "
            f"instances={len(collision.instances)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
