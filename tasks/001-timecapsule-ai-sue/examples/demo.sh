#!/bin/sh
# Reproducible, isolated end-to-end demonstration; no external files are touched.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP="$SCRIPT_DIR/../timecapsule.py"
BASE=$(mktemp -d)
trap 'rm -rf -- "$BASE"' EXIT
mkdir "$BASE/project"
printf 'version 1\n' > "$BASE/project/note.txt"
python3 "$APP" init "$BASE/repo"
python3 "$APP" snapshot "$BASE/repo" "$BASE/project"
FIRST=$(python3 "$APP" list "$BASE/repo" | head -n1 | cut -f1)
printf 'version 2\n' > "$BASE/project/note.txt"
printf 'added\n' > "$BASE/project/new.txt"
python3 "$APP" snapshot "$BASE/repo" "$BASE/project"
SECOND=$(python3 "$APP" list "$BASE/repo" | tail -n1 | cut -f1)
python3 "$APP" restore "$BASE/repo" "$FIRST" "$BASE/restored-first"
python3 "$APP" restore "$BASE/repo" "$SECOND" "$BASE/restored-second"
test "$(cat "$BASE/restored-first/note.txt")" = 'version 1'
test "$(cat "$BASE/restored-second/note.txt")" = 'version 2'
test ! -e "$BASE/restored-first/new.txt"
test "$(cat "$BASE/restored-second/new.txt")" = 'added'
python3 "$APP" verify "$BASE/repo"
printf 'DEMO PASS: both versions restored; added file only in second snapshot\n'
