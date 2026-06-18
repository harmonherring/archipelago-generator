# Archipelago Generator

## What Archipelago is

[Archipelago](https://archipelago.gg) is a **multi-world, multi-game randomizer**. A
single randomized session (a "multiworld") can contain many players, each playing a
potentially *different* game. The key mechanic is cross-game item shuffling: an item
needed to progress in one player's game can be located in another player's game. To
reach it, that other player has to find it and send it over. This interdependence is
what makes a multiworld a shared, cooperative experience rather than a set of isolated
seeds.

A multiworld is created in a one-time **generation** step that consumes every player's
configuration, shuffles all items and locations across all worlds according to each
game's logic rules, and produces a single data file plus per-player artifacts. That
output is then hosted on a server that players connect their game clients to.

This document explains the three things our site needs to understand and orchestrate:

1. **YAML files** — per-player configuration.
2. **.apworld files** — the plug-in code that teaches the generator about a game.
3. **World generation** — the process that turns YAMLs + apworlds into a playable
   multiworld.

---

## 1. YAML configuration files

A **YAML file is a single player's configuration.** It declares which game they're
playing and the full set of options for that game.

A YAML contains, at minimum:

- A **player name** (`name:`) — the slot name shown in the multiworld.
- A **game** selection (`game:`) — the name of the game this player is playing.
- A **game-specific options block**, keyed by the game name, holding every setting that
  game exposes (goal conditions, item pool tweaks, difficulty/logic level, which
  locations are enabled, starting inventory, etc.).

Important properties:

- **One YAML = one world (one slot).** A multiworld of N slots needs N worlds' worth of
  YAML. A single player can submit multiple YAMLs to play multiple slots.
- **A single YAML file can contain multiple documents** (separated by `---`), letting one
  upload define several slots at once.
- Options support **weighted random values**: instead of a fixed setting, a player can
  give weights and the generator rolls the option at generation time. This means two
  generations from the same YAML can differ.
- YAMLs are **plain, uncompressed text**. Players create them via the website's options
  page ("Export Options"), the Archipelago launcher's "Generate Template Options"
  (written to `Players/Templates`), or by hand.

In a local Archipelago install, YAMLs are placed in the **`Players/`** folder and the
generator reads every file in that folder.

**The critical constraint:** a YAML's `game:` value must correspond to a game the
generator actually has installed. If the YAML names a game whose world code isn't
present, generation fails. This is exactly the gap our site fills (see below).

---

## 2. .apworld files

An **.apworld file is the packaged implementation of a single game's randomizer logic.**
It is the plug-in that teaches the Archipelago generator what a game's items, locations,
regions, and logic rules are, and which options its YAML can specify.

Technical shape:

- An `.apworld` is just a **zip archive** with the extension `.apworld`, named in
  **all lowercase** (e.g. `ror2.apworld`). Mixed-case names break importing under
  frozen Python 3.10+.
- Inside, it contains a single folder whose name matches the file (e.g. `ror2/`)
  holding the world's Python package — `ror2/__init__.py` and supporting modules.
- It includes an **`archipelago.json`** manifest with metadata: the `game` name,
  `world_version`, and `minimum_ap_version` / `maximum_ap_version` compatibility bounds,
  authors, etc. Compatibility bounds matter — an apworld built for a newer Archipelago
  may not load on an older generator and vice versa.

Where the generator looks for worlds:

- **`worlds/`** — built-in worlds (source installs) and the default search path.
- **`<install dir>/lib/worlds/`** — worlds in a standard packaged install.
- **`custom_worlds/`** — where dropped-in third-party `.apworld` files live.

To install one normally, a user clicks "Install APWorld" in the launcher (or drags/
double-clicks the file), which places it where the generator can load it automatically.

**Built-in vs. third-party games.** Archipelago ships with a large set of supported
games already bundled. The official **`archipelago.gg/generate`** service can only
generate worlds for those bundled games. Many community games exist *only* as
third-party `.apworld` files distributed outside the main release — and the hosted
service has no way to load them.

---

## 3. World generation

Generation is the step that fuses everything into a multiworld.

**Inputs:**

- All player YAMLs (the `Players/` folder).
- The set of installed worlds — built-in plus any `.apworld` files — that cover every
  game referenced by those YAMLs.
- Optional global settings (e.g. a meta YAML, spoiler verbosity, a fixed seed).

**Process:** the generator (the `ArchipelagoGenerate` entry point, or "Generate" in the
launcher) parses each YAML, rolls any random/weighted options, instantiates each game's
world via its apworld, builds the combined item pool and location list across all
worlds, and runs the fill algorithm that places items into locations while respecting
every game's logic so the multiworld is completable. A seed makes this reproducible.

**Outputs:** a single compressed archive, conventionally named like **`AP_<seed>.zip`**,
written to the **`output/`** folder. It contains:

- A **`.archipelago`** file — the multiworld data the server hosts and that all clients
  connect against.
- A **spoiler log** (if enabled) — the full placement listing.
- **Per-game patch/data files** — e.g. ROM patches for cartridge-based games, or
  per-slot connection files — that each player loads with their game's client.

To actually play, that `.archipelago` is loaded onto a server (a hosted room on
archipelago.gg, or a self-hosted `MultiServer`), and each player connects with the
client for their game using the room's address and their slot name.

---

## Why this site exists

The official **`archipelago.gg/generate`** generator only knows about the games bundled
into the Archipelago release it runs. **If your multiworld includes any game that's only
available as a third-party `.apworld`, the hosted generator can't produce it** — there's
no supported way to upload custom world code to that service. Players are forced to
install Archipelago locally, drop in the right `.apworld` files, and run generation
themselves, which is a real barrier for anyone organizing a game with custom worlds.

**This site closes that gap.** Users upload their YAML configs (and, where needed, the
required `.apworld` files), and the site runs generation on a backend that *has those
apworlds installed*, then hands back the generated output — the same `AP_*.zip` they'd
get locally — ready to host.

### What the site has to do

At a high level the backend reproduces a local Archipelago install's generation flow:

1. **Accept uploads** — one or more YAML files, plus any third-party `.apworld` files
   needed by the games those YAMLs reference.
2. **Stage the inputs** — write the YAMLs into a `Players/`-equivalent directory and the
   `.apworld` files into a `custom_worlds/`-equivalent directory for a clean Archipelago
   environment.
3. **Validate before generating** — confirm every YAML's `game:` resolves to an
   installed world (built-in or uploaded apworld), and check apworld
   `minimum/maximum_ap_version` against the Archipelago version being run. Surface
   missing-apworld and version-mismatch errors early and clearly.
4. **Run generation** — invoke `ArchipelagoGenerate` against the staged inputs, ideally
   in an isolated/sandboxed environment per request (apworlds are arbitrary Python and
   should be treated as untrusted; resource and time limits guard against bad configs).
5. **Return the output** — deliver the resulting `output/AP_*.zip` (and surface the
   spoiler log / generation log) for download, and optionally offer to host the
   `.archipelago` directly.

### Things to get right

- **Trust & isolation:** `.apworld` files execute arbitrary Python during generation.
  Run each generation sandboxed with CPU/memory/time caps and no outbound network or
  filesystem access beyond its staging dir.
- **Version compatibility:** the Archipelago core version on the backend must satisfy
  the `minimum_ap_version`/`maximum_ap_version` of every uploaded apworld. Consider
  supporting multiple Archipelago versions, or clearly stating the supported one.
- **Determinism & seeds:** let users optionally pin a seed so a generation can be
  reproduced or shared.
- **Clear failure reporting:** generation failures (logic errors, unfillable seeds,
  missing items) produce long tracebacks — parse and present the actionable part.
- **Cleanup:** uploaded apworlds and staged YAMLs may contain personal slot names;
  treat them as user data and clean up staging dirs after each run.
