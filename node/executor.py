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

    data = (chunk_data or {}).get(str(chunk_id), {})
    args = [str(chunk_id)]

    if "start" in data:
        args += [str(data["start"]), str(data.get("end", ""))]
    if "file" in data:
        args.append(str(data["file"]))

    return run_in_docker(job_id, args)
