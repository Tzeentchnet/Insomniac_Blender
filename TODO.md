# Feature Roadmap TODO

Created from the feature-gap review of [README.md](README.md) and
[known issues](docs/known_issues.md).

All currently scoped implementation and validation milestones are complete.
The remaining unchecked entries are intentionally separate research projects
or evidence-blocked work and are not represented as implemented features.

## Working principles

- Validate format work against real game data from more than one asset or
  level when practical.
- Keep binary reads bounded and reject unsupported layouts explicitly.
- Add synthetic regressions, real-data regressions, and Blender save/reopen
  coverage for new import paths.
- Produce isolated and contextual files under `E:\Tests` for visual changes.
- Do not connect unverified material channels or silently guess ownership.
- Keep Asset Browser generation selected-level only unless that product
  decision is explicitly revisited.

## 1. Expose vegetation in the direct asset catalog

- [x] Extend the catalog schema with backward-compatible `shrubs` and
  `foliage` lists.
- [x] Include Shrub and Foliage pointers during catalog regeneration.
- [x] Preserve R2/R3 game-global identities and RFoM/TOD level-scoped
  identities.
- [x] Use source names where available and deterministic fallback names
  elsewhere.
- [x] Add Shrub and Foliage filters, icons, tooltips, and import dispatch to
  the asset picker.
- [x] Regenerate all four PS3 bundled metadata catalogs and document the
  resulting counts.
- [x] Test isolated direct imports for every supported game and vegetation
  kind.
- [x] Correct the direct-import README workflow to include RFoM.

## 2. Add Tools of Destruction Shrubs

### Format investigation

- [x] Inventory TOD levels containing `0x9540` Shrub placement containers.
- [x] Identify the Shrub lookup table and geometry owner.
- [x] Determine whether geometry is embedded in `main.dat`, stored in another
  level resource, or uses a V1/V2 hybrid layout.
- [x] Verify vertex, index, material, and transform layouts across multiple
  levels.
- [x] Record exact counts and format findings in the TOD format documentation.

### Implementation

- [x] Expose `AssetKind.SHRUB` pointers and deterministic identities.
- [x] Implement Shrub source naming or a deterministic fallback.
- [x] Implement bounded Shrub geometry and material decoding.
- [x] Decode `0x9540` placements and remove the unsupported-instance warning.
- [x] Reuse meshes across placements.
- [x] Integrate TOD Shrubs with standalone import, level import, and the Asset
  Browser cache.

### Validation

- [x] Add synthetic geometry, material, transform, and invalid-input tests.
- [x] Add real-data count and finite-transform regressions.
- [x] Run the portable and Blender test suites.
- [x] Save and reopen isolated and contextual Blender review files.
- [x] Confirm masked leaves, orientation, scale, terrain placement, and exact
  persisted object/mesh counts.

## 3. Compose R3 directional and shadow maps

- [x] Correlate `0x5410` resources with their `0x5400` baked-lightmap owners.
- [x] Determine channel semantics and UV relationships.
- [x] Establish whether the map modifies baked light, vertex light, direct
  light, or a separate term.
- [x] Compare candidate compositions against known in-game terrain and Tie
  references.
- [x] Implement only the verified composition, without speculative fallback.
- [x] Cover terrain and compact-atlas Tie paths independently.
- [x] Add material-node and Blender persistence regressions.
- [x] Produce before/after visual-review scenes.
- [x] Treat dynamic scene lights as a separate follow-up feature.

## 4. Resolve R2 cross-level texture dependencies automatically

- [x] Build a metadata-only texture TUID/path-to-owner index during catalog
  generation.
- [x] Persist the ownership index with disc identity and cache provenance.
- [x] Detect and report ambiguous owners or conflicting payloads.
- [x] Prepare only the required sibling archive when a selected level lacks a
  referenced texture.
- [x] Reuse verified extraction caches and existing NTFS hard-link behavior.
- [x] Avoid turning the feature into full-disc Asset Browser generation.
- [x] Test from a clean cache containing only the selected level.
- [x] Verify representative co-op and multiplayer shaders no longer use
  avoidable placeholder textures.

## 5. Add a shared level-collision foundation

### Shared representation

- [x] Define a game-neutral collision mesh and metadata model.
- [x] Add deterministic, non-rendering Blender collision Collections.
- [x] Preserve source groups, material or surface IDs, and instance metadata.
- [x] Reuse mesh datablocks for repeated collision geometry.

### Game backends

- [x] Investigate and implement the R2/R3 level-collision layout first.
- [x] Decode TOD's separate non-IGHW `collision.dat` format.
- [x] Decode RFoM's `ps3levelcoll.dat` format.

### Validation

- [x] Add bounds, index, transform, and malformed-record tests.
- [x] Verify representative terrain, static-prop, and gameplay collision.
- [x] Validate saved-file persistence and export-oriented mesh integrity.

## 6. Decode RFoM secondary gameplay groups

- [x] Inventory the six skipped gameplay groups across representative levels.
- [x] Identify each group's count, stride, identity lookup, and transform
  layout.
- [x] Classify records as renderable Mobys, markers, triggers, or effects.
- [x] Route only proven renderable Moby records through the shared static
  placement importer.
- [x] Preserve source group names and instance metadata.
- [x] Add exact-count and placement regressions.
- [x] Produce a contextual level review showing newly recovered objects.

## 7. Implement material refinements as verified families

- [x] Select one evidence-backed family at a time.
- [x] Investigate R3 frozen overlays.
- [x] Investigate R3 dissolve and gradient effects.
- [x] Investigate fire, pickup-sheen, and related effect-slot families.
  - [x] Classify the runtime-driven R3 on-fire family.
  - [x] Classify the runtime-driven R3 flash/pickup-sheen family.
  - [x] Classify the runtime-driven R3 weapon-upgrade family.
- [x] Verify R2/R3 detail-map scale and composition.
- [x] Investigate Shrub packed vertex metadata.
- [x] Investigate authored Foliage normals and billboarding.
- [x] Require a shader/TUID fixture, an in-game reference, and a visual review
  before enabling any new family by default.

## 8. Advance experimental Burning Skies support

- [x] Decode skin-weight ownership and influence normalization.
- [x] Import skinned SMFs as working armatures rather than static bind-pose
  geometry.
- [x] Investigate animation containers only after skinning is validated.
- [x] Expand collision-family coverage.
- [ ] Keep level, cutscene, and effect support as separate research projects.

## Explicitly deferred or evidence-blocked

- [ ] Full-disc Asset Browser batch generation: intentionally deferred in
  favor of selected-level generation.
- [ ] R2 root motion: sampled animation root-motion pointers are zero.
- [ ] Automatic Capelli body/head pairing: no embedded relationship is known,
  and the skeletons are incompatible.
- [ ] TOD/RFoM retail names: no proven retail name database is available.
- [ ] Global R3 effect-slot wiring: the slots contain unrelated runtime
  families and must be handled individually.
- [ ] Runtime vegetation wind: lower priority than static geometry,
  materials, normals, and placement correctness.
- [ ] Broad VFX, audio, and cutscene import: separate domains requiring their
  own format investigations.
