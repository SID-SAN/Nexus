import asyncio
from email import message
from threading import local
import websockets
import json
import os
import time
import random
import re
import uuid
import hashlib
import zipfile
import shutil
from websockets import exceptions as ws_exceptions
from urllib.parse import quote
from node.downloader import download_job as download_job_from_relay
from node.executor import execute_chunk, cleanup_job as cleanup_job_files
from config import (
    RELAY_URLS,
    PEER_PORT,
    PACKAGE_SERVER_PORT
)
from logger import setup_logger
from aiohttp import web, ClientSession, ClientTimeout
from chaos import (
    chaos_enabled,
    should_trigger,
    random_delay,
    DROP_MESSAGE_PROBABILITY,
    DUPLICATE_MESSAGE_PROBABILITY,
    MESSAGE_DELAY_PROBABILITY,
    MAX_DELAY_SECONDS,
    EXECUTION_FREEZE_PROBABILITY,
    MAX_EXECUTION_FREEZE_SECONDS,
    NODE_CRASH_PROBABILITY,
    RELAY_DISCONNECT_PROBABILITY,
    PARTITION_PROBABILITY,
    PARTITION_MESSAGE_TYPES,
)
metrics_lock = asyncio.Lock()
claim_lock = asyncio.Lock()
active_chunks_lock = asyncio.Lock()
runtime_state_lock = asyncio.Lock()
current_relay = None
current_relay_url = None
executing_tasks = 0
verifying_tasks = 0
recovering_tasks = 0
MAX_RETRIES = 5
download_locks = {}
owned_claims = {}
peer_connections = {}
peer_server = None
package_server = None
relay_connected = False
startup_recovery_done = False
peer_recovery_done = False
chaos_frozen_chunks = set()

logger = setup_logger("node-client")

def get_node_id():
    return os.getenv("NODE_ID", "node_default")


def get_api_key():
    return os.getenv("API_KEY")


def get_public_peer_host():
    return (
        os.getenv("PUBLIC_IP")
        or os.getenv("PEER_HOST")
        or "localhost"
    )


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


async def download_job(job_id):
    jobs_base = os.path.abspath("jobs")
    extract_path = os.path.join(
        jobs_base,
        str(job_id)
    )

    if os.path.exists(extract_path):
        logger.info(
            f"[Package] Using local package "
            f"job={job_id}"
        )
        return extract_path

    mirrors = list(
        package_registry.get(job_id, set())
    )

    random.shuffle(mirrors)

    for peer_id in mirrors:
        if peer_id == get_node_id():
            continue

        peer_result = await download_job_from_peer(
            peer_id,
            job_id
        )

        if peer_result:
            package_registry.setdefault(
                job_id,
                set()
            ).add(get_node_id())

            try:
                await broadcast_action(
                    "package_available",
                    job_id=job_id,
                    peer_id=get_node_id(),
                    package_hash=package_hash_registry.get(job_id)
                )

            except Exception:
                logger.exception(
                    f"[Package] Failed to broadcast "
                    f"mirror availability "
                    f"job={job_id}"
                )

            return peer_result

    logger.warning(
        f"[Package] Falling back to relay "
        f"job={job_id}"
    )

    extract_path = await asyncio.to_thread(
        download_job_from_relay,
        job_id
    )

    package_hash = await asyncio.to_thread(
        compute_package_hash,
        job_id
    )
    if package_hash:
        package_hash_registry[job_id] = package_hash

    package_registry.setdefault(
        job_id,
        set()
    ).add(get_node_id())

    try:

        await broadcast_action(
            "package_available",
            job_id=job_id,
            peer_id=get_node_id(),
            package_hash=package_hash_registry.get(job_id)
        )

    except Exception:

        logger.exception(
            f"[Package] Failed to broadcast "
            f"relay package availability "
            f"job={job_id}"
        )

    return extract_path

websocket_connection = None
known_peers = set()
peer_last_seen = {}
peer_scores = {}
peer_runtime = {}
peer_address_cache = {}
package_registry = {}
package_hash_registry = {}
job_manifest_registry = {}
DEFAULT_TRUST_SCORE = 100
TRUST_REWARD = 2
TRUST_PENALTY_MISMATCH = 10
TRUST_PENALTY_TIMEOUT = 5
TRUST_MIN_SCORE = 0
TRUST_MAX_SCORE = 200
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
VERIFY_QUORUM_SIZE = 3
VERIFY_MIN_AGREEMENT = 2
active_chunks = 0
MAX_CONCURRENT_CHUNKS = 2
LOAD_PRESSURE_THRESHOLD = 0.75
MAX_FAIRNESS_DELAY = 2.0
CLAIM_JITTER_MAX = 0.2
FAILED_CHUNK_BACKOFF = 1
LOCAL_VERIFY_TIMEOUT = 45
CLAIM_TIMEOUT = 900
COMPLETED_JOB_TTL = 120
STALE_JOB_TTL = 3600
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")

LOCAL_CLAIMS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "node",
    "runtime_state",
    "claims.json",
)

LOCAL_VERIFICATIONS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "node",
    "runtime_state",
    "verifications.json",
)

PEER_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "node",
    "runtime_state",
    "peers.json",
)

def _serialize_local_verifications():
    serialized = []
    for (job_id, chunk), verification in local_verifications.items():
        if not isinstance(verification, dict):
            continue
        serialized.append({
            "job_id": str(job_id),
            "chunk": str(chunk),
            "verifiers": verification.get("verifiers", []),
            "responses": verification.get("responses", {}),
            "original_result": verification.get("original_result"),
            "timestamp": verification.get("timestamp", time.time()),
            "logs": verification.get("logs", ""),
            "finalized": verification.get("finalized", False),
            "required_agreement": verification.get(
                "required_agreement",
                VERIFY_MIN_AGREEMENT
            ),
        })
    return serialized

def _serialize_owned_claims():
    serialized = []

    for (job_id, chunk), claim in owned_claims.items():

        if not isinstance(claim, dict):
            continue

        serialized.append({
            "job_id": str(job_id),
            "chunk": str(chunk),
            "timestamp": claim.get("timestamp", time.time()),
            "epoch": claim.get("epoch", 0),
        })

    return serialized

def save_local_verifications():
    os.makedirs(os.path.dirname(LOCAL_VERIFICATIONS_FILE), exist_ok=True)
    payload = {"verifications": _serialize_local_verifications()}
    temp_path = f"{LOCAL_VERIFICATIONS_FILE}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, LOCAL_VERIFICATIONS_FILE)
    except Exception:
        logger.exception("[VERIFY] Failed to persist local verifications")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

def save_owned_claims():
    os.makedirs(os.path.dirname(LOCAL_CLAIMS_FILE), exist_ok=True)

    payload = {
        "claims": _serialize_owned_claims()
    }

    temp_path = f"{LOCAL_CLAIMS_FILE}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, default=str)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, LOCAL_CLAIMS_FILE)

    except Exception:
        logger.exception("[Claims] Failed to persist owned claims")

        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

