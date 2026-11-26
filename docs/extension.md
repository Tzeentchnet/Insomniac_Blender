# ReLunacy review: candidate importer extensions

This review looks at [ReLunacy](https://github.com/VELD-Dev/ReLunacy) for
format knowledge the Blender importer can adapt. ReLunacy is a GPL-3.0 C#
level editor for Insomniac's PS3 games. Its `nightly` branch was reviewed at
commit `cc6db19` (2026-11-25, "RFOM compatibility improvements (#90)").

Each entry below is a lead to verify, not a confirmed result. Under the
working principles in [TODO.md](../TODO.md), every adopted layout must be
re-proven against our own disc builds before it reaches the importer.

Source paths name files in the ReLunacy repository. Paths under `Loading/`,
`Assets/`, `Importing/`, and `Resources/` are relative to its
`ReLunacy.Engine/` project; `docs/` (lower case) is at the repository root.

## Review checks (2026-11-26)

- Every cited ReLunacy file exists at commit `cc6db19`. The section IDs,
  strides, record offsets, and counts quoted below were checked against
  those files.
- Two claims were corrected against the source: the SoftEdge treatment in
  item 4 and the texture-format comparison in item 14.
- Our own claims were checked against this repository: the rendering-mode
  mapping in `ratchet_tod/reader.py`, the `global_cached.psarc` archive
  sets, the RFoM `level_archives`, `FORMAT_NAMES`, and the TOD collision
  face-word fields.
- A read-only scan of all 20 levels of the `BCUS98127` v02.00 disc counted
  the main.dat `0x5200` texture format codes and the `0x5000` shader
  rendering-mode bytes. Items 4 and 14 quote the results.

## Implementation status (2026-11-26)

| # | Status | Notes |
|---|---|---|
| 14 | Done, with a different fix | Our code table was right and ReLunacy's is not (see below). The defect was the A4R4G4B4 bit order, in the decoder shared by TOD, R2, and R3. |
| 1 | Done, from `DEBUG.DAT` | All 20 levels have one, so prototypes and shaders are named too, not only instances. |
| 2 | Done | 37 of 41 levels; the four multi-group levels keep numeric names. |
| 4 | Done for TOD | The same byte exists in RFoM, R2, and R3, which still use the generic mapping. |
| 3 | Done for TOD and RFoM | Gameplay Mobys, and RFoM markers, get `insomniac_class_id` and `insomniac_class_name`. R2/R3 still need the class number located. |
| 12 | Done, and extended | `insomniac_no_wall_jump` boolean face attribute on TOD level collision. Collision material bytes are now named from `kMaterialID`, and the collision vertex slot order was corrected. |
| 13 | Corroborated | Our TOD reader already reads the bangle count at `+0x18` and the table at `+0x28`; no change needed. |
| 5 | Done for TOD | All seven families on all 20 levels. The Clue and area layouts were corrected, and the volume half-extent settled at 1.0 (see below). |
| 8 | Done for TOD and RFoM | Fog and bloom (TOD) and the ambient and two directional lights (both). Light volumes are not decoded. |
| 6 | Done for TOD | Baked colour on Ties and UFrags. The light-direction pool, the light scales, and RFoM (`0x5500`) remain. |
| 7 | Done for TOD and RFoM | Decoded to equirectangular World images; the HDR exponent scale is unknown. |
| 9 | Done for TOD | All three owner families; frame rate and rotation sign as ReLunacy read them. |
| 10–11, 15–16 | Not started | |

The sections below keep the original review text. Where the work above has
overtaken it, a **Status** line says so, and the corrections that follow
take precedence over the review text.

Corrections to the review below, from our `BCUS98127` and `BCUS98107` discs:

- **Item 1.** `DEBUG.DAT` exists for every TOD level, not only metropolis. It
  is a loose file at `PS3_GAME/USRDIR/BUILT/LEVELS/<LEVEL>/DEBUG.DAT`, not a
  member of any PSARC. It also names Shrub prototypes (`0x9580`) and Shrub
  instances (`0x7560`). The "gameplay section + `0x20`" rule fails for Mobys
  (`0x7340` → `0x73C0`) and controllers (`0x77C0` → `0x77D0`). The TOD
  adapter overrides `global_archives` with `()`, so `global_cached.psarc`
  is not reachable from TOD level imports. `instances.lua` was used to
  verify the names, not to import them; its paths spell level folders with
  spaces (`built/levels/kerchu city/instances.lua`).
- **Item 2.** The kind list is incomplete. Kinds 4 (Moby groups, `+0x90`)
  and 5 (areas, `+0xA0`) also occur, and kind 6 is the `+0x80` marker family.
  No per-group `DebugStart`/`DebugCount` window was found in the group
  record. Names are therefore applied only when the gameplay header's group
  count (`+0x24`) is 1.
- **Item 14.** The descriptor high byte is the GCM format byte (`0x83`,
  `0x84`, `0x85`, ...) with identity remap `0xAAE4`. Codes 1, 5–8, and 11
  match GCM B8, A8R8G8B8, DXT1/3/5, and G8B8. So `3` is A4R4G4B4 and `4` is
  R5G6B5, as our table said. Our A4R4G4B4 decoder put alpha in the low
  nibble; GCM puts it in the high nibble. Under the old order, R2/R3 code-3
  textures decoded fully transparent (alpha 0 everywhere); under the fixed
  order they are opaque flat-colour art and a crisp stripe grid. The TOD
  glass texel `0x699B` now decodes as grey-blue (153, 153, 187) at alpha 0.4
  instead of opaque-ish teal. Code `2`/`0x82` (GCM A1R5G5B5) occurs in no
  corpus, and its decoder (alpha in bit 0) is unverified.
- **Item 3, class tables.** TOD's class table is in the game-wide
  `PS3_GAME/USRDIR/BUILT/GAME/DEBUG.DAT` (loose on the disc), not in
  `built/game/game.dat`. Section `0x25600` is the row table itself:
  `{u32 string-offset array, u32 count, u32 count, u32 flags}` per `0x10`
  row. Row 0 lists the table names; `kVTGenEnums` (row 1) has 1,754
  entries, 166 of them null. The RFoM file is `packed/game/ps3debug.dat`
  inside `GAME.PSARC`. There, section `0x25500` is a header
  `{u32 rows, u32 row count}` that points to the same row layout, and
  `kVTGenEnums` has 1,310 entries (38 null). A row is dense, and indexed
  directly by value, only when its flags are 0 and its two counts agree;
  the class row is dense in both games.
- **Item 3, class blocks.** A gameplay Moby record's first word points to an
  IGHW 0.2 block whose `0x25200` payload starts with the class number. A
  null pointer means no class data (453 of 18,251 TOD Mobys; 1,290 of
  16,422 primary-group RFoM Mobys). Every other record resolves to a
  non-null name: 17,798 TOD Mobys over 254 classes and 15,132 RFoM Mobys
  over 95 classes. Spot checks agree with the retail instance and prototype
  names: breakable crates are `BreakableBoltCrate_CLASS` and swarmer pods
  are `Swarmer_Egg_CLASS`. RFoM marker payloads carry the same class
  number; all 1,123 markers on level20 resolve, mostly to
  `FxController_CLASS`.
- **Item 3, further tables.** The same files hold other enum tables,
  including `kMaterialClass`, `kMaterialSubclass`, and `kMaterialID` (71
  `COLL_MATERIAL_*` names on TOD). These rows carry flag `0x01000000`; their
  second count (9 for `kMaterialID`) is a hand-written head of special
  values, and the row is still indexed directly by value. TOD collision
  faces store one `kMaterialID` byte each at the triangle-mesh header's
  `+0x0C` offset. On all 20 levels the IDs are 0-67 and the names fit the
  geometry: `LAVA_DEFAULT` is 99.7% horizontal, `DIRT_GRAVEL` 98.6%,
  `SNOW_DEFAULT` 94.5%, `GLASS_UNBREAKABLE_LARGE` 0%, and
  `QUICKSAND_GELATONIUM` occurs on Cobalia. The importer now names each
  collision material.
- **Item 12, vertex order.** ReLunacy's `docs/reverse/collision.md` gives the
  slot order inside each eight-vertex line as `0, 4, 1, 5, 2, 6, 3, 7`. Our
  decoder read the slots in plain order, which scrambled each group of eight
  vertices. The corrected order halves the median triangle perimeter, and
  removes the 11 "no-op" all-degenerate prototypes and nearly all
  degenerate faces (36 remain across all 20 levels).

### Resistance 2 and Resistance 3 cross-check

- **Names (items 1–2).** No change is needed. R2/R3 already decode native
  name tables: prototypes from `assetlookup` `0xD200`/`0x3410`/`0xB700`,
  Tie instances from the zone name-pointer table, and gameplay Mobys from
  `gp_prius.dat` metadata. On the 15 cached R2/R3 levels, every sampled
  prototype is named. Numeric fallbacks are 2,433 of 51,237 R2 Tie
  instances, 0 of 132,131 R3 Tie instances, 129 of 15,759 R2 Mobys, and 234
  of 18,623 R3 Mobys. In each case the source name pointer is null. Neither
  disc has per-level debug files.
- **Class names (item 3).** Both discs ship `BUILT/GAME/DEBUG.DAT` with the
  same enum lookup layout as TOD's: section `0x2503C` holds row headers
  (offset, count), `0x25040` holds string offsets, and `0x25044` is the
  string pool. Row 0 lists the enum names. Row 1 is `kVTGenEnums`, with
  2,176 class names on R2. Row 23 holds `ART_CLASS_*` names. TOD keeps the
  same file at `0x25600`/`0x25640`/`0x25680`. Item 3 therefore applies to
  R2/R3 once the class number is located in their Moby records.
- **Rendering modes (item 4).** The byte at `material_word >> 16` has the
  same 0–6 range in RFoM, R2, and R3. For example, R2 has 132 and R3 has 73
  Additive (`2`) shaders on the cached levels, and RFoM level20 has 8. These
  games still map `4` to MASK and every other non-zero byte to BLEND. Before
  sharing the TOD table, review a few Additive and SoftEdge (`5`) materials
  visually in each game.

## Licensing and attribution

- ReLunacy is GPL-3.0. Insomniac Asset IO is GPL-3.0-or-later, so the
  licenses are compatible. Any file containing code translated from ReLunacy
  can be distributed only under GPL-3.0. Keep its notices in such files.
- ReLunacy's `robots.txt` and README ask that credit be given for the
  reverse-engineering research, not only for the code. This includes section
  IDs, offsets, and field meanings. When a finding below is adopted, credit
  it in [NOTICE.md](../NOTICE.md) and in the relevant format document. The
  main contributors are:
  - @VELD-Dev and @NefariousTechSupport (ReLunacy and Lunacy)
  - @Neigy / Fianshoo (ReLunacy Resurgence, class headers, textures and
    volumes)
  - @PredatorCZ (InsomniaToolset)
  - @sowelilili (the Tools of Destruction collision layout, from
    ReLunacy-ToD-tools)
- [NOTICE.md](../NOTICE.md) now links the upstream
  `github.com/VELD-Dev/ReLunacy` rather than the
  `github.com/Mrroboto9819/ReLunacy` fork.
- Prefer reimplementing from the documented facts and checking them against
  our own data over translating the C# line by line. This matches how the
  existing adapters were written.

## Scope and build caveats

- ReLunacy covers Tools of Destruction, Quest for Booty, Resistance: Fall of
  Man, A Crack in Time, Full Frontal Assault (QForce), All 4 One, and Into
  the Nexus. Its README feature table lists Resistance 2, Retribution, and
  Resistance 3, but `Games/GameDefinition.cs` defines only Fall of Man
  ("TODO: Add later Resistance games"). Burning Skies is marked unknown (`?`)
  in every column. Direct gains are
  therefore mostly for **TOD** and **RFoM**. For R2/R3 there are only
  cross-checks taken from the newer-engine Ratchet games.
- ReLunacy's Tools of Destruction research used the PSN build `NPUA80965`;
  Quest for Booty used `NPEA00088`. We target the disc build `BCUS98127`
  v02.00. Data-file layouts should match across these builds, but EBOOT
  addresses do not. Re-verify on our disc corpus; never reuse an EBOOT
  address.
- Most of the editing and writing features are out of scope for an
  importer. This includes level saving, `gp_prius` writing, Tie appending,
  occlusion baking, the Lua environment, and script graphs.

## Summary

| # | Candidate | Games | Our current state | Effort | Value |
|---|---|---|---|---|---|
| 1 | Instance names from `instances.lua` / `debug.dat` | TOD | **Done**, from `DEBUG.DAT` | Low | High |
| 2 | Instance names from `ps3debug.dat` `0x25500` | RFoM | **Done** on the 37 single-group levels | Low | High |
| 3 | Gameplay class names (kVTGenEnums) | TOD, RFoM | **Done** for Mobys and RFoM markers | Low–Med | High |
| 4 | Complete rendering-mode table | TOD (old engine) | **Done** for TOD; other games use the generic mapping | Low | High |
| 5 | Gameplay context: volumes, paths, Clues, controllers, areas, pods, scents | TOD | **Done** | Medium | High |
| 6 | Old-engine baked lightmaps and lightmap UVs | TOD, RFoM | **Done** for TOD colour; RFoM remains | Med–High | High |
| 7 | Environment cubemaps | TOD, RFoM | **Done**; HDR scale unknown | Medium | Medium |
| 8 | Level fog, bloom, and lighting environment | TOD, RFoM | **Done**, except light volumes | Low–Med | Medium |
| 9 | UV scroll/rotate animation (`0x5C00`) | TOD?, new engine | **Done** for TOD | Medium | Medium |
| 10 | Sky definitions and shell mesh | TOD | Not decoded | Medium | Medium |
| 11 | Tie shader feature bits and material vectors | TOD | Partial | Medium | Medium |
| 12 | Collision "no wall jump" triangle flag | TOD | **Done**, with material names and vertex order | Low | Low |
| 13 | Old-engine Moby bangle header fields | TOD | **Corroborated**; offsets already match | Low | Low |
| 14 | Old-engine texture format codes 3 and 4 | TOD | **Done**; the A4R4G4B4 decoder was fixed instead | Low | Medium |
| 15 | Quest for Booty adapter | QfB | Unsupported | High | Medium |
| 16 | Newer-engine Ratchet adapters | ACiT, QForce, A4O, ITN | Unsupported | High | Medium |

The original recommended order was 14, 1, 2, 4, 3, 5, 8, 6, 7, 9, 10.
With items 1–9 and 12–14 finished, the remaining order is 10, then 11. Item 4 can also be extended to RFoM, R2, and R3 after a visual
review, and item 3 to R2/R3 once their class number is located. Item 6
needs the most new data.

## 1. Tools of Destruction instance names

**Status:** done, from each level's loose `DEBUG.DAT`. See the item 1
correction above; `instances.lua` is not used.

ReLunacy sources: `Loading/Readers/OldInstanceNames.cs`,
`Loading/Readers/DebugReader.cs`, and
`docs/reverse/tod_volumes_clues_paths.md` §1.

- Every TOD level has `/built/levels/<level>/instances.lua` inside
  `packed/game/global_cached.psarc`. Lines read
  `name = <family>_handle:new( N ); -- <guid>`. The families are `moby`,
  `volume`, `path`, `clue`, `controller`, `area`, `pod`, and `scent`.
- `N` counts from 0 and is the record index in that family's `gameplay.dat`
  table. ReLunacy reports that the counts and contiguity are proven on all
  20 levels, so `names[i]` belongs to `instance[i]`.
- `debug.dat` exists only for `metropolis`. Its name sections are the
  gameplay section ID plus `0x20`:
  - `0x73C0` Mobys, `0x7720` paths, `0x7760` volumes, `0x77A0` Clues,
    `0x77D0` controllers, `0x7920` pods, `0x7960` areas, and `0x79A0`
    scents;
  - `0x72C0` Tie instances and `0x74C0` Shrubs;
  - prototype names: `0x9480` Mobys and `0x9280` Ties;
  - `0x5D00` shaders, which also carry the names of their four textures.

  Instance records are `0x18` bytes:
  `{u64 tuid1, u64 tuid2, u32 namePtr, u16 zone, u16 0}`. On metropolis,
  `debug.dat` and `instances.lua` agree on both name and GUID.
- Our state: [known_issues.md](known_issues.md) says the retail
  `DEBUG.PSARC` has no usable name database, and we use `Moby_XXXX` names.
  `global_cached.psarc` is already in the default global archive set, so
  level imports can reach `instances.lua` without new extraction. Catalog
  generation (`catalog_global_archives = ()`) does not include it.
- Adaptation: parse the Lua lines with a strict regular expression. Name
  placed Moby objects and store the GUID as a custom property. Keep the
  numeric identity as the stable key. Only instances get names, not
  prototype assets, unless `debug.dat` `0x9480`/`0x9280` is present.
- Validate: compare counts with every gameplay section on all 20 levels;
  check that names and GUIDs equal `debug.dat` on metropolis; run a
  save/reopen test.

## 2. Resistance: Fall of Man instance names

**Status:** done. See the item 2 correction above.

ReLunacy source: `Loading/Readers/FallOfManGameplayReader.cs` (`ReadNames`)
and `Loading/IO/FallOfManFiles.cs` (`debug.dat` maps to `ps3debug.dat`).

- `ps3debug.dat` section `0x25500` holds `{u32 start, u32 count}`.
- Each `0x10`-byte record at `start` is
  `{u32 namePtr, u32 guidStringPtr, u32 localIndex, u32 kind}`. The kinds
  are 0 Moby, 1 path, 2 volume, 3 Clue, and 6 controller.
- Each named `0x25000` gameplay group owns a `DebugStart`/`DebugCount`
  window. ReLunacy uses the table only when it is complete: if there is more
  than one group, `count` must equal the sum of the group counts. Otherwise
  the local indices repeated by multiplayer groups are ambiguous.
- Our state: `level_archives` for RFoM omits `ps3debug.dat`, and
  [resistance_fom.md](formats/resistance_fom.md) says no proven name table
  exists. ReLunacy's README notes the data is present only "when
  `ps3debug.dat` has the required data". First check which `BCUS98107`
  levels ship a populated `0x25500`.
- Adaptation: add `ps3debug.dat` as an optional level resource. Apply names
  to the primary Mobys and to the existing path, volume, and named-area
  context objects. Keep the same all-or-nothing completeness rule.

## 3. Gameplay class names

**Status:** done for TOD and RFoM gameplay Mobys and for RFoM markers. TOD
controllers and Clues are not imported yet (item 5). See the item 3
corrections above for the file locations and counts.

ReLunacy sources: `Resources/Classes/*.classes`,
`Assets/LevelElements/GameClassNames.cs`, and
`docs/reverse/tod_classes.md`.

- The class names come from each game's own lookup-enum debug file
  (kVTGenEnums). For TOD this is `built/game/game.dat`, section `0x25600`
  row 1: a `u32` file offset per class number, with the string pool at
  `0x25680`. That gives 1,588 TOD names; QfB keeps 1,410 in
  `built/game/debug.dat`. ReLunacy also ships 1,272 RFoM names. The
  `.classes` header says they come from the same kind of enum file, but the
  RFoM file location is not documented in the reviewed sources.
- A placed Moby or controller record points to a mini-IGHW (v0.2). Its
  section `0x25200` payload starts with a `u32` class number. A Clue points
  to a raw payload with the class number at `+0x0C`.
- Adaptation: decode class names from the disc rather than vendoring
  ReLunacy's `.classes` files. That keeps our provenance independent and
  works with our validated builds. Attach `insomniac_class_id` and
  `insomniac_class_name` to placed objects.
- Caution: a gameplay class is not the same thing as a render Moby
  prototype. Do not rename catalog assets from class names unless a 1:1
  correspondence between class and model is proven.

## 4. Rendering modes

**Status:** done for TOD; see the shader table in
[ratchet_tod.md](formats/ratchet_tod.md).

ReLunacy sources: `Loading/Shaders/RenderingMode.cs` and
`Loading/Readers/MaterialReader.cs` (`ToRenderMode`).

| Byte | Mode | New-engine treatment (QForce EBOOT) | ReLunacy renders as | Our TOD mapping | TOD shaders |
|---|---|---|---|---|---|
| `0x00` | Opaque | Opaque | Opaque | OPAQUE | 7,544 |
| `0x01` | Overlay | Alpha blend, unsorted pass | Alpha blend | OPAQUE | 239 |
| `0x02` | Additive | `SRC_ALPHA, ONE`, depth-sorted | Additive | OPAQUE | 601 |
| `0x03` | Scunge | Alpha blend, unsorted pass | Alpha blend | unknown → opaque + warning | 3 |
| `0x04` | Cutout | Opaque pass | Alpha clip | MASK | 285 |
| `0x05` | SoftEdge | Opaque pass | Alpha blend | MASK | 915 |
| `0x06` | Blended | `SRC_ALPHA, ONE_MINUS_SRC_ALPHA`, depth-sorted | Alpha blend | BLEND | 934 |
| `0x07` | BakedOnly | Not drawn by the Tie renderer | Opaque | unknown | 0 |
| `0x08` | LitOnly | Not drawn by the Tie renderer | Opaque | unknown | 0 |

The last column counts the `0x5000` shader records of all 20 levels on our
disc (10,521 in total).

- Confidence differs by engine. For the newer engine, the treatment is
  marked PROVEN in the QForce EBOOT. For the old engine, the byte-to-name
  mapping is "reported by the user after independent investigation". The
  exact blending for Scunge, SoftEdge, Blended, BakedOnly, and LitOnly is
  marked unconfirmed.
- ReLunacy's own renderer (`MaterialReader.ToRenderMode`) draws SoftEdge as
  an alpha blend, although the EBOOT note puts it in the opaque pass. Our
  MASK mapping for `0x05` agrees with the opaque-pass reading. Keep it.
- Overlay and Additive cover 840 shaders, or 8% of the corpus. They are the
  main visible gain from this item.
- Adaptation, in
  [ratchet_tod/reader.py](../insomniac_asset_io/games/ratchet_tod/reader.py)
  (rendering mode at shader `+0x11`):
  - map `0x01`, `0x03`, and `0x06` to BLEND;
  - add an ADDITIVE alpha mode, built in Blender as an Add Shader of
    Transparent and Emission, or with blended rendering;
  - `0x07`/`0x08` do not occur in TOD's shader table, so they need no
    mapping yet;
  - keep the raw byte as a material property.

  Check whether the RFoM `0x5001` material record and the R2/R3 records
  carry the same byte, and share the mapping only where they do.
- Validate: build review scenes containing decals, glass, and glow cards,
  and inspect the three Scunge shaders (meridian_city, sargasso, and
  stratus_city).

## 5. TOD gameplay context families

**Status:** done; see the gameplay context section of
[ratchet_tod.md](formats/ratchet_tod.md). Corrections from our disc:

- A Clue record is `{u32 volume pointer, u32 payload pointer, u32 payload
  size, u32 class number}`. It holds a pointer to the volume record, not an
  index.
- An area record is `{u32 path-list pointer, u32 volume-list pointer, u16
  path count, u16 volume count, u32 0}`.
- The volume shape is the byte at `+0x80`; the other bytes to `+0x90` are 0.
- The unit size is settled: stored axes are half-extents (the unit shape
  spans -1..1). Hazard Clue volumes enclose 3,015 lava faces at 1.0 against
  13 at 0.5. The cylinder and capsule axis is still unverified.
- Path totals equal the open polyline length, with or without flag `0x100`.

ReLunacy sources: `docs/reverse/tod_volumes_clues_paths.md` and
`Loading/Readers/OldGameplayReader.cs`. All values are big-endian; record
pointers are absolute offsets in `gameplay.dat`.

| Family | Section | Stride | Layout |
|---|---|---|---|
| Volumes | `0x7740` | `0x90` | `+0x00` row-major 4×4 (rows 0–2 scaled axes, row 3 translation); `+0x40` cached inverse 3×3; `+0x70` Euler cache; `+0x80` shape: 0 box, 1 cylinder, 2 sphere, 3 capsule |
| Paths | `0x7700` + points in `0x7710` | `0x10` | `{ptr firstPoint, u32 count, f32 totalLength, u32 flags}`; each point is `x, y, z, cumulativeLength`; `0x100` flag meaning open |
| Clues | `0x7780` | `0x10` | volume index plus class data (class number at `+0x0C`); 24 TOD classes, e.g. ClueFallToDeath, ClueGrindRail, ClueWater |
| Controllers | `0x77C0` | `0x30` | `+0x00` mini-IGHW pointer, `+0x04` position, `+0x10` rotation, `+0x1C` scale, `+0x28` payload size |
| Areas | `0x7940` (+`0x7950`) | `0x10` | `u16` path and volume index lists with their counts |
| Pods | `0x7900` (+`0x7910`) | `0x10` | packed Moby handles `((i+1)<<16)\|i` |
| Scents | `0x7980` (+`0x7990`) | `0x10` | Clue handles `0x10000\|i` |

- ReLunacy reports proven counts on 6,807 TOD and 1,392 QfB volumes. The
  unit size of the shapes (0.5 or 1.0) is still open. Use one documented
  convention and record it in metadata.
- Cylinder and capsule axes are local Y. Cylinders can be elliptical
  because the scale is applied per axis.
- Adaptation: reuse the RFoM context pattern of non-rendering curves and
  empties in their own Collection. Volumes become wire primitives with the
  object scale taken from the row lengths. Paths become poly curves.
  Clues, areas, pods, and scents carry their memberships as custom
  properties. Combined with items 1 and 3, every object gets a retail name
  and a class.

## 6. Old-engine baked lighting

**Status:** done for TOD baked colour; see the baked lightmaps section of
[ratchet_tod.md](formats/ratchet_tod.md). Corrections from our disc:

- The Tie lightmap-UV stream starts at the end of the vertex block computed
  from the meshes, `vertex start + max((index + count) * 0x14)`, not at the
  header's `+0x18` value, which overruns on 1,074 Metropolis meshes.
- The light scales are kept as metadata only. The direction pool and RFoM
  (`0x5500`) are not decoded yet.

ReLunacy sources: `Loading/Vertices/TieLightmapUV.cs`,
`Loading/Meshes/OldTiePrimitiveSemantics.cs`,
`Loading/Objects/Instances/TieInstance.cs`,
`Loading/Objects/UFragMetadata.cs`,
`Loading/Readers/FallOfManLightmaps.cs`, and
`Assets/Lighting/FallOfManBake.cs`.

**Tools of Destruction**

- `main.dat` holds `0x5400` (baked colour) and `0x5410` (light direction)
  texture pools. They use the same `0x20`-byte RSX descriptor as `0x5200`.
- A Tie instance's `u16` at record `+0x5A` indexes those pools; `0xFFFF`
  means none. On metropolis, 1,728 of 4,848 Tie instances carry an index,
  and every index is distinct. UFrags carry their own lightmap index.
- The lightmap UVs are a per-mesh HALF2 stream with a 4-byte stride. It
  starts after the Tie's vertex block at `TieMetadataOld +0x18`, plus
  `verticesIndex × 4`. The gate is the `0x3300` primitive's `+0x0E`; the
  index is `+0x06`.
- All placements of a Tie share one unwrap; each lit placement has its own
  texture. In Blender this means instance-specific materials, or a
  per-object attribute that selects an image.
- Uncertain: the scale at Tie instance `+0x60` is STRONG, not PROVEN. Some
  unlit primitives bind a per-vertex UNORM8×4 prelight instead, and that
  routing is still open.

**Resistance: Fall of Man**

- Section `0x5500` holds descriptors with stride `0x20`. When
  `flags & 3 == 1`, the pixels are in `ps3leveltexs.dat`. When it is `2`,
  they are in `main.dat` section `0x9950`, which is shared with the cubemaps.
- `FallOfManBake` gives two unsigned chroma bytes per vertex plus a per-draw
  scale (`c[5].z`). Tie placements carry a four-byte prelight stream.

**Adaptation**: extend the baked-lightmap node graph built for R3 so that
the old-engine readers can feed it. Start with TOD terrain (UFrags), then
Ties. Record unproven scales as metadata and do not apply them.

**Cross-check for R2/R3**: ReLunacy's newer-engine work (QForce EBOOT;
`Loading/Objects/Instances/TieInstance.cs` and
`Loading/Objects/NewEngineBakedLighting.cs`) puts
the per-placement Tie light scale at record `+0x64/+0x66/+0x68`, uploaded as
`c[4] = (a, b, c, c/3) × ScaleUnit`. ReLunacy found that a fixed scale made
a tenth of lit Ties 4.4× too bright. Check whether our R3 compact-atlas
Tie lighting reads an equivalent field.

