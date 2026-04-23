import os
import re
import shutil
from node.docker_runner import run_in_docker

JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
BASE_DIR = os.path.abspath("jobs")


def safe_job_path(job_id):
    if not JOB_ID_RE.fullmatch(str(job_id)):
        raise ValueError("Invalid job_id")

    path = os.path.abspath(os.path.join(BASE_DIR, str(job_id)))
    if os.path.commonpath([BASE_DIR, path]) != BASE_DIR:
        raise ValueError("Path traversal detected")

    return path


def cleanup_job(job_id):
    job_dir = safe_job_path(job_id)
    zip_path = os.path.join(BASE_DIR, f"{job_id}.zip")

    shutil.rmtree(job_dir, ignore_errors=True)
    try:
        os.remove(zip_path)
    except FileNotFoundError:
        pass


def execute_chunk(job_id, chunk_id, chunk_data=None):
    try:
        extract_path = safe_job_path(job_id)
    except ValueError as e:
        return {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": str(e),
        }

    if not os.path.exists(extract_path):
        return {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": "job not downloaded",
        }

    task_path = os.path.join(extract_path, "task.py")
    if not os.path.exists(task_path):
        return {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": "task.py not found in extracted job package",
        }

    payload = chunk_data or {}
    if isinstance(payload, dict) and str(chunk_id) in payload:
        data = payload.get(str(chunk_id), {})
    else:
        # Also support passing a direct single-chunk payload.
        data = payload if isinstance(payload, dict) else {}
    args = [str(chunk_id)]

    if "start" in data:
        args += [str(data["start"]), str(data.get("end", ""))]
    if "file" in data:
        args.append(str(data["file"]))

    return run_in_docker(job_id, args)
