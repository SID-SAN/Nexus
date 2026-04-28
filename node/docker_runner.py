import os
import re
import subprocess

IMAGE_NAME = "nexus-base"
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
BASE_JOBS_DIR = os.path.abspath("jobs")

# Resource limits
DOCKER_CPU_LIMIT = "0.5"
DOCKER_MEMORY_LIMIT = "512m"

# Timeouts
DOCKER_EXEC_TIMEOUT = 60
DEPENDENCY_INSTALL_TIMEOUT = 180

def error_response(message, code="ERROR"):
    return {
        "status": "failed",
        "error": message,
        "code": code,
    }


def _is_valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.fullmatch(job_id or ""))


def _safe_job_dir(job_id: str) -> str:
    if not _is_valid_job_id(job_id):
        raise ValueError("Invalid job_id")

    path = os.path.abspath(os.path.join(BASE_JOBS_DIR, job_id))
    if os.path.commonpath([BASE_JOBS_DIR, path]) != BASE_JOBS_DIR:
        raise ValueError("Path traversal detected")
    return path

def _ensure_deps_installed(extract_path):
    marker = os.path.join(extract_path, ".deps_installed")
    if os.path.exists(marker):
        return

    cmd = [
        "docker", "run",
        "--rm",
        "--network", "none",
        "-v", f"{extract_path}:/app",
        "-w", "/app",
        IMAGE_NAME,
        "sh", "-lc",
        "if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi; touch .deps_installed",
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=DEPENDENCY_INSTALL_TIMEOUT, check=False)


def run_in_docker(job_id, args):
    extract_path = _safe_job_dir(str(job_id))
    python_args = [str(a) for a in args]

    cmd = [
        "docker", "run",
        "--rm",
        "--network", "none",
        f"--cpus={DOCKER_CPU_LIMIT}",
        f"--memory={DOCKER_MEMORY_LIMIT}",
        "-v", f"{extract_path}:/app",
        "-w", "/app",
        IMAGE_NAME,
        "python", "task.py",
    ] + python_args

    try:
        _ensure_deps_installed(extract_path)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOCKER_EXEC_TIMEOUT
        )

        output = result.stdout.strip().splitlines()        
        last_line = output[-1] if output else ""

        return {
            "status": "success" if result.returncode == 0 else "failed",
            "result": last_line,
            "logs": result.stdout,
            "error": result.stderr if result.returncode != 0 else None
        }

    except subprocess.TimeoutExpired:
        return {
            "result": None,
            "logs": "",
            **error_response("Execution timed out", "EXECUTION_TIMEOUT"),
        }
    except Exception as e:
        return {
            "result": None,
            "logs": "",
            **error_response(str(e), "EXECUTION_ERROR"),
        }
