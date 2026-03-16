from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json
import os
import uuid
from fastapi.responses import FileResponse
from fastapi import UploadFile, File

app = FastAPI()

# -----------------------------
# Storage
# -----------------------------

JOB_STORAGE = "jobs"
os.makedirs(JOB_STORAGE, exist_ok=True)

connected_nodes = {}
node_resources = {}

# v4 job queue
jobs = {}

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

@app.post("/submit_job")
async def submit_job(file: UploadFile = File(...), chunks: int = Form(...)):

    job_id = str(uuid.uuid4())

    path = os.path.join(JOB_DIR, f"{job_id}.zip")

    with open(path, "wb") as f:
        f.write(await file.read())

    jobs[job_id] = {
        "chunks": chunks,
        "queue": list(range(1, chunks + 1)),
        "results": {}
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

                payload = message["payload"]

                job_id = payload["job_id"]
                chunk = payload["chunk"]
                result = payload["result"]

                if job_id not in jobs:
                    continue

                jobs[job_id]["results"][chunk] = result

                print(f"[Result] Job {job_id} chunk {chunk} completed")

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
