from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
import json
import os
import uuid
import asyncio
import time
import random
import zipfile
import re
from relay.job_persistence import load_jobs, save_jobs
from db import supabase
import logging
import bcrypt
app = FastAPI()

logger = logging.getLogger("relay")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | relay | %(levelname)s | %(message)s")

def error_response(message, code="ERROR"):
    return {
        "status": "failed",
        "error": message,
        "code": code
    }
# -----------------------------
# Storage
# -----------------------------
JOB_DIR = "jobs"
os.makedirs(JOB_DIR, exist_ok=True)
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
JOB_DIR_ABS = os.path.abspath(JOB_DIR)

connected_nodes = {}
node_resources = {}
node_last_seen = {}
node_stats = {}
jobs = load_jobs()
node_owner_map = {}
credit_update_lock = asyncio.Lock()
save_lock = asyncio.Lock()
jobs_dirty = False
last_peer_list = set()


# -----------------------------
# CONFIG
# -----------------------------
MAX_RETRIES = 3
CHUNK_TIMEOUT = 60
NODE_TIMEOUT = 60
VERIFY_TIMEOUT = 5


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

    logger.info(f"UPDATE RESULT: {res}")


def adjust_user_credits_by_api_key(api_key, amount):
    api_key = api_key.strip()
    try:
        res = supabase.rpc("increment_credits", {
            "user_api_key": api_key,
            "amount": amount
        }).execute()

        # 🔥 STRICT VALIDATION
        if not res or getattr(res, "data", None) is None:
            logger.error(f"RPC returned no data for {api_key}")
            return False

        return True

    except Exception:
        logger.exception("increment_credits RPC failed")
        return False


def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, stored_hash: str):
    try:
        if not stored_hash or not stored_hash.startswith("$2b$"):
            return False

        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except Exception:
        return False

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
        

async def mark_jobs_dirty():
    global jobs_dirty
    async with save_lock:
        jobs_dirty = True


async def periodic_save():
    global jobs_dirty

    while True:
        await asyncio.sleep(10)

        if not jobs_dirty:
            continue

        async with save_lock:
            await asyncio.to_thread(save_jobs, jobs)
            jobs_dirty = False


# -----------------------------
# SAFE SEND
# -----------------------------
async def safe_send(ws, message, node_id=None):
    try:
        if ws.client_state.name == "CONNECTED":
            await ws.send_text(json.dumps(message))
    except Exception as e:
        if node_id:
            logger.warning(f"[Relay] Removing dead node {node_id}: {e}")
            connected_nodes.pop(node_id, None)
            node_resources.pop(node_id, None)
            node_last_seen.pop(node_id, None)


