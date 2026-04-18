from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
import json
import os
import uuid
import asyncio
import time
import hashlib
import random
import zipfile
from relay.job_persistence import load_jobs, save_jobs
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing Supabase environment variables")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
# -----------------------------
# Storage
# -----------------------------
JOB_DIR = "jobs"
os.makedirs(JOB_DIR, exist_ok=True)

connected_nodes = {}
node_resources = {}
node_last_seen = {}
node_stats = {}
jobs = load_jobs()
node_owner_map = {}
credit_update_lock = asyncio.Lock()
last_peer_list = set()


# -----------------------------
# CONFIG
# -----------------------------
MAX_RETRIES = 3
CHUNK_TIMEOUT = 60
NODE_TIMEOUT = 60
VERIFY_TIMEOUT = 45


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


async def broadcast_peer_list():
    global last_peer_list

    while True:
        await asyncio.sleep(5)

        current = set(connected_nodes.keys())
        if current == last_peer_list:
            continue

        last_peer_list = current
        print(f"[Relay] Broadcasting peers: {current}")

        message = {
            "type": "peer_list",
            "nodes": list(current)
        }

        for node_id, ws in list(connected_nodes.items()):
            await safe_send(ws, message, node_id)


# -----------------------------
# JOB MONITOR (RETRIES)
# -----------------------------
async def monitor_jobs():
    while True:
        await asyncio.sleep(5)

        for job_id, job in jobs.items():

            if job["status"] != "running":
                continue
            job.setdefault("retry_count", {})

            for chunk, status in list(job["status_map"].items()):

                if status != "running":
                    continue

                verify_requests = job.get("verify_requests", {})
                verify_started_at = job.get("verify_started_at", {})

                if chunk in verify_requests and chunk not in job["results"]:
                    started_at = verify_started_at.get(chunk)
                    if started_at and time.time() - started_at > VERIFY_TIMEOUT:
                        assigned_node = verify_requests.get(chunk)
                        if assigned_node:
                            stats = get_node_stats(assigned_node)
                            stats["timeouts"] += 1

                        retry_map = job.setdefault("retry_count", {})
                        retry_map[chunk] = retry_map.get(chunk, 0) + 1

                        if retry_map[chunk] > MAX_RETRIES:
                            print(f"[Verify] Chunk {chunk} failed permanently ❌")
                            job["status_map"][chunk] = "failed"
                            while int(chunk) in job["queue"]:
                                job["queue"].remove(int(chunk))
                            job.get("verifications", {}).pop(chunk, None)
                            job.get("verify_requests", {}).pop(chunk, None)
                            job.get("verification_originals", {}).pop(chunk, None)
                            job.get("verify_started_at", {}).pop(chunk, None)
                            job["errors"][chunk] = "Verification retries exceeded"
                            failed_chunks = [
                                c for c, s in job["status_map"].items()
                                if s == "failed"
                            ]
                            running_chunks = [
                                c for c, s in job["status_map"].items()
                                if s == "running"
                            ]
                            completed_chunks = [
                                c for c, s in job["status_map"].items()
                                if s == "completed"
                            ]
                            if failed_chunks and not running_chunks and len(failed_chunks) + len(completed_chunks) == job["chunks"]:
                                job["status"] = "failed"
                                job["completed_at"] = time.time()
                            job["updated_at"] = time.time()
                            continue

                        print(f"[Verify Timeout] chunk {chunk} for job {job_id}")
                        job.get("verifications", {}).pop(chunk, None)
                        job.get("verify_requests", {}).pop(chunk, None)
                        job.get("verification_originals", {}).pop(chunk, None)
                        job.get("verify_started_at", {}).pop(chunk, None)

                        if int(chunk) not in job["queue"]:
                            job["queue"].append(int(chunk))

                        job["status_map"][chunk] = "pending"
                        job["updated_at"] = time.time()
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
                        job.get("verifications", {}).pop(chunk, None)
                        job.get("verify_requests", {}).pop(chunk, None)
                        job.get("verification_originals", {}).pop(chunk, None)
                        job.get("verify_started_at", {}).pop(chunk, None)

                        job["errors"][chunk] = f"Retry {job['retries'][chunk]}"
                        job["updated_at"] = time.time()

                    else:
                        print(f"[Failed] chunk {chunk}")

                        job["status_map"][chunk] = "failed"
                        job["errors"][chunk] = "Max retries exceeded"
                        job.get("verifications", {}).pop(chunk, None)
                        job.get("verify_requests", {}).pop(chunk, None)
                        job.get("verification_originals", {}).pop(chunk, None)
                        job.get("verify_started_at", {}).pop(chunk, None)

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
def get_node_stats(node_id):
    return node_stats.setdefault(node_id, {
        "success": 0,
        "failures": 0,
        "mismatches": 0,
        "timeouts": 0
    })


