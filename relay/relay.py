from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json
import os
from fastapi import UploadFile, File
from fastapi.responses import FileResponse
import uuid
from relay.job_storage import router as job_router

JOB_STORAGE = "jobs"

os.makedirs(JOB_STORAGE, exist_ok=True)

app = FastAPI()
app.include_router(job_router)

connected_nodes = {}
node_resources = {}


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

            # handle resource updates
            if msg_type == "resource_update":
                node_resources[node_id] = message["payload"]
                continue

            # route task messages
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

@app.get("/cluster_status")
def cluster_status():

    return {
        "connected_nodes": list(connected_nodes.keys()),
        "resources": node_resources
    }


@app.post("/submit_job_package")
async def submit_job_package(file: UploadFile = File(...)):

    job_id = str(uuid.uuid4())

    path = f"{JOB_STORAGE}/{job_id}.zip"

    with open(path, "wb") as f:
        f.write(await file.read())

    return {
        "job_id": job_id,
        "status": "uploaded"
    }


@app.get("/jobs/{job_id}")
def download_job(job_id: str):

    path = f"{JOB_STORAGE}/{job_id}.zip"

    if not os.path.exists(path):
        return {"error": "job not found"}

    return FileResponse(path)