async def send_cleanup_job(job_id, job):
    if job.get("cleanup_sent"):
        return

    for node_id, ws in list(connected_nodes.items()):
        await safe_send(ws, {
            "type": "cleanup_job",
            "payload": {"job_id": job_id}
        }, node_id)

    job["cleanup_sent"] = True


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
                logger.info(f"[Relay] Removing stale node {node_id}")
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
        logger.info(f"[Relay] Broadcasting peers: {current}")

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

                if status not in ["running", "pending"]:
                    continue

                verify_requests = job.get("verify_requests", {})
                verify_started_at = job.get("verify_started_at", {})

                if chunk in verify_requests and chunk not in job["results"]:
                    started_at = verify_started_at.get(chunk)

                    if started_at and time.time() - started_at > VERIFY_TIMEOUT:

                        original = job.get("verification_originals", {}).get(chunk)

                        if original:
                            val = parse_result_value(original.get("result"))

                            logger.info(f"[Force Complete] chunk {chunk} after verify timeout")

                            job.setdefault("results", {})
                            job["results"][chunk] = val

                            job["status_map"][chunk] = "completed"

                            job.get("verify_requests", {}).pop(chunk, None)
                            job.get("verification_originals", {}).pop(chunk, None)
                            job.get("verify_started_at", {}).pop(chunk, None)

                            job["updated_at"] = time.time()

                        continue

                if status == "running" and chunk not in job.get("verify_requests", {}):
                    retries = job["retries"].get(chunk, 0)

                    if retries < MAX_RETRIES:
                        logger.warning(f"[Retry] chunk {chunk} for job {job_id}")

                        async with job["_queue_lock"]:
                            if chunk not in job["queue"]:
                                job["queue"].append(chunk)

                        job["status_map"][chunk] = "pending"
                        job["retries"][chunk] = retries + 1

                        job.get("verifications", {}).pop(chunk, None)
                        job.get("verify_requests", {}).pop(chunk, None)
                        job.get("verification_originals", {}).pop(chunk, None)
                        job.get("verify_started_at", {}).pop(chunk, None)

                        job["errors"][chunk] = f"Retry {job['retries'][chunk]}"
                        job["updated_at"] = time.time()

                    else:
                        logger.error(f"[Failed] chunk {chunk}")

                        job["status_map"][chunk] = "failed"
                        job["errors"][chunk] = "Max retries exceeded"

            # 🔥 FORCE COMPLETION CHECK (CRITICAL FIX)
            # 🔥 convert stuck running -> completed if result exists
            for chunk, status in job["status_map"].items():
                if status == "running" and chunk in job.get("results", {}):
                    logger.info(f"[Fix] Converting stuck chunk {chunk} to completed")
                    job["status_map"][chunk] = "completed"

            total = job["chunks"]

            completed = sum(
                1 for s in job.get("status_map", {}).values()
                if s == "completed"
            )

            failed = sum(
                1 for s in job.get("status_map", {}).values()
                if s == "failed"
            )

            if completed + failed == total and job["status"] != "completed":
                logger.info(f"[FORCE COMPLETE] Job {job_id} completed via monitor")

                job["status"] = "completed"
                job["final_result"] = compute_final_result(job)
                job["completed_at"] = time.time()
                await send_cleanup_job(job_id, job)


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
    values = []

    for v in results.values():
        if isinstance(v, (int, float)):
            values.append(v)
            continue

        if v is None:
            continue

        try:
            values.append(int(v))
        except Exception:
            try:
                values.append(float(v))
            except Exception:
                continue

    if not values:
        logger.warning("No valid numeric values for reducer")
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

    return None


def compute_final_result(job):
    results = job.get("results", {})

    # 🔥 FILL MISSING RESULTS FROM logs OR DEFAULT
    for chunk, status in job.get("status_map", {}).items():
        if status == "completed" and chunk not in results:
            logger.info(f"[Fix] Missing result for chunk {chunk}, setting fallback")
            results[chunk] = None  # or None-safe fallback

    valid_values = [
        v for v in results.values()
        if isinstance(v, (int, float))
    ]

    if not valid_values:
        logger.error("No valid results, marking job failed")
        job["status"] = "failed"
        job["final_result"] = None
        return None

    return apply_reducer(results, job.get("reducer", "sum"))

def extract_config(zip_path):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            names = set(z.namelist())
            if "task.py" not in names:
                return error_response("task.py is required in the root of the ZIP", "ERROR")
            if "config.json" in names:
                return json.loads(z.read("config.json"))
    except zipfile.BadZipFile:
        return error_response("invalid zip file", "ERROR")
    except Exception as e:
        return error_response(f"failed to parse job package: {e}", "ERROR")
    return None


def create_range_chunks(start, end, size):
    chunks = []
    chunk_id = 1
    for i in range(start, end, size):
        chunks.append({
            "id": str(chunk_id),
            "start": i,
            "end": min(i + size, end)
        })
        chunk_id += 1
    return chunks


def create_file_chunks(files):
    return [{"id": str(i + 1), "file": f} for i, f in enumerate(files)]


