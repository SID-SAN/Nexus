# relay/job_storage.py

import os
import uuid
from fastapi import UploadFile, File
from fastapi.responses import FileResponse
from fastapi import APIRouter

router = APIRouter()

JOB_DIR = "jobs"

os.makedirs(JOB_DIR, exist_ok=True)


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

    path = os.path.join(JOB_DIR, f"{job_id}.zip")

    if not os.path.exists(path):
        return {"error": "job not found"}

    return FileResponse(path)