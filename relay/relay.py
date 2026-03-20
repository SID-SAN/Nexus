from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json
import os
import uuid
from fastapi.responses import FileResponse
from fastapi import UploadFile, File
from fastapi import Form
import asyncio
import time

app = FastAPI()
@app.on_event("startup")
async def start_heartbeat():
    asyncio.create_task(heartbeat_loop())

# -----------------------------
# Storage
# -----------------------------

JOB_DIR = "jobs"
os.makedirs(JOB_DIR, exist_ok=True)

connected_nodes = {}
node_resources = {}

# v4 job queue
jobs = {}


async def heartbeat_loop():

    while True:

        await asyncio.sleep(20)

        for node_id, ws in list(connected_nodes.items()):

            try:
                if ws.client_state.name == "CONNECTED":
                    await ws.send_text(json.dumps({"type": "heartbeat"}))
            except:
                connected_nodes.pop(node_id, None)


def apply_reducer(results, reducer):

    # remove None results
    values = [v for v in results.values() if v is not None]

    if not values:
        return None

    if reducer == "sum":
        return sum(values)

    if reducer == "avg":
        return sum(values) / len(values)

    if reducer == "max":
        return max(values)

    if reducer == "min":
        return min(values)

    if reducer == "list":
        return values

    return values


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

    path = f"{JOB_DIR}/{job_id}.zip"

    if not os.path.exists(path):
        return {"error": "job not found"}

    return FileResponse(path)


# -----------------------------
# Create distributed job
# -----------------------------

@app.post("/submit_job")
async def submit_job(
    file: UploadFile = File(...),
    chunks: int = Form(...),
    reducer: str = Form("sum")
):
    job_id = str(uuid.uuid4())

    path = os.path.join(JOB_DIR, f"{job_id}.zip")

    with open(path, "wb") as f:
        f.write(await file.read())

    jobs[job_id] = {
        "chunks": chunks,
        "queue": list(range(1, chunks + 1)),
        "results": {},
        "logs": {},
        "errors": {},
        "status_map": {},
        "assigned_at": {},
        "retries": {},    
        "completed": 0,
        "status": "running",
        "reducer": reducer
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
            # Heartbeat ACK
            # -----------------------------
            if msg_type == "heartbeat_ack":
                continue

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

                for job_id, job in jobs.items():

                    if job["queue"]:

                        chunk = job["queue"].pop(0)
                        job["status_map"][chunk] = "running"
                        job["assigned_at"][chunk] = time.time()
                        job["retries"].setdefault(chunk, 0)

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

                        break
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
                
                # 🚫 ignore cancelled jobs
                if job["status"] == "cancelled":
                    return

                chunk = message["payload"]["chunk"]

                # 🚫 ignore duplicates
                if chunk in job["results"]:
                    return
                
                job["results"][chunk] = message["payload"]["result"]
                job["logs"][chunk] = message["payload"].get("logs", "")
                job["errors"][chunk] = message["payload"].get("error", "")
                job["completed"] += 1

                # mark chunk complete
                job["status_map"][chunk] = "completed"

                #  ADD THIS HERE
                if any(status == "failed" for status in job["status_map"].values()):
                    job["status"] = "failed"

                # mark job completed if all done
                elif job["completed"] == job["chunks"]:
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
        "total_chunks": job["chunks"],
        "chunk_status": job["status_map"]
    }

