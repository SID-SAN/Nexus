import json
import os

STORE_PATH = "relay/job_store/jobs.json"


def save_jobs(jobs):

    os.makedirs("relay/job_store", exist_ok=True)

    with open(STORE_PATH, "w") as f:
        json.dump(jobs, f, indent=2)


def load_jobs():

    if not os.path.exists(STORE_PATH):
        return {}

    with open(STORE_PATH, "r") as f:
        return json.load(f)