def get_node_score(node_id):
    stats = get_node_stats(node_id)
    total = (
        stats["success"] +
        stats["failures"] +
        stats["mismatches"] +
        stats["timeouts"]
    )

    if total == 0:
        return 1.0

    return stats["success"] / total


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


def extract_config(zip_path):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            names = set(z.namelist())
            if "task.py" not in names:
                return {"error": "task.py is required in the root of the ZIP"}
            if "config.json" in names:
                return json.loads(z.read("config.json"))
    except zipfile.BadZipFile:
        return {"error": "invalid zip file"}
    except Exception as e:
        return {"error": f"failed to parse job package: {e}"}
    return None


def create_range_chunks(start, end, size):
    chunks = []
    chunk_id = 1
    for i in range(start, end, size):
        chunks.append({
            "id": chunk_id,
            "start": i,
            "end": min(i + size, end)
        })
        chunk_id += 1
    return chunks


def create_file_chunks(files):
    return [{"id": i + 1, "file": f} for i, f in enumerate(files)]


def build_chunks_data(config, fallback_chunks):
    if not config or not isinstance(config, dict):
        return [{"id": i} for i in range(1, fallback_chunks + 1)]

    chunk_type = config.get("chunk_type")

    if chunk_type == "range":
        start = int(config.get("start", 0))
        end = int(config.get("end", 0))
        size = int(config.get("chunk_size", 0))
        if size <= 0 or end <= start:
            return {"error": "invalid range config"}
        return create_range_chunks(start, end, size)

    if chunk_type == "file_list":
        files = config.get("files")
        if not isinstance(files, list) or not files:
            return {"error": "invalid file_list config"}
        return create_file_chunks(files)

    return [{"id": i} for i in range(1, fallback_chunks + 1)]


def parse_result_value(raw_result):
    if raw_result is None:
        parsed_result = ""
    else:
        parsed_result = str(raw_result).strip().splitlines()
        parsed_result = parsed_result[-1].strip() if parsed_result else ""

    try:
        return int(parsed_result)
    except Exception:
        try:
            return float(parsed_result)
        except Exception:
            return None


async def forward_verify_chunk(job, source_node_id, job_id, chunk_key):
    verify_requests = job.setdefault("verify_requests", {})

    if chunk_key in verify_requests:
        return

    candidate_nodes = [
        nid for nid in connected_nodes.keys()
        if nid != source_node_id
    ]

    if not candidate_nodes:
        print(f"[Verify] No eligible peer for chunk {chunk_key} in job {job_id}")
        return

    target_node = random.choice(candidate_nodes)
    target_ws = connected_nodes.get(target_node)
    if not target_ws:
        return

    await safe_send(target_ws, {
        "type": "verify_chunk",
        "payload": {
            "job_id": job_id,
            "chunk": int(chunk_key)
        }
    }, target_node)

    verify_requests[chunk_key] = target_node
    print(f"[Verify] Forwarded chunk {chunk_key} for job {job_id} to {target_node}")


