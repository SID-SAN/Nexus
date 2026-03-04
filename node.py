# node.py
from bootstrap_client import register_self, fetch_peers
from config import HOST, PORT
from fastapi import FastAPI
from config import NODE_ID
from tasks import compute_range_sum
from logger import setup_logger
import threading
import time
from bootstrap_client import send_heartbeat
from relay_client import connect_to_relay
import asyncio
from network import register_self, send_heartbeat


app = FastAPI()
logger = setup_logger(NODE_ID)

@app.get("/health")
def health():
    logger.info("Health check received")
    return {"status": "ok", "node": NODE_ID}

@app.post("/execute_chunk")
def execute_chunk(payload: dict):
    start = payload.get("start")
    end = payload.get("end")

    logger.info(f"Executing chunk: {start} to {end}")

    result = compute_range_sum(start, end)

    logger.info(f"Chunk result: {result}")

    return {
        "node": NODE_ID,
        "result": result
    }


from relay_task import send_chunk_to_node
import asyncio
from compute import compute_range_sum


@app.post("/distributed_sum")
async def distributed_sum():

    total_start = 1
    total_end = 10

    peers = fetch_peers()

    peer_ids = []

    for p in peers:
        if isinstance(p, dict):
            node_id = p.get("node_id")
            if node_id and node_id != NODE_ID:
                peer_ids.append(node_id)
    total_nodes = len(peer_ids) + 1
    range_size = total_end - total_start + 1

    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    current = total_start
    results = []

    # send chunks to peers
    for i, peer in enumerate(peer_ids):

        extra = 1 if i < remainder else 0
        end = current + chunk_size + extra - 1

        result = await send_chunk_to_node(peer, current, end)
        results.append(result or 0)

        current = end + 1

    # local chunk
    local_result = compute_range_sum(current, total_end)
    results.append(local_result)

    final = sum(results)

    return {
        "node": NODE_ID,
        "result": final
    }


@app.on_event("startup")
async def startup_event():
    address = f"http://{HOST}:{PORT}"

    # Register node with bootstrap server
    register_self(address)
    logger.info(f"Registered {NODE_ID} at {address}")

    # Start heartbeat loop in background thread
    def heartbeat_loop():
        while True:
            send_heartbeat(address)
            time.sleep(5)

    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()

    # Connect node to relay server (async)
    asyncio.create_task(connect_to_relay())