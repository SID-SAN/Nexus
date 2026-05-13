import asyncio
import websockets
import json
import os
import time
import random
import re
from websockets import exceptions as ws_exceptions
from urllib.parse import quote
from node.downloader import download_job
from node.executor import execute_chunk, cleanup_job as cleanup_job_files
from config import RELAY_URLS
from logger import setup_logger

metrics_lock = asyncio.Lock()
claim_lock = asyncio.Lock()
active_chunks_lock = asyncio.Lock()
current_relay = None
node_runtime_state = "IDLE"
MAX_RETRIES = 5
download_locks = {}
logger = setup_logger("node-client")


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

def get_download_lock(job_id):
    if job_id not in download_locks:
        download_locks[job_id] = asyncio.Lock()
    return download_locks[job_id]

websocket_connection = None
known_peers = set()
peer_last_seen = {}
peer_scores = {}
MAX_PEERS = 8
PEER_GOSSIP_INTERVAL = 30
PEER_STALE_TIMEOUT = 90

job_cache = {}
local_verifications = {}
send_queue = asyncio.Queue(maxsize=1000)
work_loop_started = False
verify_success_count = 0
verify_mismatch_count = 0
verify_timeout_count = 0
last_relay_warning = 0

active_chunks = 0
MAX_CONCURRENT_CHUNKS = 2
CLAIM_JITTER_MAX = 0.2
FAILED_CHUNK_BACKOFF = 1
LOCAL_VERIFY_TIMEOUT = 45
CLAIM_TIMEOUT = 900
COMPLETED_JOB_TTL = 120
STALE_JOB_TTL = 3600
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")


def error_response(message, code="ERROR"):
    return {
        "status": "failed",
        "error": message,
        "code": code,
    }


def is_valid_job_id(job_id):
    return bool(JOB_ID_RE.fullmatch(str(job_id or "")))


async def enqueue_message(message):
    try:
        await send_queue.put({
            "data": message,
            "retries": 0
        })
    except asyncio.QueueFull:
        logger.warning("[Sender] Queue full, dropping message")


async def enqueue_runtime_snapshot():
    async with active_chunks_lock:
        current_active = active_chunks

    await enqueue_message({
        "type": "heartbeat_ack",
        "source": get_node_id(),
        "payload": {
            "status": node_runtime_state,
            "active_chunks": current_active,
            "known_peers": len(known_peers),
            "relay": current_relay
        }
    })


def build_job_state(job):
    return {
        "total_chunks": job.get("total_chunks", 0),
        "chunks": list(job.get("chunks", set())),
        "completed": list(job.get("completed", set())),
        "claims": job.get("claims", {}),
        "status": job.get("status", "running"),
        "chunk_data_map": job.get("chunk_data_map", {}),
        "last_updated": job.get("last_updated", time.time())
    }


def init_job(job_id, total_chunks=0, chunk_data_map=None):
    state = job_cache.setdefault(job_id, {
        "chunks": set(),
        "completed": set(),
        "claims": {},
        "status": "running",
        "cleanup_scheduled": False,
        "cleanup_completed": False,
        "chunk_data_map": {},
        "total_chunks": total_chunks,
        "last_sync": 0,
        "last_updated": time.time()
    })

    if total_chunks:
        state["total_chunks"] = max(state.get("total_chunks", 0), total_chunks)

    if state.get("total_chunks", 0) > 0 and not state["chunks"]:
        state["chunks"] = {str(i) for i in range(1, state["total_chunks"] + 1)}

    if chunk_data_map and isinstance(chunk_data_map, dict):
        state["chunk_data_map"].update(chunk_data_map)

    return state


def pick_next_chunk():
    for job_id, job in job_cache.items():
        if job.get("status") != "running":
            continue

        claimed_chunks = set(job.get("claims", {}).keys())
        available = (
            job["chunks"]
            - claimed_chunks
            - job["completed"]
        )
        if available:
            return job_id, str(random.choice(list(available)))

    return None, None