def load_local_verifications():
    if not os.path.exists(LOCAL_VERIFICATIONS_FILE):
        return

    try:
        with open(LOCAL_VERIFICATIONS_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        logger.exception("[VERIFY] Failed to load local verifications")
        return

    if isinstance(payload, dict):
        records = payload.get("verifications", [])
    elif isinstance(payload, list):
        records = payload
    else:
        logger.warning("[VERIFY] Invalid verification state format, ignoring")
        return

    restored = {}
    for record in records:
        if not isinstance(record, dict):
            continue

        job_id = record.get("job_id")
        chunk = record.get("chunk")
        verifiers = record.get("verifiers", [])
        responses = record.get("responses", {})
        if not job_id or chunk is None or not verifiers:
            continue

        timestamp = record.get("timestamp", time.time())
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            timestamp = time.time()

        restored[(str(job_id), str(chunk))] = {
            "original_result": record.get("original_result"),
            "timestamp": timestamp,
            "logs": record.get("logs", ""),
            "verifiers": record.get("verifiers", []),
            "responses": record.get("responses", {}),
            "finalized": bool(record.get("finalized", False)),
            "required_agreement": int(
                record.get(
                    "required_agreement",
                    VERIFY_MIN_AGREEMENT
                )
            ),
        }

    local_verifications.clear()
    local_verifications.update(restored)
    if restored:
        logger.info(
            f"[VERIFY] Restored {len(restored)} pending local verifications"
        )

def load_owned_claims():

    if not os.path.exists(LOCAL_CLAIMS_FILE):
        return

    try:
        with open(LOCAL_CLAIMS_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

    except Exception:
        logger.exception("[Claims] Failed to load owned claims")
        return

    if isinstance(payload, dict):
        records = payload.get("claims", [])

    elif isinstance(payload, list):
        records = payload

    else:
        logger.warning("[Claims] Invalid owned claims format")
        return

    restored = {}

    for record in records:

        if not isinstance(record, dict):
            continue

        job_id = record.get("job_id")
        chunk = record.get("chunk")

        if not job_id or chunk is None:
            continue

        timestamp = record.get("timestamp", time.time())

        try:
            timestamp = float(timestamp)

        except (TypeError, ValueError):
            timestamp = time.time()

        restored[(str(job_id), str(chunk))] = {
            "timestamp": timestamp,
            "epoch": int(record.get("epoch", 0))
        }

    owned_claims.clear()
    owned_claims.update(restored)

    if restored:
        logger.info(
            f"[Claims] Restored {len(restored)} owned claims"
        )

def save_peer_cache():

    os.makedirs(
        os.path.dirname(PEER_CACHE_FILE),
        exist_ok=True
    )

    payload = {
        "peers": peer_address_cache
    }

    temp_path = f"{PEER_CACHE_FILE}.tmp"

    try:

        with open(temp_path, "w", encoding="utf-8") as handle:

            json.dump(payload, handle)

            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, PEER_CACHE_FILE)

    except Exception:

        logger.exception(
            "[Peers] Failed to save peer cache"
        )

        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_peer_cache():

    if not os.path.exists(PEER_CACHE_FILE):
        return

    try:

        with open(
            PEER_CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as handle:

            payload = json.load(handle)

    except Exception:

        logger.exception(
            "[Peers] Failed to load peer cache"
        )

        return

    peers = payload.get("peers", {})

    if not isinstance(peers, dict):
        return

    peer_address_cache.clear()

    for peer_id, data in peers.items():

        if not isinstance(data, dict):
            continue

        peer_address_cache[peer_id] = {
            "host": data.get("host"),
            "port": data.get("port", PEER_PORT),
            "package_port": data.get("package_port"),
            "last_seen": data.get("last_seen", time.time()),
            "trust": data.get(
                "trust",
                DEFAULT_TRUST_SCORE
            )
        }

    logger.info(
        f"[Peers] Restored "
        f"{len(peer_address_cache)} cached peers"
    )

def add_local_verification(verification_key, verification_data):
    local_verifications[verification_key] = verification_data
    save_local_verifications()


def remove_local_verification(verification_key):
    removed = local_verifications.pop(verification_key, None)
    save_local_verifications()
    return removed

def update_peer_cache(
    peer_id,
    host=None,
    port=PEER_PORT,
    package_port=None
):

    if not peer_id:
        return

    if peer_id == get_node_id():
        return

    existing = peer_address_cache.get(
        peer_id,
        {}
    )

    if host and not is_valid_peer_host(host):
        return

    peer_address_cache[peer_id] = {
        "host": host or existing.get("host"),
        "port": port or existing.get("port", PEER_PORT),
        "package_port": (
            package_port
            if package_port is not None
            else existing.get("package_port")
        ),
        "last_seen": time.time(),
        "trust": peer_scores.get(
            peer_id,
            {}
        ).get(
            "trust",
            DEFAULT_TRUST_SCORE
        )
    }

    save_peer_cache()

load_local_verifications()
load_owned_claims()
load_peer_cache()

async def get_runtime_state():

    if not relay_connected:
        return "DISCONNECTED"

    async with runtime_state_lock:

        if recovering_tasks > 0:
            return "RECOVERING"

        if verifying_tasks > 0:
            return "VERIFYING"

        if executing_tasks > 0:
            return "EXECUTING"

        return "IDLE"

def error_response(message, code="ERROR"):
    return {
        "status": "failed",
        "error": message,
        "code": code,
    }


def is_valid_job_id(job_id):
    return bool(JOB_ID_RE.fullmatch(str(job_id or "")))

def get_job_zip_path(job_id):

    return os.path.abspath(
        os.path.join(
            "jobs",
            f"{job_id}.zip"
        )
    )


def extract_job_zip(job_id):
    zip_path = get_job_zip_path(job_id)
    jobs_base = os.path.abspath("jobs")
    extract_path = os.path.join(
        jobs_base,
        str(job_id)
    )
    if os.path.exists(extract_path):
        shutil.rmtree(extract_path)
    os.makedirs(
        extract_path,
        exist_ok=True
    )

    with zipfile.ZipFile(zip_path, "r") as archive:

        archive.extractall(extract_path)

    return extract_path

def compute_package_hash(job_id):
    zip_path = get_job_zip_path(job_id)
    if not os.path.exists(zip_path):
        return None
    sha256 = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()

def build_job_manifest(
    job_id,
    total_chunks,
    chunk_data
):
    manifest = {
        "job_id": job_id,
        "package_hash": compute_package_hash(job_id),
        "total_chunks": total_chunks,
        "chunk_data": chunk_data,
        "created_by": get_node_id(),
        "created_at": time.time(),
        "manifest_version": 1
    }
    job_manifest_registry[job_id] = manifest
    return manifest

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

    runtime_state = await get_runtime_state()
    public_ip = get_public_peer_host()

    await enqueue_message({
        "type": "heartbeat_ack",
        "source": get_node_id(),
        "payload": {
            "status": runtime_state,
            "active_chunks": current_active,
            "known_peers": len(known_peers),
            "peer_host": public_ip,
            "peer_port": PEER_PORT,
            "package_port": PACKAGE_SERVER_PORT,
            "relay": current_relay_url,
        },
        "peer_port": PEER_PORT,
        "peer_host": public_ip,
        "package_port": PACKAGE_SERVER_PORT,
    })


def build_job_state(job):
    return {
        "total_chunks": job.get("total_chunks", 0),
        "chunks": list(job.get("chunks", set())),
        "completed": list(job.get("completed", set())),
        "claims": job.get("claims", {}),
        "status": job.get("status", "running"),
        "chunk_data_map": job.get("chunk_data_map", {}),
        "last_updated": job.get("last_updated", time.time()),
        "version_vector": job.get("version_vector",{}),
        "conflicts": job.get("conflicts", [])
    }

def build_job_digest():

    digest = {}

    for job_id, state in job_cache.items():

        digest[job_id] = {
            "completed": len(
                state.get("completed", [])
            ),
            "claims": len(
                state.get("claims", {})
            ),
            "status": state.get("status"),
            "version_vector": state.get(
                "version_vector",
                {}
            ),
            "merkle": compute_job_merkle(
                job_id,
                state
            )
        }

    return digest

def compute_job_merkle(job_id, state):

    payload = {
        "completed": sorted(
            list(state.get("completed", []))
        ),
        "claims": {
            str(k): v
            for k, v in sorted(
                state.get("claims", {}).items()
            )
        },
        "version_vector": state.get(
            "version_vector",
            {}
        ),
        "status": state.get("status")
    }

    serialized = json.dumps(
        payload,
        sort_keys=True
    )

    return hashlib.sha256(
        serialized.encode()
    ).hexdigest()

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
        "last_updated": time.time(),
        "applied_deltas": set(),
        "version_vector": {},
        "conflicts": [],
        "delta_log": []
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

def get_peer_score(peer_id):
    if peer_id not in peer_scores:
        peer_scores[peer_id] = {
            "success": 0,
            "timeouts": 0,
            "mismatches": 0,
            "trust": DEFAULT_TRUST_SCORE
        }
    return peer_scores[peer_id]

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
    update_peer_cache(peer_id)
    if peer_id not in peer_scores:
        peer_scores[peer_id] = {
            "success": 0,
            "timeouts": 0,
            "mismatches": 0,
            "trust": DEFAULT_TRUST_SCORE
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
        peer_runtime.pop(peer, None)

        logger.info(f"[Peers] Removed stale peer {peer}")

def build_peer_metadata(peer_id):

    cache = peer_address_cache.get(
        peer_id,
        {}
    )

    return {
        "peer_id": peer_id,
        "host": cache.get("host"),
        "port": cache.get("port", PEER_PORT),
        "package_port": cache.get("package_port"),
        "trust": cache.get(
            "trust",
            DEFAULT_TRUST_SCORE
        ),
        "last_seen": cache.get(
            "last_seen",
            time.time()
        )
    }

def is_valid_peer_host(host):

    if not host:
        return False

    if not isinstance(host, str):
        return False

    host = host.strip()

    if not host:
        return False

    return True

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
                            build_peer_metadata(p)
                            for p in known_subset
                            if p != peer_id
                        ]
                    }
                })

        except Exception:
            logger.exception("[Peers] Gossip loop failed")
            await asyncio.sleep(5)

