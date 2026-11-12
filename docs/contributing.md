# Contributing formats and game adapters

## Evidence rules

Document whether a field is:

- verified on real data;
- inherited from a cited implementation;
- inferred from repeated values;
- speculative.

Do not silently reuse a layout because a class ID has the same name in another
game. Add explicit dispatch when the bytes differ.

## Workflow

1. Record game, region/build, engine era, archive path, and resource TUID.
2. Add a bounded probe or parser assertion.
3. Compare at least two unrelated assets and one edge case.
4. Add a synthetic test that contains no game data.
5. Run the optional real-data smoke test locally.
6. Update the game's format document and known issues.

## Parser requirements

- Bounds-check before every pointer/table read.
- Reject unreasonable counts and allocation sizes.
- Preserve endianness in one well-defined layer.
- Use NumPy strided views for large homogeneous buffers.
- Read archive slices rather than materializing an entire large `.dat`.
- Surface unsupported variants with IDs, offsets, and format bytes.
- Do not return an empty success-shaped asset after a decode failure.

## Blender requirements

- Keep game parsing free of `bpy`.
- Preserve one source coordinate hierarchy and convert axes once at a root.
- Share mesh/image/material datablocks for repeated level instances.
- Store source TUIDs as custom properties.
- Keep actions persistent and record source FPS/loop/additive metadata.

## Adding a game

Create `insomniac_asset_io/games/<game>/` with:

- typed semantic outputs;
- source-layout detection;
- archive/resource readers;
- model/static/animation/material/level readers as evidence permits;
- tests;
- `docs/formats/<game>.md`.

Register it in `games/registry.py`. Do not add a game to the UI until its
adapter can validate a source and clearly report unsupported features.
