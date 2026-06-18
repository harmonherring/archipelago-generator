# Project Overview

A web app that generates [Archipelago](https://archipelago.gg) multiworlds from
user-uploaded YAML configs — including third-party `.apworld` games the official
`archipelago.gg/generate` service can't handle. See [archipelago.md](./archipelago.md)
for background on Archipelago itself and [plan.md](./plan.md) for the original plan.

## How it works

```
User ──HTTP──> Flask app ──spawns──> generator container (AP 0.6.7 + apworlds)
                  │  bind-mounts job dir        runs ArchipelagoGenerate
                  └──<── serves AP_<seed>.zip
```

1. Visiting `/` creates a **room** (just a job dir, pre-generation) and redirects to
   `/rooms/<id>` — that URL is what gets shared with friends.
2. Anyone with the link can upload player YAMLs (plus optional `.apworld` files) into the
   room; uploads are saved with a unique-prefixed filename so concurrent uploaders never
   clobber each other, and the room page polls and shows everyone's uploads live.
3. Anyone in the room can click Generate once enough YAMLs are in. Flask flips the room to
   the "running" state (an atomic on-disk marker prevents double-starts) and **spawns a
   separate generator container** for the job via the Docker socket (generation does **not**
   run in the API process).
4. The job dir is bind-mounted at `/job`; the container runs `ArchipelagoGenerate`
   against `/job/Players` and writes the result to `/job/output`.
5. The room page polls status and shows a download link for the resulting `AP_<seed>.zip`
   once generation finishes.

## Components

### Generator image — `Dockerfile.generator` + `entrypoint.sh`
- Debian slim + the Archipelago 0.6.7 `linux-x86_64.tar.gz` frozen build at
  `/opt/archipelago`.
- Common apworlds in `apworlds/` are baked into `/opt/archipelago/custom_worlds/`.
- `entrypoint.sh` copies any per-job apworlds from `/job/custom_worlds` into the install,
  then runs `ArchipelagoGenerate --player_files_path /job/Players --outputpath /job/output`.
- Runs as a non-root user, and the **whole install is chowned to that user** — required,
  because Archipelago only scans `custom_worlds/` when the install dir is writable
  (otherwise it looks for apworlds in the user's home dir and ignores the baked-in ones).

### Flask app — `app.py` + `generator.py` + `templates/room.html`
- Routes: `GET /` (creates a room, redirects to `/rooms/<id>`), `GET /rooms/<id>` (the
  room page), `GET /rooms/<id>/state` (polled status + upload list, JSON), `POST
  /rooms/<id>/uploads` (add YAMLs/apworlds), `POST /rooms/<id>/generate` (start
  generation), `GET /rooms/<id>/download` (the zip).
- Generation runs in a background thread pool; the generate POST returns immediately and
  the page polls for completion.
- `generator.py` launches each job's container with `--network none`, a memory/CPU cap,
  and a wait timeout; captures container logs on failure.
- **No in-memory room/job store** — state is derived from the room dir on disk: `error.txt`
  present → error; `output/AP_*.zip` present → done; `generating.marker` present →
  running; otherwise → open (still accepting uploads). This survives restarts and works
  across multiple worker processes. Room/job ids are validated against `^[0-9a-f]{32}$`
  to prevent path traversal. The `generating.marker` file is created with an exclusive
  (`O_EXCL`) touch so simultaneous "Generate" clicks can't double-submit a job. Uploaded
  files are saved with a random 8-hex-char prefix so concurrent uploaders can't clobber
  each other's same-named files; Archipelago reads the player name from YAML content, not
  the filename, so this is purely a disk-safety measure.
- A reaper thread deletes job dirs older than `AP_JOB_TTL` — this also cleans up rooms
  that were created but never generated.

### Orchestration — `docker-compose.yaml` + `Dockerfile.web`
- `web` service: the Flask app under gunicorn, with the Docker socket mounted so it can
  spawn generator containers.
- The job dir is bind-mounted at the **same absolute path** (`/srv/ap-jobs`) on the host
  and in the web container, so the path the app hands the host daemon resolves correctly
  for the generator's mount.
- `generator` is a **build-only** service (`profiles: ["build"]`) — it produces the
  per-job image and never runs as a long-lived service.

## Running it

Requires Docker on the host.

```bash
docker compose --profile build build generator   # build the per-job generator image
docker compose up --build -d                      # build + start the web server
# -> http://localhost:5000
```

Note: after changing baked-in apworlds or the generator Dockerfile, rebuild the
generator explicitly — `docker compose up --build` only rebuilds `web`, not the
profiled `generator` service.

## Configuration (env vars on the `web` service)

| Var | Default | Purpose |
|-----|---------|---------|
| `AP_IMAGE` | `archipelago-generator:0.6.7` | Image spawned per job |
| `AP_JOBS_DIR` | `/srv/ap-jobs` | Job dir root (must match host bind-mount path) |
| `AP_MAX_WORKERS` | `2` | Concurrent generations |
| `AP_JOB_TTL` | `3600` | Seconds before a job dir is reaped |
| `AP_MEM_LIMIT` / `AP_CPUS` / `AP_TIMEOUT` | `2g` / `2` / `600` | Per-job container limits |
| `AP_MAX_UPLOAD` | `26214400` | Max upload bytes |

## Status & known caveats

**Working / built:** image definitions, the Flask app, compose orchestration, the
disk-based job model, and the apworld-loading fix (writable install → portable mode).

**Not yet verified end-to-end** (no full generation has been run here yet):
- The generator binary name (`ArchipelagoGenerate`) and that the frozen build has all
  runtime shared libs — confirm on first real generation; errors surface in the web UI.
- The tarball's top-level dir layout (the `--strip-components=1` assumption).

**Security note:** the web container mounts the Docker socket and runs as root, which is
root-equivalent on the host. Don't expose this unauthenticated on an untrusted network.
Uploaded apworlds are arbitrary Python; the `--network none` + resource-capped sandbox is
the main mitigation.

**Scope not yet built:** auth/rate limiting, multi-version Archipelago support, and any
persistence beyond the TTL-reaped job dirs.