def increment_version_vector(state):

    node_id = get_node_id()

    vector = state.setdefault(
        "version_vector",
        {}
    )

    vector[node_id] = (
        vector.get(node_id, 0) + 1
    )


def compare_version_vectors(a, b):

    a = a or {}
    b = b or {}

    a_bigger = False
    b_bigger = False

    keys = set(a.keys()) | set(b.keys())

    for key in keys:

        av = a.get(key, 0)
        bv = b.get(key, 0)

        if av > bv:
            a_bigger = True

        elif bv > av:
            b_bigger = True

    if a_bigger and not b_bigger:
        return "newer"

    if b_bigger and not a_bigger:
        return "older"

    if not a_bigger and not b_bigger:
        return "equal"

    return "concurrent"

def compare_claims(local_claim, incoming_claim):

    if not local_claim:
        return incoming_claim

    if not incoming_claim:
        return local_claim

    local_epoch = local_claim.get("epoch", 0)
    incoming_epoch = incoming_claim.get("epoch", 0)

    if incoming_epoch > local_epoch:
        return incoming_claim

    if local_epoch > incoming_epoch:
        return local_claim

    local_ts = local_claim.get("timestamp", 0)
    incoming_ts = incoming_claim.get("timestamp", 0)

    if incoming_ts > local_ts:
        return incoming_claim

    if local_ts > incoming_ts:
        return local_claim

    local_owner = str(
        local_claim.get("owner", "")
    )

    incoming_owner = str(
        incoming_claim.get("owner", "")
    )

    if incoming_owner > local_owner:
        return incoming_claim

    return local_claim

def append_delta(state, operation, data):

    delta_log = state.setdefault(
        "delta_log",
        []
    )

    delta_log.append({
        "delta_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "operation": operation,
        "data": data,
        "version_vector": dict(
            state.get("version_vector", {})
        )
    })

    state["delta_log"] = delta_log[-100:]

async def gossip_digest_loop():
    while True:

        try:

            await asyncio.sleep(15)

            if not known_peers:
                continue

            digest = build_job_digest()

            peers = list(known_peers)

            selected = random.sample(
                peers,
                min(2, len(peers))
            )

            for peer_id in selected:

                await enqueue_message({
                    "type": "direct_message",
                    "payload": {
                        "target": peer_id,
                        "action": "digest_gossip",
                        "digest": digest
                    }
                })

        except Exception:
            logger.exception(
                "[Gossip] Digest loop failed"
            )

            await asyncio.sleep(5)

async def broadcast_action(action, **kwargs):
    peers = list(known_peers)
    for peer_id in peers:
        if (
            chaos_enabled()
            and should_trigger(PARTITION_PROBABILITY)
        ):

            logger.warning(
                f"[Chaos] Partition blocked "
                f"broadcast action={action} "
                f"peer={peer_id}"
            )

            continue
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
        await send_direct_or_relay(
            peer_id,
            {
                "type": "direct_message",
                "payload": {
                    "target": peer_id,
                    "action": "job_sync",
                    "job_id": job_id,
                    "status": payload_state
                }
            }
        )

async def send_job_delta_sync(job_id):

    state = job_cache.get(job_id)

    if not state:
        return

    if state.get("status") == "completed":
        return

    peers = list(known_peers)

    if not peers:
        return

    deltas = state.get(
        "delta_log",
        []
    )[-20:]

    if not deltas:
        return

    selected_peers = random.sample(
        peers,
        min(2, len(peers))
    )

    for peer_id in selected_peers:

        await send_direct_or_relay(
            peer_id,
            {
                "type": "direct_message",
                "payload": {
                    "target": peer_id,
                    "action": "job_delta_sync",
                    "job_id": job_id,
                    "deltas": deltas
                }
            }
        )

async def cleanup_job_cache(job_id):
    await asyncio.sleep(24 * 60 * 60)
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
    stale_claims = [
        key for key in owned_claims
        if key[0] == job_id
    ]

    for key in stale_claims:
        owned_claims.pop(key, None)
    save_owned_claims()

    stale_keys = [
        key for key in local_verifications
        if key[0] == job_id
    ]
    for key in stale_keys:
        remove_local_verification(key)
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
                owned_claims.pop((job_id, chunk), None)
                save_owned_claims()
                increment_version_vector(state)
                append_delta(
                    state,
                    "chunk_requeue",
                    {
                        "chunk": chunk
                    }
                )
                state["last_updated"] = time.time()
                await broadcast_action(
                    "chunk_requeue",
                    job_id=job_id,
                    chunk=chunk
                )

            for verifier_id in verification.get("verifiers", []):

                if verifier_id not in verification.get(
                    "responses",
                    {}
                ):

                    score = get_peer_score(verifier_id)

                    score["timeouts"] = (
                        score.get("timeouts", 0) + 1
                    )

                    current = score.get(
                        "trust",
                        DEFAULT_TRUST_SCORE
                    )

                    score["trust"] = max(
                        TRUST_MIN_SCORE,
                        current - TRUST_PENALTY_TIMEOUT
                    )

            remove_local_verification(key)


async def metrics_loop():
    while True:
        await asyncio.sleep(30)

        async with metrics_lock:
            success = verify_success_count
            mismatch = verify_mismatch_count
            timeout = verify_timeout_count
            top_trusted = sorted(
                peer_scores.items(),
                key=lambda x: x[1].get("trust", 0),
                reverse=True
            )[:5]
        async with active_chunks_lock:
            current_active = active_chunks
        claims = sum(
            len(state.get("claims", {}))
            for state in job_cache.values()
        )
        send_queue_depth = send_queue.qsize()

        attempts = success + mismatch + timeout
        verification_rate = (
            success / attempts
            if attempts > 0
            else 0
        )

        logger.info(
            "[Metrics] "
            f"verify_success={success} "
            f"verify_mismatch={mismatch} "
            f"verify_timeout={timeout} "
            f"verify_rate={verification_rate:.2%} "
            f"claims={claims} "
            f"send_queue={send_queue_depth} "
            f"relay={current_relay} "
            f"known_peers={len(known_peers)} "
            f"jobs={len(job_cache)} "
            f"active_chunks={current_active}"
        )
        logger.info(
            f"[Trust] Top peers: {top_trusted}"
        )


async def execute_verify_chunk(job_id, chunk, target_node):
    global verifying_tasks
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

    async with runtime_state_lock:
        verifying_tasks += 1
    logger.debug(
        f"[VERIFY] Node verifying chunk {chunk} "
        f"for job {job_id}"
    )
    await enqueue_runtime_snapshot()

    try:
        if not is_valid_job_id(job_id):
            raise ValueError("Invalid job_id")

        jobs_base = os.path.abspath("jobs")
        extract_path = os.path.join(jobs_base, str(job_id))
        lock = get_download_lock(job_id)
        async with lock:
            if not os.path.exists(extract_path):
                await download_job(job_id)

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
        await enqueue_runtime_snapshot()
        async with runtime_state_lock:
            verifying_tasks -= 1

            if verifying_tasks < 0:
                verifying_tasks = 0

    await send_direct_or_relay(
        target_node,
        {
            "type": "direct_message",
            "payload": {
                "target": target_node,
                "action": "verify_result",
                **response_payload
            }
        }
    )


