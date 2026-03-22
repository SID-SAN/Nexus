import asyncio
import websockets
import json
import os
import time

from config import NODE_ID
from node.downloader import download_job
from node.executor import execute_job
import aiohttp

RELAY_URL = f"wss://nexus-relay-5wog.onrender.com/ws/{NODE_ID}"
RELAY_HTTP_URL = "https://nexus-relay-5wog.onrender.com"

websocket_connection = None
pending_results = {}
job_cache = {}

send_queue = asyncio.Queue()
work_loop_started = False

# -----------------------------
# 🔥 BACKGROUND EXECUTION
# -----------------------------
async def execute_chunk_batch(job_id, chunks, total_chunks):

    global websocket_connection

    try:
        # check cancellation
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{RELAY_HTTP_URL}/job_status_simple/{job_id}") as resp:
                status_data = await resp.json()

                if status_data["status"] == "cancelled":
                    print("[V4] Job cancelled, skipping batch")
                    return

        # download job once
        if job_id not in job_cache:
            if os.path.exists(f"jobs/{job_id}"):
                job_cache[job_id] = f"jobs/{job_id}"
            else:
                job_cache[job_id] = download_job(job_id)

        job_path = job_cache[job_id]

        for chunk in chunks:

            # run blocking docker safely
            exec_output = await asyncio.to_thread(
                execute_job,
                job_path,
                chunk,
                total_chunks
            )

            response = {
                "type": "submit_result",
                "source": NODE_ID,
                "payload": {
                    "job_id": job_id,
                    "chunk": chunk,
                    "result": exec_output.get("result"),
                    "logs": exec_output.get("logs", ""),
                    "error": exec_output.get("error", "")
                }
            }

            # only send if connection alive
            if websocket_connection:
                await send_queue.put(response)
            else:
                print("[V4] Skipping send (no connection)")

            print(f"[V4] Submitted chunk {chunk}")

    except Exception as e:
        print(f"[V4] Batch execution failed: {e}")


# -----------------------------
# 🔁 SINGLE SENDER LOOP
# -----------------------------
async def sender_loop():

    global websocket_connection

    while True:

        message = await send_queue.get()

        ws = websocket_connection  # snapshot

        if ws is None:
            continue

        try:
            await ws.send(json.dumps(message))
        except Exception as e:
            print(f"[Sender] Dropping message (connection issue): {e}")


# -----------------------------
# 🔌 CONNECT TO RELAY
# -----------------------------
async def connect_to_relay():

    global websocket_connection
    global work_loop_started

    while True:

        try:
            async with websockets.connect(
                RELAY_URL,
                ping_interval=20,
                ping_timeout=20
            ) as websocket:

                websocket_connection = websocket
                print(f"[Relay] Connected as {NODE_ID}")

                if not work_loop_started:
                    asyncio.create_task(request_work_loop())
                    asyncio.create_task(sender_loop())
                    work_loop_started = True

                while True:

                    message = await websocket.recv()
                    data = json.loads(message)

                    msg_type = data.get("type")

                    # -----------------------------
                    # SINGLE CHUNK
                    # -----------------------------
                    if msg_type == "assign_chunk":

                        payload = data["payload"]
                        job_id = payload["job_id"]
                        chunk = payload["chunk"]
                        total_chunks = payload["total_chunks"]

                        print(f"[V4] Assigned chunk {chunk}/{total_chunks}")

                        asyncio.create_task(
                            execute_chunk_batch(job_id, [chunk], total_chunks)
                        )

                    # -----------------------------
                    # BATCH CHUNKS
                    # -----------------------------
                    elif msg_type == "assign_chunk_batch":

                        payload = data["payload"]
                        job_id = payload["job_id"]
                        chunks = payload["chunks"]
                        total_chunks = payload["total_chunks"]

                        print(f"[V4] Batch assigned {len(chunks)} chunks")

                        asyncio.create_task(
                            execute_chunk_batch(job_id, chunks, total_chunks)
                        )

                    # -----------------------------
                    # V3 compatibility
                    # -----------------------------
                    elif msg_type == "execute_task":

                        payload = data["payload"]
                        task = payload["task"]
                        start = payload["start"]
                        end = payload["end"]

                        from tasks_registry import get_task
                        task_function = get_task(task)

                        result = task_function(start, end) if task_function else None

                        response = {
                            "target": data["source"],
                            "source": NODE_ID,
                            "type": "task_result",
                            "payload": {"result": result}
                        }

                        await send_queue.put(response)

                    # -----------------------------
                    # Heartbeat
                    # -----------------------------
                    elif msg_type == "heartbeat":

                        await send_queue.put({
                            "type": "heartbeat_ack",
                            "source": NODE_ID
                        })

                    # -----------------------------
                    # Task results (v3)
                    # -----------------------------
                    elif msg_type in ("chunk_result", "task_result"):

                        source = data["source"]
                        result = data["payload"]["result"]

                        pending_results[source] = result

        except Exception as e:

            if websocket_connection is not None:
                print(f"[Relay] Connection lost. Reconnecting... {e}")

            websocket_connection = None
            await asyncio.sleep(3)


# -----------------------------
# 📡 WORK REQUEST LOOP
# -----------------------------
async def request_work_loop():

    idle_counter = 0

    while True:

        if websocket_connection is None:
            await asyncio.sleep(2)
            continue

        request = {
            "type": "request_chunk",
            "source": NODE_ID
        }

        try:
            await send_queue.put(request)
        except:
            pass

        # adaptive backoff
        if idle_counter < 5:
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(5)

        idle_counter += 1