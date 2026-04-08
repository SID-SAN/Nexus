import asyncio
import websockets
import json
import os
import time

from config import NODE_ID
from node.downloader import download_job
from node.executor import execute_job
import aiohttp

API_KEY = os.getenv("API_KEY")

RELAY_URL = f"wss://nexus-relay-5wog.onrender.com/ws/{NODE_ID}?api_key={API_KEY}"
RELAY_HTTP_URL = "https://nexus-relay-5wog.onrender.com"

websocket_connection = None
pending_results = {}
job_cache = {}
active_chunks = 0
request_in_flight = False

send_queue = asyncio.Queue()
work_loop_started = False

MAX_CONCURRENT_CHUNKS = 2
semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)

# -----------------------------
# 🔥 BACKGROUND EXECUTION
# -----------------------------

async def run_single_chunk(job_id, chunk, total_chunks):

    global websocket_connection, active_chunks

    async with semaphore:

        print(f"[V4] Running chunk {chunk}")

        exec_output = await asyncio.to_thread(
            execute_job,
            job_cache[job_id],
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

        await send_queue.put(response)
        print("[Node] Queued result:", response)

        print(f"[V4] Submitted chunk {chunk}")
        active_chunks -= 1


async def execute_chunk_batch(job_id, chunks, total_chunks):

    try:
        global active_chunks
        active_chunks += len(chunks)

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{RELAY_HTTP_URL}/job_status/{job_id}") as resp:

                if resp.status != 200:
                    print(f"[V4] Invalid response: {await resp.text()}")
                    return

                status_data = await resp.json()
                status = status_data.get("status")

                if not status:
                    print(f"[V4] Invalid response: {status_data}")
                    return

                if status == "cancelled":
                    print("[V4] Job cancelled, skipping batch")
                    return

        # download job once
        if job_id not in job_cache:
            if os.path.exists(f"jobs/{job_id}"):
                job_cache[job_id] = f"jobs/{job_id}"
            else:
                job_cache[job_id] = download_job(job_id)

        # 🔥 PARALLEL EXECUTION
        tasks = []

        for chunk in chunks:
            task = asyncio.create_task(
                run_single_chunk(job_id, chunk, total_chunks)
            )
            tasks.append(task)

        await asyncio.gather(*tasks)

    except Exception as e:
        print(f"[V4] Batch execution failed: {e}")

# -----------------------------
# 🔁 SINGLE SENDER LOOP
# -----------------------------
async def sender_loop():

    global websocket_connection

    while True:

        message = await send_queue.get()

        if websocket_connection is None:
            await asyncio.sleep(1)
            await send_queue.put(message)
            continue

        try:
            await websocket_connection.send(json.dumps(message))
            print("[Sender] Sent:", message["type"])
        except Exception as e:
            print(f"[Sender] Retry sending: {e}")
            await asyncio.sleep(1)
            await send_queue.put(message)

# -----------------------------
# 🔌 CONNECT TO RELAY
# -----------------------------
async def connect_to_relay():

    global request_in_flight
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
                connect_to_relay.retry_count = 0

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
                        request_in_flight = False

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
                        request_in_flight = False

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
                print(f"[Relay] Connection lost: {e}")

            websocket_connection = None

            if not hasattr(connect_to_relay, "retry_count"):
                connect_to_relay.retry_count = 0

            connect_to_relay.retry_count += 1
            wait_time = min(10, 2 ** connect_to_relay.retry_count)

            print(f"[Relay] Reconnecting in {wait_time}s...")

            await asyncio.sleep(wait_time)


# -----------------------------
# 📡 WORK REQUEST LOOP
# -----------------------------
async def request_work_loop():

    idle_counter = 0

    while True:

        if websocket_connection is None:
            await asyncio.sleep(2)
            continue

        global request_in_flight

        if active_chunks < MAX_CONCURRENT_CHUNKS and not request_in_flight:

            request = {
                "type": "request_chunk",
                "source": NODE_ID
            }

            try:
                await send_queue.put(request)
                request_in_flight = True

                async def unlock_request():
                    await asyncio.sleep(5)
                    global request_in_flight
                    request_in_flight = False

                asyncio.create_task(unlock_request())

            except:
                pass

        sleep_time = 2 if active_chunks == 0 else 4
        await asyncio.sleep(sleep_time)