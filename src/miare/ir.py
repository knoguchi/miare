from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


@dataclass(frozen=True)
class Pitch:
    step: str
    octave: int
    alter: int = 0

    @property
    def midi(self) -> int:
        pitch_class = {
            "C": 0,
            "D": 2,
            "E": 4,
            "F": 5,
            "G": 7,
            "A": 9,
            "B": 11,
        }[self.step]
        return (self.octave + 1) * 12 + pitch_class + self.alter

    @classmethod
    def from_midi(cls, midi: int, *, prefer_flats: bool = False) -> Pitch:
        sharp_names = (
            ("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
            ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0),
        )
        flat_names = (
            ("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
            ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0),
        )
        step, alter = (flat_names if prefer_flats else sharp_names)[midi % 12]
        return cls(step=step, alter=alter, octave=midi // 12 - 1)


@dataclass
class Event:
    id: str
    part_id: str
    measure_index: int
    start: Fraction
    duration: Fraction
    pitch: Pitch | None
    voice: str = "1"
    staff: str = "1"
    tie_start: bool = False
    tie_stop: bool = False
    notations: list[str] = field(default_factory=list)
    written_type: str | None = None
    written_dots: int = 0
    time_modification: tuple[int, int] | None = None

    @property
    def end(self) -> Fraction:
        return self.start + self.duration

    @property
    def is_rest(self) -> bool:
        return self.pitch is None


@dataclass
class Measure:
    index: int
    number: str
    start: Fraction
    duration: Fraction
    beats: int
    beat_type: int
    implicit: bool = False

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class Part:
    id: str
    name: str
    events: list[Event] = field(default_factory=list)
    directions: list["Direction"] = field(default_factory=list)


@dataclass
class Direction:
    offset: Fraction
    kind: str
    value: str
    placement: str = "above"


@dataclass
class SourceScore:
    title: str
    parts: list[Part]
    measures: list[Measure]
    fifths: int = 0
    mode: str = "major"
    directions: list[Direction] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class Phrase:
    id: str
    start: Fraction
    end: Fraction
    apex_midi: int | None
    cadence: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class HarmonyCandidate:
    degree: int
    root_pc: int
    quality: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class HarmonicSlice:
    id: str
    start: Fraction
    duration: Fraction
    candidates: list[HarmonyCandidate]
    selected_degree: int

    @property
    def selected(self) -> HarmonyCandidate:
        return next(candidate for candidate in self.candidates if candidate.degree == self.selected_degree)


@dataclass
class MusicalLanguage:
    phrase_lengths: list[str]
    rhythmic_vocabulary: dict[str, int]
    accent_patterns: dict[str, int]
    motif_vocabulary: list[dict[str, Any]]
    non_chord_tone_ratio: float
    cadence_patterns: list[str]
    register_usage: dict[str, int | None]
    density_curve: list[float]
    unknown_fields: list[str]
    evidence: list[dict[str, Any]]


@dataclass
class SongIR:
    tonic_pc: int
    mode: str
    melody_part_id: str
    melody_voice: str
    melody: list[Event]
    structural_melody: list[Event]
    phrases: list[Phrase]
    harmony: list[HarmonicSlice]
    musical_language: MusicalLanguage
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class GeneratedEvent:
    id: str
    start: Fraction
    duration: Fraction
    pitch: Pitch
    role: str
    source_ids: list[str]
    rationale: list[str]
    tie_start: bool = False
    tie_stop: bool = False
    notations: list[str] = field(default_factory=list)
    written_type: str | None = None
    written_dots: int = 0
    time_modification: tuple[int, int] | None = None

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass(frozen=True)
class InstrumentProfile:
    id: str
    name: str
    min_midi: int
    max_midi: int
    preferred_min: int
    preferred_max: int
    clef_sign: str
    clef_line: int


@dataclass
class OutputVoice:
    id: str
    profile: InstrumentProfile
    events: list[GeneratedEvent]


@dataclass
class ArrangementIR:
    voices: list[OutputVoice]
    relationship: str
    diagnostics: list[str]
    provenance: list[dict[str, Any]]


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Fraction):
        return fraction_text(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