async def execute_chunk_task(job_id, chunk):
    global active_chunks
    global executing_tasks
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
                    await download_job(job_id)
                except FileExistsError:
                    pass
    except Exception:
        logger.exception("[Node] Download failed")
        state["claims"].pop(chunk, None)
        owned_claims.pop((job_id, chunk), None)
        save_owned_claims()
        return

    chunk_data = state.get("chunk_data_map", {}).get(str(chunk), {})

    async with runtime_state_lock:
        executing_tasks += 1
    logger.debug(f"[EXECUTE] Starting chunk {chunk} for job {job_id}")
    async with active_chunks_lock:
        active_chunks += 1
    await enqueue_runtime_snapshot()

    async def refresh_claim():

        while True:

            if (job_id, chunk) in chaos_frozen_chunks:
                await asyncio.sleep(1)
                continue

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
            increment_version_vector(state)
            append_delta(
                state,
                "claim_chunk",
                {
                    "chunk": chunk,
                    "claim": current_claim
                }
            )
            owned_claims[(job_id, chunk)] = {
                "timestamp": current_claim["timestamp"],
                "epoch": current_claim["epoch"],
            }
            save_owned_claims()

    heartbeat_task = asyncio.create_task(refresh_claim())
    if chaos_enabled():

        try:

            freeze_probability = EXECUTION_FREEZE_PROBABILITY

            if random.random() < freeze_probability:

                freeze_time = random.uniform(
                    5,
                    MAX_EXECUTION_FREEZE_SECONDS
                )

                logger.warning(
                    f"[Chaos] Freezing execution "
                    f"job={job_id} chunk={chunk} "
                    f"for {freeze_time:.2f}s"
                )

                chaos_frozen_chunks.add((job_id, chunk))

                await asyncio.sleep(freeze_time)

            crash_probability = NODE_CRASH_PROBABILITY

            if random.random() < crash_probability:

                logger.warning(
                    f"[Chaos] Simulating node crash "
                    f"job={job_id} chunk={chunk}"
                )

                raise SystemExit(
                    "[Chaos] Simulated node crash"
                )

        finally:

            chaos_frozen_chunks.discard((job_id, chunk))
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

            if active_chunks < 0:
                active_chunks = 0

        async with runtime_state_lock:
            executing_tasks -= 1

            if executing_tasks < 0:
                executing_tasks = 0
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
            weighted_peers = sorted(
                eligible_peers,
                key=lambda p: (
                    peer_scores.get(
                        p,
                        {}
                    ).get(
                        "trust",
                        DEFAULT_TRUST_SCORE
                    )
                    -
                    (
                        peer_runtime.get(
                            p,
                            {}
                        ).get(
                            "active_chunks",
                            0
                        ) * 10
                    )
                ),
                reverse=True
            )

            logger.debug(
                f"[Fairness] Weighted verifier ranking: "
                f"{weighted_peers}"
            )

            top_candidates = weighted_peers[
                :max(
                    VERIFY_QUORUM_SIZE * 2,
                    VERIFY_QUORUM_SIZE
                )
            ]

            selected_verifiers = random.sample(
                top_candidates,
                min(
                    VERIFY_QUORUM_SIZE,
                    len(top_candidates)
                )
            )
            required_agreement = min(
                VERIFY_MIN_AGREEMENT,
                len(selected_verifiers)
            )           
            verification_key = (job_id, str(chunk))
            add_local_verification(verification_key, {
                "verifiers": selected_verifiers,
                "responses": {},
                "original_result": exec_output.get("result"),
                "required_agreement": required_agreement,
                "timestamp": time.time(),
                "logs": exec_output.get("logs", "")
            })
            for verifier in selected_verifiers:
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
                increment_version_vector(state)
                append_delta(
                    state,
                    "complete_chunk",
                    {
                        "chunk": chunk
                    }
                )
                state["claims"].pop(chunk, None)
                owned_claims.pop((job_id, chunk), None)
                save_owned_claims()
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
        owned_claims.pop((job_id, chunk), None)
        save_owned_claims()

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

    await send_job_delta_sync(job_id)

async def scheduler_loop():
    while True:
        if websocket_connection is None:
            await asyncio.sleep(1)
            continue

        async with active_chunks_lock:
            current_active = active_chunks

        load_ratio = (
            current_active / max(1, MAX_CONCURRENT_CHUNKS)
        )

        if load_ratio >= LOAD_PRESSURE_THRESHOLD:

            fairness_delay = (
                load_ratio * MAX_FAIRNESS_DELAY
            )

            logger.debug(
                f"[Fairness] Local load pressure "
                f"delay={fairness_delay:.2f}s"
            )
            await asyncio.sleep(fairness_delay)

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
            increment_version_vector(state)
            append_delta(
                state,
                "claim_chunk",
                {
                    "chunk": chunk,
                    "claim": claim
                }
            )
            owned_claims[(job_id, chunk)] = {
                "timestamp": claim["timestamp"],
                "epoch": claim["epoch"],
            }
            save_owned_claims()

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

async def startup_claim_recovery():

    if not owned_claims:
        return

    logger.info(
        f"[Recovery] Restoring {len(owned_claims)} persisted claims"
    )

    now = time.time()

    stale_claim_keys = []

    for (job_id, chunk), claim in list(owned_claims.items()):

        timestamp = claim.get("timestamp", 0)

        if now - timestamp > CLAIM_TIMEOUT:

            logger.warning(
                f"[Recovery] Found stale persisted claim "
                f"job={job_id} chunk={chunk}"
            )

            stale_claim_keys.append((job_id, chunk))

            await broadcast_action(
                "chunk_requeue",
                job_id=job_id,
                chunk=chunk
            )

            continue

        state = init_job(job_id)

        state["claims"][chunk] = {
            "owner": get_node_id(),
            "timestamp": timestamp,
            "epoch": claim.get("epoch", 0)
        }

        state["last_updated"] = now

        logger.info(
            f"[Recovery] Restored claim "
            f"job={job_id} chunk={chunk}"
        )

        asyncio.create_task(
            execute_chunk_task(job_id, chunk)
        )

    for key in stale_claim_keys:
        owned_claims.pop(key, None)

    if stale_claim_keys:
        save_owned_claims()

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
                remove_local_verification(key)
            stale_claims = [
                key for key in owned_claims
                if key[0] == job_id
            ]

            for key in stale_claims:
                owned_claims.pop(key, None)

            save_owned_claims()
            job_cache.pop(job_id, None)
            logger.info(f"[Cache] Pruned stale job {job_id}")


async def claim_cleanup_loop():
    global recovering_tasks
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
                    is_recovering = True

                    async with runtime_state_lock:
                        recovering_tasks += 1

                    await enqueue_runtime_snapshot()

                logger.warning(
                    f"[Claims] Expired stale claim "
                    f"{chunk} for job {job_id}"
                )
                state["claims"].pop(chunk, None)
                owned_claims.pop((job_id, chunk), None)
                save_owned_claims()
                remove_local_verification((job_id, chunk))
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
                increment_version_vector(state)
                append_delta(
                    state,
                    "chunk_requeue",
                    {
                        "chunk": chunk
                    }
                )

        if is_recovering:

            await enqueue_runtime_snapshot()

            async with runtime_state_lock:
                recovering_tasks -= 1

                if recovering_tasks < 0:
                    recovering_tasks = 0
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

            payload = json.dumps(message)

            msg_type = message.get("type", "unknown")

            # =====================================================
            # CHAOS: DROP MESSAGE
            # =====================================================

            if should_trigger(DROP_MESSAGE_PROBABILITY):

                logger.warning(
                    f"[Chaos] Dropping outgoing message type={msg_type}"
                )

                continue

            # =====================================================
            # CHAOS: DELAY MESSAGE
            # =====================================================

            if should_trigger(MESSAGE_DELAY_PROBABILITY):

                logger.warning(
                    f"[Chaos] Delaying outgoing message type={msg_type}"
                )

                await random_delay(MAX_DELAY_SECONDS)

            # =====================================================
            # NORMAL SEND
            # =====================================================

            await websocket_connection.send(payload)

            # =====================================================
            # CHAOS: DUPLICATE MESSAGE
            # =====================================================

            if should_trigger(DUPLICATE_MESSAGE_PROBABILITY):

                logger.warning(
                    f"[Chaos] Duplicating outgoing message type={msg_type}"
                )

                await websocket_connection.send(payload)
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
    
    local_vector = local.get(
        "version_vector",
        {}
    )

    incoming_vector = incoming.get(
        "version_vector",
        {}
    )

    vector_result = compare_version_vectors(
        incoming_vector,
        local_vector
    )

    if vector_result == "concurrent":
        local.setdefault(
            "conflicts",
            []
        ).append({
            "timestamp": time.time(),
            "incoming_vector": incoming_vector,
            "local_vector": local_vector
        })
        local["conflicts"] = local["conflicts"][-20:]

        logger.warning(
            "[VectorClock] Concurrent state "
            "detected during merge"
        )

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

            winner = compare_claims(
                local_claim,
                incoming_claim
            )

            if winner is incoming_claim:

                local["claims"][chunk] = incoming_claim

                logger.warning(
                    f"[Consistency] Claim arbitration "
                    f"chunk={chunk} "
                    f"winner={incoming_claim.get('owner')}"
                )

    for chunk in list(local["completed"]):
        local["claims"].pop(chunk, None)

    if isinstance(incoming.get("chunk_data_map"), dict):
        local["chunk_data_map"].update(incoming["chunk_data_map"])

    local["total_chunks"] = max(local.get("total_chunks", 0), int(incoming.get("total_chunks", 0) or 0))

    if incoming.get("status") == "completed":
        local["status"] = "completed"

    local["last_updated"] = max(local.get("last_updated", 0), incoming.get("last_updated", 0) or 0)

    merged_vector = local.setdefault(
        "version_vector",
        {}
    )

    for node_id, counter in incoming_vector.items():

        merged_vector[node_id] = max(
            merged_vector.get(node_id, 0),
            counter
        )

    return local


