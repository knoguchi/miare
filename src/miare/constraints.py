"""Voice-leading rules both generators need, one copy each.

DESIGN.md §17 lists `constraints` as a module boundary: hard constraints,
soft preferences and their diagnostics.  DESIGN.md §3.5 separates the two —
a **constraint** violation blocks output, a **preference** only ranks the
solutions that are already legal.

`generation.py` (support) and `counterpoint.py` (countermelody) each describe
how a generated voice may move against the primary melody.  Four of those
relationships are the same musical rule, and three had drifted apart in
strength before this module existed:

| Rule             | counterpoint.py | generation.py   | resolved to    |
| ---------------- | --------------- | --------------- | -------------- |
| leap over octave | hard rejection  | `-4.0` penalty  | hard rejection |
| parallel perfect | hard rejection  | `-inf` via DP   | hard rejection |
| contrary motion  | `+1.1`          | `+1.2`          | `+1.1`         |
| voice crossing   | `-3.0`          | `-5.0`          | `-3.0`         |

The two generators have different shapes — a gate plus critics in
`counterpoint.py`, closures inside a Viterbi DP in `generation.py` — so this
module owns the *predicates and weights*, not the control flow.  Each
generator keeps its own way of acting on a violation: a `Rejection` from the
hard-constraint gate, or `float("-inf")` that the DP's own gate prunes.

Pure: no I/O, no RNG, no state.  Imports nothing from the package, so it
cannot acquire a cycle with a generator.

Everything here compares *motion* (a signed semitone delta) or an *interval
class* (a semitone distance mod 12), never a pitch name, so a rule reads the
same in any key.
"""
from __future__ import annotations


# Unison/octave and the perfect fifth, as interval classes.  Parallel motion
# between two of these is the classic prohibition: the voices momentarily fuse
# into one, so the second voice stops being heard as independent.
PERFECT_INTERVAL_CLASSES = {0, 7}


# Preference weights.  Both generators add these to a candidate's score; the
# scales are comparable because both score in the same rough ±5 band per rule.
CONTRARY_MOTION_BONUS = 1.1
VOICE_CROSSING_PENALTY = -3.0

# The widest leap a generated voice may make, in semitones.  An octave is
# singable and playable; wider than that stops reading as one line.
MAX_LEAP_SEMITONES = 12


# --- Constraints (DESIGN.md §3.5: a violation blocks output) ---------------

def is_leap_over_octave(motion: int) -> bool:
    """Does this move leap further than an octave?

    `motion` is the signed semitone delta from the previous pitch of the same
    voice.  A constraint, not a preference: scoring it away (the old
    `generation.py` `-4.0`) let a strong enough emission score buy a
    thirteenth, which no instrument should be asked to play as one line.
    """
    return abs(motion) > MAX_LEAP_SEMITONES


def is_parallel_perfect(
    *,
    previous_interval_class: int | None,
    interval_class: int,
    melody_motion: int,
    voice_motion: int,
) -> bool:
    """Do the two voices move in similar motion between two perfect intervals?

    `previous_interval_class` is `None` at the first sounding slot: there is
    no previous interval to be parallel to, so nothing is a violation yet.

    Similar motion is `melody_motion * voice_motion > 0` — the same sign.
    Oblique motion (either voice holding, so the product is 0) and contrary
    motion are both allowed, which is why the comparison is the product and
    not the directions.
    """
    if previous_interval_class is None:
        return False
    return (
        previous_interval_class in PERFECT_INTERVAL_CLASSES
        and interval_class in PERFECT_INTERVAL_CLASSES
        and melody_motion * voice_motion > 0
    )


# --- Preferences (ranked, never blocking) ----------------------------------

def is_contrary_motion(melody_motion: int, voice_motion: int) -> bool:
    """Do melody and generated voice move in opposite directions?

    Worth `CONTRARY_MOTION_BONUS`: contrary motion is what makes two lines
    sound independent rather than like one line doubled.
    """
    return melody_motion * voice_motion < 0


def is_voice_crossing(midi: int, melody_midi: int) -> bool:
    """Is the generated voice at or above the melody?

    Equal pitches count: a unison hides the melody just as a crossing does.
    Worth `VOICE_CROSSING_PENALTY` where it is a preference, and a hard
    rejection inside a protected phrase (`counterpoint.py`).
    """
    return midi >= melody_midi