## 7. Environment cubemaps

**Status:** done for TOD and RFoM. The layout held on all 20 TOD levels and
the RFoM levels checked; face orientation was confirmed by edge
continuity. The faces are HDR (alpha is a shared exponent, per ReLunacy's
`Assets/Cubemaps/Cubemap.cs`), and its scale and bias are still unknown.

ReLunacy source: `Loading/Readers/CubemapReader.cs`.

- TOD stores cubemaps in `main.dat` section `0x5920`, using the shared
  `0x20`-byte RSX descriptor. LOCAL offsets are relative to `textures.dat`;
  MAIN offsets are relative to `main.dat` section `0x9650`.
- There are 6 faces, ordered `+X, -X, +Y, -Y, +Z, -Z`. They are stored face
  by face, each with its complete mip chain, padded to a 128-byte stride.
- The `CELL_GCM_TEXTURE_LN` bit applies to each mip. Swizzled mips are
  Morton-decoded at their own dimensions.
- RFoM uses RGB565 cubes in `0x9950`: 64×64 with six mips, `0x2B00` bytes
  per face and `0x10200` per cube.
- Adaptation: decode with our existing RSX and Morton paths. Store each cube
  as six images, plus an optional equirectangular conversion so it can drive
  the World background or a reflection probe.

## 8. Fog, bloom, and lighting environment

