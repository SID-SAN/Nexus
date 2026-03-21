import subprocess
import os
import uuid

def run_in_docker(job_path, chunk, total_chunks):

    container_name = f"nexus_job_{uuid.uuid4().hex[:8]}"

    cmd = [
        "docker", "run",
        "--rm",
        "--name", container_name,
        "--cpus=1",
        "--memory=512m",
        "-v", f"{os.path.abspath(job_path)}:/app",
        "python:3.10",
        "python", "/app/main.py",
        str(chunk),
        str(total_chunks)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        return {
            "result": result.stdout.strip(),
            "logs": result.stdout,
            "error": result.stderr
        }

    except subprocess.TimeoutExpired:
        return {
            "result": None,
            "logs": "",
            "error": "Execution timed out"
        }