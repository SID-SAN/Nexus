import os
from node.docker_runner import run_in_docker


def execute_chunk(job_id, chunk_id, chunk_data=None):
    extract_path = f"jobs/{job_id}"
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
