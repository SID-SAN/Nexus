import asyncio
import websockets
import json
import os
from urllib.parse import quote

from node.downloader import download_job
from node.executor import execute_chunk
import aiohttp
from config import RELAY_URL

DEFAULT_RELAY_HTTP_URL = RELAY_URL


def get_node_id():
    return os.getenv("NODE_ID", "node_default")


def get_api_key():
    return os.getenv("API_KEY")


def get_relay_http_url():
    return os.getenv("RELAY_HTTP_URL", DEFAULT_RELAY_HTTP_URL).rstrip("/")


def get_relay_ws_url(node_id, api_key):
    relay_base = get_relay_http_url()
    if relay_base.startswith("https://"):
        relay_base = "wss://" + relay_base[len("https://"):]
    elif relay_base.startswith("http://"):
        relay_base = "ws://" + relay_base[len("http://"):]

    return f"{relay_base}/ws/{quote(node_id)}?api_key={quote(api_key)}"

websocket_connection = None
active_chunks = 0
request_in_flight = False

send_queue = asyncio.Queue()
work_loop_started = False

MAX_CONCURRENT_CHUNKS = 2

# -----------------------------
# 🔥 BACKGROUND EXECUTION
# -----------------------------
async def execute_chunk_batch(job_id, chunks, total_chunks, chunk_data=None):

    try:
        global active_chunks

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{get_relay_http_url()}/job_status/{job_id}") as resp:

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

        # ensure job package exists locally
        if not os.path.exists(f"jobs/{job_id}.zip") and not os.path.exists(f"jobs/{job_id}"):
            download_job(job_id)

        for chunk in chunks:
            active_chunks += 1
            try:
                exec_output = await asyncio.wait_for(
                    asyncio.to_thread(execute_chunk, job_id, chunk, chunk_data or {}),
                    timeout=70
                )
                response = {
                    "type": "submit_result",
                    "source": get_node_id(),
                    "payload": {
                        "job_id": job_id,
                        "chunk": chunk,
                        "status": exec_output["status"],
                        "result": exec_output["result"],
                        "logs": exec_output["logs"],
                        "error": exec_output["error"],
                    }
                }
            except asyncio.TimeoutError:
                response = {
                    "type": "submit_result",
                    "source": get_node_id(),
                    "payload": {
                        "job_id": job_id,
                        "chunk": chunk,
                        "status": "failed",
                        "result": None,
                        "logs": "",
                        "error": "Chunk execution timed out",
                    }
                }
            except Exception as e:
                response = {
                    "type": "submit_result",
                    "source": get_node_id(),
                    "payload": {
                        "job_id": job_id,
                        "chunk": chunk,
                        "status": "failed",
                        "result": None,
                        "logs": "",
                        "error": str(e),
                    }
                }
            finally:
                active_chunks -= 1

            await send_queue.put(response)
            print("[Node] Queued result:", response)
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
            node_id = get_node_id()
            api_key = get_api_key()

            if not api_key:
                print("[Relay] API_KEY is not set. Waiting before reconnect...")
                await asyncio.sleep(2)
                continue

            relay_url = get_relay_ws_url(node_id, api_key)

            async with websockets.connect(
                relay_url,
                ping_interval=20,
                ping_timeout=20
            ) as websocket:

                websocket_connection = websocket
                print(f"[Relay] Connected as {node_id}")
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
                        chunk_data = payload.get("chunk_data", {})

                        print(f"[V4] Assigned chunk {chunk}/{total_chunks}")

                        asyncio.create_task(
                            execute_chunk_batch(job_id, [chunk], total_chunks, chunk_data)
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
                        chunk_data = payload.get("chunk_data", {})

                        print(f"[V4] Batch assigned {len(chunks)} chunks")

                        asyncio.create_task(
                            execute_chunk_batch(job_id, chunks, total_chunks, chunk_data)
                        )


                    # -----------------------------
                    # Heartbeat
                    # -----------------------------
                    elif msg_type == "heartbeat":

                        await send_queue.put({
                            "type": "heartbeat_ack",
                            "source": get_node_id()
                        })

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

    while True:

        if websocket_connection is None:
            await asyncio.sleep(2)
            continue

        global request_in_flight

        if active_chunks < MAX_CONCURRENT_CHUNKS and not request_in_flight:

            request = {
                "type": "request_chunk",
                "source": get_node_id()
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