**Status:** done for TOD and RFoM; see
[ratchet_tod.md](formats/ratchet_tod.md) and
[resistance_fom.md](formats/resistance_fom.md). All offsets below held on
every TOD level and on the RFoM levels checked. Light directions point
towards the light. The `0x8C00`/`0x8D00` light volumes and voxel grids are
not decoded.

ReLunacy sources: `Assets/Lighting/LevelFog.cs`,
`Loading/Readers/LightingEnvironmentReader.cs`.

- Fog lives in `main.dat` `0x7100`, one `0xF0` record:
  - colour at `+0x40..+0x48`;
  - near, far, and amount at `+0x4C..+0x54`;
  - bloom parameters at `+0x70..+0x8C`.

  The runtime computes `scale = amount / (far − near)`. Which fog
  coordinate reaches the GPU is still open, so treat these values as
  authored values, not an exact visual match.
- The lighting environment is `0x8B00`, one `0x80` root record. It holds
  the global ambient colour and two directional lights (colour and
  direction), and it points to `0x8C00` spatial volumes with voxel data in
  `0x8D00`. RFoM uses `0x8A00` instead.
- Adaptation: record fog and bloom on the level root as custom properties,
  with an optional World mist and volume setup. Create the two directional
  lights as Sun lamps and set the ambient as the World colour. Keep the
  voxel grid as metadata only.

