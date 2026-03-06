from fastapi import FastAPI
import asyncio

from config import NODE_ID
from logger import setup_logger
from compute import compute_range_sum

from relay_client import connect_to_relay
from relay_task import send_chunk_to_node
from relay_registry import fetch_nodes
from resource_monitor import start_resource_monitor


app = FastAPI()
logger = setup_logger(NODE_ID)


# -----------------------------
# Health endpoint
# -----------------------------
@app.get("/health")
def health():
    logger.info("Health check received")
    return {"status": "ok", "node": NODE_ID}


# -----------------------------
# Execute compute chunk
# -----------------------------
from tasks_registry import get_task


@app.post("/execute_chunk")
def execute_chunk(payload: dict):

    task_name = payload.get("task")
    start = payload.get("start")
    end = payload.get("end")

    logger.info(f"Received task {task_name} : {start} → {end}")

    task_function = get_task(task_name)

    if not task_function:
        return {"error": "unknown task"}

    result = task_function(start, end)

    logger.info(f"Task result: {result}")

    return {
        "node": NODE_ID,
        "result": result
    }

# -----------------------------
# Distributed computation
# -----------------------------
@app.post("/distributed_sum")
async def distributed_sum():

    total_start = 1
    total_end = 10

    # get active nodes from relay
    peers = fetch_nodes()

    peer_ids = [p for p in peers if p != NODE_ID]

    total_nodes = len(peer_ids) + 1
    range_size = total_end - total_start + 1

    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    current = total_start
    results = []

    # send work to peer nodes
    for i, peer in enumerate(peer_ids):

        extra = 1 if i < remainder else 0
        end = current + chunk_size + extra - 1

        logger.info(f"Sending chunk {current}-{end} to {peer}")

        result = await send_chunk_to_node(peer, "sum", current, end)

        results.append(result or 0)

        current = end + 1

    # local computation
    local_result = compute_range_sum(current, total_end)
    results.append(local_result)

    final = sum(results)

    logger.info(f"Final distributed result: {final}")

    return {
        "node": NODE_ID,
        "result": final
    }


# -----------------------------
# Startup event
# -----------------------------
@app.on_event("startup")
async def startup_event():

    logger.info(f"Node starting: {NODE_ID}")

    # connect to relay server
    asyncio.create_task(connect_to_relay())
    asyncio.create_task(start_resource_monitor())