from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
import json
import os
import uuid
import asyncio
import time

from relay.job_persistence import load_jobs, save_jobs

app = FastAPI()

# -----------------------------
# Storage
# -----------------------------
JOB_DIR = "jobs"
os.makedirs(JOB_DIR, exist_ok=True)

connected_nodes = {}
node_resources = {}
node_last_seen = {}

jobs = load_jobs()

# -----------------------------
# CONFIG
# -----------------------------
MAX_RETRIES = 2
CHUNK_TIMEOUT = 60
NODE_TIMEOUT = 60


# -----------------------------
# SAFE SEND
# -----------------------------
async def safe_send(ws, message, node_id=None):
    try:
        if ws.client_state.name == "CONNECTED":
            await ws.send_text(json.dumps(message))
    except Exception as e:
        if node_id:
            print(f"[Relay] Removing dead node {node_id}: {e}")
            connected_nodes.pop(node_id, None)
            node_resources.pop(node_id, None)
            node_last_seen.pop(node_id, None)


# -----------------------------
# HEARTBEAT + CLEANUP
# -----------------------------
async def heartbeat_loop():
    while True:
        await asyncio.sleep(20)

        now = time.time()

        for node_id, ws in list(connected_nodes.items()):

            last_seen = node_last_seen.get(node_id, now)

            # 🔥 remove stale nodes
            if now - last_seen > NODE_TIMEOUT:
                print(f"[Relay] Removing stale node {node_id}")
                connected_nodes.pop(node_id, None)
                node_resources.pop(node_id, None)
                node_last_seen.pop(node_id, None)
                continue

            await safe_send(ws, {"type": "heartbeat"}, node_id)


# -----------------------------
# JOB MONITOR (RETRIES)
# -----------------------------
async def monitor_jobs():
    while True:
        await asyncio.sleep(5)

        for job_id, job in jobs.items():

            if job["status"] != "running":
                continue

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

                        job["queue"].append(int(chunk))
                        job["status_map"][chunk] = "pending"
                        job["retries"][chunk] += 1

                    else:
                        print(f"[Failed] chunk {chunk}")

                        job["status_map"][chunk] = "failed"
                        job["errors"][chunk] = "Max retries exceeded"

        save_jobs(jobs)


# -----------------------------
# HELPERS
# -----------------------------
def get_node_capacity(node_id):
    res = node_resources.get(node_id, {})
    cpu = res.get("cpu", 100)
    ram = res.get("ram", 100)

    return max(1, int((100 - cpu) * 0.7 + (100 - ram) * 0.3))


def auto_calculate_chunks():
    if not node_resources:
        return 5

    total = sum(get_node_capacity(n) for n in node_resources)
    chunks = total // 10

    return min(max(chunks, 5), 100)


def apply_reducer(results, reducer):
    values = [v for v in results.values() if v is not None]

    if not values:
        return None

    if reducer == "sum":
        return sum(v for v in values if isinstance(v, (int, float)))
    if reducer == "avg":
        nums = [v for v in values if isinstance(v, (int, float))]
        return sum(nums) / len(nums) if nums else None
    if reducer == "max":
        return max(values)
    if reducer == "min":
        return min(values)
    if reducer == "list":
        return values

    return None


# -----------------------------
# STARTUP
# -----------------------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(monitor_jobs())


# -----------------------------
# BASIC API
# -----------------------------
@app.get("/")
def root():
    return {"message": "Relay running"}


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
    return FileResponse(path) if os.path.exists(path) else {"error": "not found"}


# -----------------------------
# JOB SUBMISSION
# -----------------------------
@app.post("/submit_job")
async def submit_job(file: UploadFile = File(...), chunks: int = Form(None), reducer: str = Form("sum")):

    if not chunks or chunks <= 0:
        chunks = auto_calculate_chunks()

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

    save_jobs(jobs)

    return {"job_id": job_id, "chunks": chunks}


