"""Flask front-end: open a shareable room, upload YAMLs (+ apworlds) into it from any number
of browsers, generate a multiworld, download the result.

Room/job state lives entirely on disk under JOBS_ROOT/<id>/ -- there is no in-memory store, so
state survives restarts and works across multiple worker processes. Status is derived from
the room dir's contents:

    error.txt present          -> error    (file holds the message + container logs)
    output/AP_*.zip present    -> done
    generating.marker present  -> running
    otherwise                  -> open      (still accepting uploads)
"""
import glob
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from generator import GenerationError, run_generation

app = Flask(__name__)

JOBS_ROOT = Path(os.environ.get("AP_JOBS_DIR", "/tmp/ap-jobs"))
MAX_WORKERS = int(os.environ.get("AP_MAX_WORKERS", "2"))
JOB_TTL = int(os.environ.get("AP_JOB_TTL", "3600"))  # seconds before a job dir is reaped
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("AP_MAX_UPLOAD", str(25 * 1024 * 1024)))

JOBS_ROOT.mkdir(parents=True, exist_ok=True)
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PREFIX_RE = re.compile(r"^[0-9a-f]{8}_")


def _job_dir(job_id: str) -> Path:
    """Return the validated job dir, or abort 404 for unknown/malformed ids."""
    if not _ID_RE.match(job_id):
        abort(404)
    d = JOBS_ROOT / job_id
    if not d.is_dir():
        abort(404)
    return d


def _output_zip(job_dir: Path):
    matches = glob.glob(str(job_dir / "output" / "AP_*.zip"))
    return os.path.basename(matches[0]) if matches else None


def _status(job_dir: Path):
    """Derive (status, detail) from disk. detail is the error text or the zip filename."""
    error_file = job_dir / "error.txt"
    if error_file.exists():
        return "error", error_file.read_text(errors="replace")
    zip_name = _output_zip(job_dir)
    if zip_name:
        return "done", zip_name
    if (job_dir / "generating.marker").exists():
        return "running", None
    return "open", None


def _create_room_dirs(job_dir: Path):
    players, customs, output = job_dir / "Players", job_dir / "custom_worlds", job_dir / "output"
    for d in (players, customs, output):
        d.mkdir(parents=True, exist_ok=True)
    # The container runs as a non-root user and writes into output/, so the job
    # tree must be writable regardless of which uid owns it.
    for d in (job_dir, players, customs, output):
        os.chmod(d, 0o777)


def _save_unique(f, dest_dir: Path, default_name: str, lower: bool = False) -> str:
    """Save an upload under a collision-proof name; multiple uploaders may pick the same
    filename, and Archipelago reads the player name from YAML content, not the filename."""
    name = secure_filename(f.filename) or default_name
    if lower:
        name = name.lower()
    unique_name = f"{uuid.uuid4().hex[:8]}_{name}"
    f.save(dest_dir / unique_name)
    return unique_name


def _display_name(name: str) -> str:
    return _PREFIX_RE.sub("", name)


def _list_uploads(job_dir: Path):
    yamls = sorted(_display_name(p.name) for p in (job_dir / "Players").glob("*"))
    apworlds = sorted(_display_name(p.name) for p in (job_dir / "custom_worlds").glob("*"))
    return yamls, apworlds


def _run(job_dir: Path):
    try:
        run_generation(str(job_dir))
    except GenerationError as exc:
        _write_error(job_dir, (str(exc) + "\n\n" + exc.logs).strip())
    except Exception as exc:  # docker not reachable, etc.
        _write_error(job_dir, f"Unexpected error: {exc}")


def _write_error(job_dir: Path, message: str):
    # Write atomically so a concurrent status poll never reads a partial file.
    tmp = job_dir / "error.txt.tmp"
    tmp.write_text(message)
    tmp.replace(job_dir / "error.txt")


@app.route("/")
def index():
    """Visiting the site creates a fresh room and redirects there -- the room URL is what
    gets shared with friends."""
    room_id = uuid.uuid4().hex
    _create_room_dirs(JOBS_ROOT / room_id)
    return redirect(url_for("room_page", room_id=room_id), code=303)


@app.route("/rooms/<room_id>")
def room_page(room_id):
    _job_dir(room_id)  # 404s on bad/missing id
    return render_template("room.html", room_id=room_id)


@app.route("/rooms/<room_id>/state")
def room_state(room_id):
    job_dir = _job_dir(room_id)
    status, detail = _status(job_dir)
    yamls, apworlds = _list_uploads(job_dir)
    resp = {"id": room_id, "status": status, "yamls": yamls, "apworlds": apworlds}
    if status == "done":
        resp["download"] = f"/rooms/{room_id}/download"
    elif status == "error":
        resp["error"] = detail
    return jsonify(resp)


@app.route("/rooms/<room_id>/uploads", methods=["POST"])
def room_uploads(room_id):
    job_dir = _job_dir(room_id)
    if _status(job_dir)[0] != "open":
        return jsonify(error="This room is no longer accepting uploads."), 409

    yamls = [f for f in request.files.getlist("yamls") if f.filename]
    apworlds = [f for f in request.files.getlist("apworlds") if f.filename]
    if not yamls and not apworlds:
        return jsonify(error="No files were uploaded."), 400

    for f in yamls:
        base = secure_filename(f.filename) or "config.yaml"
        if not base.lower().endswith((".yaml", ".yml")):
            base += ".yaml"
        _save_unique(f, job_dir / "Players", base)

    for f in apworlds:
        # apworld filenames must be lowercase or the frozen import breaks.
        if secure_filename(f.filename).lower().endswith(".apworld"):
            _save_unique(f, job_dir / "custom_worlds", "custom.apworld", lower=True)

    yamls_now, apworlds_now = _list_uploads(job_dir)
    return jsonify(status="open", yamls=yamls_now, apworlds=apworlds_now), 201


@app.route("/rooms/<room_id>/generate", methods=["POST"])
def room_generate(room_id):
    job_dir = _job_dir(room_id)
    marker = job_dir / "generating.marker"
    try:
        marker.touch(exist_ok=False)  # atomic O_EXCL create -- doubles as the start lock
    except FileExistsError:
        return jsonify(error="Generation already started."), 409

    if not any((job_dir / "Players").iterdir()):
        marker.unlink(missing_ok=True)
        return jsonify(error="At least one YAML config is required."), 400

    _executor.submit(_run, job_dir)
    return jsonify(status="running"), 202


@app.route("/rooms/<room_id>/download")
def room_download(room_id):
    job_dir = _job_dir(room_id)
    zip_name = _output_zip(job_dir)
    if not zip_name:
        abort(404)
    return send_from_directory(job_dir / "output", zip_name, as_attachment=True)


def _reaper():
    while True:
        time.sleep(min(JOB_TTL, 300))
        cutoff = time.time() - JOB_TTL
        for d in JOBS_ROOT.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)


threading.Thread(target=_reaper, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
