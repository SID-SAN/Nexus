# node.py

from fastapi import FastAPI
from config import NODE_ID
from tasks import compute_range_sum
from logger import setup_logger

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
    total_end = 1_000_000

    # Split into 2 halves
    mid = (total_start + total_end) // 2

    local_chunk = (total_start, mid)
    remote_chunk = (mid, total_end)

    logger.info(f"Local chunk: {local_chunk}")
    logger.info(f"Remote chunk: {remote_chunk}")

    # Execute remote chunk
    peer = PEERS[0]
    remote_response = send_task(
        peer,
        {"start": remote_chunk[0], "end": remote_chunk[1]}
    )

    remote_result = remote_response.get("result", 0)

    # Execute local chunk
    local_result = compute_range_sum(
        local_chunk[0],
        local_chunk[1]
    )

    final_result = local_result + remote_result

    logger.info(f"Local result: {local_result}")
    logger.info(f"Remote result: {remote_result}")
    logger.info(f"Final result: {final_result}")

    return {
        "node": NODE_ID,
        "final_result": final_result
    }