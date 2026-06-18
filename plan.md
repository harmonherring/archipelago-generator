# Plan: Archipelago Generator Site

A web app that generates Archipelago multiworlds from uploaded YAML configs, including
third-party `.apworld` games the official generator can't handle.

See [archipelago.md](./archipelago.md) for how generation works.

## Architecture

```
User ──HTTP──> Flask app ──spins up──> Docker container (AP 0.6.7 + apworlds)
                  │  mounts job dir         runs ArchipelagoGenerate
                  └──<── returns AP_<seed>.zip
```

## 1. Generator Docker image

- Base on the Archipelago 0.6.7 `linux-x86_64.tar.gz` (frozen build, no Python setup).
- Bake common `.apworld` files into `custom_worlds/`.
- Entrypoint: given a mounted job dir, run
  `ArchipelagoGenerate --player_files_path /job/Players --outputpath /job/output`.
- Run non-root, `--network none`, with CPU/memory/time limits (apworlds = untrusted code).

## 2. Flask app

- `GET /` — upload form (YAMLs + optional apworlds).
- `POST /generate` — save uploads to a temp job dir, queue the job, return a job id.
- `GET /job/<id>` — status, download link, or error log.

Generation runs **per job in its own container**, **in the background** (the POST returns
a job id immediately; the client polls `/job/<id>`). Flask launches containers via the
Docker socket (`/var/run/docker.sock`) — the one privileged dependency to be careful with.

Per-job flow: temp dir with `Players/`, `custom_worlds/`, `output/` → save uploads →
background worker runs the container with the dir mounted → glob `output/AP_*.zip` (random
seed in name) + capture stdout/stderr for errors → expose for download → clean up on TTL.

## Milestones

1. Build the image; generate a built-in game from a template YAML.
2. Add common apworlds; generate a custom-game YAML.
3. Flask app: upload YAML → run container → download zip.
4. Allow apworld uploads + show generation errors.
