"""Tools of Destruction animated UVs (``main.dat`` section ``0x5C00``).

A ``0x20``-byte header holds three counts (UFrag, Tie, and Moby records),
followed by ``0x40``-byte records in that order:

- ``+0x00`` ``u16`` tag; its high nibble is 1 UFrag, 0 Tie, 2 Moby, or
  3 skinned Moby.
- ``+0x02`` ``u16`` vertex count, rounded up to 4.
- ``+0x04`` ``u16`` owner: the UFrag ordinal (``0x6200``) or the Tie
  (``0x3400``) or Moby (``0xD100``) class index.
- ``+0x08`` ``u32`` frag: a Tie mesh's first vertex index or a Moby mesh's
  local vertex byte offset.
- ``+0x10``/``+0x14`` scroll in UV units per second; ``+0x18`` rotation in
  radians per frame at 60 Hz. The rest is runtime state.

Each frame the game adds the rotation to an angle and applies the rows
``(cos, sin)`` / ``(-sin, cos)`` about UV (0, 0), then adds the scroll
offset. ReLunacy (``Assets/Geometry/UvAnimation.cs``) published the layout
and the update. On all 20 levels of our disc every Tie record matches a
mesh's first vertex and rounded count (1,313), every UFrag record its
UFrag's rounded count (95), and every Moby record a mesh's local vertex
offset and rounded count (1,075).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import math

from ...core.binary import IghwFile
from ...core.errors import FormatError
from ..resistance_2.formats import UvAnimation

UV_ANIMATION_SECTION = 0x5C00
HEADER_SIZE = 0x20
RECORD_SIZE = 0x40
# The game advances rotation once per 60 Hz frame.
FRAMES_PER_SECOND = 60.0
MAX_RECORDS = 65_536
_KINDS = {1: "ufrag", 0: "tie", 2: "moby", 3: "moby"}


@dataclass(slots=True)
class TodUvAnimations:
    ufrags: dict[int, UvAnimation] = field(default_factory=dict)
    # Keyed by (Tie class index, mesh first vertex index).
    ties: dict[tuple[int, int], UvAnimation] = field(default_factory=dict)
    # Keyed by (Moby class index, mesh local vertex byte offset).
    mobys: dict[tuple[int, int], UvAnimation] = field(default_factory=dict)


def parse_uv_animations(main: IghwFile) -> TodUvAnimations:
    result = TodUvAnimations()
    section = main.section(UV_ANIMATION_SECTION)
    if section is None:
        return result
    size = section.count * section.length
    main.require(section.offset, size, purpose="UV animation section")
    if size < HEADER_SIZE:
        raise FormatError(f"{main.label}: UV animation header is truncated")
    counts = [main.u32(section.offset + index * 4) for index in range(3)]
    total = sum(counts)
    if total > MAX_RECORDS or HEADER_SIZE + total * RECORD_SIZE > size:
        raise FormatError(
            f"{main.label}: UV animation counts {counts} exceed the section"
        )
    expected = ["ufrag"] * counts[0] + ["tie"] * counts[1] + ["moby"] * counts[2]
    for index, family in enumerate(expected):
        base = section.offset + HEADER_SIZE + index * RECORD_SIZE
        kind = _KINDS.get(main.u16(base) >> 12)
        if kind != family:
            raise FormatError(
                f"{main.label}: UV animation record {index} has tag "
                f"{main.u16(base):#06x} in the {family} block"
            )
        scroll_u = main.f32(base + 0x10)
        scroll_v = main.f32(base + 0x14)
        rotation = main.f32(base + 0x18) * FRAMES_PER_SECOND
        if not all(math.isfinite(value) for value in (scroll_u, scroll_v, rotation)):
            raise FormatError(
                f"{main.label}: UV animation record {index} is not finite"
            )
        if not (scroll_u or scroll_v or rotation):
            continue
        # The game's rows (cos, sin) / (-sin, cos) rotate by -angle.
        animation = UvAnimation(scroll_u, scroll_v, -rotation)
        owner = main.u16(base + 4)
        frag = main.u32(base + 8)
        if family == "ufrag":
            result.ufrags.setdefault(owner, animation)
        elif family == "tie":
            result.ties.setdefault((owner, frag), animation)
        else:
            result.mobys.setdefault((owner, frag), animation)
    return result