## 9. UV animation

**Status:** done for TOD. TOD does not use the `0x60` record quoted below:
its `0x5C00` is a `0x20`-byte header of UFrag, Tie, and Moby counts
followed by `0x40`-byte records, with scroll at `+0x10`/`+0x14` and the
rotation per frame at `+0x18`, about UV (0, 0) with no pivot. ReLunacy's
`UvAnimation.ReadOldEngine` documents this; the owner and frag pairing was
verified on all 20 levels (see [ratchet_tod.md](formats/ratchet_tod.md)).

ReLunacy source: `Assets/Geometry/UvAnimation.cs`.

- Section `0x5C00` holds `0x60`-byte records; `0x5C01` holds base UVs.
- `+0x00` is a tag: the high nibble means 0 Tie, 1 UFrag, 2 Moby, 3 skinned
  Moby.
- Other fields: `+0x02` vertex count, `+0x08` frag vertex offset, `+0x20`
  pivot, `+0x30` scroll per second, `+0x38` rotation per frame (radians).
- Runtime formula: rotate about the pivot by an angle that accumulates every
  frame, then add the accumulated scroll, wrapped to [-1, 1].
- The `0x60` record is proven on QForce, A4O, and ITN. A Crack in Time
  writes a `0x50` record that omits the pivot and the `+0x20..+0x2F` fields,
  so the layout varies by game. The comments call it the "ToD-era
  VertAnim", but the TOD record layout must be verified.