def build_chunks_data(config, fallback_chunks):
    if not config or not isinstance(config, dict):
        return [{"id": str(i)} for i in range(1, fallback_chunks + 1)]

    chunk_type = config.get("chunk_type")

    if chunk_type == "range":
        start = int(config.get("start", 0))
        end = int(config.get("end", 0))
        size = int(config.get("chunk_size", 0))
        if size <= 0 or end <= start:
            return error_response("invalid range config", "ERROR")
        return create_range_chunks(start, end, size)

    if chunk_type == "file_list":
        files = config.get("files")
        if not isinstance(files, list) or not files:
            return error_response("invalid file_list config", "ERROR")
        return create_file_chunks(files)

    return [{"id": str(i)} for i in range(1, fallback_chunks + 1)]


def parse_result_value(raw_result):
    if raw_result is None:
        return None

    try:
        # extract last number from string
        matches = re.findall(r"-?\d+\.?\d*", str(raw_result))
        if not matches:
            return None

        val = matches[-1]

        if "." in val:
            return float(val)
        return int(val)

    except Exception:
        return None


def get_completed_count(job):
    completed_chunks = job.get("completed_chunks")
    if isinstance(completed_chunks, set):
        return len(completed_chunks)
    if isinstance(completed_chunks, list):
        return len(completed_chunks)
    return len(job.get("results", {}))


def is_valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.fullmatch(job_id or ""))


def safe_job_zip_path(job_id: str) -> str:
    if not is_valid_job_id(job_id):
        raise ValueError("Invalid job_id")

    path = os.path.abspath(os.path.join(JOB_DIR_ABS, f"{job_id}.zip"))
    if os.path.commonpath([JOB_DIR_ABS, path]) != JOB_DIR_ABS:
        raise ValueError("Path traversal detected")
    return path


async def forward_verify_chunk(job, source_node_id, job_id, chunk_key):
    verify_requests = job.setdefault("verify_requests", {})

    if chunk_key in verify_requests:
        return

    candidate_nodes = [
        nid for nid in connected_nodes.keys()
        if nid != source_node_id
    ]

    if not candidate_nodes:
        logger.warning(f"[Verify] No eligible peer for chunk {chunk_key} in job {job_id}")
        return

    target_node = random.choice(candidate_nodes)
    target_ws = connected_nodes.get(target_node)
    if not target_ws:
        return

    await safe_send(target_ws, {
        "type": "verify_chunk",
        "payload": {
            "job_id": job_id,
            "chunk": str(chunk_key)
        }
    }, target_node)

    verify_requests[chunk_key] = target_node
    logger.info(f"[Verify] Forwarded chunk {chunk_key} for job {job_id} to {target_node}")


async def broadcast_job_manifest(job_id, job):
    chunk_data_map = {
        str(c["id"]): c
        for c in job.get("chunks_data", [])
    }
    message = {
        "type": "job_manifest",
        "payload": {
            "job_id": job_id,
            "total_chunks": job.get("chunks", 0),
            "chunk_data": chunk_data_map
        }
    }
    for node_id, ws in list(connected_nodes.items()):
        await safe_send(ws, message, node_id)


# -----------------------------
# STARTUP
# -----------------------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(monitor_jobs())
    asyncio.create_task(periodic_save())
    asyncio.create_task(broadcast_peer_list())


