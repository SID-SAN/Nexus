from fastapi import FastAPI
import asyncio
import platform
import requests
from tasks_registry import TASK_REGISTRY
from config import NODE_ID
from config import PORT
from logger import setup_logger

from relay_client import connect_to_relay
from relay_task import send_task_to_node
from relay_registry import fetch_nodes
from resource_monitor import resource_monitor_loop
from scheduler import select_best_nodes
import uuid

jobs = {}
chunk_tasks = {}
chunk_assignments = []

app = FastAPI()
logger = setup_logger(NODE_ID)


# -----------------------------
# Health endpoint
# -----------------------------
@app.get("/health", tags=["System"])
def health():
    logger.info("Health check received")
    return {"status": "ok", "node": NODE_ID}


@app.get("/cluster_nodes", tags=["Cluster"])
def cluster_nodes():
    nodes = fetch_nodes()
    if isinstance(nodes, dict):
        nodes = nodes.get("nodes", [])
    return {"nodes": nodes}


@app.get("/cluster_dashboard", tags=["Cluster"])
def cluster_dashboard():

    # get nodes from relay
    nodes = fetch_nodes()

    if isinstance(nodes, dict):
        nodes = nodes.get("nodes", [])

    # get resources from relay
    try:
        resources = requests.get(
            "https://nexus-wr6s.onrender.com/resources",
            timeout=3
        ).json()
    except:
        resources = {}

    # job summary
    active_jobs = []

    for jid in jobs:
        active_jobs.append({
            "job_id": jid,
            "status": jobs[jid]["status"]
        })

    return {
        "cluster_nodes": nodes,
        "node_resources": resources,
        "available_tasks": list(TASK_REGISTRY.keys()),
        "active_jobs": active_jobs
    }


@app.get("/tasks", tags=["Tasks"])
def available_tasks():

    return {
        "available_tasks": list(TASK_REGISTRY.keys())
    }



# -----------------------------
# Execute compute chunk
# -----------------------------
from tasks_registry import get_task


@app.post("/execute_chunk", tags=["Compute"])
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


async def execute_with_retry(node, task, start, end, available_nodes):

    attempts = 0
    max_attempts = 3

    # create chunk id for tracking
    chunk_id = str(uuid.uuid4())

    chunk_tasks[chunk_id] = {
        "node": node,
        "start": start,
        "end": end,
        "status": "running",
        "result": None
    }

    while attempts < max_attempts:

        try:
            logger.info(f"[Chunk {chunk_id}] Sending {start}-{end} → {node}")

            result = await send_task_to_node(node, task, start, end)

            if result is not None:

                chunk_tasks[chunk_id]["status"] = "completed"
                chunk_tasks[chunk_id]["result"] = result

                logger.info(f"[Chunk {chunk_id}] Completed on {node}")

                return result

        except Exception as e:

            logger.warning(f"[Chunk {chunk_id}] Node {node} failed: {e}")

        attempts += 1

        # mark retry
        chunk_tasks[chunk_id]["status"] = "retrying"

        # remove failed node
        available_nodes = [n for n in available_nodes if n != node]

        # refresh cluster state
        alive_nodes = fetch_nodes()

        # keep only alive nodes
        available_nodes = [n for n in available_nodes if n in alive_nodes]

        if not available_nodes:

            chunk_tasks[chunk_id]["status"] = "failed"

            raise Exception(
                f"[Chunk {chunk_id}] No nodes available for retry of {start}-{end}"
            )

        # choose next node
        node = available_nodes[0]

        chunk_tasks[chunk_id]["node"] = node

        logger.warning(
            f"[Chunk {chunk_id}] Reassigning {start}-{end} → {node}"
        )

    chunk_tasks[chunk_id]["status"] = "failed"

    raise Exception(
        f"[Chunk {chunk_id}] Failed after {max_attempts} attempts ({start}-{end})"
    )


