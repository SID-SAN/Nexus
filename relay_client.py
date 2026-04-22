import asyncio
import websockets
import json
import os
import time
import random
from urllib.parse import quote
from node.downloader import download_job
from node.executor import execute_chunk
from config import RELAY_URLS

claim_lock = asyncio.Lock()
current_relay = None
MAX_RETRIES = 5


def get_node_id():
    return os.getenv("NODE_ID", "node_default")


def get_api_key():
    return os.getenv("API_KEY")

def get_relay_ws_url(base_url, node_id, api_key):
    relay_base = base_url

    if relay_base.startswith("https://"):
        relay_base = "wss://" + relay_base[len("https://"):]
    elif relay_base.startswith("http://"):
        relay_base = "ws://" + relay_base[len("http://"):]

    return f"{relay_base}/ws/{quote(node_id)}?api_key={quote(api_key)}"


websocket_connection = None
known_peers = set()
job_cache = {}
pending_verifications = {}
send_queue = asyncio.Queue(maxsize=1000)
work_loop_started = False

active_chunks = 0
MAX_CONCURRENT_CHUNKS = 2
VERIFY_TIMEOUT = 30
CLAIM_JITTER_MAX = 0.2
FAILED_CHUNK_BACKOFF = 1
COMPLETED_JOB_TTL = 120
STALE_JOB_TTL = 3600


async def enqueue_message(message):
    try:
        await send_queue.put({
            "data": message,
            "retries": 0
        })
    except asyncio.QueueFull:
        print("[Sender] Queue full, dropping message")


def build_job_state(job):
    return {
        "total_chunks": job.get("total_chunks", 0),
        "chunks": list(job.get("chunks", set())),
        "completed": list(job.get("completed", set())),
        "in_progress": list(job.get("in_progress", set())),
        "status": job.get("status", "running"),
        "chunk_data_map": job.get("chunk_data_map", {}),
        "last_updated": job.get("last_updated", time.time())
    }


def init_job(job_id, total_chunks=0, chunk_data_map=None):
    state = job_cache.setdefault(job_id, {
        "chunks": set(),
        "completed": set(),
        "in_progress": set(),
        "status": "running",
        "cleanup_scheduled": False,
        "chunk_data_map": {},
        "total_chunks": total_chunks,
        "last_updated": time.time()
    })

    if total_chunks:
        state["total_chunks"] = max(state.get("total_chunks", 0), total_chunks)

    if state.get("total_chunks", 0) > 0 and not state["chunks"]:
        state["chunks"] = set(range(1, state["total_chunks"] + 1))

    if chunk_data_map and isinstance(chunk_data_map, dict):
        state["chunk_data_map"].update(chunk_data_map)

    return state


def pick_next_chunk():
    for job_id, job in job_cache.items():
        if job.get("status") != "running":
            continue

        available = job["chunks"] - job["in_progress"] - job["completed"]
        if available:
            return job_id, random.choice(list(available))

    return None, None


async def broadcast_action(action, **kwargs):
    peers = list(known_peers)
    for peer_id in peers:
        await enqueue_message({
            "type": "direct_message",
            "payload": {
                "target": peer_id,
                "action": action,
                **kwargs
            }
        })


async def send_job_sync(job_id):
    state = job_cache.get(job_id)
    if not state:
        return

    peers = list(known_peers)
    if not peers:
        return

    selected_peers = random.sample(peers, min(2, len(peers)))
    payload_state = build_job_state(state)

    for peer_id in selected_peers:
        await enqueue_message({
            "type": "direct_message",
            "payload": {
                "target": peer_id,
                "action": "job_sync",
                "job_id": job_id,
                "status": payload_state
            }
        })


async def cleanup_job(job_id):
    await asyncio.sleep(60)
    if job_id in job_cache:
        await broadcast_action("job_cleanup", job_id=job_id)
    job_cache.pop(job_id, None)
    print(f"[Cache] Cleaned job {job_id}")