@app.on_event("shutdown")
async def shutdown():
    async with save_lock:
        await asyncio.to_thread(save_jobs, jobs)


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
    try:
        path = safe_job_zip_path(job_id)
    except ValueError:
        return error_response("invalid job id", "ERROR")

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
    except Exception:
        logger.exception("Supabase error during submit_job")
        return error_response("internal server error", "INTERNAL_SERVER_ERROR")
    
    if not user:
        return error_response("invalid api key", "ERROR")

    if user["credits"] < price:
        return error_response("insufficient credits", "ERROR")

    if not chunks or chunks <= 0:
        chunks = auto_calculate_chunks()

    job_id = str(uuid.uuid4())

    path = os.path.join(JOB_DIR, f"{job_id}.zip")
    with open(path, "wb") as f:
        f.write(await file.read())

    config = extract_config(path)
    if isinstance(config, dict) and config.get("status") == "failed":
        return config

    chunks_data = build_chunks_data(config, chunks)
    if isinstance(chunks_data, dict) and chunks_data.get("status") == "failed":
        return chunks_data

    total_chunks = len(chunks_data)
    if total_chunks <= 0:
        return error_response("no chunks generated from config", "ERROR")

    if not adjust_user_credits_by_api_key(user["api_key"], -price):
        async with credit_update_lock:
            user = get_user_by_api_key(user["api_key"])
            if not user or user["credits"] < price:
                return error_response("insufficient credits", "ERROR")
            new_credits = user["credits"] - price
            update_user_credits_by_api_key(user["api_key"], new_credits)

    jobs[job_id] = {
        "name": job_name,
        "chunks": total_chunks,
        "total_chunks": total_chunks,
        "chunks_data": chunks_data,
        "queue": [str(c["id"]) for c in chunks_data],
        "_queue_lock": asyncio.Lock(),
        "results": {},
        "completed_chunks": set(),
        "verifications": {},
        "rewarded_chunks": set(),
        "logs": {},
        "errors": {},
        "status_map": {},
        "assigned_at": {},
        "retries": {},
        "retry_count": {},
        "status": "running",
        "final_result": None,
        "reducer": reducer,
        "price": price,
        "owner": user["user_id"],
        "created_at": time.time(),
        "updated_at": time.time()
    }

    await mark_jobs_dirty()
    asyncio.create_task(broadcast_job_manifest(job_id, jobs[job_id]))

    return {"job_id": job_id, "chunks": total_chunks}


