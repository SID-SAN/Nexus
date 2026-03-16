from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json
import os
import uuid
from fastapi.responses import FileResponse
from fastapi import UploadFile, File
from fastapi import Form
import asyncio

app = FastAPI()
@app.on_event("startup")
async def start_heartbeat():
    asyncio.create_task(heartbeat_loop())

# -----------------------------
# Storage
# -----------------------------

JOB_STORAGE = "jobs"
os.makedirs(JOB_STORAGE, exist_ok=True)

connected_nodes = {}
node_resources = {}

# v4 job queue
jobs = {}


async def heartbeat_loop():

    while True:

        await asyncio.sleep(20)

        for node_id, ws in list(connected_nodes.items()):

            try:
                await ws.send_text(json.dumps({
                    "type": "heartbeat"
                }))
            except:
                pass


# -----------------------------
# Basic endpoints
# -----------------------------

@app.get("/")
def root():
    return {"message": "Relay server running"}


@app.get("/health")
def health():
    return {
        "status": "relay_alive",
        "connected_nodes": list(connected_nodes.keys())
    }


@app.get("/nodes")
def get_nodes():
    return {"nodes": list(connected_nodes.keys())}


@app.get("/resources")
def get_resources():
    return node_resources


@app.get("/cluster_status")
def cluster_status():
    return {
        "connected_nodes": list(connected_nodes.keys()),
        "resources": node_resources,
        "active_jobs": list(jobs.keys())
    }



@app.get("/jobs/{job_id}")
def download_job(job_id: str):

    path = f"{JOB_STORAGE}/{job_id}.zip"

    if not os.path.exists(path):
        return {"error": "job not found"}

    return FileResponse(path)


# -----------------------------
# Create distributed job
# -----------------------------
JOB_DIR = "jobs"

os.makedirs(JOB_DIR, exist_ok=True)
@app.post("/submit_job")
async def submit_job(file: UploadFile = File(...), chunks: int = Form(...)):

    job_id = str(uuid.uuid4())

    path = os.path.join(JOB_DIR, f"{job_id}.zip")

    with open(path, "wb") as f:
        f.write(await file.read())

    jobs[job_id] = {
        "chunks": chunks,
        "queue": list(range(1, chunks + 1)),
        "results": {},
        "completed": 0,
        "status": "running"
    }

    return {
        "job_id": job_id,
        "chunks": chunks,
        "status": "job_created"
    }

# -----------------------------
# WebSocket relay
# -----------------------------

@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):

    await websocket.accept()

    connected_nodes[node_id] = websocket
    print(f"Node connected: {node_id}")

    try:
        while True:

            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            # -----------------------------
            # Resource updates
            # -----------------------------
            if msg_type == "resource_update":
                node_resources[node_id] = message["payload"]
                continue

            # -----------------------------
            # Node requests chunk
            # -----------------------------
            elif msg_type == "request_chunk":

                job_id = message["payload"]["job_id"]

                job = jobs.get(job_id)

                if not job:
                    continue

                if not job["queue"]:
                    continue

                chunk = job["queue"].pop(0)

                response = {
                    "type": "assign_chunk",
                    "target": node_id,
                    "payload": {
                        "job_id": job_id,
                        "chunk": chunk,
                        "total_chunks": job["chunks"]
                    }
                }

                await websocket.send_text(json.dumps(response))

                print(f"[Scheduler] Assigned chunk {chunk} → {node_id}")

            # -----------------------------
            # Node submits result
            # -----------------------------
            elif msg_type == "submit_result":

                job_id = message["payload"]["job_id"]
                chunk = message["payload"]["chunk"]
                result = message["payload"]["result"]

                job = jobs.get(job_id)

                if not job:
                    return

                job["results"][chunk] = result
                job["completed"] += 1

                print(f"[JOB] chunk {chunk} completed ({job['completed']}/{job['chunks']})")

                if job["completed"] == job["chunks"]:
                    job["status"] = "completed"
            # -----------------------------
            # Old relay routing (v3 compatibility)
            # -----------------------------
            else:

                target = message.get("target")

                if target in connected_nodes:

                    target_socket = connected_nodes[target]

                    await target_socket.send_text(json.dumps(message))

                    print(f"Routed message {msg_type} from {node_id} → {target}")

                else:

                    print(f"Target {target} not connected")

    except WebSocketDisconnect:

        print(f"Node disconnected: {node_id}")

        connected_nodes.pop(node_id, None)
        node_resources.pop(node_id, None)


@app.get("/job_status/{job_id}")
def job_status(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    return {
        "job_id": job_id,
        "status": job["status"],
        "completed": job["completed"],
        "total_chunks": job["chunks"]
    }

@app.get("/job_result/{job_id}")
def job_result(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    if job["status"] != "completed":
        return {"status": "job still running"}

    final_result = sum(job["results"].values())

    return {
        "job_id": job_id,
        "result": final_result
    }