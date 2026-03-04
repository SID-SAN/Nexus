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


from network import send_task
from config import PEERS
@app.post("/distributed_sum")
def distributed_sum():

    total_start = 1
    total_end = 10  # keep small for testing

    peers = fetch_peers()
    logger.info(f"Discovered peers: {peers}")
    
    # Fetch live peers from bootstrap
    peers = fetch_peers()

    total_nodes = len(peers) + 1
    range_size = total_end - total_start + 1

    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    results = []
    current_start = total_start

    # Assign chunks to peers
    for i, peer in enumerate(peers):
        extra = 1 if i < remainder else 0
        current_end = current_start + chunk_size + extra

        chunk = {"start": current_start, "end": current_end}
        assigned = False

        logger.info(f"Trying peer {peer} for chunk {chunk}")

        response = send_task(peer, chunk)

        if "error" not in response:
            results.append(response.get("result", 0))
            assigned = True
        else:
            logger.warning(f"Peer {peer} failed for chunk {chunk}")

        if not assigned:
            logger.warning(f"Executing failed chunk locally {chunk}")
            local_result = compute_range_sum(chunk["start"], chunk["end"])
            results.append(local_result)

        current_start = current_end

    # Coordinator executes its own chunk (remaining portion)
    logger.info(f"Executing coordinator chunk {current_start} to {total_end}")
    local_result = compute_range_sum(current_start, total_end)
    results.append(local_result)

    final_result = sum(results)

    logger.info(f"Final aggregated result: {final_result}")

    return {
        "node": NODE_ID,
        "final_result": final_result
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