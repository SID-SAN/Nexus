import asyncio
import websockets
import json

from config import NODE_ID
from compute import compute_range_sum
from node.downloader import download_job
from node.executor import execute_job
import aiohttp

RELAY_URL = f"wss://nexus-relay-5wog.onrender.com/ws/{NODE_ID}"
RELAY_HTTP_URL = "https://nexus-relay-5wog.onrender.com"

websocket_connection = None
pending_results = {}
job_cache = {}

work_loop_started = False


async def connect_to_relay():

    global websocket_connection
    global work_loop_started

    while True:

        try:

            async with websockets.connect(
                RELAY_URL,
                ping_interval=15,
                ping_timeout=10
            ) as websocket:

                websocket_connection = websocket
                print(f"[Relay] Connected as {NODE_ID}")

                # start worker loop once
                if not work_loop_started:
                    asyncio.create_task(request_work_loop())
                    work_loop_started = True

                while True:

                    message = await websocket.recv()
                    data = json.loads(message)

                    msg_type = data.get("type")

                    # -----------------------------
                    # V4 distributed job execution
                    # -----------------------------
                    if msg_type == "assign_chunk":

                        payload = data["payload"]

                        job_id = payload["job_id"]
                        chunk = payload["chunk"]
                        total_chunks = payload["total_chunks"]

                        print(f"[V4] Assigned chunk {chunk}/{total_chunks} for job {job_id}")

                        try:

                            # 🔥 CHECK IF JOB CANCELLED
                            async with aiohttp.ClientSession() as session:
                                async with session.get(f"{RELAY_HTTP_URL}/job_status_simple/{job_id}") as resp:
                                    status_data = await resp.json()

                                    if status_data["status"] == "cancelled":
                                        print(f"[V4] Skipping chunk {chunk} (job cancelled)")
                                        continue

                            # download job if needed
                            if job_id not in job_cache:
                                job_cache[job_id] = download_job(job_id)

                            job_path = job_cache[job_id]

                            exec_output = execute_job(job_path, chunk, total_chunks)

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

                            await websocket.send(json.dumps(response))

                            print(f"[V4] Submitted result for chunk {chunk}")

                        except Exception as e:

                            response = {
                                "type": "submit_result",
                                "source": NODE_ID,
                                "payload": {
                                    "job_id": job_id,
                                    "chunk": chunk,
                                    "result": None,
                                    "logs": "",
                                    "error": str(e)
                                }
                            }

                            await websocket.send(json.dumps(response))

                            print(f"[V4] Execution crashed: {e}")
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

                        await websocket.send(json.dumps(response))

                    # -----------------------------
                    # Heartbeat
                    # -----------------------------
                    elif msg_type == "heartbeat":

                        await websocket.send(json.dumps({
                            "type": "heartbeat_ack",
                            "source": NODE_ID
                        }))

                    # -----------------------------
                    # Task results
                    # -----------------------------
                    elif msg_type in ("chunk_result", "task_result"):

                        source = data["source"]
                        result = data["payload"]["result"]

                        pending_results[source] = result

        except Exception as e:

            websocket_connection = None
            print(f"[Relay] Connection lost. Reconnecting... {e}")

            await asyncio.sleep(3)


async def request_work_loop():

    while True:

        await asyncio.sleep(2)

        ws = websocket_connection

        if ws is None:
            continue

        request = {
            "type": "request_chunk",
            "source": NODE_ID
        }

        try:
            await ws.send(json.dumps(request))
        except:
            pass