def validate_chunk_data(chunk_data):
    if not isinstance(chunk_data, dict):
        return {}

    valid_data = {}

    for k, v in chunk_data.items():
        # key must be numeric (chunk id)
        if not str(k).isdigit():
            continue

        if not isinstance(v, dict):
            continue

        clean = {}

        if "start" in v and isinstance(v["start"], int):
            clean["start"] = v["start"]

        if "end" in v and isinstance(v["end"], int):
            clean["end"] = v["end"]

        if "file" in v and isinstance(v["file"], str):
            clean["file"] = v["file"]

        valid_data[str(k)] = clean

    return valid_data

def add_peer(peer_id):
    if not peer_id:
        return

    if peer_id == get_node_id():
        return

    peer_last_seen[peer_id] = time.time()

    if len(known_peers) >= MAX_PEERS and peer_id not in known_peers:
        return

    is_new_peer = peer_id not in known_peers
    known_peers.add(peer_id)
    if peer_id not in peer_scores:
        peer_scores[peer_id] = {
            "success": 0,
            "timeouts": 0,
            "mismatches": 0
        }
    if is_new_peer:
        logger.info(
            f"[Peers] Discovered peer {peer_id} "
            f"(known_peers={len(known_peers)})"
        )


def remove_stale_peers():
    now = time.time()

    stale = [
        peer
        for peer, last_seen in peer_last_seen.items()
        if now - last_seen > PEER_STALE_TIMEOUT
    ]

    for peer in stale:
        known_peers.discard(peer)
        peer_last_seen.pop(peer, None)
        peer_scores.pop(peer, None)

        logger.info(f"[Peers] Removed stale peer {peer}")

async def peer_gossip_loop():
    while True:
        try:
            await asyncio.sleep(PEER_GOSSIP_INTERVAL)

            if not known_peers:
                continue

            remove_stale_peers()

            peers = list(known_peers)

            selected = random.sample(
                peers,
                min(2, len(peers))
            )

            known_subset = random.sample(
                peers,
                min(5, len(peers))
            )

            for peer_id in selected:
                await enqueue_message({
                    "type": "direct_message",
                    "payload": {
                        "target": peer_id,
                        "action": "peer_exchange",
                        "peers": [
                            p for p in known_subset
                            if p != peer_id
                        ]
                    }
                })

        except Exception:
            logger.exception("[Peers] Gossip loop failed")
            await asyncio.sleep(5)

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


SYNC_INTERVAL = 5
async def send_job_sync(job_id):
    state = job_cache.get(job_id)
    if not state:
        return
    if state.get("status") == "completed":
        return

    now = time.time()
    if now - state.get("last_sync", 0) < SYNC_INTERVAL:
        return

    state["last_sync"] = now
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


async def cleanup_job_cache(job_id):
    await asyncio.sleep(60)
    state = job_cache.get(job_id)
    if not state:
        return
    if state.get("cleanup_completed"):
        return

    state["cleanup_completed"] = True
    await broadcast_action("job_cleanup", job_id=job_id)
    await asyncio.to_thread(cleanup_job_files, job_id)
    job_cache.pop(job_id, None)
    download_locks.pop(job_id, None)
    stale_keys = [
        key for key in local_verifications
        if key[0] == job_id
    ]
    for key in stale_keys:
        local_verifications.pop(key, None)
    logger.info(f"[Cache] Cleaned job {job_id}")


async def verification_timeout_loop():
    global verify_timeout_count
    while True:
        await asyncio.sleep(5)

        now = time.time()
        expired = []

        for key, verification in list(local_verifications.items()):
            if now - verification["timestamp"] > LOCAL_VERIFY_TIMEOUT:
                expired.append(key)

        for key in expired:
            job_id, chunk = key
            async with metrics_lock:
                verify_timeout_count += 1

            logger.warning(
                f"[VERIFY] Verification timeout "
                f"for chunk {chunk}"
            )

            state = job_cache.get(job_id)

            if state:
                state["completed"].discard(chunk)
                state["claims"].pop(chunk, None)
                state["last_updated"] = time.time()
                await broadcast_action(
                    "chunk_requeue",
                    job_id=job_id,
                    chunk=chunk
                )

            local_verifications.pop(key, None)


