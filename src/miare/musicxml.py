from __future__ import annotations

from fractions import Fraction
from math import lcm
from pathlib import Path
import xml.etree.ElementTree as ET

from .ir import (
    ArrangementIR,
    Direction,
    Event,
    GeneratedEvent,
    Measure,
    Part,
    Pitch,
    SourceScore,
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _local(child.tag) == name), None)


def _text(element: ET.Element | None, default: str = "") -> str:
    return default if element is None or element.text is None else element.text.strip()


def _int_text(element: ET.Element | None, default: int) -> int:
    text = _text(element)
    return default if not text else int(text)


def _pitch_from_note(note: ET.Element, chromatic_transpose: int) -> Pitch | None:
    pitch_element = _child(note, "pitch")
    if pitch_element is None:
        return None
    pitch = Pitch(
        step=_text(_child(pitch_element, "step"), "C"),
        alter=_int_text(_child(pitch_element, "alter"), 0),
        octave=_int_text(_child(pitch_element, "octave"), 4),
    )
    if chromatic_transpose:
        return Pitch.from_midi(pitch.midi + chromatic_transpose, prefer_flats=chromatic_transpose < 0)
    return pitch


def parse_musicxml(path: str | Path) -> SourceScore:
    path = Path(path)
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"cannot read MusicXML {path}: {error}") from error

    root = tree.getroot()
    if _local(root.tag) != "score-partwise":
        raise ValueError("only score-partwise MusicXML is supported")

    work = _child(root, "work")
    title = _text(_child(work, "work-title")) if work is not None else ""
    title = title or _text(_child(root, "movement-title")) or path.stem

    part_names: dict[str, str] = {}
    part_list = _child(root, "part-list")
    if part_list is not None:
        for score_part in _children(part_list, "score-part"):
            part_id = score_part.get("id", "")
            part_names[part_id] = _text(_child(score_part, "part-name"), part_id)

    diagnostics: list[str] = []
    parts: list[Part] = []
    measures: list[Measure] = []
    global_fifths = 0
    global_mode = "major"
    key_seen = False

    for part_index, part_element in enumerate(_children(root, "part")):
        part_id = part_element.get("id", f"P{part_index + 1}")
        part = Part(id=part_id, name=part_names.get(part_id, part_id))
        divisions = 1
        beats = 4
        beat_type = 4
        chromatic_transpose = 0
        running_start = Fraction(0)
        event_number = 0

        for measure_index, measure_element in enumerate(_children(part_element, "measure")):
            if part_index == 0:
                measure_start = running_start
            elif measure_index < len(measures):
                measure_start = measures[measure_index].start
                beats = measures[measure_index].beats
                beat_type = measures[measure_index].beat_type
            else:
                diagnostics.append(
                    f"{part_id}: extra measure {measure_index + 1} has no first-part timeline"
                )
                measure_start = running_start

            cursor = Fraction(0)
            furthest = Fraction(0)
            last_note_start = Fraction(0)

            for item in measure_element:
                name = _local(item.tag)
                if name == "attributes":
                    divisions = _int_text(_child(item, "divisions"), divisions)
                    if divisions <= 0:
                        raise ValueError(f"{part_id} measure {measure_index + 1}: divisions must be positive")
                    key = _child(item, "key")
                    if key is not None and part_index == 0:
                        new_fifths = _int_text(_child(key, "fifths"), global_fifths)
                        new_mode = _text(_child(key, "mode"), global_mode) or global_mode
                        if not key_seen:
                            global_fifths = new_fifths
                            global_mode = new_mode
                            key_seen = True
                        elif (new_fifths, new_mode) != (global_fifths, global_mode):
                            diagnostics.append(
                                "UNSUPPORTED-OUTPUT: key-signature changes are present; "
                                "the MVP analyzes the opening key only"
                            )
                    time = _child(item, "time")
                    if time is not None:
                        beats = _int_text(_child(time, "beats"), beats)
                        beat_type = _int_text(_child(time, "beat-type"), beat_type)
                    transpose = _child(item, "transpose")
                    if transpose is not None:
                        chromatic_transpose = _int_text(
                            _child(transpose, "chromatic"), chromatic_transpose
                        )
                        diagnostics.append(
                            f"{part_id}: transposition was converted to concert pitch"
                        )
                elif name == "backup":
                    cursor -= Fraction(_int_text(_child(item, "duration"), 0), divisions)
                elif name == "forward":
                    cursor += Fraction(_int_text(_child(item, "duration"), 0), divisions)
                    furthest = max(furthest, cursor)
                elif name == "note":
                    if _child(item, "grace") is not None:
                        diagnostics.append(
                            f"{part_id} measure {measure_index + 1}: "
                            "grace note omitted from arranged output"
                        )
                        continue
                    duration_node = _child(item, "duration")
                    if duration_node is None:
                        diagnostics.append(
                            f"UNSUPPORTED-OUTPUT: {part_id} measure {measure_index + 1}: "
                            "note without duration was skipped"
                        )
                        continue
                    duration = Fraction(_int_text(duration_node, 0), divisions)
                    is_chord_tone = _child(item, "chord") is not None
                    relative_start = last_note_start if is_chord_tone else cursor
                    if not is_chord_tone:
                        last_note_start = cursor
                        cursor += duration
                    furthest = max(furthest, relative_start + duration, cursor)
                    event_number += 1
                    tie_types = {
                        tie.get("type", "") for tie in _children(item, "tie")
                    }
                    if _child(item, "lyric") is not None:
                        diagnostics.append(
                            "UNSUPPORTED-OUTPUT: lyrics are not yet carried to arranged output"
                        )
                    written_type = _text(_child(item, "type")) or None
                    written_dots = len(_children(item, "dot"))
                    tm_el = _child(item, "time-modification")
                    time_modification: tuple[int, int] | None = None
                    if tm_el is not None:
                        actual = _int_text(_child(tm_el, "actual-notes"), 0)
                        normal = _int_text(_child(tm_el, "normal-notes"), 0)
                        if actual and normal:
                            time_modification = (actual, normal)
                    note_notations: list[str] = []
                    notations_elem = _child(item, "notations")
                    if notations_elem is not None:
                        for child in notations_elem:
                            child_name = _local(child.tag)
                            if child_name == "tied":
                                pass
                            elif child_name == "slur":
                                slur_type = child.get("type", "")
                                if slur_type in ("start", "stop"):
                                    note_notations.append(f"slur-{slur_type}")
                            elif child_name == "articulations":
                                for art in child:
                                    art_name = _local(art.tag)
                                    if art_name in (
                                        "staccato", "accent", "tenuto",
                                        "strong-accent", "staccatissimo", "spiccato",
                                    ):
                                        note_notations.append(art_name)
                            elif child_name == "ornaments":
                                for orn in child:
                                    orn_name = _local(orn.tag)
                                    if orn_name in ("trill-mark", "turn", "mordent",
                                                    "inverted-turn", "inverted-mordent"):
                                        note_notations.append(orn_name)
                            elif child_name == "fermata":
                                note_notations.append("fermata")
                            elif child_name == "tuplet":
                                tuplet_type = child.get("type", "")
                                if tuplet_type in ("start", "stop"):
                                    note_notations.append(f"tuplet-{tuplet_type}")
                    part.events.append(
                        Event(
                            id=f"{part_id}:m{measure_index + 1}:e{event_number}",
                            part_id=part_id,
                            measure_index=measure_index,
                            start=measure_start + relative_start,
                            duration=duration,
                            pitch=_pitch_from_note(item, chromatic_transpose),
                            voice=_text(_child(item, "voice"), "1"),
                            staff=_text(_child(item, "staff"), "1"),
                            tie_start="start" in tie_types,
                            tie_stop="stop" in tie_types,
                            notations=note_notations,
                            written_type=written_type,
                            written_dots=written_dots,
                            time_modification=time_modification,
                        )
                    )
                elif name == "direction":
                    placement = item.get("placement", "above")
                    abs_offset = measure_start + cursor
                    for dir_type in _children(item, "direction-type"):
                        for child in dir_type:
                            child_name = _local(child.tag)
                            if child_name == "dynamics":
                                for dyn_child in child:
                                    part.directions.append(
                                        Direction(abs_offset, "dynamics", _local(dyn_child.tag), placement)
                                    )
                            elif child_name == "wedge":
                                part.directions.append(
                                    Direction(abs_offset, "wedge", child.get("type", "crescendo"), placement)
                                )
                            elif child_name == "metronome":
                                beat_unit = _text(_child(child, "beat-unit"), "quarter")
                                per_minute = _text(_child(child, "per-minute"), "")
                                if per_minute:
                                    part.directions.append(
                                        Direction(abs_offset, "metronome", f"{beat_unit}={per_minute}", placement)
                                    )
                            elif child_name == "words":
                                text = _text(child, "")
                                if text:
                                    part.directions.append(
                                        Direction(abs_offset, "words", text, placement)
                                    )
                elif name == "harmony":
                    diagnostics.append(
                        "UNSUPPORTED-OUTPUT: written chord symbols are present; "
                        "the MVP currently infers harmony"
                    )
                elif name == "barline" and _child(item, "repeat") is not None:
                    diagnostics.append(
                        "UNSUPPORTED-OUTPUT: repeat barlines are present; "
                        "the MVP writes linear measures"
                    )

            nominal_duration = Fraction(beats * 4, beat_type)
            implicit = measure_element.get("implicit") == "yes"
            duration = furthest if implicit and furthest > 0 else nominal_duration
            if part_index == 0:
                measures.append(
                    Measure(
                        index=measure_index,
                        number=measure_element.get("number", str(measure_index + 1)),
                        start=measure_start,
                        duration=duration,
                        beats=beats,
                        beat_type=beat_type,
                        implicit=implicit,
                    )
                )
                running_start = measure_start + duration
            elif measure_index < len(measures) and furthest > measures[measure_index].duration:
                diagnostics.append(
                    f"{part_id} measure {measure_index + 1}: content exceeds the first-part measure"
                )

        part.events.sort(key=lambda event: (event.start, event.voice, event.pitch.midi if event.pitch else -1))
        parts.append(part)

    if not parts or not measures:
        raise ValueError("MusicXML contains no readable parts or measures")
    if not any(event.pitch is not None for part in parts for event in part.events):
        raise ValueError("MusicXML contains no pitched notes")

    # Deduplicate directions across parts by (offset, kind, value)
    seen_directions: set[tuple] = set()
    all_directions: list[Direction] = []
    for part in parts:
        for direction in part.directions:
            key = (direction.offset, direction.kind, direction.value)
            if key not in seen_directions:
                seen_directions.add(key)
                all_directions.append(direction)
    all_directions.sort(key=lambda d: d.offset)

    return SourceScore(
        title=title,
        parts=parts,
        measures=measures,
        fifths=global_fifths,
        mode=global_mode,
        directions=all_directions,
        diagnostics=list(dict.fromkeys(diagnostics)),
    )


