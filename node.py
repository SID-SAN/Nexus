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

app = FastAPI()
logger = setup_logger(NODE_ID)


# -----------------------------
# Health endpoint
# -----------------------------
@app.get("/health")
def health():
    logger.info("Health check received")
    return {"status": "ok", "node": NODE_ID}


@app.get("/cluster_nodes")
def cluster_nodes():
    nodes = fetch_nodes()
    if isinstance(nodes, dict):
        nodes = nodes.get("nodes", [])
    return {"nodes": nodes}


@app.get("/cluster_dashboard")
def cluster_dashboard():

    # get nodes from relay
    nodes = fetch_nodes()

    if isinstance(nodes, dict):
        nodes = nodes.get("nodes", [])

    # get resources from relay
    try:
        resources = requests.get(
            "https://nexus-relay-5wog.onrender.com/resources",
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


@app.get("/tasks")
def available_tasks():

    return {
        "available_tasks": list(TASK_REGISTRY.keys())
    }



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


from tasks_registry import TASK_REGISTRY

@app.post("/submit_job")
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


@app.get("/job_status/{job_id}")
def job_status(job_id: str):

    if job_id not in jobs:
        return {"error": "job not found"}

    return {
        "job_id": job_id,
        "status": jobs[job_id]["status"]
    }


@app.get("/job_result/{job_id}")
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
@app.post("/benchmark")
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