async def metrics_loop():
    while True:
        await asyncio.sleep(30)

        async with metrics_lock:
            success = verify_success_count
            mismatch = verify_mismatch_count
            timeout = verify_timeout_count
        async with active_chunks_lock:
            current_active = active_chunks
        claims = sum(
            len(state.get("claims", {}))
            for state in job_cache.values()
        )
        pending_chunks = send_queue.qsize()
        verification_rate = (
            success / max(success + mismatch + timeout, 1)
        )

        logger.info(
            "[Metrics] "
            f"verify_success={success} "
            f"verify_mismatch={mismatch} "
            f"verify_timeout={timeout} "
            f"verify_rate={verification_rate:.2%} "
            f"claims={claims} "
            f"send_queue={pending_chunks} "
            f"relay={current_relay} "
            f"known_peers={len(known_peers)} "
            f"jobs={len(job_cache)} "
            f"active_chunks={current_active}"
        )


async def execute_verify_chunk(job_id, chunk, target_node):
    global node_runtime_state
    state = job_cache.get(job_id)

    if not state:
        return

    claim = state.get("claims", {}).get(str(chunk))

    if claim and claim.get("owner") != target_node:
        logger.warning(
            f"[VERIFY] Rejecting stale verifier request "
            f"for chunk {chunk}"
        )
        return

    if str(chunk) in state.get("completed", set()):
        logger.debug(
            f"[VERIFY] Skipping already completed "
            f"chunk {chunk}"
        )
        return
    
    logger.debug(
        f"[VERIFY] Node verifying chunk {chunk} "
        f"for job {job_id}"
    )
    node_runtime_state = "VERIFYING"
    await enqueue_runtime_snapshot()

    try:
        if not is_valid_job_id(job_id):
            raise ValueError("Invalid job_id")

        jobs_base = os.path.abspath("jobs")
        extract_path = os.path.join(jobs_base, str(job_id))
        lock = get_download_lock(job_id)
        async with lock:
            if not os.path.exists(extract_path):
                await asyncio.to_thread(download_job, job_id)

        chunk_data = job_cache.get(job_id, {}).get("chunk_data_map", {}).get(str(chunk), {})
        result = await asyncio.to_thread(execute_chunk, job_id, chunk, chunk_data)
        logger.debug(
            f"[VERIFY] Verification complete "
            f"for chunk {chunk} | status={result.get('status')}"
        )

        response_payload = {
            "job_id": job_id,
            "chunk": str(chunk),
            "status": result["status"],
            "result": result["result"]
        }
    except Exception as e:
        logger.exception("[Verify] Unexpected error")
        response_payload = {
            "job_id": job_id,
            "chunk": str(chunk),
            "result": None,
            **error_response(str(e), "VERIFY_EXECUTION_ERROR"),
        }
    finally:
        async with active_chunks_lock:
            if active_chunks > 0:
                node_runtime_state = "EXECUTING"
            else:
                node_runtime_state = "IDLE"
        await enqueue_runtime_snapshot()

    await enqueue_message({
        "type": "direct_message",
        "payload": {
            "target": target_node,
            "action": "verify_result",
            **response_payload
        }
    })


