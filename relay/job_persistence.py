import json
import os

STORE_PATH = "relay/job_store/jobs.json"

def _to_json_safe(value):
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, set):
        return list(value)
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return value

def save_jobs(jobs):
    os.makedirs("relay/job_store", exist_ok=True)

    tmp_path = STORE_PATH + ".tmp"

    with open(tmp_path, "w") as f:
        json.dump(_to_json_safe(jobs), f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, STORE_PATH)


def load_jobs():

    if not os.path.exists(STORE_PATH):
        return {}

    with open(STORE_PATH, "r") as f:
        jobs = json.load(f)

    for job in jobs.values():
        failed_nodes = job.get("failed_nodes", {})
        if isinstance(failed_nodes, dict):
            for chunk, nodes in list(failed_nodes.items()):
                if isinstance(nodes, list):
                    failed_nodes[chunk] = set(nodes)

        rewarded_chunks = job.get("rewarded_chunks")
        if isinstance(rewarded_chunks, list):
            job["rewarded_chunks"] = set(rewarded_chunks)

        completed_chunks = job.get("completed_chunks")
        if isinstance(completed_chunks, list):
            job["completed_chunks"] = set(completed_chunks)

        # ✅ Ensure node_owner_snapshot is always a plain dict (safe after JSON round-trip)
        if "node_owner_snapshot" not in job:
            job["node_owner_snapshot"] = {}

    return jobs
