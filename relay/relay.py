from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
import json
import os
import uuid
import asyncio
import time
import hashlib
import random

from relay.job_persistence import load_jobs, save_jobs

app = FastAPI()
from supabase import create_client

SUPABASE_URL = "https://cdbbdmhxzlmumthlgesi.supabase.co"
SUPABASE_KEY = "sb_publishable_dap71uPIBSGUM6mvtEjXgQ_ufDpsELi"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
# -----------------------------
# Storage
# -----------------------------
JOB_DIR = "jobs"
os.makedirs(JOB_DIR, exist_ok=True)

connected_nodes = {}
node_resources = {}
node_last_seen = {}
jobs = load_jobs()
node_owner_map = {}


# -----------------------------
# CONFIG
# -----------------------------
MAX_RETRIES = 2
CHUNK_TIMEOUT = 60
NODE_TIMEOUT = 60


# -----------------------------
# USER MANAGEMENT
# -----------------------------
def get_user_by_api_key(api_key):
    res = supabase.table("users").select("*").eq("api_key", api_key).execute()
    return res.data[0] if res.data else None


def get_user_by_id(user_id):
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_credits_by_api_key(api_key, new_credits):

    api_key = api_key.strip()  # 🔥 IMPORTANT FIX

    res = supabase.table("users").update({
        "credits": new_credits
    }).eq("api_key", api_key).execute()

    print("UPDATE RESULT:", res)


def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_load(user_id):
    load = 0

    for job in jobs.values():
        if job.get("owner") != user_id:
            continue

        # count running chunks
        for status in job["status_map"].values():
            if status == "running":
                load += 1

    return load
        

async def periodic_save():
    while True:
        await asyncio.sleep(3)  # 🔥 every 3 sec
        save_jobs(jobs)


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

                        job["errors"][chunk] = f"Retry {job['retries'][chunk]}"
                        job["updated_at"] = time.time()

                    else:
                        print(f"[Failed] chunk {chunk}")

                        job["status_map"][chunk] = "failed"
                        job["errors"][chunk] = "Max retries exceeded"

                        # 🔥 check if job should fail
                        failed_chunks = [
                            c for c, s in job["status_map"].items()
                            if s == "failed"
                        ]

                        pending_chunks = [
                            c for c in job["queue"]
                        ]

                        running_chunks = [
                            c for c, s in job["status_map"].items()
                            if s == "running"
                        ]

                        # if nothing left to process and failures exist → fail job
                        if failed_chunks and not pending_chunks and not running_chunks:
                            job["status"] = "failed"
                            job["completed_at"] = time.time()
                            print(f"[Job Failed] {job_id}")


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
    asyncio.create_task(periodic_save())


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
async def submit_job(
    file: UploadFile = File(...),
    chunks: int = Form(None),
    reducer: str = Form("sum"),
    api_key: str = Form(...),
    price: int = Form(...)
):
    
    try:
        user = get_user_by_api_key(api_key)
    except Exception as e:
        print("❌ Supabase error:", e)
        return {"error": "internal server error"}
    
    if not user:
        return {"error": "invalid api key"}

    if user["credits"] < price:
        return {"error": "insufficient credits"}

    new_credits = user["credits"] - price
    # find api_key of this user
    api_key = user["api_key"]

    update_user_credits_by_api_key(api_key, new_credits) 

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
        "status": "running",
        "reducer": reducer,
        "price": price,
        "owner": user["user_id"],
        "created_at": time.time(),
        "updated_at": time.time()
    }

    save_jobs(jobs)

    return {"job_id": job_id, "chunks": chunks}


