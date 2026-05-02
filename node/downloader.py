# node/downloader.py

import requests
import zipfile
import os
import re
import shutil
import time
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
    tmp_path = f"{path}_tmp"

    for member in zip_ref.namelist():
        member_path = os.path.abspath(os.path.join(tmp_path, member))
        if os.path.commonpath([tmp_path, member_path]) != tmp_path:
            raise Exception("Zip path traversal detected")

    if os.path.exists(tmp_path):
        shutil.rmtree(tmp_path)

    os.makedirs(tmp_path, exist_ok=True)

    zip_ref.extractall(tmp_path)

    if os.path.exists(path):
        shutil.rmtree(path)

    shutil.move(tmp_path, path)

def download_job(job_id):
    if not JOB_ID_RE.fullmatch(str(job_id)):
        raise ValueError("Invalid job_id")

    zip_path = os.path.join(BASE_DIR, f"{job_id}.zip")
    extract_path = safe_job_path(job_id)

    url = f"{get_active_relay()}/jobs/{job_id}"
    try:
        r = requests.get(url, stream=True, timeout=(5, 15))
        r.raise_for_status()
    except requests.exceptions.Timeout:
        raise Exception("Download timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Download failed: {e}")

    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_path)
        return extract_path

    finally:
        if os.path.exists(zip_path):
            for _ in range(5):
                try:
                    os.remove(zip_path)
                    break
                except PermissionError:
                    time.sleep(0.2)
            else:
                print(f"[WARN] Could not delete zip after extraction: {zip_path}")