_STANDARD_DURATIONS: dict[Fraction, tuple[str, int]] = {
    Fraction(8): ("breve", 0),
    Fraction(6): ("whole", 1),
    Fraction(4): ("whole", 0),
    Fraction(3): ("half", 1),
    Fraction(2): ("half", 0),
    Fraction(3, 2): ("quarter", 1),
    Fraction(1): ("quarter", 0),
    Fraction(3, 4): ("eighth", 1),
    Fraction(1, 2): ("eighth", 0),
    Fraction(3, 8): ("16th", 1),
    Fraction(1, 4): ("16th", 0),
    Fraction(3, 16): ("32nd", 1),
    Fraction(1, 8): ("32nd", 0),
    Fraction(3, 32): ("64th", 1),
    Fraction(1, 16): ("64th", 0),
}
# Tolerance for integer rounding when sources use high division values (e.g. 1024)
_DURATION_TOL = Fraction(1, 1024)


def _simple_duration_type(duration: Fraction) -> tuple[str, int] | None:
    exact = _STANDARD_DURATIONS.get(duration)
    if exact is not None:
        return exact
    for standard, result in _STANDARD_DURATIONS.items():
        if abs(duration - standard) <= _DURATION_TOL:
            return result
    return None


def _duration_notation(
    duration: Fraction,
) -> tuple[str, int, tuple[int, int] | None] | None:
    simple = _simple_duration_type(duration)
    if simple is not None:
        return simple[0], simple[1], None
    # Try common tuplet ratios (actual:normal). 3:2 is tried first so it takes
    # priority over equivalent 6:4 for triplets.
    for actual, normal in [(3, 2), (4, 3), (5, 4), (7, 4), (6, 5), (7, 6), (9, 8)]:
        scaled = duration * Fraction(actual, normal)
        simple = _simple_duration_type(scaled)
        if simple is not None:
            return simple[0], simple[1], (actual, normal)
    return None


