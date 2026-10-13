"""Differential probe: run the NEW gate over a large candidate space and
compare its accept/reject verdict against a reimplementation of the OLD
inlined rejection logic lifted verbatim from staging's _score_action."""
import itertools
from fractions import Fraction
from dataclasses import replace

from miare.counterpoint import (
    BeamState, CandidateContext, CounterpointSlot, SlotContext,
    check_hard_constraints, CONSONANT_INTERVAL_CLASSES, PERFECT_INTERVAL_CLASSES,
)
from miare.instruments import get_profile

CELLO = get_profile("cello")

def old_reject(profile, slot, state, midi, context):
    """Verbatim transcription of staging's rejection paths, in original order."""
    melody_midi = context.melody_midi
    source_pcs = context.source_pcs
    if midi is None:
        if state.pending_resolution is not None or slot.cadence:
            return "rest"
        return None
    interval_class = (melody_midi - midi) % 12 if melody_midi is not None else None
    if not source_pcs and midi % 12 not in context.tonal_pcs:
        return "tonal"
    consonant = interval_class in CONSONANT_INTERVAL_CLASSES if interval_class is not None else True
    strong = slot.metric_strength >= 0.7
    motion = midi - state.previous_midi if state.previous_midi is not None else 0
    if state.pending_resolution is not None:
        if state.previous_midi is None or not consonant or abs(motion) > 2:
            return "res-step"
        if state.pending_resolution < 0 and motion >= 0:
            return "res-fall"
        if state.pending_resolution > 0 and motion <= 0:
            return "res-rise"
    elif not consonant:
        if strong:
            if state.previous_midi != midi:
                return "strong-diss"
        else:
            if state.previous_midi is None or not 0 < abs(motion) <= 2:
                return "weak-diss"
    if slot.identity_policy == "protected" and strong and not consonant:
        return "prot-diss"
    if state.previous_midi is not None:
        if abs(motion) > 12:
            return "leap"
    if melody_midi is not None and state.previous_melody is not None and state.previous_midi is not None:
        melody_motion = melody_midi - state.previous_melody
        if (state.previous_interval_class in PERFECT_INTERVAL_CLASSES
                and interval_class in PERFECT_INTERVAL_CLASSES
                and melody_motion * motion > 0):
            return "parallel"
    if melody_midi is not None:
        spacing = melody_midi - midi
        if 3 <= spacing <= 19:
            pass
        elif midi >= melody_midi:
            if slot.identity_policy == "protected" and melody_midi - profile.min_midi >= 3:
                return "cross"
    return None

C_MAJOR = frozenset({0, 2, 4, 5, 7, 9, 11})
BASE_CTX = dict(
    source_midis=(), source_ids=(), source_bass_midi=None,
    harmony_pcs=frozenset({0, 4, 7}), harmony_root=0, tonal_pcs=C_MAJOR,
    future_melody=None, phrase_start=Fraction(0), phrase_end=Fraction(8),
    desired_register=60, motif_intervals=frozenset(),
)
BASE_SLOT = dict(
    index=0, start=Fraction(0), duration=Fraction(1), phrase_id="p1",
    melody_activity=1, texture="counterpoint", structural_importance=0.5,
)

checked = 0
mismatches = []
for melody_midi in (None, 60, 72, 40, 43):
  for source_pcs in (frozenset(), frozenset({1, 5})):
    ctx = SlotContext(melody_midi=melody_midi, source_pcs=source_pcs, **BASE_CTX)
    for strength in (0.4, 0.7, 1.0):
      for cadence in (False, True):
        for policy in ("free", "protected"):
          slot = CounterpointSlot(metric_strength=strength, cadence=cadence,
                                  identity_policy=policy, **BASE_SLOT)
          for prev_midi in (None, 48, 59, 60, 61, 62, 72, 74):
            for prev_mel in (None, 58, 60, 72):
              for prev_ic in (None, 0, 3, 7):
                for pending in (None, -1, 1):
                  for last_motion in (0, 8, -8):
                    state = BeamState(previous_midi=prev_midi, previous_melody=prev_mel,
                                      previous_interval_class=prev_ic,
                                      pending_resolution=pending, last_motion=last_motion)
                    for midi in (None, 36, 40, 48, 55, 59, 60, 61, 62, 65, 72, 73, 84):
                      cand = CandidateContext.build(slot, state, midi, ctx)
                      new = check_hard_constraints(CELLO, slot, state, midi, ctx, cand)
                      old = old_reject(CELLO, slot, state, midi, ctx)
                      checked += 1
                      if (new is None) != (old is None):
                        mismatches.append((melody_midi, source_pcs, strength, cadence,
                                           policy, prev_midi, prev_mel, prev_ic, pending,
                                           last_motion, midi,
                                           new.rule if new else None, old))
print(f"combinations checked: {checked}")
print(f"accept/reject mismatches: {len(mismatches)}")
for m in mismatches[:10]:
    print(m)
