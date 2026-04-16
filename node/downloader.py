# node/downloader.py

import requests
import zipfile
import os
from config import RELAY_URLS

JOB_DIR = "jobs"

os.makedirs(JOB_DIR, exist_ok=True)

def get_active_relay():
    return os.getenv("RELAY_HTTP_URL", RELAY_URLS[0])

def download_job(job_id):

    zip_path = os.path.join(JOB_DIR, f"{job_id}.zip")
    extract_path = os.path.join(JOB_DIR, job_id)

    url = f"{get_active_relay()}/jobs/{job_id}"
    r = requests.get(url)
    if r.status_code != 200:
        raise Exception("Failed to download job")

    with open(zip_path, "wb") as f:
        f.write(r.content)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_path)

    return extract_path