def _add_note(
    measure_element: ET.Element,
    pitch: Pitch | None,
    duration: Fraction,
    divisions: int,
    *,
    tie_start: bool = False,
    tie_stop: bool = False,
    notations: list[str] | None = None,
    written_type: str | None = None,
    written_dots: int = 0,
    time_modification: tuple[int, int] | None = None,
) -> None:
    note = ET.SubElement(measure_element, "note")
    if pitch is None:
        ET.SubElement(note, "rest")
    else:
        pitch_element = ET.SubElement(note, "pitch")
        ET.SubElement(pitch_element, "step").text = pitch.step
        if pitch.alter:
            ET.SubElement(pitch_element, "alter").text = str(pitch.alter)
        ET.SubElement(pitch_element, "octave").text = str(pitch.octave)
    ET.SubElement(note, "duration").text = str(int(duration * divisions))
    ET.SubElement(note, "voice").text = "1"
    # Prefer the type stored from the source; fall back to derivation for generated notes.
    if written_type is not None:
        type_name: str | None = written_type
        dots = written_dots
        tm = time_modification
    else:
        derived = _duration_notation(duration)
        if derived is not None:
            type_name, dots, tm = derived
        else:
            type_name, dots, tm = None, 0, None
    if type_name is not None:
        ET.SubElement(note, "type").text = type_name
        for _ in range(dots):
            ET.SubElement(note, "dot")
        if tm is not None:
            actual_notes, normal_notes = tm
            time_element = ET.SubElement(note, "time-modification")
            ET.SubElement(time_element, "actual-notes").text = str(actual_notes)
            ET.SubElement(time_element, "normal-notes").text = str(normal_notes)
    if tie_stop:
        ET.SubElement(note, "tie", {"type": "stop"})
    if tie_start:
        ET.SubElement(note, "tie", {"type": "start"})
    if pitch is not None and (tie_start or tie_stop or notations):
        notations_elem = ET.SubElement(note, "notations")
        if tie_stop:
            ET.SubElement(notations_elem, "tied", {"type": "stop"})
        if tie_start:
            ET.SubElement(notations_elem, "tied", {"type": "start"})
        if notations:
            arts: ET.Element | None = None
            orns: ET.Element | None = None
            for tag in notations:
                if tag.startswith("slur-"):
                    ET.SubElement(notations_elem, "slur",
                                  {"type": tag[5:], "number": "1"})
                elif tag in ("staccato", "accent", "tenuto",
                             "strong-accent", "staccatissimo", "spiccato"):
                    if arts is None:
                        arts = ET.SubElement(notations_elem, "articulations")
                    ET.SubElement(arts, tag)
                elif tag in ("trill-mark", "turn", "mordent",
                             "inverted-turn", "inverted-mordent"):
                    if orns is None:
                        orns = ET.SubElement(notations_elem, "ornaments")
                    ET.SubElement(orns, tag)
                elif tag == "fermata":
                    ET.SubElement(notations_elem, "fermata")
                elif tag.startswith("tuplet-"):
                    ET.SubElement(notations_elem, "tuplet",
                                  {"type": tag[7:], "number": "1"})