async def connect_to_relay():
    global websocket_connection
    global work_loop_started
    global current_relay
    global current_relay_url
    global known_peers
    global verify_success_count, verify_mismatch_count
    global last_relay_warning
    global relay_connected
    global startup_recovery_done
    global peer_server
    global peer_recovery_done

    if peer_server is None:

        peer_server = await websockets.serve(
            peer_server_handler,
            "0.0.0.0",
            PEER_PORT
        )
        await start_package_server()
        logger.info(
            "[PeerMesh] Direct peer server started"
        )
        if not peer_recovery_done:
            await restore_cached_peers()
            peer_recovery_done = True

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
                    current_relay_url = relay_url
                    relay_connected = True
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
                    logger.info(
                        f"[Chaos] "
                        f"enabled={os.getenv('NEXUS_CHAOS', '0')} "
                        f"freeze_prob={os.getenv('NEXUS_EXECUTION_FREEZE_PROBABILITY', '0')} "
                        f"crash_prob={os.getenv('NEXUS_NODE_CRASH_PROBABILITY', '0')}"
                    )
                    logger.info(f"[Chaos] Enabled={chaos_enabled()}")

                    if not startup_recovery_done:
                        await startup_claim_recovery()
                        startup_recovery_done = True

                    if not work_loop_started:
                        asyncio.create_task(sender_loop())
                        asyncio.create_task(scheduler_loop())
                        asyncio.create_task(cache_maintenance_loop())
                        asyncio.create_task(claim_cleanup_loop())
                        asyncio.create_task(peer_gossip_loop())
                        asyncio.create_task(gossip_digest_loop())
                        asyncio.create_task(verification_timeout_loop())
                        asyncio.create_task(metrics_loop())
                        work_loop_started = True

                    await enqueue_runtime_snapshot()

                    while True:
                        # =====================================================
                        # CHAOS: RELAY DISCONNECT
                        # =====================================================

                        message = await websocket.recv()

                        if (
                            chaos_enabled()
                            and should_trigger(RELAY_DISCONNECT_PROBABILITY)
                        ):

                            logger.warning(
                                f"[Chaos] Simulating relay disconnect "
                                f"relay={current_relay}"
                            )

                            await websocket.close()

                            break

                        data = json.loads(message)
                        if chaos_enabled():
                            msg_type = data.get("type")
                            payload = data.get("payload", {})
                            action = payload.get("action")
                            partition_key = action or msg_type
                            if (
                                partition_key in PARTITION_MESSAGE_TYPES
                                and should_trigger(PARTITION_PROBABILITY)
                            ):
                                logger.warning(
                                    f"[Chaos] Partition dropped incoming "
                                    f"message={partition_key}"
                                )
                                continue
                        await handle_direct_peer_message(data)

            except ws_exceptions.ConnectionClosed:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                    current_relay_url = None
                relay_connected = False
                logger.warning(f"[Relay] Connection closed: {base_url}")
                logger.warning(
                    f"[Recovery] Relay failover triggered "
                    f"known_peers={len(known_peers)} "
                    f"jobs={len(job_cache)}"
                )
            except (ws_exceptions.WebSocketException, OSError) as exc:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                    current_relay_url = None
                relay_connected = False
                logger.warning(
                    f"[Relay] Connection failed: {base_url} | error={exc}"
                )
            except Exception:
                websocket_connection = None
                if current_relay == base_url:
                    current_relay = None
                    current_relay_url = None
                relay_connected = False
                logger.exception(
                    f"[Relay] Unexpected relay failure: {base_url}"
                )

        now = time.time()
        relay_connected = False
        current_relay_url = None
        if now - last_relay_warning > 30:
            logger.warning("[Relay] All relays failed. Retrying in 3s...")
            last_relay_warning = now
        await asyncio.sleep(
            random.uniform(2, 8)
        )

async def peer_server_handler(websocket):

    try:

        async for message in websocket:

            data = json.loads(message)
            payload = data.get("payload", {})
            source = (
                data.get("source")
                or payload.get("source")
            )
            if source:
                peer_connections[source] = websocket

            await handle_direct_peer_message(data)

    except Exception:
        logger.exception(
            "[PeerMesh] Peer server handler crashed"
        )

async def connect_to_peer(
    peer_id,
    host,
    port
):

    if peer_id == get_node_id():
        return
    
    if not is_valid_peer_host(host):
        return

    if peer_id in peer_connections:
        return

    try:

        ws = await websockets.connect(
            f"ws://{host}:{port}"
        )

        peer_connections[peer_id] = ws
        update_peer_cache(
            peer_id,
            host=host,
            port=port
        )

        asyncio.create_task(
            listen_to_peer(
                peer_id,
                ws
            )
        )

        logger.info(
            f"[PeerMesh] Connected "
            f"peer={peer_id}"
        )
        logger.info(
            f"[Manifest] Requesting sync from {peer_id}"
        )

        await send_direct_or_relay(
            peer_id,
            {
                "type": "direct_message",
                "payload": {
                    "target": peer_id,
                    "action": "manifest_sync_request"
                }
            }
        )

    except Exception:

        logger.warning(
            f"[PeerMesh] Connection failed "
            f"peer={peer_id}"
        )

async def listen_to_peer(
    peer_id,
    websocket
):

    try:

        async for message in websocket:

            data = json.loads(message)

            await handle_direct_peer_message(
                data
            )

    except Exception:
        peer_last_seen.pop(peer_id, None)
        peer_connections.pop(
            peer_id,
            None
        )
        logger.warning(
            f"[PeerMesh] Lost peer "
            f"peer={peer_id}"
        )

async def restore_cached_peers():

    if not peer_address_cache:

        logger.info(
            "[PeerRecovery] No cached peers"
        )

        return

    logger.info(
        f"[PeerRecovery] Attempting restore "
        f"for {len(peer_address_cache)} peers"
    )

    recovery_tasks = []

    for peer_id, data in peer_address_cache.items():

        if peer_id == get_node_id():
            continue

        host = data.get("host")
        port = data.get("port", PEER_PORT)

        if not host:
            continue

        recovery_tasks.append(
            asyncio.create_task(
                connect_to_peer(
                    peer_id,
                    host,
                    port
                )
            )
        )

    if recovery_tasks:

        await asyncio.gather(
            *recovery_tasks,
            return_exceptions=True
        )

    logger.info(
        f"[PeerRecovery] Connected peers="
        f"{len(peer_connections)}"
    )

async def serve_job_package(request):

    job_id = request.match_info.get(
        "job_id"
    )

    if not is_valid_job_id(job_id):

        return web.Response(
            status=400,
            text="invalid job id"
        )

    zip_path = get_job_zip_path(job_id)

    logger.info(
        f"[PackageDebug] job={job_id} "
        f"path={zip_path} "
        f"exists={os.path.exists(zip_path)}"
    )

    if not os.path.exists(zip_path):

        return web.Response(
            status=404,
            text="job package not found"
        )

    logger.info(
        f"[Package] Serving package "
        f"job={job_id}"
    )

    return web.FileResponse(zip_path)

