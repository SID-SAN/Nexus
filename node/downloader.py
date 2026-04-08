# node/downloader.py

import requests
import zipfile
import os

RELAY_URL = "https://nexus-relay-5wog.onrender.com"

JOB_DIR = "jobs"

os.makedirs(JOB_DIR, exist_ok=True)


def download_job(job_id):

    zip_path = os.path.join(JOB_DIR, f"{job_id}.zip")
    extract_path = os.path.join(JOB_DIR, job_id)

    url = f"{RELAY_URL}/jobs/{job_id}"

    r = requests.get(url)

    with open(zip_path, "wb") as f:
        f.write(r.content)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_path)

    return extract_path