async def execute_chunk_task(job_id, chunk):
    global active_chunks, node_runtime_state
    state = job_cache.get(job_id)

    if not state:
        return

    claim = state.get("claims", {}).get(chunk)

    if not claim:
        logger.debug(
            f"[Claims] Lost ownership of chunk {chunk}"
        )
        return

    if claim.get("owner") != get_node_id():
        logger.debug(
            f"[Claims] Another node owns chunk {chunk}"
        )
        return
    if not state or state.get("status") != "running":
        return

    try:
        if not is_valid_job_id(job_id):
            raise ValueError("Invalid job_id")

        jobs_base = os.path.abspath("jobs")
        extract_path = os.path.join(jobs_base, str(job_id))
        lock = get_download_lock(job_id)
        async with lock:
            if not os.path.exists(extract_path):
                try:
                    await asyncio.to_thread(download_job, job_id)
                except FileExistsError:
                    pass
    except Exception:
        logger.exception("[Node] Download failed")
        state["claims"].pop(chunk, None)
        return

    chunk_data = state.get("chunk_data_map", {}).get(str(chunk), {})

    logger.debug(f"[EXECUTE] Starting chunk {chunk} for job {job_id}")
    node_runtime_state = "EXECUTING"
    async with active_chunks_lock:
        active_chunks += 1
    await enqueue_runtime_snapshot()

    async def refresh_claim():
        while True:
            await asyncio.sleep(30)

            current_state = job_cache.get(job_id)
            if not current_state:
                return

            current_claim = current_state.get("claims", {}).get(chunk)
            if not current_claim:
                return

            if current_claim.get("owner") != get_node_id():
                return

            current_claim["timestamp"] = time.time()
            current_state["last_updated"] = time.time()

    heartbeat_task = asyncio.create_task(refresh_claim())
    try:
        exec_output = await asyncio.wait_for(
            asyncio.to_thread(execute_chunk, job_id, chunk, chunk_data),
            timeout=70
        )
    except asyncio.TimeoutError:
        exec_output = {
            "result": None,
            "logs": "",
            **error_response("Chunk execution timed out", "CHUNK_EXECUTION_TIMEOUT"),
        }
    except Exception as e:
        exec_output = {
            "result": None,
            "logs": "",
            **error_response(str(e), "CHUNK_EXECUTION_ERROR"),
        }
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        async with active_chunks_lock:
            active_chunks -= 1
            if active_chunks <= 0:
                active_chunks = 0
                node_runtime_state = "IDLE"
        await enqueue_runtime_snapshot()

    state = job_cache.get(job_id)

    if not state:
        return

    latest_claim = state.get("claims", {}).get(chunk)

    if latest_claim and latest_claim.get("owner") != get_node_id():
        logger.warning(
            f"[Claims] Lost ownership during execution "
            f"chunk={chunk} "
            f"owner={latest_claim.get('owner')} "
            f"epoch={latest_claim.get('epoch')}"
        )
        return

    if exec_output.get("status") == "success":
        logger.info(
            f"[EXECUTE] Completed chunk {chunk} "
            f"for job {job_id} | status=success"
        )
        eligible_peers = [
            p for p in known_peers
            if p != get_node_id()
        ]
        if eligible_peers:
            verifier = random.choice(eligible_peers)
            verification_key = (job_id, str(chunk))
            local_verifications[verification_key] = {
                "verifier": verifier,
                "original_result": exec_output.get("result"),
                "timestamp": time.time(),
                "logs": exec_output.get("logs", "")
            }
            await enqueue_message({
                "type": "direct_message",
                "payload": {
                    "target": verifier,
                    "action": "verify_chunk",
                    "job_id": job_id,
                    "chunk": str(chunk)
                }
            })
        else:
            logger.warning(
                f"[VERIFY] No verifier available, "
                f"auto-accepting chunk {chunk}"
            )
            state = job_cache.get(job_id)
            if state and chunk not in state["completed"]:
                state["completed"].add(chunk)
                state["claims"].pop(chunk, None)
                state["last_updated"] = time.time()

                await broadcast_action(
                    "complete_chunk",
                    job_id=job_id,
                    chunk=chunk
                )

            await enqueue_message({
                "type": "submit_result",
                "source": get_node_id(),
                "payload": {
                    "job_id": job_id,
                    "chunk": str(chunk),
                    "status": "success",
                    "result": exec_output.get("result"),
                    "logs": exec_output.get("logs", ""),
                    "error": ""
                }
            })
    else:
        logger.error(
            f"[EXECUTE] Failed chunk {chunk} "
            f"for job {job_id} | error={exec_output.get('error')}"
        )
        await asyncio.sleep(FAILED_CHUNK_BACKOFF)

        state = job_cache.get(job_id)
        if not state:
            return

        state["claims"].pop(chunk, None)

    state = job_cache.get(job_id)
    if not state:
        return

    total_chunks = state.get("total_chunks", 0)
    if total_chunks and len(state["completed"]) >= total_chunks:
        state["status"] = "completed"
        await broadcast_action("job_complete", job_id=job_id)
        if not state.get("cleanup_scheduled"):
            state["cleanup_scheduled"] = True
            asyncio.create_task(cleanup_job_cache(job_id))

    await send_job_sync(job_id)