@app.get("/job_result/{job_id}")
def job_result(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    if job["status"] == "failed":
        return {
            "job_id": job_id,
            "status": "failed",
            "errors": job["errors"]
        }

    if job["status"] != "completed":
        return {"status": "job still running"}

    final_result = apply_reducer(job["results"], job["reducer"])

    return {
        "job_id": job_id,
        "result": final_result
    }

@app.get("/job_logs/{job_id}")
def job_logs(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    return {
        "job_id": job_id,
        "logs": job["logs"],
        "errors": job["errors"]
    }


import asyncio
import time

MAX_RETRIES = 2
CHUNK_TIMEOUT = 60  # seconds


async def monitor_jobs():

    while True:

        await asyncio.sleep(5)

        if job["status"] != "running":
            continue

        for job_id, job in jobs.items():

            for chunk, status in list(job["status_map"].items()):

                if status != "running":
                    continue

                assigned_time = job["assigned_at"].get(chunk)

                if not assigned_time:
                    continue

                if time.time() - assigned_time > CHUNK_TIMEOUT:

                    retries = job["retries"].get(chunk, 0)

                    if retries < MAX_RETRIES:

                        print(f"[Retry] chunk {chunk} for job {job_id}")

                        job["queue"].append(chunk)
                        job["status_map"][chunk] = "pending"
                        job["retries"][chunk] += 1

                    else:

                        print(f"[Failed] chunk {chunk} exceeded retries")

                        job["status_map"][chunk] = "failed"
                        job["errors"][chunk] = "Max retries exceeded"


@app.on_event("startup")
async def start_monitor():
    asyncio.create_task(monitor_jobs())



from fastapi.responses import HTMLResponse

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
    <html>
    <head>
        <title>Nexus Dashboard</title>

        <style>
            body {
                font-family: Arial;
                background: #0f172a;
                color: white;
                margin: 0;
                padding: 20px;
            }

            h1 { color: #38bdf8; }

            .grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
            }

            .card {
                background: #1e293b;
                padding: 15px;
                border-radius: 12px;
                box-shadow: 0 4px 10px rgba(0,0,0,0.3);
            }

            input, select {
                padding: 8px;
                width: 100%;
                margin-top: 5px;
                border-radius: 6px;
                border: none;
            }

            button {
                padding: 10px;
                background: #38bdf8;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                margin-top: 10px;
                width: 100%;
            }

            .status-running { color: #facc15; }
            .status-completed { color: #22c55e; }
            .status-failed { color: #ef4444; }
            .status-cancelled { color: #94a3b8; }

            .progress-bar {
                width: 100%;
                background: #334155;
                border-radius: 8px;
                overflow: hidden;
                margin-top: 10px;
            }

            .progress-fill {
                height: 10px;
                background: #38bdf8;
                width: 0%;
            }
        </style>

    </head>

    <body>

        <h1>⚡ Nexus Cluster Dashboard</h1>

        <!-- JOB SUBMISSION -->
        <h2>🚀 Submit Job</h2>

        <div class="card">
            <input type="file" id="file"><br><br>

            <input type="number" id="chunks" placeholder="Chunks" value="5"><br><br>

            <select id="reducer">
                <option value="sum">sum</option>
                <option value="avg">avg</option>
                <option value="max">max</option>
                <option value="min">min</option>
                <option value="list">list</option>
            </select><br><br>

            <button onclick="submitJob()">Submit Job</button>

            <p id="submitStatus"></p>
        </div>

        <!-- NODES -->
        <h2>🖥️ Nodes</h2>
        <div id="nodes" class="grid"></div>

        <!-- JOBS -->
        <h2>📦 Jobs</h2>
        <div id="jobs" class="grid"></div>

        <script>

        async function viewLogs(job_id) {

            document.getElementById("logPanel").style.display = "block";

            const res = await fetch(`/job_logs/${job_id}`);
            const data = await res.json();

            let html = "";

            for (let chunk in data.logs) {

                html += `
                    <div style="margin-bottom:10px; padding:10px; background:#1e293b; border-radius:8px;">
                        <b>Chunk ${chunk}</b><br>
                        <pre>${data.logs[chunk] || "No logs"}</pre>
                        <pre style="color:red;">${data.errors[chunk] || ""}</pre>
                    </div>
                `;
            }

            document.getElementById("logContent").innerHTML = html;
        }


        function closeLogs() {
            document.getElementById("logPanel").style.display = "none";
        }        

        async function submitJob() {

            const file = document.getElementById("file").files[0];
            const chunks = document.getElementById("chunks").value;
            const reducer = document.getElementById("reducer").value;

            if (!file) {
                alert("Please upload a file");
                return;
            }

            const formData = new FormData();
            formData.append("file", file);
            formData.append("chunks", chunks);
            formData.append("reducer", reducer);

            const res = await fetch('/submit_job', {
                method: 'POST',
                body: formData
            });

            const data = await res.json();

            document.getElementById("submitStatus").innerText =
                "Job submitted: " + data.job_id;

            trackJob(data.job_id);
        }

        
        async function cancelJob(job_id) {

            if (!confirm("Are you sure you want to cancel this job?")) {
                return;
            }

            const res = await fetch(`/cancel_job/${job_id}`, {
                method: "POST"
            });

            const data = await res.json();

            alert("Job cancelled: " + job_id);
        }


        async function trackJob(job_id) {

            let interval = setInterval(async () => {

                const status = await fetch(`/job_status/${job_id}`).then(r => r.json());

                if (status.status === "completed") {

                    clearInterval(interval);

                    const result = await fetch(`/job_result/${job_id}`).then(r => r.json());

                    alert("✅ Job Completed!\\nResult: " + JSON.stringify(result.result));
                }

                if (status.status === "failed") {

                    clearInterval(interval);

                    const logs = await fetch(`/job_logs/${job_id}`).then(r => r.json());

                    alert("❌ Job Failed!\\nErrors: " + JSON.stringify(logs.errors));
                }

            }, 2000);
        }


        async function fetchData() {

            const nodes = await fetch('/cluster_status').then(r => r.json());
            const jobs = await fetch('/all_jobs').then(r => r.json());

            // ---- NODES ----
            const nodeHTML = nodes.connected_nodes.map(n => `
                <div class="card">
                    <b>Node:</b> ${n}
                </div>
            `).join('');

            document.getElementById("nodes").innerHTML = nodeHTML;

            // ---- JOBS ----
            const jobHTML = Object.entries(jobs).map(([id, j]) => {

                let percent = Math.floor((j.completed / j.total) * 100);

                let statusClass = "status-running";
                if (j.status === "completed") statusClass = "status-completed";
                if (j.status === "failed") statusClass = "status-failed";
                if (j.status === "cancelled") statusClass = "status-cancelled";
                
                return `
                    <div class="card" onclick="viewLogs('${id}')">
                        <b>Job ID:</b> ${id}<br>
                        <b>Status:</b> <span class="${statusClass}">${j.status}</span><br>
                        <b>Progress:</b> ${j.completed}/${j.total}

                        <div class="progress-bar">
                            <div class="progress-fill" style="width:${percent}%"></div>
                        </div>

                        ${j.status === "running" ? `
                            <button onclick="event.stopPropagation(); cancelJob('${id}')" 
                            style="background:#ef4444; margin-top:10px;">
                                Cancel Job
                            </button>
                        ` : ""}
                    </div>
                `;
            }).join('');

            document.getElementById("jobs").innerHTML = jobHTML;
        }

        setInterval(fetchData, 2000);
        fetchData();

        </script>
        <!-- LOG PANEL -->
        <div id="logPanel" style="
            position: fixed;
            right: 0;
            top: 0;
            width: 400px;
            height: 100%;
            background: #020617;
            padding: 15px;
            overflow-y: auto;
            display: none;
            border-left: 2px solid #38bdf8;
        ">

            <h2>📄 Job Logs</h2>
            <button onclick="closeLogs()">Close</button>

            <div id="logContent" style="margin-top: 10px;"></div>
        </div>
    </body>
    </html>
    """



@app.get("/all_jobs")
def all_jobs():

    output = {}

    for job_id, job in jobs.items():
        output[job_id] = {
            "status": job["status"],
            "completed": job["completed"],
            "total": job["chunks"]
        }

    return output



@app.post("/cancel_job/{job_id}")
def cancel_job(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    job["status"] = "cancelled"

    return {
        "job_id": job_id,
        "status": "cancelled"
    }



@app.get("/job_status_simple/{job_id}")
def job_status_simple(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"status": "not_found"}

    return {"status": job["status"]}