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
    total_end = 10  # use small number for testing

    total_nodes = len(PEERS) + 1
    range_size = total_end - total_start
    chunk_size = range_size // total_nodes
    remainder = range_size % total_nodes

    results = []
    current_start = total_start

    all_nodes = PEERS.copy()

    # Process peer chunks
    for i in range(len(PEERS)):
        extra = 1 if i < remainder else 0
        current_end = current_start + chunk_size + extra

        chunk = {"start": current_start, "end": current_end}

        assigned = False

        for peer in all_nodes:
            logger.info(f"Trying peer {peer} for chunk {chunk}")

            response = send_task(peer, chunk)

            if "error" not in response:
                results.append(response.get("result", 0))
                assigned = True
                break
            else:
                logger.warning(f"Peer {peer} failed for chunk {chunk}")

        if not assigned:
            logger.warning(f"All peers failed. Executing locally {chunk}")
            local_result = compute_range_sum(chunk["start"], chunk["end"])
            results.append(local_result)

        current_start = current_end

    # Local coordinator chunk (remaining portion)
    logger.info(f"Executing coordinator chunk {current_start} to {total_end}")
    local_result = compute_range_sum(current_start, total_end)
    results.append(local_result)

    final_result = sum(results)

    logger.info(f"Final aggregated result: {final_result}")

    return {
        "node": NODE_ID,
        "final_result": final_result
    }