- Adaptation: add a Mapping node driven by `#frame` (location from scroll,
  rotation about the pivot), or bake keyframes. Keep the raw record as
  metadata.

## 10. Sky

ReLunacy source: `Loading/Readers/SkyReader.cs`.

- The sky definitions are in `main.dat` `0x8150` (`0x100` bytes).
- There are two shell families, `0x8160` and `0x8170`, plus cloud texture
  references.
- A shared shell mesh is stored in `vertices.dat` `0x9000`/`0x9100`.
- ReLunacy reads these as separate stages and does not build a generic
  skybox. Our import should do the same: build an unlit, non-collidable
  shell mesh in its own Collection, and keep the stage records as metadata.

## 11. TOD Tie shader keys and material vectors

ReLunacy source: `Loading/Shaders/TodTieShaders.cs`.

- The Tie fragment shader is chosen by a key of feature bits:
  - `0x01` parallax;
  - `0x04` cube reflection;
  - `0x08` normal map or directional bake;
  - `0x10` detail map.
- There is a baked (full) family and an unbaked (reduced, prelit) family.
  Two-layer shaders also bind a second material and a mask sampled at the
  lightmap UV.
- The material record maps to shader constant groups:
  - `+0x30` is a general vector;
  - `+0x40` is the reflection tint;
  - `+0x50` holds the parallax scale and bias and the detail tiling.

  ReLunacy proved this mapping by set equality with RPCS3-patched
  constants on 24 levels.
