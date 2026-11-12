from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.source import GameSource


@dataclass(frozen=True, slots=True)
class GameAdapter:
    id: str
    label: str
    engine: str
    create_reader: Callable[[GameSource], object]


def _create_resistance_2_reader(source: GameSource):
    from .resistance_2.reader import Resistance2Reader

    return Resistance2Reader(source)


GAME_ADAPTERS = {
    "RESISTANCE_2": GameAdapter(
        id="RESISTANCE_2",
        label="Resistance 2",
        engine="Insomniac Engine v2 (PS3)",
        create_reader=_create_resistance_2_reader,
    ),
}


def get_game_adapter(game_id: str) -> GameAdapter:
    return GAME_ADAPTERS[game_id]