# -----------------------------
# WEBSOCKET
# -----------------------------
@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):


    api_key = websocket.query_params.get("api_key")
    user = get_user_by_api_key(api_key)

    if not user:
        logger.error("Invalid API KEY")
        await websocket.close(code=1008)
        return

    await websocket.accept()

    user_id = user["user_id"]
    node_owner_map[node_id] = user_id
    
    logger.info(f"[Auth] Node {node_id} linked to user {user_id}")
    
    # continue normal flow
    connected_nodes[node_id] = websocket
    node_last_seen[node_id] = time.time()

    logger.info(f"Node connected: {node_id}")
    for jid, job in jobs.items():
        if job.get("status") != "running":
            continue
        await safe_send(websocket, {
            "type": "job_manifest",
            "payload": {
                "job_id": jid,
                "total_chunks": job.get("chunks", 0),
                "chunk_data": {
                    str(c["id"]): c
                    for c in job.get("chunks_data", [])
                }
            }
        }, node_id)

    try:
        while True:

            data = await websocket.receive_text()
            message = json.loads(data)

            node_last_seen[node_id] = time.time()

            msg_type = message.get("type")

            if msg_type == "resource_update":
                payload = message.get("payload", {})

                if not isinstance(payload, dict):
                    logger.warning(f"Invalid resource payload from {node_id}")
                    continue

                try:
                    cpu = float(payload.get("cpu", 0))
                    ram = float(payload.get("ram", 0))
                except (TypeError, ValueError):
                    logger.warning(f"Malformed resource values from {node_id}")
                    continue

                # Clamp to valid range
                cpu = max(0.0, min(cpu, 100.0))
                ram = max(0.0, min(ram, 100.0))

                node_resources[node_id] = {
                    "cpu": cpu,
                    "ram": ram
                }

            elif msg_type == "direct_message":
                payload = message.get("payload", {})
                target = payload.get("target")

                if not target:
                    continue

                target_ws = connected_nodes.get(target)

                if not target_ws:
                    continue

                await safe_send(target_ws, {
                    "type": "direct_message",
                    "source": node_id,
                    "payload": payload
                }, target)

            elif msg_type == "request_chunk":
                # Relay no longer assigns chunks. Nodes schedule locally.
                continue

            elif msg_type == "submit_result":

                logger.info(f"RESULT RECEIVED FROM: {node_id}")
                logger.info(f"FULL MESSAGE: {message}")
                payload = message["payload"]
                job_id = payload["job_id"]
                chunk_key = str(payload["chunk"])
                status = payload.get("status", "success")

                job = jobs.get(job_id)
                if not job or job["status"] == "cancelled":
                    continue

                if job["status_map"].get(chunk_key) == "completed":
                    continue
                if job["status_map"].get(chunk_key) == "failed":
                    continue

                if status == "success":
                    completed_chunks = job.setdefault("completed_chunks", set())
                    if isinstance(completed_chunks, list):
                        completed_chunks = set(completed_chunks)
                        job["completed_chunks"] = completed_chunks
                    completed_chunks.add(chunk_key)

                    total_chunks = job.get("total_chunks", job.get("chunks", 0))

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
                        logger.error(f"[All nodes failed chunk {chunk_key}]")

                        job["status_map"][chunk_key] = "failed"
                        job["errors"][chunk_key] = "All nodes failed execution"

                        async with job["_queue_lock"]:
                            job["queue"] = [c for c in job["queue"] if c != chunk_key]
                    else:
                        retries = job["retries"].get(chunk_key, 0)

                        if retries < MAX_RETRIES:
                            logger.warning(f"[Retry Triggered] chunk {chunk_key} for job {job_id}")

                            async with job["_queue_lock"]:
                                if chunk_key not in job["queue"]:
                                    job["queue"].append(chunk_key)

                            job["status_map"][chunk_key] = "pending"
                            job["retries"][chunk_key] = retries + 1
                            job["errors"][chunk_key] = payload.get("error", "Execution failed")

                        else:
                            logger.error(f"[Permanent Failure] chunk {chunk_key}")

                            job["status_map"][chunk_key] = "failed"
                            job["errors"][chunk_key] = "Max retries exceeded"
                            async with job["_queue_lock"]:
                                job["queue"] = [c for c in job["queue"] if c != chunk_key]

                    job["logs"].setdefault(chunk_key, "")
                    job["logs"][chunk_key] += payload.get("logs") or ""
                    job.get("verify_requests", {}).pop(chunk_key, None)
                    job.get("verification_originals", {}).pop(chunk_key, None)
                    job.get("verify_started_at", {}).pop(chunk_key, None)
                    job["updated_at"] = time.time()
                    logger.info(f"Retries: {job['retries']}")

                    total = job["chunks"]

                    completed_chunks = [
                        c for c, s in job["status_map"].items()
                        if s == "completed"
                    ]

                    failed_chunks = [
                        c for c, s in job["status_map"].items()
                        if s == "failed"
                    ]

                    if len(completed_chunks) + len(failed_chunks) == total:
                        job["status"] = "completed"
                        job["final_result"] = compute_final_result(job)
                        job["completed_at"] = time.time()
                        await send_cleanup_job(job_id, job)

                    await mark_jobs_dirty()
                    continue

                raw_result = payload.get("result")
                val = parse_result_value(raw_result)
                if val is None:
                    logger.warning(f"[Parse Warning] Chunk {chunk_key} produced invalid result: {raw_result}")

                verification_map = job.setdefault("verifications", {})
                chunk_verify = verification_map.setdefault(chunk_key, {})
                originals = job.setdefault("verification_originals", {})
                verify_started_at = job.setdefault("verify_started_at", {})

                if node_id in chunk_verify:
                    continue

                chunk_verify[node_id] = val
                if len(connected_nodes) <= 1:
                    logger.info(f"[Auto Complete] Only one node, skipping verification for chunk {chunk_key}")

                    job.setdefault("results", {})
                    job["results"][chunk_key] = val

                    job["status_map"][chunk_key] = "completed"
                    job["updated_at"] = time.time()

                    continue
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
                job["status_map"][chunk_key] = "pending"
                job["updated_at"] = time.time()
                await mark_jobs_dirty()
                continue

            elif msg_type == "verify_result":

                payload = message["payload"]
                job_id = payload["job_id"]
                chunk_key = str(payload["chunk"])

                job = jobs.get(job_id)
                if not job or job["status"] == "cancelled":
                    continue

                if job["status_map"].get(chunk_key) == "completed":
                    continue
                if job["status_map"].get(chunk_key) == "failed":
                    continue

                originals = job.get("verification_originals", {})
                original = originals.get(chunk_key)
                if not original:
                    logger.warning(f"[Verify] Missing original result for chunk {chunk_key} in job {job_id}")
                    continue

                original_source = original.get("source")
                if node_id == original_source:
                    logger.warning(f"[Verify] Ignoring self-verification for chunk {chunk_key}")
                    continue

                original_result = original.get("result")
                verify_result = payload.get("result")
                original_val = parse_result_value(original_result)
                verify_val = parse_result_value(verify_result)

                if original_val == verify_val:
                    logger.info(f"[Verify] Chunk {chunk_key} verified by {node_id} against {original_source} ✅")
                else:
                    logger.warning(f"[Verify] Chunk {chunk_key} mismatch")

                verification_map = job.get("verifications", {})
                chunk_verify = verification_map.get(chunk_key)

                if chunk_verify and node_id in chunk_verify:
                    continue

                verification_map = job.setdefault("verifications", {})
                chunk_verify = verification_map.setdefault(chunk_key, {})
                chunk_verify[node_id] = verify_val

                if original_val == verify_val:
                    job.setdefault("results", {})
                    final_val = original_val if original_val is not None else verify_val
                    job["results"][chunk_key] = final_val
                    logger.info(f"[DEBUG] STORED RESULT → chunk {chunk_key}: {final_val}")

                    job["status_map"][chunk_key] = "completed"
                    job.get("retry_count", {}).pop(chunk_key, None)

                    price = job.get("price", 0)
                    rewarded_chunks = job.setdefault("rewarded_chunks", set())

                    if price > 0 and chunk_key not in rewarded_chunks:
                        reward_per_chunk = price / job["chunks"]
                        reward_nodes = set(chunk_verify.keys())

                        original_source = original.get("source")

                        if original_source:
                            reward_nodes.add(original_source)

                        reward_per_node = round(
                            reward_per_chunk / max(len(reward_nodes), 1),
                            4
                        )

                        for node in reward_nodes:
                            user_id = node_owner_map.get(node)

                            if not user_id:
                                continue

                            try:
                                user = get_user_by_id(user_id)

                                if not user:
                                    continue

                                api_key = user["api_key"].strip()

                                success = adjust_user_credits_by_api_key(api_key, reward_per_node)
                                if not success:
                                    logger.error(f"[CRITICAL] Credit update failed for {api_key}")

                                logger.info(f"[Reward] {reward_per_node} -> node {node}")

                            except Exception:
                                logger.exception("Reward update failed")
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
                        logger.error(f"[Verify] Chunk {chunk_key} failed permanently")
                        job["status_map"][chunk_key] = "failed"
                        async with job["_queue_lock"]:
                            job["queue"] = [c for c in job["queue"] if c != chunk_key]
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

                        async with job["_queue_lock"]:
                            if retry_map[chunk_key] <= MAX_RETRIES and chunk_key not in job["queue"]:
                                job["queue"].append(chunk_key)

                        job["status_map"][chunk_key] = "pending"

                job["updated_at"] = time.time()

                total = job["chunks"]

                completed_chunks = [
                    c for c, s in job["status_map"].items()
                    if s == "completed"
                ]

                failed_chunks = [
                    c for c, s in job["status_map"].items()
                    if s == "failed"
                ]

                # 🔥 COMPLETE if ALL chunks are done (success OR fail)
                if len(completed_chunks) + len(failed_chunks) == total:
                    job["status"] = "completed"
                    job["final_result"] = compute_final_result(job)
                    job["completed_at"] = time.time()
                    await send_cleanup_job(job_id, job)

                await mark_jobs_dirty()

    except WebSocketDisconnect:
        logger.info(f"Node disconnected: {node_id}")
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
        return error_response("not found", "ERROR")

    completed = get_completed_count(job)

    return {
        "status": job["status"],
        "completed": completed,
        "total": job.get("total_chunks", job["chunks"])
    }