async def scheduler_loop():
    while True:
        if websocket_connection is None:
            await asyncio.sleep(1)
            continue

        async with active_chunks_lock:
            current_active = active_chunks
        if current_active >= MAX_CONCURRENT_CHUNKS:
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
            claimed_chunks = set(state.get("claims", {}).keys())
            available = state["chunks"] - claimed_chunks - state["completed"]

            if chunk not in available or state.get("status") != "running":
                continue

            # 🔥 ATOMIC CLAIM
            claim = {
                "owner": get_node_id(),
                "timestamp": time.time(),
                "epoch": state["claims"].get(chunk, {}).get("epoch", 0) + 1
            }
            state["claims"][chunk] = claim
            state["last_updated"] = time.time()

        await broadcast_action(
            "claim_chunk",
            job_id=job_id,
            chunk=chunk,
            owner=claim["owner"],
            timestamp=claim["timestamp"],
            epoch=claim["epoch"]
        )
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
            await asyncio.to_thread(cleanup_job_files, job_id)
            download_locks.pop(job_id, None)
            stale_keys = [
                key for key in local_verifications
                if key[0] == job_id
            ]
            for key in stale_keys:
                local_verifications.pop(key, None)
            job_cache.pop(job_id, None)
            logger.info(f"[Cache] Pruned stale job {job_id}")


async def claim_cleanup_loop():
    global node_runtime_state
    while True:
        await asyncio.sleep(10)
        now = time.time()
        is_recovering = False

        for job_id, state in list(job_cache.items()):
            stale = []

            for chunk, claim in list(state.get("claims", {}).items()):
                if now - claim.get("timestamp", 0) > CLAIM_TIMEOUT:
                    stale.append(chunk)

            for chunk in stale:
                if not is_recovering:
                    node_runtime_state = "RECOVERING"
                    is_recovering = True
                    await enqueue_runtime_snapshot()
                logger.warning(
                    f"[Claims] Expired stale claim "
                    f"{chunk} for job {job_id}"
                )
                state["claims"].pop(chunk, None)
                local_verifications.pop((job_id, chunk), None)
                await broadcast_action(
                    "chunk_requeue",
                    job_id=job_id,
                    chunk=chunk
                )
                logger.info(
                    f"[Recovery] Requeued stale chunk "
                    f"{chunk} for job {job_id}"
                )
                state["last_updated"] = time.time()

        if is_recovering:
            async with active_chunks_lock:
                if active_chunks > 0:
                    node_runtime_state = "EXECUTING"
                else:
                    node_runtime_state = "IDLE"
            await enqueue_runtime_snapshot()


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
                logger.error(f"[Sender] Dropping message after {MAX_RETRIES} retries")

            continue

        try:
            await websocket_connection.send(json.dumps(message))
        except Exception:
            logger.warning("[Sender] Retry sending failed")

            if retries < MAX_RETRIES:
                wrapped["retries"] = retries + 1
                await asyncio.sleep(1)
                await send_queue.put(wrapped)
            else:
                logger.error(f"[Sender] Dropping failed message after {MAX_RETRIES} retries")