- Adaptation: use `+0x50` to set detail tiling where our detail graph
  already applies. Keep parallax and the two-layer blends as metadata until
  they are verified visually.

## 12–14. Small items

**Status:** all three are finished. Item 12 adds the
`insomniac_no_wall_jump` face attribute, item 13 needed no change, and
item 14 was fixed in the A4R4G4B4 decoder (see the correction above).

- **Collision flags** (`docs/reverse/collision.md`): the triangle flags
  are bits 27–31 of the face word, and flag bit 0 (word bit 27) means
  *no wall jump*. ReLunacy's fork traced this to the TOD wall-jump entry;
  the layout was first decoded by @sowelilili. TOD and QfB use flag values
  0–7. Our decoder already stores `word >> 27` as the `surface` face
  attribute, so this needs only a derived boolean attribute and a note in
  [ratchet_tod.md](formats/ratchet_tod.md) that this field holds flags.
  Material IDs 0–67 are now named from `kMaterialID` (see the item 3
  correction above).
- **Old Moby bangles** (`Loading/Objects/OldMoby.cs`): `u16 bangleCount` is
  at `+0x18` and the bangle array is at `+0x28`. Use this to corroborate
  our unverified TOD bangle semantics.
- **Texture formats** (`Loading/Textures/TextureFormat.cs`): ReLunacy's
  enum uses the old-engine 4-bit code `(bitfield >> 8) & 0xF`, the same
  value our TOD reader takes at texture `+0x06`. Our reader then looks the
  code up in the R2 `FORMAT_NAMES` table, which was built for the
  newer-engine byte. The two disagree on 16-bit formats:

  | Code | ReLunacy (old engine) | Our `FORMAT_NAMES` | TOD textures |
  |---|---|---|---|
  | `0x02` | — | RGB5A1 | 0 |
  | `0x03` | R5G6B5 | RGBA4 | 8 (one on each of eight levels) |
  | `0x04` | A1R5G5B5 | R5G6B5 | 2 (cragmite_ruins, metropolis) |
  | `0x09`, `0x0A` | BC4, BC5 | — | 0 |

  All three formats are 16 bpp, so sizes and mip offsets agree and nothing
  fails to load. Colours and alpha differ, however, and one side decodes
  these ten textures wrongly. Decode them both ways and compare, then give
  TOD its own code table rather than sharing the R2 one.
  [ratchet_tod.md](formats/ratchet_tod.md) lists R5G6B5 among the observed
  codes; correct it once the right mapping is known.

  RGBA16F (`0x9A`) and RGBA4 (`0x83`) are newer-engine bytes in ReLunacy's
  enum. `0x83` is already in our table; `0x9A` is not. Add it only if a
  corpus scan finds it on our R2/R3 discs.

