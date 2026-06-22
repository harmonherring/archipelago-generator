#!/usr/bin/env python3
"""Copy only the library apworlds the uploaded YAMLs actually reference into custom_worlds.

Runs inside the generator container (see entrypoint.sh) before ArchipelagoGenerate. AP
eagerly imports every world in custom_worlds/ at startup, so baking all ~460 apworlds there
makes each generation slow and noisy. Instead the full set lives in a library dir and this
selects per job: parse each player's `game:` value(s), resolve them against the build-time
game->file index, and copy just those.

Games not in the library are left alone — they may be built-in AP games (need no apworld),
user-uploaded apworlds (already copied by entrypoint.sh), or genuinely unsupported, in
which case ArchipelagoGenerate emits a clear "No world found" error.
"""
import glob
import json
import os
import shutil
import sys

import yaml

LIB = os.environ.get("AP_LIBRARY", "/opt/apworlds-library")
DEST = os.path.join(os.environ.get("AP_HOME", "/opt/archipelago"), "custom_worlds")
PLAYERS = "/job/Players"


def games_in_doc(doc):
    """A `game:` value is a single name, or a {name: weight} map for random selection."""
    if not isinstance(doc, dict):
        return []
    g = doc.get("game")
    if isinstance(g, str):
        return [g]
    if isinstance(g, dict):
        return list(g.keys())
    return []


def main():
    try:
        index = json.load(open(os.path.join(LIB, "index.json")))
    except FileNotFoundError:
        print("no apworld library index; skipping selection", file=sys.stderr)
        return 0

    needed = set()
    for path in glob.glob(os.path.join(PLAYERS, "*")):
        try:
            with open(path, encoding="utf-8") as f:
                for doc in yaml.safe_load_all(f):
                    needed.update(g.strip() for g in games_in_doc(doc))
        except Exception as e:
            print(f"warn: could not parse {os.path.basename(path)}: {e}", file=sys.stderr)

    os.makedirs(DEST, exist_ok=True)
    copied, absent = [], []
    for game in sorted(needed):
        fname = index.get(game)
        src = os.path.join(LIB, fname) if fname else None
        if src and os.path.exists(src):
            shutil.copy(src, DEST)
            copied.append(f"{game} -> {fname}")
        else:
            absent.append(game)

    print(f"selected {len(copied)} library apworld(s): {copied}")
    if absent:
        print(f"not in library (built-in/uploaded/unsupported): {sorted(absent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