def merge_job_state(local, incoming):
    if not isinstance(incoming, dict):
        return local

    incoming_chunks = incoming.get("chunks", [])
    incoming_completed = incoming.get("completed", [])
    incoming_claims = incoming.get("claims", {})

    if isinstance(incoming_chunks, list):
        local["chunks"].update({str(c) for c in incoming_chunks})
    if isinstance(incoming_completed, list):
        local["completed"].update({str(c) for c in incoming_completed})
    if isinstance(incoming_claims, dict):
        for chunk, incoming_claim in incoming_claims.items():
            chunk = str(chunk)
            if not isinstance(incoming_claim, dict):
                continue

            local_claim = local["claims"].get(chunk)

            if not local_claim:
                local["claims"][chunk] = incoming_claim
                continue

            incoming_epoch = incoming_claim.get("epoch", 0)
            local_epoch = local_claim.get("epoch", 0)

            if incoming_epoch > local_epoch:
                local["claims"][chunk] = incoming_claim
            elif (
                incoming_epoch == local_epoch
                and incoming_claim.get("timestamp", 0) > local_claim.get("timestamp", 0)
            ):
                local["claims"][chunk] = incoming_claim

    for chunk in list(local["completed"]):
        local["claims"].pop(chunk, None)

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
    global verify_success_count, verify_mismatch_count
    global last_relay_warning
    global node_runtime_state

    while True:
        node_id = get_node_id()
        api_key = get_api_key()

        if not api_key:
            logger.error("[Relay] API_KEY is not set. Waiting...")
            await asyncio.sleep(2)
            continue

        for base_url in RELAY_URLS:
            try:
                relay_url = get_relay_ws_url(base_url, node_id, api_key)
                logger.info(f"[Relay] Trying {base_url}")

                async with websockets.connect(relay_url, ping_interval=20, ping_timeout=20) as websocket:
                    websocket_connection = websocket
                    current_relay = base_url
                    node_runtime_state = "IDLE"
                    os.environ["RELAY_HTTP_URL"] = base_url
                    if work_loop_started:
                        logger.info(
                            f"[Relay] Reconnected to {base_url} as {node_id}"
                        )
                    else:
                        logger.info(
                            f"[Relay] Connected to {base_url} as {node_id}"
                        )
                    logger.info(
                        f"[System] Nexus node active | "
                        f"max_chunks={MAX_CONCURRENT_CHUNKS} "
                        f"claim_timeout={CLAIM_TIMEOUT}s "
                        f"known_peers={len(known_peers)}"
                    )

                    if not work_loop_started:
                        asyncio.create_task(sender_loop())
                        asyncio.create_task(scheduler_loop())
                        asyncio.create_task(cache_maintenance_loop())
                        asyncio.create_task(claim_cleanup_loop())
                        asyncio.create_task(peer_gossip_loop())
                        asyncio.create_task(verification_timeout_loop())
                        asyncio.create_task(metrics_loop())
                        work_loop_started = True

                    await enqueue_runtime_snapshot()

                    while True:
                        message = await websocket.recv()
                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "heartbeat":
                            await enqueue_runtime_snapshot()

                        elif msg_type == "peer_list":
                            peers = data.get("nodes", [])
                            self_id = get_node_id()
                            for peer in peers:
                                if peer != self_id:
                                    add_peer(peer)

                        elif msg_type == "job_manifest":
                            payload = data.get("payload", {})
                            job_id = payload.get("job_id")
                            if not job_id or not is_valid_job_id(job_id):
                                continue

                            total_chunks = int(payload.get("total_chunks", 0) or 0)
                            chunk_data_raw = payload.get("chunk_data", {})
                            chunk_data = validate_chunk_data(chunk_data_raw)
                            state = init_job(job_id, total_chunks=total_chunks, chunk_data_map=chunk_data)
                            state["status"] = "running"
                            state["last_updated"] = time.time()

                        elif msg_type == "cleanup_job":
                            payload = data.get("payload", {})
                            job_id = payload.get("job_id")
                            if job_id and is_valid_job_id(job_id):
                                await asyncio.to_thread(cleanup_job_files, job_id)

                                job_cache.pop(job_id, None)
                                download_locks.pop(job_id, None)

                                stale_keys = [
                                    key for key in local_verifications
                                    if key[0] == job_id
                                ]

                                for key in stale_keys:
                                    local_verifications.pop(key, None)                                

                        elif msg_type == "direct_message":
                            payload = data.get("payload", {})
                            source = data.get("source")
                            action = payload.get("action")

                            if action == "claim_chunk":
                                job_id = payload.get("job_id")
                                chunk = str(payload.get("chunk"))
                                if job_id is None or chunk is None:
                                    continue
                                state = init_job(job_id)
                                incoming_claim = {
                                    "owner": payload.get("owner"),
                                    "timestamp": payload.get("timestamp", 0),
                                    "epoch": payload.get("epoch", 0)
                                }
                                local_claim = state["claims"].get(chunk)

                                accept = False
                                if not local_claim:
                                    accept = True
                                else:
                                    local_epoch = local_claim.get("epoch", 0)
                                    incoming_epoch = incoming_claim.get("epoch", 0)

                                    if incoming_epoch > local_epoch:
                                        accept = True
                                    elif (
                                        incoming_epoch == local_epoch
                                        and incoming_claim["timestamp"] > local_claim.get("timestamp", 0)
                                    ):
                                        accept = True

                                if accept and chunk not in state["completed"]:
                                    state["claims"][chunk] = incoming_claim
                                    logger.debug(
                                        f"[Claims] Accepted claim for chunk {chunk} "
                                        f"job {job_id} owner={incoming_claim['owner']} "
                                        f"epoch={incoming_claim['epoch']}"
                                    )
                                else:
                                    logger.debug(
                                        f"[Claims] Rejected stale claim for chunk {chunk} "
                                        f"job {job_id}"
                                    )
                                state["last_updated"] = time.time()

                            elif action == "complete_chunk":
                                job_id = payload.get("job_id")
                                chunk = str(payload.get("chunk"))
                                if job_id is None or chunk is None:
                                    continue
                                state = init_job(job_id)
                                if chunk not in state["completed"]:
                                    state["completed"].add(chunk)
                                    state["claims"].pop(chunk, None)
                                    state["last_updated"] = time.time()
                                else:
                                    logger.warning(
                                        f"[Consistency] Duplicate completion "
                                        f"job={job_id} chunk={chunk}"
                                    )

                            elif action == "verify_chunk":
                                job_id = payload.get("job_id")
                                chunk = str(payload.get("chunk"))
                                if job_id is None or chunk is None or not source:
                                    continue
                                asyncio.create_task(execute_verify_chunk(job_id, chunk, source))

                            elif action == "verify_result":
                                job_id = payload.get("job_id")
                                chunk = str(payload.get("chunk"))
                                verification_key = (job_id, str(chunk))
                                verification = local_verifications.get(verification_key)

                                if not verification:
                                    continue
                                if source != verification.get("verifier"):
                                    logger.warning(
                                        f"[VERIFY] Ignoring verification from "
                                        f"unexpected source for chunk {chunk} job {job_id}"
                                    )
                                    local_verifications.pop(verification_key, None)
                                    continue

                                original_result = str(
                                    verification["original_result"]
                                ).strip()
                                verify_result = str(
                                    payload.get("result")
                                ).strip()

                                if (
                                    payload.get("status") == "success"
                                    and original_result == verify_result
                                ):
                                    async with metrics_lock:
                                        verify_success_count += 1
                                    logger.debug(
                                        f"[VERIFY] Verified chunk {chunk} "
                                        f"for job {job_id}"
                                    )

                                    state = job_cache.get(job_id)
                                    if state and chunk not in state["completed"]:
                                        state["completed"].add(chunk)
                                        state["claims"].pop(chunk, None)
                                        state["last_updated"] = time.time()
                                        await broadcast_action("complete_chunk", job_id=job_id, chunk=chunk)

                                    await enqueue_message({
                                        "type": "submit_result",
                                        "source": get_node_id(),
                                        "payload": {
                                            "job_id": job_id,
                                            "chunk": str(chunk),
                                            "status": "success",
                                            "result": verification["original_result"],
                                            "logs": verification.get("logs", ""),
                                            "error": ""
                                        }
                                    })
                                    state = job_cache.get(job_id)

                                    if state:
                                        total_chunks = state.get("total_chunks", 0)

                                        if total_chunks and len(state["completed"]) >= total_chunks:
                                            state["status"] = "completed"

                                            await broadcast_action(
                                                "job_complete",
                                                job_id=job_id
                                            )

                                            if not state.get("cleanup_scheduled"):
                                                state["cleanup_scheduled"] = True
                                                asyncio.create_task(cleanup_job_cache(job_id))
                                else:
                                    async with metrics_lock:
                                        verify_mismatch_count += 1
                                    logger.warning(
                                        f"[VERIFY] Mismatch for chunk {chunk} "
                                        f"job {job_id}"
                                    )

                                    state = job_cache.get(job_id)
                                    if state:
                                        state["completed"].discard(chunk)
                                        state["claims"].pop(chunk, None)
                                        state["last_updated"] = time.time()
                                        await broadcast_action(
                                                "chunk_requeue",
                                                job_id=job_id,
                                                chunk=chunk
                                            )

                                local_verifications.pop(verification_key, None)

                            elif action == "job_complete":
                                job_id = payload.get("job_id")

                                if job_id:
                                    state = init_job(job_id)
                                    state["status"] = "completed"
                                    state["claims"].clear()
                                    state["last_updated"] = time.time()

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
                                    await asyncio.to_thread(cleanup_job_files, job_id)
                                    job_cache.pop(job_id, None)
                                    download_locks.pop(job_id, None)
                                    stale_keys = [
                                        key for key in local_verifications
                                        if key[0] == job_id
                                    ]
                                    for key in stale_keys:
                                        local_verifications.pop(key, None)

                            elif action == "peer_exchange":
                                if source:
                                    add_peer(source)
                                else:
                                    continue
                                peers = payload.get("peers", [])

                                if isinstance(peers, list):
                                    for peer_id in peers:
                                        if isinstance(peer_id, str):
                                            add_peer(peer_id)

                            elif action == "chunk_requeue":
                                job_id = payload.get("job_id")
                                chunk = str(payload.get("chunk"))

                                if job_id is None or chunk is None:
                                    continue

                                state = init_job(job_id)
                                if state.get("status") == "completed":
                                    continue

                                state["completed"].discard(chunk)
                                state["claims"].pop(chunk, None)
                                state["last_updated"] = time.time()
                                local_verifications.pop((job_id, chunk), None)

            except ws_exceptions.ConnectionClosed:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                node_runtime_state = "DISCONNECTED"
                logger.warning(f"[Relay] Connection closed: {base_url}")
            except (ws_exceptions.WebSocketException, OSError) as exc:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                node_runtime_state = "DISCONNECTED"
                logger.warning(
                    f"[Relay] Connection failed: {base_url} | error={exc}"
                )
            except Exception:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                node_runtime_state = "DISCONNECTED"
                logger.exception(
                    f"[Relay] Unexpected relay failure: {base_url}"
                )

        now = time.time()
        node_runtime_state = "DISCONNECTED"
        if now - last_relay_warning > 30:
            logger.warning("[Relay] All relays failed. Retrying in 3s...")
            last_relay_warning = now
        await asyncio.sleep(3)