async def request_verification(job_id, chunk, original_result):
    if not known_peers:
        return True

    peer = random.choice(list(known_peers))
    key = (job_id, str(chunk))
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_verifications[key] = fut

    await enqueue_message({
        "type": "direct_message",
        "payload": {
            "target": peer,
            "action": "verify_chunk",
            "job_id": job_id,
            "chunk": chunk
        }
    })

    try:
        verify_payload = await asyncio.wait_for(fut, timeout=VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        pending_verifications.pop(key, None)
        return False

    verify_status = verify_payload.get("status")
    verify_result = verify_payload.get("result")
    return verify_status == "success" and str(verify_result).strip() == str(original_result).strip()


async def execute_verify_chunk(job_id, chunk, target_node):
    print(f"[Verify] Verifying chunk {chunk} for job {job_id}")

    try:
        if not os.path.exists(f"jobs/{job_id}.zip") and not os.path.exists(f"jobs/{job_id}"):
            download_job(job_id)

        chunk_data = job_cache.get(job_id, {}).get("chunk_data_map", {}).get(str(chunk), {})
        result = await asyncio.to_thread(execute_chunk, job_id, chunk, chunk_data)

        response_payload = {
            "job_id": job_id,
            "chunk": chunk,
            "status": result["status"],
            "result": result["result"]
        }
    except Exception as e:
        response_payload = {
            "job_id": job_id,
            "chunk": chunk,
            "status": "failed",
            "result": None,
            "error": str(e)
        }

    await enqueue_message({
        "type": "direct_message",
        "payload": {
            "target": target_node,
            "action": "verify_result",
            **response_payload
        }
    })


async def execute_chunk_task(job_id, chunk):
    global active_chunks

    state = job_cache.get(job_id)
    if not state or state.get("status") != "running":
        return

    try:
        if not os.path.exists(f"jobs/{job_id}.zip") and not os.path.exists(f"jobs/{job_id}"):
            download_job(job_id)
    except Exception:
        print("[Node] Cannot download job package")
        state["in_progress"].discard(chunk)
        return

    chunk_data = state.get("chunk_data_map", {}).get(str(chunk), {})

    active_chunks += 1
    try:
        exec_output = await asyncio.wait_for(
            asyncio.to_thread(execute_chunk, job_id, chunk, chunk_data),
            timeout=70
        )
    except asyncio.TimeoutError:
        exec_output = {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": "Chunk execution timed out"
        }
    except Exception as e:
        exec_output = {
            "status": "failed",
            "result": None,
            "logs": "",
            "error": str(e)
        }
    finally:
        active_chunks -= 1

    if exec_output.get("status") == "success":
        is_verified = await request_verification(job_id, chunk, exec_output.get("result"))
        if is_verified:
            state = job_cache.get(job_id)
            if state:
                if chunk not in state["completed"]:
                    state["completed"].add(chunk)
                    state["in_progress"].discard(chunk)
                    state["last_updated"] = time.time()

                    await broadcast_action("complete_chunk", job_id=job_id, chunk=chunk)

                    await enqueue_message({
                        "type": "submit_result",
                        "source": get_node_id(),
                        "payload": {
                            "job_id": job_id,
                            "chunk": chunk,
                            "status": "success",
                            "result": exec_output.get("result"),
                            "logs": exec_output.get("logs", ""),
                            "error": ""
                        }
                    })
        else:
            await asyncio.sleep(FAILED_CHUNK_BACKOFF)
            state["in_progress"].discard(chunk)
    else:
        await asyncio.sleep(FAILED_CHUNK_BACKOFF)
        state["in_progress"].discard(chunk)

    total_chunks = state.get("total_chunks", 0)
    if total_chunks and len(state["completed"]) >= total_chunks:
        state["status"] = "completed"
        await broadcast_action("job_complete", job_id=job_id)
        if not state.get("cleanup_scheduled"):
            state["cleanup_scheduled"] = True
            asyncio.create_task(cleanup_job(job_id))

    await send_job_sync(job_id)


async def scheduler_loop():
    while True:
        if websocket_connection is None:
            await asyncio.sleep(1)
            continue

        if active_chunks >= MAX_CONCURRENT_CHUNKS:
            await asyncio.sleep(1)
            continue

        job_id, chunk = pick_next_chunk()
        if not job_id:
            await asyncio.sleep(2)
            continue

        state = job_cache.get(job_id)
        if not state:
            await asyncio.sleep(1)
            continue

        await asyncio.sleep(random.uniform(0, CLAIM_JITTER_MAX))

        async with claim_lock:
            # 🔥 RE-CHECK INSIDE LOCK (CRITICAL)
            available = state["chunks"] - state["in_progress"] - state["completed"]

            if chunk not in available or state.get("status") != "running":
                continue

            # 🔥 ATOMIC CLAIM
            state["in_progress"].add(chunk)
            state["last_updated"] = time.time()

        await broadcast_action("claim_chunk", job_id=job_id, chunk=chunk)
        asyncio.create_task(execute_chunk_task(job_id, chunk))

        await asyncio.sleep(0.1)


async def cache_maintenance_loop():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        to_remove = []

        for job_id, state in list(job_cache.items()):
            last_updated = state.get("last_updated", now)
            status = state.get("status", "running")

            if status == "completed" and now - last_updated > COMPLETED_JOB_TTL:
                to_remove.append(job_id)
                continue

            if now - last_updated > STALE_JOB_TTL:
                to_remove.append(job_id)

        for job_id in to_remove:
            job_cache.pop(job_id, None)
            print(f"[Cache] Pruned stale job {job_id}")


async def sender_loop():
    global websocket_connection

    while True:
        wrapped = await send_queue.get()
        message = wrapped["data"]
        retries = wrapped.get("retries", 0)
        if websocket_connection is None:
            await asyncio.sleep(1)

            if retries < MAX_RETRIES:
                wrapped["retries"] = retries + 1
                await send_queue.put(wrapped)
            else:
                print(f"[Sender] Dropping message after {MAX_RETRIES} retries")

            continue

        try:
            await websocket_connection.send(json.dumps(message))
        except Exception as e:
            print(f"[Sender] Retry sending: {e}")

            if retries < MAX_RETRIES:
                wrapped["retries"] = retries + 1
                await asyncio.sleep(1)
                await send_queue.put(wrapped)
            else:
                print(f"[Sender] Dropping failed message after {MAX_RETRIES} retries")


def merge_job_state(local, incoming):
    if not isinstance(incoming, dict):
        return local

    incoming_chunks = incoming.get("chunks", [])
    incoming_completed = incoming.get("completed", [])
    incoming_in_progress = incoming.get("in_progress", [])

    if isinstance(incoming_chunks, list):
        local["chunks"].update(set(incoming_chunks))
    if isinstance(incoming_completed, list):
        local["completed"].update(set(incoming_completed))
    if isinstance(incoming_in_progress, list):
        local["in_progress"].update(set(incoming_in_progress))

    local["in_progress"] -= local["completed"]

    if isinstance(incoming.get("chunk_data_map"), dict):
        local["chunk_data_map"].update(incoming["chunk_data_map"])

    local["total_chunks"] = max(local.get("total_chunks", 0), int(incoming.get("total_chunks", 0) or 0))

    if incoming.get("status") == "completed":
        local["status"] = "completed"

    local["last_updated"] = max(local.get("last_updated", 0), incoming.get("last_updated", 0) or 0)
    return local


async def connect_to_relay():
    global websocket_connection
    global work_loop_started
    global current_relay
    global known_peers

    while True:
        node_id = get_node_id()
        api_key = get_api_key()

        if not api_key:
            print("[Relay] API_KEY is not set. Waiting...")
            await asyncio.sleep(2)
            continue

        for base_url in RELAY_URLS:
            try:
                relay_url = get_relay_ws_url(base_url, node_id, api_key)
                print(f"[Relay] Trying {base_url}")

                async with websockets.connect(relay_url, ping_interval=20, ping_timeout=20) as websocket:
                    websocket_connection = websocket
                    current_relay = base_url
                    os.environ["RELAY_HTTP_URL"] = base_url
                    print(f"[Relay] Connected to {base_url} as {node_id}")

                    if not work_loop_started:
                        asyncio.create_task(sender_loop())
                        asyncio.create_task(scheduler_loop())
                        asyncio.create_task(cache_maintenance_loop())
                        work_loop_started = True

                    while True:
                        message = await websocket.recv()
                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "heartbeat":
                            await enqueue_message({
                                "type": "heartbeat_ack",
                                "source": get_node_id()
                            })

                        elif msg_type == "peer_list":
                            peers = data.get("nodes", [])
                            self_id = get_node_id()
                            known_peers = set([peer for peer in peers if peer != self_id])

                        elif msg_type == "job_manifest":
                            payload = data.get("payload", {})
                            job_id = payload.get("job_id")
                            if not job_id:
                                continue

                            total_chunks = int(payload.get("total_chunks", 0) or 0)
                            chunk_data = payload.get("chunk_data", {})
                            state = init_job(job_id, total_chunks=total_chunks, chunk_data_map=chunk_data)
                            state["status"] = "running"
                            state["last_updated"] = time.time()

                        elif msg_type == "direct_message":
                            payload = data.get("payload", {})
                            source = data.get("source")
                            action = payload.get("action")

                            if action == "claim_chunk":
                                job_id = payload.get("job_id")
                                chunk = payload.get("chunk")
                                if job_id is None or chunk is None:
                                    continue
                                state = init_job(job_id)
                                state["in_progress"].add(chunk)
                                state["last_updated"] = time.time()

                            elif action == "complete_chunk":
                                job_id = payload.get("job_id")
                                chunk = payload.get("chunk")
                                if job_id is None or chunk is None:
                                    continue
                                state = init_job(job_id)
                                if chunk not in state["completed"]:
                                    state["completed"].add(chunk)
                                    state["in_progress"].discard(chunk)
                                    state["last_updated"] = time.time()

                            elif action == "verify_chunk":
                                job_id = payload.get("job_id")
                                chunk = payload.get("chunk")
                                if job_id is None or chunk is None or not source:
                                    continue
                                asyncio.create_task(execute_verify_chunk(job_id, chunk, source))

                            elif action == "verify_result":
                                job_id = payload.get("job_id")
                                chunk = payload.get("chunk")
                                key = (job_id, str(chunk))
                                fut = pending_verifications.pop(key, None)
                                if fut and not fut.done():
                                    fut.set_result(payload)

                            elif action == "job_complete":
                                job_id = payload.get("job_id")
                                if job_id:
                                    state = init_job(job_id)
                                    state["status"] = "completed"

                            elif action == "job_sync":
                                job_id = payload.get("job_id")
                                status = payload.get("status")
                                if job_id and status:
                                    local = init_job(job_id)
                                    merge_job_state(local, status)
                                    job_cache[job_id] = local

                            elif action == "job_cleanup":
                                job_id = payload.get("job_id")
                                if job_id:
                                    job_cache.pop(job_id, None)

            except Exception as e:
                print(f"[Relay] Failed {base_url}: {e}")
                websocket_connection = None
                current_relay = None
                os.environ.pop("RELAY_HTTP_URL", None)

        print("[Relay] All relays failed. Retrying in 3s...\n")
        await asyncio.sleep(3)