# -----------------------------
# WEBSOCKET
# -----------------------------
@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):

    print("🔥 AUTH BLOCK EXECUTING")   # ADD THIS

    await websocket.accept()

    api_key = websocket.query_params.get("api_key")

    print("DEBUG API KEY:", api_key)
    user = get_user_by_api_key(api_key)

    if not user:
        print("❌ INVALID API KEY")
        await websocket.close()
        return

    user_id = user["user_id"]
    node_owner_map[node_id] = user_id
    
    print(f"[Auth] Node {node_id} linked to user {user_id}")
    
    # continue normal flow
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

                best_job = None
                best_score = float("inf")

                for jid, job in jobs.items():

                    if job["status"] != "running" or not job["queue"]:
                        continue

                    owner = job.get("owner")

                    user_load = get_user_load(owner)

                    completed = len(job["results"])
                    total = job["chunks"]

                    progress = completed / total if total else 1

                    size_penalty = (total ** 0.5) / 15

                    # 🔥 NEW: fairness penalty
                    fairness_penalty = user_load * 0.1

                    score = progress + size_penalty + fairness_penalty

                    score += random.uniform(0, 0.05)

                    if score < best_score:
                        best_score = score
                        best_job = (jid, job)

                if not best_job:
                    continue

                jid, job = best_job

                node_capacity = get_node_capacity(node_id)
                batch_size = min(
                    max(1, node_capacity // 20),
                    len(job["queue"])
                    )

                assigned = []

                for _ in range(batch_size):
                    if not job["queue"]:
                        break

                    chunk = job["queue"].pop(0)

                    if str(chunk) in job["results"]:
                        continue

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

                print("RESULT RECEIVED FROM:", node_id)
                print("FULL MESSAGE:", message)
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
                job["updated_at"] = time.time()

                # 🔥 CREDIT REWARD LOGIC
                sender_node_id = node_id
                user_id = node_owner_map.get(sender_node_id)

                if user_id:
                    price = job.get("price", 0)

                    if price > 0:
                        reward = price / job["chunks"]
                        
                        try:
                            user = get_user_by_id(user_id)

                            if not user:
                                print("❌ User not found in DB")
                                continue
                            
                            api_key = user["api_key"].strip()

                            print("DEBUG API KEY USED:", api_key)
                            update_user_credits_by_api_key(api_key, user["credits"] + reward)
                            
                        except Exception as e:
                            print("❌ Credit update failed:", e)
                    else:
                        print("⚠️ No price set for job, skipping reward")
                

                if len(job["results"]) == job["chunks"]:
                    job["status"] = "completed"
                    job["completed_at"] = time.time()

                asyncio.create_task(asyncio.to_thread(save_jobs, jobs))

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

    completed = len(job["results"])

    return {
        "status": job["status"],
        "completed": completed,
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

    now = time.time()

    for jid, job in jobs.items():

        completed = len(job["results"])
        total = job["chunks"]

        end_time = job.get("completed_at", now)
        duration = int(end_time - job.get("created_at", now))

        speed = 0
        if duration > 0:
            speed = round(completed / duration, 2)
        else:
            speed = completed

        out[jid] = {
            "status": job["status"],
            "completed": completed,
            "total": total,
            "result": apply_reducer(job["results"], job["reducer"]) if job["status"] == "completed" else None,
            "duration": duration,
            "speed": speed
        }

    return out


@app.post("/cancel_job/{job_id}")
def cancel_job(job_id: str):
    job = jobs.get(job_id)

    if not job:
        return {"error": "not found"}

    if job["status"] != "running":
        return {"status": job["status"]}

    job["status"] = "cancelled"
    job["completed_at"] = time.time()

    completed = len(job["results"])
    total = job["chunks"]
    price = job.get("price", 0)

    used = (completed / total) * price
    refund = price - used

    user_id = job.get("owner")

    if user_id:
        try:
            user = get_user_by_id(user_id)
            if user:
                api_key = user["api_key"].strip()
                new_credits = user["credits"] + refund

                update_user_credits_by_api_key(api_key, new_credits)

        except Exception as e:
            print("Refund failed:", e)

    job["queue"] = []

    for chunk, status in job["status_map"].items():
        if status == "running":
            job["status_map"][chunk] = "cancelled"

    return {
        "status": "cancelled",
        "refund": refund
    }


@app.get("/job_logs/{job_id}")
def job_logs(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {"error": "job not found"}

    return {
        "job_id": job_id,
        "logs": job.get("logs", {}),
        "errors": job.get("errors", {})
    }


@app.post("/create_user")
def create_user(email: str = Form(...), password: str = Form(...)):

    import uuid

    user_id = f"user_{uuid.uuid4().hex[:6]}"
    api_key = f"key_{uuid.uuid4().hex}"

    supabase.table("users").insert({
        "user_id": user_id,
        "api_key": api_key,
        "email": email,
        "password": hash_password(password),
        "credits": 100
    }).execute()

    return {
        "user_id": user_id,
        "api_key": api_key
    }


@app.get("/user/{api_key}")
def get_user(api_key: str):

    user = get_user_by_api_key(api_key)

    if not user:
        return {"error": "not found"}

    return user


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):

    hashed = hash_password(password)
    res = supabase.table("users")\
        .select("*")\
        .eq("email", email)\
        .eq("password", hashed)\
        .execute()

    if not res.data:
        return {"error": "invalid credentials"}

    user = res.data[0]

    return {
        "api_key": user["api_key"],
        "user_id": user["user_id"],
        "credits": user["credits"]
    }


import os
from fastapi.responses import FileResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/dashboard")
def dashboard():
    file_path = os.path.join(BASE_DIR, "frontend", "dashboard.html")

    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    return FileResponse(file_path)