# -----------------------------
# WEBSOCKET
# -----------------------------
@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):

    await websocket.accept()

    connected_nodes[node_id] = websocket
    node_last_seen[node_id] = time.time()

    print(f"Node connected: {node_id}")

    try:
        while True:

            data = await websocket.receive_text()
            message = json.loads(data)

            node_last_seen[node_id] = time.time()

            msg_type = message.get("type")

            if msg_type == "resource_update":
                node_resources[node_id] = message["payload"]

            elif msg_type == "request_chunk":

                node_capacity = get_node_capacity(node_id)
                batch_size = max(1, node_capacity // 20)

                # pick job
                best_job = None
                best_score = float("inf")

                for jid, job in jobs.items():
                    if job["status"] != "running" or not job["queue"]:
                        continue

                    progress = job["completed"] / job["chunks"]

                    if progress < best_score:
                        best_score = progress
                        best_job = (jid, job)

                if not best_job:
                    continue   # 🔥 FIX (NOT return)

                jid, job = best_job

                assigned = []

                for _ in range(batch_size):
                    if not job["queue"]:
                        break

                    chunk = job["queue"].pop(0)

                    job["status_map"][str(chunk)] = "running"
                    job["assigned_at"][str(chunk)] = time.time()
                    job["retries"].setdefault(str(chunk), 0)

                    assigned.append(chunk)

                if assigned:
                    await safe_send(websocket, {
                        "type": "assign_chunk_batch",
                        "payload": {
                            "job_id": jid,
                            "chunks": assigned,
                            "total_chunks": job["chunks"]
                        }
                    }, node_id)

            elif msg_type == "submit_result":

                payload = message["payload"]
                job_id = payload["job_id"]
                chunk = str(payload["chunk"])

                job = jobs.get(job_id)
                if not job or job["status"] == "cancelled":
                    continue

                if chunk in job["results"]:
                    continue

                try:
                    val = int(payload["result"])
                except:
                    try:
                        val = float(payload["result"])
                    except:
                        val = None

                job["results"][chunk] = val
                job["logs"][chunk] = payload.get("logs", "")
                job["errors"][chunk] = payload.get("error", "")
                job["status_map"][chunk] = "completed"
                job["completed"] += 1

                if job["completed"] == job["chunks"]:
                    job["status"] = "completed"

                save_jobs(jobs)

    except WebSocketDisconnect:
        print(f"Node disconnected: {node_id}")
        connected_nodes.pop(node_id, None)
        node_resources.pop(node_id, None)
        node_last_seen.pop(node_id, None)


# -----------------------------
# JOB APIs
# -----------------------------
@app.get("/job_status/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"error": "not found"}

    return {
        "status": job["status"],
        "completed": job["completed"],
        "total": job["chunks"]
    }


@app.get("/job_result/{job_id}")
def job_result(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return {"status": "running"}

    return {"result": apply_reducer(job["results"], job["reducer"])}


@app.get("/all_jobs")
def all_jobs():
    out = {}
    for jid, job in jobs.items():
        out[jid] = {
            "status": job["status"],
            "completed": job["completed"],
            "total": job["chunks"],
            "result": apply_reducer(job["results"], job["reducer"]) if job["status"] == "completed" else None
        }
    return out


@app.post("/cancel_job/{job_id}")
def cancel_job(job_id: str):
    if job_id in jobs:
        jobs[job_id]["status"] = "cancelled"
    return {"status": "cancelled"}


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

            <input type="number" id="chunks" placeholder="Auto"><br><br>

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

        let logInterval = null;
        let currentJobId = null;

        async function viewLogs(job_id) {

            document.getElementById("logPanel").style.display = "block";

            currentJobId = job_id;

            // clear previous loop if any
            if (logInterval) {
                clearInterval(logInterval);
            }

            async function fetchLogs() {

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

            // run immediately
            fetchLogs();

            // 🔥 live update every 1 sec
            logInterval = setInterval(fetchLogs, 1000);
        }


        function closeLogs() {

            document.getElementById("logPanel").style.display = "none";

            if (logInterval) {
                clearInterval(logInterval);
                logInterval = null;
            }
        }       

        async function submitJob() {

            const file = document.getElementById("file").files[0];

            let chunks = document.getElementById("chunks").value;
            if (!chunks) {
                chunks = null;}        

            const reducer = document.getElementById("reducer").value;

            if (!file) {
                alert("Please upload a file");
                return;
            }

            const formData = new FormData();
            formData.append("file", file);

            if (chunks !== null) {
                formData.append("chunks", chunks);
            }            

            formData.append("reducer", reducer);

            const res = await fetch('/submit_job', {
                method: 'POST',
                body: formData
            });

            const data = await res.json();

            document.getElementById("submitStatus").innerText =
                "Job submitted: " + data.job_id +  " | Chunks: " + data.chunks;

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

                        ${j.status === "completed" ? `
                            <div style="margin-top:10px; color:#22c55e;">
                                <b>Result:</b> ${j.result}
                            </div>
                        ` : ""}

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

