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
    total_end = 1_500_000

    total_nodes = len(PEERS) + 1
    chunk_size = (total_end - total_start) // total_nodes

    results = []
    current_start = total_start

    # Send chunks to peers
    for peer in PEERS:
        current_end = current_start + chunk_size

        logger.info(f"Sending chunk {current_start} to {current_end} to {peer}")

        response = send_task(
            peer,
            {"start": current_start, "end": current_end}
        )

        results.append(response.get("result", 0))
        current_start = current_end

    # Local chunk (remaining part)
    logger.info(f"Executing local chunk {current_start} to {total_end}")

    local_result = compute_range_sum(current_start, total_end)
    results.append(local_result)

    final_result = sum(results)

    logger.info(f"Final aggregated result: {final_result}")

    return {
        "node": NODE_ID,
        "final_result": final_result
    }