async def start_package_server():

    global package_server

    if package_server:
        return

    app = web.Application()

    app.router.add_get(
        "/job_package/{job_id}",
        serve_job_package
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PACKAGE_SERVER_PORT
    )

    await site.start()

    package_server = runner

    logger.info(
        f"[Package] Package server started "
        f"port={PACKAGE_SERVER_PORT}"
    )

async def download_job_from_peer(
    peer_id,
    job_id
):
    temp_zip = None

    peer_info = peer_address_cache.get(
        peer_id,
        {}
    )

    host = peer_info.get("host")

    package_port = peer_info.get(
        "package_port",
        PACKAGE_SERVER_PORT
    )

    if not is_valid_peer_host(host):
        return None

    url = (
        f"http://{host}:{package_port}"
        f"/job_package/{job_id}"
    )

    logger.info(
        f"[Package] Attempting peer download "
        f"peer={peer_id} "
        f"job={job_id}"
    )

    try:

        timeout = ClientTimeout(total=60)

        async with ClientSession(timeout=timeout) as session:

            async with session.get(url) as response:

                if response.status != 200:

                    logger.warning(
                        f"[Package] Peer download failed "
                        f"peer={peer_id} "
                        f"status={response.status}"
                    )

                    return None

                zip_path = get_job_zip_path(job_id)
                temp_zip = f"{zip_path}.tmp"

                os.makedirs(
                    os.path.dirname(zip_path),
                    exist_ok=True
                )

                with open(temp_zip, "wb") as handle:

                    async for chunk in response.content.iter_chunked(65536):

                        handle.write(chunk)

                os.replace(temp_zip, zip_path)

        package_hash = compute_package_hash(job_id)
        expected_hash = package_hash_registry.get(job_id)
        if (
            expected_hash
            and package_hash != expected_hash
        ):
            logger.error(
                f"[Security] Package hash mismatch "
                f"peer={peer_id} "
                f"job={job_id}"
            )
            os.remove(zip_path)
            return None

        logger.info(
            f"[Package] Peer download success "
            f"peer={peer_id} "
            f"job={job_id}"
        )

        extract_path = await asyncio.to_thread(
            extract_job_zip,
            job_id
        )

        return extract_path

    except Exception:

        try:
            if temp_zip and os.path.exists(temp_zip):
                os.remove(temp_zip)
        except OSError:
            pass

        logger.exception(
            f"[Package] Peer download crashed "
            f"peer={peer_id} "
            f"job={job_id}"
        )
        return None

async def recover_missing_package(
    job_id,
    source=None
):

    zip_path = get_job_zip_path(job_id)

    if os.path.exists(zip_path):
        return

    logger.info(
        f"[Recovery] Missing package detected "
        f"job={job_id}"
    )

    if source:

        result = await download_job_from_peer(
            source,
            job_id
        )

        if result:
            logger.info(
                f"[Recovery] Package restored "
                f"job={job_id}"
            )
            return

    logger.warning(
        f"[Recovery] Could not restore package "
        f"job={job_id}"
    )

async def send_direct_or_relay(
    target,
    payload
):

    peer_ws = peer_connections.get(
        target
    )

    if peer_ws:

        try:

            direct_payload = dict(payload)

            direct_payload["source"] = get_node_id()

            await peer_ws.send(
                json.dumps(direct_payload)
            )

            logger.debug(
                f"[PeerMesh] Direct send "
                f"target={target}"
            )

            return

        except Exception:

            logger.warning(
                f"[PeerMesh] Direct send failed "
                f"target={target}"
            )

            peer_connections.pop(
                target,
                None
            )

    await enqueue_message(payload)

async def replicate_package(job_id, source):

    await asyncio.sleep(5)

    zip_path = get_job_zip_path(job_id)

    if not os.path.exists(zip_path):
        return

    package_registry.setdefault(
        job_id,
        set()
    ).add(get_node_id())

    await broadcast_action(
        "package_available",
        job_id=job_id,
        peer_id=get_node_id(),
        package_hash=package_hash_registry.get(job_id)
    )      