# -----------------------------
# STARTUP
# -----------------------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(monitor_jobs())
    asyncio.create_task(periodic_save())
    asyncio.create_task(broadcast_peer_list())


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

    nodes = []

    for node_id in connected_nodes.keys():
        stats = node_resources.get(node_id, {})

        nodes.append({
            "id": node_id,
            "cpu": stats.get("cpu", 0),
            "ram": stats.get("ram", 0)
        })

    return {
        "connected_nodes": nodes,
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
    job_name: str = Form("Untitled Job"),
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

    if not chunks or chunks <= 0:
        chunks = auto_calculate_chunks()

    job_id = str(uuid.uuid4())

    path = os.path.join(JOB_DIR, f"{job_id}.zip")
    with open(path, "wb") as f:
        f.write(await file.read())

    config = extract_config(path)
    if isinstance(config, dict) and config.get("error"):
        return {"error": config["error"]}

    chunks_data = build_chunks_data(config, chunks)
    if isinstance(chunks_data, dict) and chunks_data.get("error"):
        return {"error": chunks_data["error"]}

    total_chunks = len(chunks_data)
    if total_chunks <= 0:
        return {"error": "no chunks generated from config"}

    new_credits = user["credits"] - price
    update_user_credits_by_api_key(user["api_key"], new_credits)

    jobs[job_id] = {
        "name": job_name,
        "chunks": total_chunks,
        "chunks_data": chunks_data,
        "queue": [c["id"] for c in chunks_data],
        "results": {},
        "verifications": {},
        "rewarded_chunks": set(),
        "logs": {},
        "errors": {},
        "status_map": {},
        "assigned_at": {},
        "retries": {},
        "retry_count": {},
        "status": "running",
        "reducer": reducer,
        "price": price,
        "owner": user["user_id"],
        "created_at": time.time(),
        "updated_at": time.time()
    }

    save_jobs(jobs)

    return {"job_id": job_id, "chunks": total_chunks}


# -----------------------------
# WEBSOCKET
# -----------------------------
@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):


    api_key = websocket.query_params.get("api_key")
    user = get_user_by_api_key(api_key)

    if not user:
        print("❌ INVALID API KEY")
        await websocket.close(code=1008)
        return

    await websocket.accept()

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

                    if job["status"] != "running":
                        continue

                    if not job["queue"]:
                        continue

                    owner = job.get("owner")

                    user_load = get_user_load(owner)

                    completed = len(job["results"])
                    total = job["chunks"]

                    progress = completed / total if total else 1

                    size_penalty = (total ** 0.5) / 15

                    # 🔥 NEW: fairness penalty
                    fairness_penalty = user_load * 0.1

                    node_score = get_node_score(node_id)
                    reliability_penalty = (1 - node_score) * 2
                    score = progress + size_penalty + fairness_penalty + reliability_penalty

                    score += random.uniform(0, 0.05)

                    if score < best_score:
                        best_score = score
                        best_job = (jid, job)

                if not best_job:
                    continue

                jid, job = best_job
                if job.get("status") in ("completed", "failed"):
                    continue

                node_capacity = get_node_capacity(node_id)
                total_available = len(job["queue"])

                node_score = get_node_score(node_id)
                adjusted_capacity = int(node_capacity * node_score)
                batch_size = min(
                    max(1, adjusted_capacity // 20),
                    max(1, total_available)
                )
                if get_node_score(node_id) < 0.2:
                    continue

                assigned = []

                for _ in range(batch_size):
                    chunk = None

                    while job["queue"] and chunk is None:
                        candidate = job["queue"].pop(0)
                        candidate_key = str(candidate)

                        if candidate_key in job["results"]:
                            continue

                        candidate_status = job["status_map"].get(candidate_key)
                        verification_count = len(
                            job.get("verifications", {}).get(candidate_key, {})
                        )

                        # Allow reassignment while the chunk is not completed and
                        # still needs independent verification votes.
                        if candidate_status in ("completed", "failed") or verification_count >= 2:
                            continue

                        # Skip assigning the same verification chunk to a node
                        # that has already submitted a vote for it.
                        if node_id in job.get("verifications", {}).get(candidate_key, {}):
                            continue

                        chunk = candidate

                    if chunk is None:
                        break

                    chunk_key = str(chunk)
                    job["status_map"][chunk_key] = "running"
                    job["assigned_at"][chunk_key] = time.time()
                    job["retries"].setdefault(chunk_key, 0)

                    assigned.append(chunk)

                if assigned:
                    chunk_data_map = {
                        str(c["id"]): c
                        for c in job.get("chunks_data", [])
                    }
                    await safe_send(websocket, {
                        "type": "assign_chunk_batch",
                        "payload": {
                            "job_id": jid,
                            "chunks": assigned,
                            "total_chunks": job["chunks"],
                            "chunk_data": chunk_data_map
                        }
                    }, node_id)

            elif msg_type == "submit_result":

                print("RESULT RECEIVED FROM:", node_id)
                print("FULL MESSAGE:", message)
                payload = message["payload"]
                job_id = payload["job_id"]
                chunk = str(payload["chunk"])
                chunk_key = str(chunk)
                status = payload.get("status", "success")

                job = jobs.get(job_id)
                if not job or job["status"] == "cancelled":
                    continue

                if chunk_key in job["results"]:
                    continue
                if job["status_map"].get(chunk_key) == "completed":
                    continue
                if job["status_map"].get(chunk_key) == "failed":
                    continue

                if status == "failed":
                    stats = get_node_stats(node_id)
                    stats["failures"] += 1

                    failed_nodes_map = job.setdefault("failed_nodes", {})
                    failed_node_set = failed_nodes_map.setdefault(chunk_key, set())
                    if isinstance(failed_node_set, list):
                        failed_node_set = set(failed_node_set)
                        failed_nodes_map[chunk_key] = failed_node_set
                    failed_node_set.add(node_id)

                    available_nodes = set(connected_nodes.keys())
                    failed_nodes = failed_node_set

                    if failed_nodes.issuperset(available_nodes) and available_nodes:
                        print(f"[All nodes failed chunk {chunk}]")

                        job["status_map"][chunk_key] = "failed"
                        job["errors"][chunk_key] = "All nodes failed execution"

                        while int(chunk) in job["queue"]:
                            job["queue"].remove(int(chunk))
                    else:
                        retries = job["retries"].get(chunk_key, 0)

                        if retries < MAX_RETRIES:
                            print(f"[Retry Triggered] chunk {chunk} for job {job_id}")

                            if int(chunk) not in job["queue"]:
                                job["queue"].append(int(chunk))
                            job["status_map"][chunk_key] = "pending"
                            job["retries"][chunk_key] = retries + 1
                            job["errors"][chunk_key] = payload.get("error", "Execution failed")

                        else:
                            print(f"[Permanent Failure] chunk {chunk}")

                            job["status_map"][chunk_key] = "failed"
                            job["errors"][chunk_key] = "Max retries exceeded"
                            while int(chunk) in job["queue"]:
                                job["queue"].remove(int(chunk))

                    job["logs"].setdefault(chunk_key, "")
                    job["logs"][chunk_key] += payload.get("logs") or ""
                    job.get("verify_requests", {}).pop(chunk_key, None)
                    job.get("verification_originals", {}).pop(chunk_key, None)
                    job.get("verify_started_at", {}).pop(chunk_key, None)
                    job["updated_at"] = time.time()
                    print("Retries:", job["retries"])

                    completed_chunks = [
                        c for c, s in job["status_map"].items()
                        if s == "completed"
                    ]

                    failed_chunks = [
                        c for c, s in job["status_map"].items()
                        if s == "failed"
                    ]

                    if len(completed_chunks) == job["chunks"]:
                        job["status"] = "completed"
                        job["completed_at"] = time.time()
                    elif failed_chunks and len(failed_chunks) + len(completed_chunks) == job["chunks"]:
                        job["status"] = "failed"
                        job["completed_at"] = time.time()

                    asyncio.create_task(asyncio.to_thread(save_jobs, jobs))
                    continue

                raw_result = payload.get("result")
                val = parse_result_value(raw_result)

                verification_map = job.setdefault("verifications", {})
                chunk_verify = verification_map.setdefault(chunk_key, {})
                originals = job.setdefault("verification_originals", {})
                verify_started_at = job.setdefault("verify_started_at", {})

                if node_id in chunk_verify:
                    continue

                chunk_verify[node_id] = val
                originals.setdefault(chunk_key, {
                    "source": node_id,
                    "result": raw_result
                })

                if len(chunk_verify) == 1:
                    await forward_verify_chunk(job, node_id, job_id, chunk_key)
                    verify_started_at[chunk_key] = time.time()

                # submit_result only records and forwards. verify_result finalizes.
                job["logs"].setdefault(chunk_key, "")
                job["logs"][chunk_key] += payload.get("logs") or ""
                job["errors"][chunk_key] = payload.get("error", "")
                job["status_map"][chunk_key] = "running"
                job["updated_at"] = time.time()
                asyncio.create_task(asyncio.to_thread(save_jobs, jobs))
                continue

            elif msg_type == "verify_result":

                payload = message["payload"]
                job_id = payload["job_id"]
                chunk_key = str(payload["chunk"])

                job = jobs.get(job_id)
                if not job or job["status"] == "cancelled":
                    continue

                if chunk_key in job["results"]:
                    continue
                if job["status_map"].get(chunk_key) == "completed":
                    continue
                if job["status_map"].get(chunk_key) == "failed":
                    continue

                originals = job.get("verification_originals", {})
                original = originals.get(chunk_key)
                if not original:
                    print(f"[Verify] Missing original result for chunk {chunk_key} in job {job_id}")
                    continue

                original_source = original.get("source")
                if node_id == original_source:
                    print(f"[Verify] Ignoring self-verification for chunk {chunk_key}")
                    continue

                original_result = original.get("result")
                verify_result = payload.get("result")
                original_val = parse_result_value(original_result)
                verify_val = parse_result_value(verify_result)

                if original_val == verify_val:
                    print(f"[Verify] Chunk {chunk_key} verified by {node_id} against {original_source} ✅")
                else:
                    print(f"[Verify] Chunk {chunk_key} mismatch ❌")

                verification_map = job.get("verifications", {})
                chunk_verify = verification_map.get(chunk_key)

                if chunk_verify and node_id in chunk_verify:
                    continue

                verification_map = job.setdefault("verifications", {})
                chunk_verify = verification_map.setdefault(chunk_key, {})
                chunk_verify[node_id] = verify_val

                if original_val == verify_val:
                    job["results"][chunk_key] = original_val
                    job["status_map"][chunk_key] = "completed"
                    job.get("retry_count", {}).pop(chunk_key, None)

                    price = job.get("price", 0)
                    rewarded_chunks = job.setdefault("rewarded_chunks", set())

                    if price > 0 and chunk_key not in rewarded_chunks:
                        reward_per_chunk = price / job["chunks"]
                        reward_per_node = round(reward_per_chunk / len(chunk_verify), 4)

                        for node in chunk_verify.keys():
                            user_id = node_owner_map.get(node)

                            if not user_id:
                                continue

                            try:
                                user = get_user_by_id(user_id)

                                if not user:
                                    continue

                                api_key = user["api_key"].strip()

                                try:
                                    supabase.rpc("increment_credits", {
                                        "user_api_key": api_key,
                                        "amount": reward_per_node
                                    }).execute()
                                except Exception:
                                    async with credit_update_lock:
                                        user = get_user_by_id(user_id)
                                        if not user:
                                            continue
                                        api_key = user["api_key"].strip()
                                        new_credits = user["credits"] + reward_per_node
                                        update_user_credits_by_api_key(api_key, new_credits)

                                print(f"[Reward] {reward_per_node} -> node {node}")

                            except Exception as e:
                                print("❌ Reward update failed:", e)
                        rewarded_chunks.add(chunk_key)

                    for node in chunk_verify.keys():
                        stats = get_node_stats(node)
                        stats["success"] += 1

                    job.get("verifications", {}).pop(chunk_key, None)
                    job.get("failed_nodes", {}).pop(chunk_key, None)
                    job.get("verify_requests", {}).pop(chunk_key, None)
                    job.get("verification_originals", {}).pop(chunk_key, None)
                    job.get("verify_started_at", {}).pop(chunk_key, None)
                    job["logs"].setdefault(chunk_key, "")
                    verify_logs = payload.get("logs", "")
                    if verify_logs:
                        job["logs"][chunk_key] += f"\n[VERIFY]\n{verify_logs}"
                    job["errors"][chunk_key] = payload.get("error", "")
                else:
                    stats = get_node_stats(node_id)
                    stats["mismatches"] += 1

                    job.setdefault("mismatch_count", 0)
                    job["mismatch_count"] += 1
                    retry_map = job.setdefault("retry_count", {})
                    retry_map[chunk_key] = retry_map.get(chunk_key, 0) + 1

                    if retry_map[chunk_key] > MAX_RETRIES:
                        print(f"[Verify] Chunk {chunk_key} failed permanently ❌")
                        job["status_map"][chunk_key] = "failed"
                        while int(chunk_key) in job["queue"]:
                            job["queue"].remove(int(chunk_key))
                        job.get("verifications", {}).pop(chunk_key, None)
                        job.get("verify_requests", {}).pop(chunk_key, None)
                        job.get("verification_originals", {}).pop(chunk_key, None)
                        job.get("verify_started_at", {}).pop(chunk_key, None)
                        job["errors"][chunk_key] = "Verification retries exceeded"
                    else:
                        job["verifications"][chunk_key] = {}
                        job.get("verify_requests", {}).pop(chunk_key, None)
                        job.get("verification_originals", {}).pop(chunk_key, None)
                        job.get("verify_started_at", {}).pop(chunk_key, None)

                        if retry_map[chunk_key] <= MAX_RETRIES and int(chunk_key) not in job["queue"]:
                            job["queue"].append(int(chunk_key))

                        job["status_map"][chunk_key] = "pending"

                job["updated_at"] = time.time()

                completed_chunks = [
                    c for c, s in job["status_map"].items()
                    if s == "completed"
                ]

                failed_chunks = [
                    c for c, s in job["status_map"].items()
                    if s == "failed"
                ]

                if len(completed_chunks) == job["chunks"]:
                    job["status"] = "completed"
                    job["completed_at"] = time.time()
                elif failed_chunks and len(failed_chunks) + len(completed_chunks) == job["chunks"]:
                    job["status"] = "failed"
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
def job_result(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return {"error": "invalid api key"}

    job = jobs.get(job_id)
    if not job or job.get("owner") != user["user_id"]:
        return {"error": "unauthorized"}

    if job["status"] != "completed":
        return {"status": "running"}

    return {"result": apply_reducer(job["results"], job["reducer"])}


@app.get("/all_jobs")
def all_jobs(api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return {"error": "invalid api key"}

    user_id = user["user_id"]
    out = {}

    now = time.time()

    for jid, job in jobs.items():
        if job.get("owner") != user_id:
            continue

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
            "name": job.get("name", "Untitled Job"),
            "status": job["status"],
            "completed": completed,
            "total": total,
            "result": apply_reducer(job["results"], job["reducer"]) if job["status"] == "completed" else None,
            "duration": duration,
            "speed": speed
        }

    return out


@app.post("/cancel_job/{job_id}")
def cancel_job(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return {"error": "invalid api key"}

    job = jobs.get(job_id)

    if not job:
        return {"error": "not found"}
    if job.get("owner") != user["user_id"]:
        return {"error": "unauthorized"}

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
def job_logs(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return {"error": "invalid api key"}

    job = jobs.get(job_id)

    if not job or job.get("owner") != user["user_id"]:
        return {"error": "unauthorized"}

    return {
        "job_id": job_id,
        "status": job.get("status"),
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
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
@app.get("/dashboard")
def dashboard():
    file_path = os.path.join(BASE_DIR, "frontend", "dashboard.html")

    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    return FileResponse(file_path)