## 15–16. Scope expansions

- **Quest for Booty** uses the same old-engine layouts as TOD. ReLunacy
  verified collision, volumes, paths, Clues, and controllers on all five
  levels. It is a PSN-only title, so it needs a non-ISO source path and its
  own build identity.
  - Level names collide between games (`level_transitions` exists in both
    TOD and QfB). ReLunacy settles this by scoring sibling level names
    (`GameLibraryScanner.MatchGameAmong`).
  - Only `level_transitions` ships an `instances.lua`. The other QfB
    levels have their own `debug.dat`.
- **Newer-engine Ratchet games** (ACiT, QForce, A4O, ITN) are closest to our
  R2/R3 adapter family. Ready-made leads:
  - navmesh in sections `0x19000`/`0x19200`;
  - water bodies in `0x6641` (`0x50`-byte records);
  - ACiT Moby collision in `0xD900`/`0xDA00`;
  - new-engine collision primitive sets in `0x2900`.

  The collision primitives are worth comparing with our R2/R3 SIMD
  primitive decoder. ReLunacy settles the capsule-versus-cylinder
  ambiguity using bounding-box tightness.

## Not recommended

- **Effects/VFX simulation** (`Assets/Effects/*`): the results depend on
  runtime phases that are not stored in the data. This conflicts with our
  rule of avoiding speculative material and runtime reconstruction.
- **Audio (Scream banks)**: outside the Blender importer's purpose.
- **Editor-only systems**: level writing, project files, the occlusion
  bake, gizmos, and the Lua and script tooling.
