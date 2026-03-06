from fastapi import FastAPI
import asyncio

from config import NODE_ID
from logger import setup_logger
from compute import compute_range_sum

from relay_client import connect_to_relay
from relay_task import send_task_to_node
from relay_registry import fetch_nodes
from resource_monitor import resource_monitor_loop
from scheduler import select_best_nodes

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
from pydantic import BaseModel

class TaskRequest(BaseModel):
    task: str
    start: int
    end: int


@app.post("/distributed_task")
async def distributed_task(req: TaskRequest):

    task_name = req.task
    total_start = req.start
    total_end = req.end

    peers = fetch_nodes()
    peer_ids = [p for p in peers if p != NODE_ID]
    peer_ids = select_best_nodes(peer_ids)

    total_nodes = len(peer_ids) + 1
    range_size = total_end - total_start + 1

    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    current = total_start
    results = []

    tasks = []

    for i, peer in enumerate(peer_ids):

        extra = 1 if i < remainder else 0
        end = current + chunk_size + extra - 1

        logger.info(f"Sending task '{task_name}' chunk {current}-{end} to {peer}")

        tasks.append(send_task_to_node(peer, task_name, current, end))

        current = end + 1

    peer_results = await asyncio.gather(*tasks)

    results.extend([r or 0 for r in peer_results])

    # local compute
    from tasks_registry import get_task
    task_func = get_task(task_name)

    local_result = task_func(current, total_end)

    results.append(local_result)

    final = sum(results)

    logger.info(f"Final distributed result: {final}")

    return {
        "task": task_name,
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
    asyncio.create_task(resource_monitor_loop())