def _segments_for_measure(
    events: list[GeneratedEvent], measure: Measure
) -> list[tuple[Fraction, Fraction, Pitch, bool, bool]]:
    segments: list[tuple[Fraction, Fraction, Pitch, bool, bool]] = []
    for event in events:
        segment_start = max(event.start, measure.start)
        segment_end = min(event.end, measure.end)
        if segment_end <= segment_start:
            continue
        segments.append(
            (
                segment_start - measure.start,
                segment_end - segment_start,
                event.pitch,
                event.tie_start or event.end > measure.end,
                event.tie_stop or event.start < measure.start,
            )
        )
    return sorted(segments, key=lambda item: item[0])


def write_arrangement(
    path: str | Path,
    source: SourceScore,
    arrangement: ArrangementIR,
) -> None:
    denominators = [measure.duration.denominator for measure in source.measures]
    for voice in arrangement.voices:
        for event in voice.events:
            denominators.extend((event.start.denominator, event.duration.denominator))
    divisions = 1
    for denominator in denominators:
        divisions = lcm(divisions, denominator)
    if divisions > 4096:
        raise ValueError(f"required MusicXML divisions is too large: {divisions}")

    root = ET.Element("score-partwise", {"version": "4.0"})
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = f"{source.title} — miare arrangement"
    identification = ET.SubElement(root, "identification")
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = "miare 0.1.0"

    part_list = ET.SubElement(root, "part-list")
    for index, voice in enumerate(arrangement.voices, 1):
        score_part = ET.SubElement(part_list, "score-part", {"id": f"P{index}"})
        ET.SubElement(score_part, "part-name").text = voice.profile.name

    for part_index, voice in enumerate(arrangement.voices, 1):
        part_element = ET.SubElement(root, "part", {"id": f"P{part_index}"})
        previous_meter: tuple[int, int] | None = None
        for measure in source.measures:
            attributes_dict = {"number": measure.number}
            if measure.implicit:
                attributes_dict["implicit"] = "yes"
            measure_element = ET.SubElement(part_element, "measure", attributes_dict)
            meter = (measure.beats, measure.beat_type)
            if measure.index == 0 or meter != previous_meter:
                attributes = ET.SubElement(measure_element, "attributes")
                ET.SubElement(attributes, "divisions").text = str(divisions)
                if measure.index == 0:
                    key = ET.SubElement(attributes, "key")
                    ET.SubElement(key, "fifths").text = str(source.fifths)
                    ET.SubElement(key, "mode").text = source.mode
                time = ET.SubElement(attributes, "time")
                ET.SubElement(time, "beats").text = str(measure.beats)
                ET.SubElement(time, "beat-type").text = str(measure.beat_type)
                clef = ET.SubElement(attributes, "clef")
                ET.SubElement(clef, "sign").text = voice.profile.clef_sign
                ET.SubElement(clef, "line").text = str(voice.profile.clef_line)
            previous_meter = meter

            # Emit directions (dynamics, wedges, tempo, text) at their score positions
            for direction in source.directions:
                if measure.start <= direction.offset < measure.end:
                    dir_elem = ET.SubElement(measure_element, "direction",
                                             {"placement": direction.placement})
                    dir_type = ET.SubElement(dir_elem, "direction-type")
                    if direction.kind == "dynamics":
                        dyn = ET.SubElement(dir_type, "dynamics")
                        ET.SubElement(dyn, direction.value)
                    elif direction.kind == "wedge":
                        ET.SubElement(dir_type, "wedge", {"type": direction.value})
                    elif direction.kind == "metronome":
                        beat_unit, per_minute = direction.value.split("=", 1)
                        metro = ET.SubElement(dir_type, "metronome")
                        ET.SubElement(metro, "beat-unit").text = beat_unit
                        ET.SubElement(metro, "per-minute").text = per_minute
                    elif direction.kind == "words":
                        ET.SubElement(dir_type, "words").text = direction.value
                    offset_ticks = int((direction.offset - measure.start) * divisions)
                    if offset_ticks:
                        ET.SubElement(dir_elem, "offset").text = str(offset_ticks)

            # Per-onset lookup for melody note metadata (notations, written type).
            # Keyed by the segment's absolute start (covers both event-start and
            # tie-continuation segments via max(event.start, measure.start)).
            notation_by_onset: dict[Fraction, list[str]] = {}
            type_by_onset: dict[Fraction, tuple[str | None, int, tuple[int, int] | None]] = {}
            for event in voice.events:
                seg_onset = max(event.start, measure.start)
                if seg_onset < measure.end and event.end > measure.start:
                    if event.notations:
                        notation_by_onset[seg_onset] = event.notations
                    if event.written_type is not None:
                        type_by_onset[seg_onset] = (
                            event.written_type, event.written_dots, event.time_modification
                        )
            cursor = Fraction(0)
            for start, duration, pitch, tie_start, tie_stop in _segments_for_measure(
                voice.events, measure
            ):
                if start < cursor:
                    arrangement.diagnostics.append(
                        f"{voice.id} measure {measure.number}: overlapping event omitted"
                    )
                    continue
                if start > cursor:
                    _add_note(measure_element, None, start - cursor, divisions)
                abs_onset = measure.start + start
                wt, wd, wtm = type_by_onset.get(abs_onset, (None, 0, None))
                _add_note(
                    measure_element,
                    pitch,
                    duration,
                    divisions,
                    tie_start=tie_start,
                    tie_stop=tie_stop,
                    notations=notation_by_onset.get(abs_onset),
                    written_type=wt,
                    written_dots=wd,
                    time_modification=wtm,
                )
                cursor = start + duration
            if cursor < measure.duration:
                _add_note(measure_element, None, measure.duration - cursor, divisions)
            if measure.index == len(source.measures) - 1:
                barline = ET.SubElement(measure_element, "barline", {"location": "right"})
                ET.SubElement(barline, "bar-style").text = "light-heavy"

    ET.indent(root, space="  ")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def validate_round_trip(
    path: str | Path,
    expected_measures: list[Measure],
    expected_parts: int,
    expected_arrangement: ArrangementIR | None = None,
) -> list[str]:
    parsed = parse_musicxml(path)
    errors: list[str] = []
    if len(parsed.parts) != expected_parts:
        errors.append(f"expected {expected_parts} parts, read back {len(parsed.parts)}")
    if len(parsed.measures) != len(expected_measures):
        errors.append(
            f"expected {len(expected_measures)} measures, read back {len(parsed.measures)}"
        )
    for expected, actual in zip(expected_measures, parsed.measures):
        if expected.duration != actual.duration:
            errors.append(
                f"measure {expected.number}: expected duration {expected.duration}, "
                f"read back {actual.duration}"
            )
    if expected_arrangement is not None and len(parsed.parts) == len(expected_arrangement.voices):
        for part, voice in zip(parsed.parts, expected_arrangement.voices):
            expected_notes: list[tuple[Fraction, Fraction, int, bool, bool]] = []
            for measure in expected_measures:
                for start, duration, pitch, tie_start, tie_stop in _segments_for_measure(
                    voice.events, measure
                ):
                    expected_notes.append(
                        (
                            measure.start + start,
                            duration,
                            pitch.midi,
                            tie_start,
                            tie_stop,
                        )
                    )
            actual_notes = [
                (
                    event.start,
                    event.duration,
                    event.pitch.midi,
                    event.tie_start,
                    event.tie_stop,
                )
                for event in part.events
                if event.pitch is not None
            ]
            if expected_notes != actual_notes:
                errors.append(
                    f"{voice.id}: pitch/time/duration/tie content changed during round-trip"
                )
    return errors
