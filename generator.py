"""Run an Archipelago generation inside a throwaway container."""
import glob
import os

import docker
from docker.errors import APIError

IMAGE = os.environ.get("AP_IMAGE", "archipelago-generator:0.6.7")
MEM_LIMIT = os.environ.get("AP_MEM_LIMIT", "2g")
CPUS = float(os.environ.get("AP_CPUS", "2"))
TIMEOUT = int(os.environ.get("AP_TIMEOUT", "600"))  # seconds

_client = docker.from_env()


class GenerationError(Exception):
    """Raised when generation fails; `logs` holds the container output."""

    def __init__(self, message, logs=""):
        super().__init__(message)
        self.logs = logs


def run_generation(job_dir):
    """Generate a multiworld from `job_dir` (host path). Returns the output zip filename.

    `job_dir` must contain Players/ (YAMLs), custom_worlds/ (optional apworlds), and an
    output/ dir. It is bind-mounted into the container at /job.

    NB: this path is handed to the Docker daemon, so it must be a path the daemon can see.
    If this app itself runs in a container, mount the jobs dir from the host and pass the
    host path here, not the in-container path.
    """
    container = _client.containers.run(
        IMAGE,
        detach=True,
        network_disabled=True,
        mem_limit=MEM_LIMIT,
        nano_cpus=int(CPUS * 1_000_000_000),
        volumes={os.path.abspath(job_dir): {"bind": "/job", "mode": "rw"}},
    )
    try:
        try:
            result = container.wait(timeout=TIMEOUT)
        except Exception as exc:  # read timeout -> container still running
            try:
                container.kill()
            except APIError:
                pass
            raise GenerationError(f"Generation timed out after {TIMEOUT}s.") from exc

        logs = container.logs().decode("utf-8", "replace")
        if result.get("StatusCode", 1) != 0:
            raise GenerationError("Archipelago generation failed.", logs)
    finally:
        try:
            container.remove(force=True)
        except APIError:
            pass

    matches = glob.glob(os.path.join(job_dir, "output", "AP_*.zip"))
    if not matches:
        raise GenerationError("Generation produced no output file.", logs)
    return os.path.basename(matches[0])
