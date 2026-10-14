from __future__ import annotations

from fractions import Fraction

from .ir import Event, GeneratedEvent, InstrumentProfile, Pitch


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


def generated_from_source(
    events: list[Event],
    *,
    profile_min: int,
    profile_max: int,
    prefer_flats: bool,
    phrase_boundaries: set[Fraction] | None = None,
) -> tuple[list[GeneratedEvent], list[str]]:
    pitched_events = [event for event in events if event.pitch is not None]
    if not pitched_events:
        return [], []
    boundaries = phrase_boundaries or set()
    candidate_rows: list[list[int]] = []
    for event in pitched_events:
        original = event.pitch.midi
        candidates = [
            original + 12 * shift
            for shift in range(-8, 9)
            if profile_min <= original + 12 * shift <= profile_max
        ]
        if not candidates:
            raise ValueError(
                f"melody pitch {event.pitch.step}{event.pitch.octave} cannot fit output profile"
            )
        candidate_rows.append(candidates)

    # Keep octave displacement coherent across a phrase. Changing octave on
    # each note independently can invert the original contour at range edges.
    costs: list[list[tuple[float, int | None]]] = []
    first_original = pitched_events[0].pitch.midi
    costs.append([
        (abs(candidate - first_original) / 12 * 0.35, None)
        for candidate in candidate_rows[0]
    ])
    for index in range(1, len(pitched_events)):
        event = pitched_events[index]
        previous_event = pitched_events[index - 1]
        original_interval = event.pitch.midi - previous_event.pitch.midi
        at_boundary = (
            event.start in boundaries
            or previous_event.end in boundaries
            or event.start > previous_event.end
        )
        row: list[tuple[float, int | None]] = []
        for candidate in candidate_rows[index]:
            options = []
            for previous_index, previous_candidate in enumerate(candidate_rows[index - 1]):
                realized_interval = candidate - previous_candidate
                contour_changed = (
                    original_interval != 0
                    and realized_interval != 0
                    and (original_interval > 0) != (realized_interval > 0)
                )
                shift_change = abs(
                    (candidate - event.pitch.midi)
                    - (previous_candidate - previous_event.pitch.midi)
                ) / 12
                transition_cost = abs(realized_interval - original_interval) * 0.35
                transition_cost += shift_change * (0.8 if at_boundary else 4.0)
                if contour_changed:
                    transition_cost += 8.0
                options.append(
                    (
                        costs[index - 1][previous_index][0]
                        + transition_cost
                        + abs(candidate - event.pitch.midi) / 12 * 0.35,
                        previous_index,
                    )
                )
            row.append(min(options, key=lambda option: option[0]))
        costs.append(row)

    selected = [0] * len(pitched_events)
    selected[-1] = min(
        range(len(costs[-1])), key=lambda candidate_index: costs[-1][candidate_index][0]
    )
    for index in range(len(pitched_events) - 1, 0, -1):
        previous_index = costs[index][selected[index]][1]
        selected[index - 1] = 0 if previous_index is None else previous_index

    generated: list[GeneratedEvent] = []
    diagnostics: list[str] = []
    shifted = 0
    for event, candidate_row, selected_index in zip(
        pitched_events, candidate_rows, selected
    ):
        original = event.pitch.midi
        midi = candidate_row[selected_index]
        if midi != original:
            shifted += 1
        generated.append(
            GeneratedEvent(
                id=f"melody:{event.id}",
                start=event.start,
                duration=event.duration,
                pitch=Pitch.from_midi(midi, prefer_flats=prefer_flats),
                role="melody",
                source_ids=[event.id],
                rationale=["preserved primary melody", "octave adjusted" if midi != original else "original register"],
                tie_start=event.tie_start,
                tie_stop=event.tie_stop,
                notations=list(event.notations),
                written_type=event.written_type,
                written_dots=event.written_dots,
                time_modification=event.time_modification,
            )
        )
    if shifted:
        diagnostics.append(
            f"{shifted} melody events octave-shifted with phrase-coherent register optimization"
        )
    return generated, diagnostics

