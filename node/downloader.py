# node/downloader.py

import requests
import zipfile
import os
import re
from config import RELAY_URLS

JOB_DIR = "jobs"
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
BASE_DIR = os.path.abspath(JOB_DIR)

os.makedirs(JOB_DIR, exist_ok=True)

def get_active_relay():
    return os.getenv("RELAY_HTTP_URL", RELAY_URLS[0])


def safe_job_path(job_id):
    if not JOB_ID_RE.fullmatch(str(job_id)):
        raise ValueError("Invalid job_id")

    path = os.path.abspath(os.path.join(BASE_DIR, str(job_id)))
    if os.path.commonpath([BASE_DIR, path]) != BASE_DIR:
        raise ValueError("Path traversal detected")

    return path

def safe_extract(zip_ref, path):
    for member in zip_ref.namelist():
        member_path = os.path.abspath(os.path.join(path, member))
        if not member_path.startswith(path):
            raise Exception("Zip path traversal detected")
    zip_ref.extractall(path)

def download_job(job_id):
    if not JOB_ID_RE.fullmatch(str(job_id)):
        raise ValueError("Invalid job_id")

    zip_path = os.path.join(BASE_DIR, f"{job_id}.zip")
    extract_path = safe_job_path(job_id)

    url = f"{get_active_relay()}/jobs/{job_id}"
    r = requests.get(url)
    if r.status_code != 200:
        raise Exception("Failed to download job")

    with open(zip_path, "wb") as f:
        f.write(r.content)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        safe_extract(zip_ref, extract_path)

    return extract_path
