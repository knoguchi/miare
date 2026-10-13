from __future__ import annotations

from .ir import InstrumentProfile


PROFILES = {
    "violin": InstrumentProfile("violin", "Violin", 55, 103, 60, 96, "G", 2),
    "viola": InstrumentProfile("viola", "Viola", 48, 88, 53, 81, "C", 3),
    "cello": InstrumentProfile("cello", "Violoncello", 36, 76, 43, 69, "F", 4),
    "flute": InstrumentProfile("flute", "Flute", 60, 96, 62, 91, "G", 2),
    "bassoon": InstrumentProfile("bassoon", "Bassoon", 34, 75, 40, 67, "F", 4),
}


def get_profile(name: str) -> InstrumentProfile:
    try:
        return PROFILES[name.lower()]
    except KeyError as error:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown instrument profile {name!r}; choose one of: {choices}") from error

