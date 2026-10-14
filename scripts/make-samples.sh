#!/usr/bin/env bash
# Regenerate the viewer's bundled sample arrangements from their committed sources.
#
# Each samples/<name>/ holds one committed source.musicxml plus the arrangements this
# script writes next to it: <relationship>.musicxml and <relationship>.miare.json.
# Run it after any arranger change so score_viewer.html shows current output.
#
#   ./scripts/make-samples.sh            # all samples
#   ./scripts/make-samples.sh greensleeves
set -euo pipefail

cd "$(dirname "$0")/.."

SEED=1
BEAM_WIDTH=32

# <sample>:<relationship>:<voice1>:<voice2>
ARRANGEMENTS=(
  "short-tune:support:violin:cello"
  "short-tune:countermelody:violin:cello"
  "greensleeves:support:violin:cello"
  "greensleeves:countermelody:violin:viola"
)

wanted=("$@")          # empty = every sample
set +u                 # bash 3.2 (macOS) treats an empty array as unset

run_miare() {
  if command -v uv >/dev/null 2>&1; then
    uv run miare "$@"
  else
    PYTHONPATH=src python3 -m miare "$@"
  fi
}

for spec in "${ARRANGEMENTS[@]}"; do
  IFS=: read -r sample relationship voice1 voice2 <<<"$spec"

  if [ ${#wanted[@]} -gt 0 ]; then
    match=no
    for name in "${wanted[@]}"; do
      [ "$name" = "$sample" ] && match=yes
    done
    [ "$match" = yes ] || continue
  fi

  source_path="samples/$sample/source.musicxml"
  if [ ! -f "$source_path" ]; then
    echo "missing $source_path — samples/<name>/source.musicxml is the committed input" >&2
    exit 1
  fi

  echo "==> $sample · $relationship ($voice1 + $voice2)"
  run_miare arrange "$source_path" \
    -o "samples/$sample/$relationship.musicxml" \
    --relationship "$relationship" \
    --voice1-profile "$voice1" \
    --voice2-profile "$voice2" \
    --beam-width "$BEAM_WIDTH" \
    --seed "$SEED"
done

echo
echo "Open the viewer:  python3 -m http.server  →  http://localhost:8000/score_viewer.html"
