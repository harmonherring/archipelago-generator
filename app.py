"""Flask front-end: upload YAMLs (+ apworlds), generate a multiworld, download the result.

Job state lives entirely on disk under JOBS_ROOT/<id>/ -- there is no in-memory store, so
state survives restarts and works across multiple worker processes. Status is derived from
the job dir's contents:

    error.txt present       -> error   (file holds the message + container logs)
    output/AP_*.zip present -> done
    otherwise               -> running
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

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
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
    return "running", None


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
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    yamls = [f for f in request.files.getlist("yamls") if f.filename]
    apworlds = [f for f in request.files.getlist("apworlds") if f.filename]
    if not yamls:
        return jsonify(error="At least one YAML config is required."), 400

    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    players, customs, output = job_dir / "Players", job_dir / "custom_worlds", job_dir / "output"
    for d in (players, customs, output):
        d.mkdir(parents=True, exist_ok=True)
    # The container runs as a non-root user and writes into output/, so the job
    # tree must be writable regardless of which uid owns it.
    for d in (job_dir, players, customs, output):
        os.chmod(d, 0o777)

    for f in yamls:
        name = secure_filename(f.filename) or "config.yaml"
        if not name.lower().endswith((".yaml", ".yml")):
            name += ".yaml"
        f.save(players / name)

    for f in apworlds:
        # apworld filenames must be lowercase or the frozen import breaks.
        name = secure_filename(f.filename).lower()
        if name.endswith(".apworld"):
            f.save(customs / name)

    _executor.submit(_run, job_dir)
    return jsonify(id=job_id), 202


@app.route("/job/<job_id>")
def job_status(job_id):
    job_dir = _job_dir(job_id)
    status, detail = _status(job_dir)
    resp = {"id": job_id, "status": status}
    if status == "done":
        resp["download"] = f"/job/{job_id}/download"
    elif status == "error":
        resp["error"] = detail
    return jsonify(resp)


@app.route("/job/<job_id>/download")
def job_download(job_id):
    job_dir = _job_dir(job_id)
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