async def handle_direct_peer_message(data):
    global verify_success_count, verify_mismatch_count

    msg_type = data.get("type")
    if msg_type == "heartbeat":
        await enqueue_runtime_snapshot()

    elif msg_type == "heartbeat_ack":
        source = data.get("source")
        payload = data.get("payload", {})
        peer_host = payload.get(
            "peer_host",
            data.get(
                "peer_host",
                "localhost"
            )
        )
        peer_port = payload.get(
            "peer_port",
            data.get("peer_port")
        )
        package_port = payload.get(
            "package_port",
            data.get("package_port")
        )

        if source:

            peer_runtime[source] = {
                "status": payload.get("status"),
                "active_chunks": payload.get(
                    "active_chunks",
                    0
                ),
                "known_peers": payload.get(
                    "known_peers",
                    0
                ),
                "relay": payload.get("relay"),
                "timestamp": time.time(),
                "peer_port": peer_port,
                "package_port": package_port,
                "peer_host": peer_host,
            }

            logger.info(
                f"[HeartbeatAck] source={source} "
                f"host={peer_host} "
                f"port={peer_port}"
            )
            
            update_peer_cache(
                source,
                host=peer_host,
                port=peer_port or PEER_PORT,
                package_port=package_port
            )

            try:
                resolved_peer_port = int(peer_port)
            except (TypeError, ValueError):
                resolved_peer_port = None

            if (
                resolved_peer_port
                and resolved_peer_port > 0
                and source not in peer_connections
                and is_valid_peer_host(peer_host)
            ):

                asyncio.create_task(
                    connect_to_peer(
                        source,
                        peer_host,
                        resolved_peer_port
                    )
                )

    elif msg_type == "peer_list":
        peers = data.get("nodes", [])
        logger.info(
            f"[PeerListDebug] {json.dumps(peers, indent=2)}"
        )
        self_id = get_node_id()
        if not isinstance(peers, list):
            return

        for peer in peers:
            if isinstance(peer, str):
                if peer != self_id:
                    add_peer(peer)
                continue

            if not isinstance(peer, dict):
                continue

            peer_id = peer.get("node_id")
            if not peer_id or peer_id == self_id:
                continue

            peer_host = peer.get("peer_host")
            peer_port = peer.get("peer_port")
            package_port = peer.get("package_port")

            add_peer(peer_id)
            update_peer_cache(
                peer_id,
                host=peer_host,
                port=peer_port or PEER_PORT,
                package_port=package_port
            )

            if peer_id in peer_connections:
                continue

            if not is_valid_peer_host(peer_host):
                continue

            try:
                resolved_peer_port = int(peer_port)
            except (TypeError, ValueError):
                continue

            if resolved_peer_port <= 0:
                continue

            asyncio.create_task(
                connect_to_peer(
                    peer_id,
                    peer_host,
                    resolved_peer_port
                )
            )

    elif msg_type == "job_manifest":

        source = data.get("source")

        payload = data.get("payload", {})

        manifest = payload.get("manifest", {})
        rebroadcasted = False

        if not isinstance(manifest, dict) or not manifest:
            job_id = payload.get("job_id")

            if not job_id or not is_valid_job_id(job_id):
                return

            total_chunks = int(
                payload.get("total_chunks", 0) or 0
            )

            chunk_data = validate_chunk_data(
                payload.get("chunk_data", {})
            )

            manifest = build_job_manifest(
                job_id,
                total_chunks,
                chunk_data
            )

            incoming_hash = payload.get("package_hash")

            if (
                incoming_hash
                and job_id not in package_hash_registry
            ):
                package_hash_registry[job_id] = incoming_hash
                manifest["package_hash"] = incoming_hash
                job_manifest_registry[job_id] = manifest

            zip_path = get_job_zip_path(job_id)
            if not os.path.exists(zip_path):
                asyncio.create_task(
                    recover_missing_package(
                        job_id,
                        source
                    )
                )

            asyncio.create_task(
                recover_missing_package(
                    job_id,
                    source
                )
            )

            package_registry.setdefault(
                job_id,
                set()
            ).add(source)

            await broadcast_action(
                "job_manifest",
                manifest=manifest
            )
            rebroadcasted = True

        if not isinstance(manifest, dict):
            return

        if not source and not rebroadcasted:
            await broadcast_action(
                "job_manifest",
                manifest=manifest
            )

        job_id = manifest.get("job_id")

        if not job_id or not is_valid_job_id(job_id):
            return

        package_hash = manifest.get(
            "package_hash"
        )

        if (
            package_hash
            and job_id not in package_hash_registry
        ):
            package_hash_registry[job_id] = package_hash

        total_chunks = int(
            manifest.get("total_chunks", 0) or 0
        )

        chunk_data_raw = manifest.get(
            "chunk_data",
            {}
        )

        chunk_data = validate_chunk_data(
            chunk_data_raw
        )

        state = init_job(
            job_id,
            total_chunks=total_chunks,
            chunk_data_map=chunk_data
        )

        state["status"] = "running"

        state["last_updated"] = time.time()

        increment_version_vector(state)

        job_manifest_registry[job_id] = manifest

        package_registry.setdefault(
            job_id,
            set()
        ).add(source or get_node_id())

        asyncio.create_task(
            recover_missing_package(
                job_id,
                source
            )
        )

        asyncio.create_task(
            replicate_package(
                job_id,
                source
            )
        )

        logger.info(
            f"[Manifest] Registered manifest "
            f"job={job_id}"
        )

    elif msg_type == "cleanup_job":
        payload = data.get("payload", {})
        job_id = payload.get("job_id")
        if job_id and is_valid_job_id(job_id):
            await asyncio.to_thread(cleanup_job_files, job_id)

            job_cache.pop(job_id, None)
            download_locks.pop(job_id, None)
            stale_claims = [
                key for key in owned_claims
                if key[0] == job_id
            ]

            for key in stale_claims:
                owned_claims.pop(key, None)

            save_owned_claims()

            stale_keys = [
                key for key in local_verifications
                if key[0] == job_id
            ]

            for key in stale_keys:
                remove_local_verification(key)

    elif msg_type == "direct_message":
        payload = data.get("payload", {})
        source = data.get("source")
        action = payload.get("action")
        if action == "job_manifest":
            manifest = payload.get("manifest", {})

            if not isinstance(manifest, dict):
                return

            await handle_direct_peer_message({
                "type": "job_manifest",
                "source": source,
                "payload": {
                    "manifest": manifest
                }
            })

            return

        if action == "package_available":
            job_id = payload.get("job_id")
            peer_id = source or payload.get("peer_id")

            if (
                not job_id
                or not is_valid_job_id(job_id)
                or not peer_id
            ):
                return

            incoming_hash = payload.get(
                "package_hash"
            )

            local_hash = package_hash_registry.get(
                job_id
            )

            if (
                incoming_hash
                and local_hash
                and incoming_hash != local_hash
            ):

                logger.error(
                    f"[Security] Package hash conflict "
                    f"job={job_id}"
                )

                return

            if (
                incoming_hash
                and job_id not in package_hash_registry
            ):
                package_hash_registry[job_id] = incoming_hash

            package_registry.setdefault(
                job_id,
                set()
            ).add(peer_id)

        elif action == "manifest_sync_request":
            try:
                logger.info(
                    f"[Manifest] Sync requested by {source}"
                )

                await send_direct_or_relay(
                    source,
                    {
                        "type": "direct_message",
                        "payload": {
                            "target": source,
                            "action": "manifest_sync_response",
                            "manifests": job_manifest_registry
                        }
                    }
                )
            except Exception:
                logger.exception(
                    "[Manifest] Failed to send manifest sync response"
                )

        elif action == "manifest_sync_response":

            manifests = payload.get(
                "manifests",
                {}
            )

            if not isinstance(manifests, dict):
                return

            added = 0

            for job_id, manifest in manifests.items():

                if job_id in job_manifest_registry:
                    continue

                job_manifest_registry[job_id] = manifest
                asyncio.create_task(
                    recover_missing_package(
                        job_id,
                        source
                    )
                )

                package_hash = manifest.get(
                    "package_hash"
                )

                if package_hash:

                    package_hash_registry[
                        job_id
                    ] = package_hash

                added += 1

            logger.info(
                f"[Manifest] Imported "
                f"{added} manifests "
                f"from peer"
            )
            logger.info(
                f"[Manifest] Sync response from {source}"
            )

        elif action == "claim_chunk":
            job_id = payload.get("job_id")
            chunk = str(payload.get("chunk"))
            if job_id is None or chunk is None:
                return
            state = init_job(job_id)
            incoming_claim = {
                "owner": payload.get("owner"),
                "timestamp": payload.get("timestamp", 0),
                "epoch": payload.get("epoch", 0)
            }
            local_claim = state["claims"].get(chunk)
            if chunk in state["completed"]:
                return

            winner = compare_claims(
                local_claim,
                incoming_claim
            )

            if winner is incoming_claim:

                state["claims"][chunk] = incoming_claim

                logger.debug(
                    f"[Claims] Accepted claim "
                    f"chunk={chunk} "
                    f"owner={incoming_claim['owner']}"
                )

            else:

                logger.debug(
                    f"[Claims] Rejected stale claim "
                    f"chunk={chunk}"
                )

            state["last_updated"] = time.time()

        elif action == "complete_chunk":
            job_id = payload.get("job_id")
            chunk = str(payload.get("chunk"))
            if job_id is None or chunk is None:
                return
            state = init_job(job_id)
            if chunk not in state["completed"]:
                state["completed"].add(chunk)
                state["claims"].pop(chunk, None)
                owned_claims.pop((job_id, chunk), None)
                save_owned_claims()
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
                return
            asyncio.create_task(execute_verify_chunk(job_id, chunk, source))

        elif action == "verify_result":
            job_id = payload.get("job_id")
            chunk = str(payload.get("chunk"))
            verification_key = (job_id, str(chunk))
            verification = local_verifications.get(verification_key)

            if not verification:
                return
            if source not in verification.get("verifiers", []):

                logger.warning(
                    f"[VERIFY] Ignoring unauthorized verifier "
                    f"{source} for chunk {chunk}"
                )

                return

            original_result = str(
                verification["original_result"]
            ).strip()
            verify_result = str(
                payload.get("result")
            ).strip()
            verification.setdefault("responses", {})
            if source in verification["responses"]:
                return
            verification["responses"][source] = {
                "status": payload.get("status"),
                "result": verify_result
            }

            matching = 0
            for response in verification["responses"].values():
                if (
                    response.get("status") == "success"
                    and response.get("result") == original_result
                ):
                    matching += 1

            if matching < verification["required_agreement"]:
                total_responses = len(verification["responses"])

                if total_responses >= len(verification["verifiers"]):
                    async with metrics_lock:
                        verify_mismatch_count += 1

                    logger.warning(
                        f"[Consensus] Quorum failed "
                        f"job={job_id} chunk={chunk}"
                    )

                    for verifier_id, response in verification["responses"].items():
                        if (
                            response.get("result") != original_result
                        ):
                            score = get_peer_score(verifier_id)

                            score["mismatches"] = (
                                score.get("mismatches", 0) + 1
                            )

                            current = score.get(
                                "trust",
                                DEFAULT_TRUST_SCORE
                            )

                            score["trust"] = max(
                                TRUST_MIN_SCORE,
                                current - TRUST_PENALTY_MISMATCH
                            )

                    state = job_cache.get(job_id)
                    if state:
                        state["completed"].discard(chunk)
                        state["claims"].pop(chunk, None)
                        owned_claims.pop((job_id, chunk), None)
                        save_owned_claims()
                        increment_version_vector(state)
                        append_delta(
                            state,
                            "chunk_requeue",
                            {
                                "chunk": chunk
                            }
                        )
                        state["last_updated"] = time.time()                                            
                        await broadcast_action(
                            "chunk_requeue",
                            job_id=job_id,
                            chunk=chunk
                        )
                        remove_local_verification(verification_key)

                else:
                    logger.info(
                        f"[VERIFY] Waiting quorum "
                        f"chunk={chunk} "
                        f"matches={matching}"
                    )

                return

            if matching >= verification["required_agreement"]:
                if verification.get("finalized"):
                    return
                verification["finalized"] = True

                for verifier_id, response in verification["responses"].items():
                    if (
                        response.get("status") == "success"
                        and response.get("result") == original_result
                    ):
                        score = get_peer_score(verifier_id)

                        score["success"] = score.get("success", 0) + 1

                        current = score.get(
                            "trust",
                            DEFAULT_TRUST_SCORE
                        )

                        score["trust"] = min(
                            TRUST_MAX_SCORE,
                            current + TRUST_REWARD
                        )

                async with metrics_lock:
                    verify_success_count += 1
                logger.debug(
                    f"[VERIFY] Quorum verified chunk {chunk} "
                    f"for job {job_id} matches={matching}"
                )
                remove_local_verification(verification_key)

                state = job_cache.get(job_id)
                if state and chunk not in state["completed"]:
                    state["completed"].add(chunk)
                    increment_version_vector(state)
                    append_delta(
                        state,
                        "complete_chunk",
                        {
                            "chunk": chunk
                        }
                    )
                    state["claims"].pop(chunk, None)
                    owned_claims.pop((job_id, chunk), None)
                    save_owned_claims()
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

        elif action == "job_complete":
            job_id = payload.get("job_id")

            if job_id:
                state = init_job(job_id)
                state["status"] = "completed"
                state["claims"].clear()
                stale_claims = [
                    key for key in owned_claims
                    if key[0] == job_id
                ]

                for key in stale_claims:
                    owned_claims.pop(key, None)

                save_owned_claims()
                state["last_updated"] = time.time()

        elif action == "job_sync":
            job_id = payload.get("job_id")
            status = payload.get("status")
            if job_id and status:
                local = init_job(job_id)
                merge_job_state(local, status)
                job_cache[job_id] = local

        elif action == "job_delta_sync":

            job_id = payload.get("job_id")
            deltas = payload.get("deltas", [])

            if not job_id:
                return

            if not isinstance(deltas, list):
                return

            state = init_job(job_id)

            for delta in deltas:

                if not isinstance(delta, dict):
                    continue

                delta_id = delta.get("delta_id")

                if not delta_id:
                    continue

                applied = state.setdefault(
                    "applied_deltas",
                    set()
                )

                if delta_id in applied:

                    logger.debug(
                        f"[DeltaSync] Duplicate delta ignored "
                        f"job={job_id} "
                        f"delta={delta_id}"
                    )

                    continue

                applied.add(delta_id)

                if len(applied) > 500:

                    applied_list = list(applied)[-500:]

                    state["applied_deltas"] = set(
                        applied_list
                    )

                operation = delta.get("operation")

                data = delta.get("data", {})
                delta_vector = delta.get(
                    "version_vector",
                    {}
                )

                merged_vector = state.setdefault(
                    "version_vector",
                    {}
                )

                for node_id, counter in delta_vector.items():

                    merged_vector[node_id] = max(
                        merged_vector.get(node_id, 0),
                        counter
                    )

                if operation == "claim_chunk":

                    chunk = str(data.get("chunk"))

                    claim = data.get("claim")

                    if not chunk or not isinstance(claim, dict):
                        continue

                    if chunk in state["completed"]:
                        continue

                    local_claim = state["claims"].get(chunk)

                    winner = compare_claims(
                        local_claim,
                        claim
                    )

                    if winner is claim:

                        state["claims"][chunk] = claim

                elif operation == "complete_chunk":

                    chunk = str(data.get("chunk"))

                    if not chunk:
                        continue

                    state["completed"].add(chunk)

                    state["claims"].pop(chunk, None)

                elif operation == "chunk_requeue":

                    chunk = str(data.get("chunk"))

                    if not chunk:
                        continue

                    state["completed"].discard(chunk)

                    state["claims"].pop(chunk, None)

            state["last_updated"] = time.time()

        elif action == "job_sync_request":

            job_id = payload.get("job_id")

            if not job_id:
                return

            state = job_cache.get(job_id)

            if not state:
                return

            await enqueue_message({
                "type": "direct_message",
                "payload": {
                    "target": source,
                    "action": "job_sync",
                    "job_id": job_id,
                    "status": build_job_state(state)
                }
            })

        elif action == "job_cleanup":
            job_id = payload.get("job_id")
            if job_id:
                await asyncio.to_thread(cleanup_job_files, job_id)
                job_cache.pop(job_id, None)
                download_locks.pop(job_id, None)
                stale_claims = [
                    key for key in owned_claims
                    if key[0] == job_id
                ]
                for key in stale_claims:
                    owned_claims.pop(key, None)
                save_owned_claims()

                stale_keys = [
                    key for key in local_verifications
                    if key[0] == job_id
                ]
                for key in stale_keys:
                    remove_local_verification(key)

        elif action == "peer_exchange":
            if source:
                add_peer(source)
            else:
                return
            peers = payload.get("peers", [])

            if isinstance(peers, list):
                for peer in peers:

                    if not isinstance(peer, dict):
                        continue

                    peer_id = peer.get("peer_id")

                    if not peer_id:
                        continue

                    add_peer(peer_id)

                    update_peer_cache(
                        peer_id,
                        host=peer.get("host"),
                        port=peer.get("port", PEER_PORT),
                        package_port=peer.get("package_port")
                    )

                    peer_host = peer.get("host")
                    peer_port = peer.get("port", PEER_PORT)

                    logger.info(
                        f"[HeartbeatAck] source={source} "
                        f"host={peer_host} "
                        f"port={peer_port}"
                    )

                    if (
                        peer_id not in peer_connections
                        and is_valid_peer_host(peer_host)
                    ):

                        asyncio.create_task(
                            connect_to_peer(
                                peer_id,
                                peer_host,
                                peer_port
                            )
                        )

        elif action == "digest_gossip":

            incoming_digest = payload.get(
                "digest",
                {}
            )

            if not isinstance(incoming_digest, dict):
                return

            local_digest = build_job_digest()

            for job_id, remote_state in incoming_digest.items():

                if not isinstance(remote_state, dict):
                    continue

                local_state = local_digest.get(job_id)

                if not local_state:

                    logger.info(
                        f"[Gossip] Missing job discovered "
                        f"job={job_id}"
                    )

                    await enqueue_message({
                        "type": "direct_message",
                        "payload": {
                            "target": source,
                            "action": "job_sync_request",
                            "job_id": job_id
                        }
                    })
                    continue

                remote_merkle = remote_state.get("merkle")
                local_merkle = local_state.get("merkle")

                if remote_merkle == local_merkle:
                    continue
                else:
                    logger.debug(
                        f"[Merkle] Divergence detected "
                        f"job={job_id}"
                    )

                remote_vector = remote_state.get(
                    "version_vector",
                    {}
                )

                local_vector = local_state.get(
                    "version_vector",
                    {}
                )

                vector_result = compare_version_vectors(
                    remote_vector,
                    local_vector
                )

                if vector_result == "newer":

                    logger.info(
                        f"[Gossip] Remote vector newer "
                        f"job={job_id}"
                    )

                    await enqueue_message({
                        "type": "direct_message",
                        "payload": {
                            "target": source,
                            "action": "job_sync_request",
                            "job_id": job_id
                        }
                    })

                elif vector_result == "older":

                    logger.info(
                        f"[Gossip] Local vector newer "
                        f"job={job_id}"
                    )

                    await enqueue_message({
                        "type": "direct_message",
                        "payload": {
                            "target": source,
                            "action": "job_sync",
                            "job_id": job_id,
                            "status": build_job_state(
                                job_cache[job_id]
                            )
                        }
                    })

                elif vector_result == "concurrent":

                    logger.warning(
                        f"[Gossip] Concurrent divergence "
                        f"job={job_id}"
                    )

                    await enqueue_message({
                        "type": "direct_message",
                        "payload": {
                            "target": source,
                            "action": "job_sync_request",
                            "job_id": job_id
                        }
                    })
                    logger.warning(
                        f"[AntiEntropy] Split-brain divergence "
                        f"detected for job={job_id}"
                    )

        elif action == "chunk_requeue":
            job_id = payload.get("job_id")
            chunk = str(payload.get("chunk"))

            if job_id is None or chunk is None:
                return

            state = init_job(job_id)
            if state.get("status") == "completed":
                return

            state["completed"].discard(chunk)
            state["claims"].pop(chunk, None)
            owned_claims.pop((job_id, chunk), None)
            save_owned_claims()
            state["last_updated"] = time.time()
            remove_local_verification((job_id, chunk))
            
