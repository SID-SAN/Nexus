import os
import shlex
import subprocess
import uuid

IMAGE_NAME = "nexus-base"


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
    subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)


def run_in_docker(job_id, args):
    extract_path = os.path.abspath(f"jobs/{job_id}")
    container_name = f"nexus_job_{uuid.uuid4().hex[:8]}"
    arg_text = " ".join([shlex.quote(str(a)) for a in args])

    cmd = [
        "docker", "run",
        "--rm",
        "--name", container_name,
        "--network", "none",
        "--cpus=0.5",
        "--memory=512m",
        "-v", f"{extract_path}:/app",
        "-w", "/app",
        IMAGE_NAME,
        "sh", "-lc",
        f"python task.py {arg_text}"
    ]

    try:
        _ensure_deps_installed(extract_path)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        output = result.stdout.strip().split("\n")
        last_line = output[-1] if output else ""

        return {
            "status": "success" if result.returncode == 0 else "failed",
            "result": last_line,
            "logs": result.stdout,
            "error": result.stderr if result.returncode != 0 else None
        }

    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": "Execution timed out"
        }
    except Exception as e:
        return {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": str(e)
        }