@app.post("/distributed_task", tags=["Compute"])
async def distributed_task(req: TaskRequest):

    chunk_assignments.clear()

    task_name = req.task
    total_start = req.start
    total_end = req.end

    peers = fetch_nodes()
    peer_ids = [p for p in peers if p != NODE_ID]
    peer_ids = select_best_nodes(peer_ids)

    all_nodes = peer_ids + [NODE_ID]

    total_nodes = len(all_nodes)
    range_size = total_end - total_start + 1

    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    current = total_start

    results = []
    tasks = []
    task_chunk_map = []

    for i, node in enumerate(all_nodes):

        extra = 1 if i < remainder else 0
        end = current + chunk_size + extra - 1

        logger.info(f"Assigning chunk {current}-{end} → {node}")

        chunk_id = str(uuid.uuid4())

        chunk_tasks[chunk_id] = {
            "node": node,
            "start": current,
            "end": end,
            "status": "running",
            "result": None
        }

        chunk_assignments.append((chunk_id, node, current, end))

        if node == NODE_ID:
            # local compute
            task_func = get_task(task_name)
            local_result = task_func(current, end)

            chunk_tasks[chunk_id]["status"] = "completed"
            chunk_tasks[chunk_id]["result"] = local_result

            results.append(local_result)

        else:
            task = execute_with_retry(node, task_name, current, end, all_nodes)

            tasks.append(task)
            task_chunk_map.append(chunk_id)

        current = end + 1

    logger.info(f"Chunk assignments: {chunk_assignments}")

    if tasks:
        peer_results = await asyncio.gather(*tasks, return_exceptions=True)

        for chunk_id, r in zip(task_chunk_map, peer_results):

            if isinstance(r, Exception):
                logger.error(f"Chunk {chunk_id} failed: {r}")
                raise r

            if r is None:
                raise Exception("Distributed task failed due to missing chunk")

            chunk_tasks[chunk_id]["status"] = "completed"
            chunk_tasks[chunk_id]["result"] = r

            results.append(r)

    final = sum(results)

    logger.info(f"Final distributed result: {final}")

    return {
        "task": task_name,
        "result": final
    }



@app.post("/submit_job", tags=["Jobs"])
async def submit_job(req: TaskRequest):

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "status": "running",
        "result": None
    }

    async def run_job():

        result = await distributed_task(req)

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result

    asyncio.create_task(run_job())

    return {
        "job_id": job_id,
        "status": "submitted"
    }


@app.get("/job_status/{job_id}", tags=["Jobs"])
def job_status(job_id: str):

    if job_id not in jobs:
        return {"error": "job not found"}

    return {
        "job_id": job_id,
        "status": jobs[job_id]["status"]
    }


@app.get("/job_result/{job_id}", tags=["Jobs"])
def job_result(job_id: str):

    if job_id not in jobs:
        return {"error": "job not found"}

    if jobs[job_id]["status"] != "completed":
        return {"status": "still running"}

    return {
        "job_id": job_id,
        "result": jobs[job_id]["result"]
    }



# -----------------------------
# Startup event
# -----------------------------
@app.on_event("startup")
async def startup_event():
    show_startup_banner()

    logger.info(f"Node starting: {NODE_ID}")

    # connect to relay server
    asyncio.create_task(connect_to_relay())
    asyncio.create_task(resource_monitor_loop())

import time
from pydantic import BaseModel
@app.post("/benchmark", tags=["Testing"])
async def benchmark():
    class BenchmarkRequest(BaseModel):
        task: str
        start: int
        end: int

    req = BenchmarkRequest(
        task="sum",
        start=1,
        end=5000000000
    )

    start_time = time.time()

    result = await distributed_task(req)

    end_time = time.time()

    return {
        "node": NODE_ID,
        "task": "sum",
        "result": result["result"],
        "execution_time_seconds": end_time - start_time
    }



def show_startup_banner():
    print("\n")
    print("=====================================")
    print("           NEXUS NODE STARTING       ")
    print("=====================================")
    print(f"Node ID      : {NODE_ID}")
    print(f"Port         : {PORT}")
    print(f"Platform     : {platform.system()} {platform.release()}")
    print("Scheduler    : Enabled")
    print("Resource Mon : Enabled")
    print("=====================================")
    print("\n")