@app.get("/job_result/{job_id}")
def job_result(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return error_response("invalid api key", "ERROR")

    job = jobs.get(job_id)
    if not job or job.get("owner") != user["user_id"]:
        return error_response("unauthorized", "ERROR")

    if job["status"] != "completed":
        return {"status": "running"}

    final_result = job.get("final_result")
    if final_result is None:
        final_result = compute_final_result(job)
        job["final_result"] = final_result
    return {"result": final_result}


@app.get("/all_jobs")
def all_jobs(api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return error_response("invalid api key", "ERROR")

    user_id = user["user_id"]
    out = {}

    now = time.time()

    for jid, job in jobs.items():
        if job.get("owner") != user_id:
            continue

        completed = get_completed_count(job)
        total = job.get("total_chunks", job["chunks"])

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
            "result": None,
            "duration": duration,
            "speed": speed
        }
        if job["status"] == "completed":
            final_result = job.get("final_result")
            if final_result is None:
                final_result = compute_final_result(job)
                job["final_result"] = final_result
            out[jid]["result"] = final_result

    return out


@app.post("/cancel_job/{job_id}")
async def cancel_job(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return error_response("invalid api key", "ERROR")

    job = jobs.get(job_id)

    if not job:
        return error_response("not found", "ERROR")
    if job.get("owner") != user["user_id"]:
        return error_response("unauthorized", "ERROR")

    if job["status"] != "running":
        return {"status": job["status"]}

    job["status"] = "cancelled"
    job["completed_at"] = time.time()

    completed = get_completed_count(job)

    total = job.get("total_chunks")
    if not isinstance(total, int) or total <= 0:
        total = len(job.get("chunks_data", []))

    price = job.get("price", 0)

    if total > 0:
        used = (completed / total) * price
        refund = price - used
    else:
        refund = price  # full refund if no chunks

    refund = max(0, min(price, refund))
    refund = round(refund, 2)

    user_id = job.get("owner")

    if user_id:
        try:
            user = get_user_by_id(user_id)
            if user:
                api_key = user["api_key"].strip()
                if not adjust_user_credits_by_api_key(api_key, refund):
                    async with credit_update_lock:
                        user = get_user_by_id(user_id)
                        if not user:
                            return {"status": "cancelled", "refund": refund}
                        new_credits = user["credits"] + refund
                        update_user_credits_by_api_key(api_key, new_credits)

        except Exception:
            logger.exception("Refund failed")

    async with job["_queue_lock"]:
        job["queue"].clear()

    for chunk, status in job["status_map"].items():
        if status == "running" and chunk not in job.get("verify_requests", {}):
            job["status_map"][chunk] = "cancelled"

    return {
        "status": "cancelled",
        "refund": refund
    }


@app.get("/job_logs/{job_id}")
def job_logs(job_id: str, api_key: str):
    user = get_user_by_api_key(api_key)
    if not user:
        return error_response("invalid api key", "ERROR")

    job = jobs.get(job_id)

    if not job or job.get("owner") != user["user_id"]:
        return error_response("unauthorized", "ERROR")

    return {
        "job_id": job_id,
        "status": job.get("status"),
        "logs": job.get("logs", {}),
        "errors": job.get("errors", {})
    }


@app.post("/create_user")
def create_user(email: str = Form(...), password: str = Form(...)):
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
        return error_response("not found", "ERROR")

    return user


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):

    res = supabase.table("users")\
        .select("*")\
        .eq("email", email)\
        .execute()

    if not res.data:
        return error_response("invalid credentials", "ERROR")

    user = res.data[0]

    if not verify_password(password, user["password"]):
        return error_response("invalid credentials", "ERROR")

    if not user["password"].startswith("$2b$"):
        new_hash = hash_password(password)
        supabase.table("users")\
            .update({"password": new_hash})\
            .eq("user_id", user["user_id"])\
            .execute()

        logger.info(f"[Auth] Upgraded user {user['user_id']} to bcrypt")

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
        return error_response(f"File not found: {file_path}", "ERROR")

    return FileResponse(file_path)



