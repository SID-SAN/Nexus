# relay/job_storage.py

import os
import uuid
import re
from fastapi import UploadFile, File
from fastapi.responses import FileResponse
from fastapi import APIRouter

router = APIRouter()

JOB_DIR = "jobs"
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
JOB_DIR_ABS = os.path.abspath(JOB_DIR)

os.makedirs(JOB_DIR, exist_ok=True)

def error_response(message, code="ERROR"):
    return {
        "status": "failed",
        "error": message,
        "code": code
    }


def safe_job_zip_path(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id or ""):
        raise ValueError("Invalid job_id")

    path = os.path.abspath(os.path.join(JOB_DIR_ABS, f"{job_id}.zip"))
    if os.path.commonpath([JOB_DIR_ABS, path]) != JOB_DIR_ABS:
        raise ValueError("Path traversal detected")
    return path


@router.post("/submit_job_package")
async def submit_job_package(file: UploadFile = File(...)):
    
    job_id = str(uuid.uuid4())
    path = os.path.join(JOB_DIR, f"{job_id}.zip")

    with open(path, "wb") as f:
        f.write(await file.read())

    return {
        "job_id": job_id,
        "status": "uploaded"
    }


@router.get("/jobs/{job_id}")
def download_job(job_id: str):
    try:
        path = safe_job_zip_path(job_id)
    except ValueError:
        return error_response("invalid job id", "INVALID_JOB_ID")

    if not os.path.exists(path):
        return error_response("job not found", "JOB_NOT_FOUND")

    return FileResponse(path)
