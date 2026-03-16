import asyncio
import websockets
import json

from config import NODE_ID
from compute import compute_range_sum

# v4 imports
from node.downloader import download_job
from node.executor import execute_job

RELAY_URL = f"wss://nexus-relay-5wog.onrender.com/ws/{NODE_ID}"

websocket_connection = None
pending_results = {}


async def connect_to_relay():
    global websocket_connection

    while True:
        try:
            async with websockets.connect(
                RELAY_URL,
                ping_interval=20,
                ping_timeout=20
            ) as websocket:

                websocket_connection = websocket
                print(f"[Relay] Connected as {NODE_ID}")
                asyncio.create_task(request_work_loop())


                while True:
                    message = await websocket.recv()
                    data = json.loads(message)

                    print("RELAY MESSAGE:", data)

                    msg_type = data.get("type")

                    # --------------------------------
                    # OLD PROTOCOL (V2) execute_chunk
                    # --------------------------------
                    if msg_type == "execute_chunk":

                        payload = data["payload"]
                        start = payload["start"]
                        end = payload["end"]

                        print(f"[Relay] Executing chunk {start}-{end}")

                        result = compute_range_sum(start, end)

                        response = {
                            "target": data["source"],
                            "source": NODE_ID,
                            "type": "chunk_result",
                            "payload": {
                                "result": result
                            }
                        }

                        await websocket.send(json.dumps(response))

                        print(f"[Relay] Sent result {result} back to {data['source']}")

                    # --------------------------------
                    # NEW PROTOCOL (V3) execute_task
                    # --------------------------------
                    elif msg_type == "execute_task":

                        payload = data["payload"]
                        task = payload["task"]
                        start = payload["start"]
                        end = payload["end"]

                        print(f"[Relay] Executing task '{task}' {start}-{end}")

                        from tasks_registry import get_task
                        task_function = get_task(task)

                        if task_function:
                            result = task_function(start, end)
                        else:
                            print(f"[Relay] Unknown task: {task}")
                            result = None

                        response = {
                            "target": data["source"],
                            "source": NODE_ID,
                            "type": "task_result",
                            "payload": {
                                "result": result
                            }
                        }

                        await websocket.send(json.dumps(response))

                        print(f"[Relay] Sent task result {result} back to {data['source']}")

                    # --------------------------------
                    # V4 PROTOCOL assign_chunk
                    # --------------------------------
                    elif msg_type == "assign_chunk":

                        payload = data["payload"]

                        job_id = payload["job_id"]
                        chunk = payload["chunk"]
                        total_chunks = payload["total_chunks"]

                        print(f"[V4] Assigned chunk {chunk}/{total_chunks} for job {job_id}")

                        try:
                            # download job from relay
                            job_cache = {}

                            if job_id not in job_cache:
                                job_cache[job_id] = download_job(job_id)

                            job_path = job_cache[job_id]


                            # execute job
                            result = execute_job(job_path, chunk, total_chunks)

                            print(f"[V4] Job result for chunk {chunk}: {result}")

                            # send result back to relay
                            response = {
                                "type": "submit_result",
                                "source": NODE_ID,
                                "payload": {
                                    "job_id": job_id,
                                    "chunk": chunk,
                                    "result": result
                                }
                            }

                            await websocket.send(json.dumps(response))

                            print(f"[V4] Submitted result for chunk {chunk}")

                        except Exception as e:
                            print(f"[V4] Job execution failed: {e}")

                    # --------------------------------
                    # Receive OLD results
                    # --------------------------------
                    elif msg_type == "chunk_result":

                        source = data["source"]
                        result = data["payload"]["result"]

                        print(f"[Relay] Received result from {source}: {result}")

                        pending_results[source] = result

                    # --------------------------------
                    # Receive NEW results
                    # --------------------------------
                    elif msg_type == "task_result":

                        source = data["source"]
                        result = data["payload"]["result"]

                        print(f"[Relay] Received task result from {source}: {result}")

                        pending_results[source] = result

        except Exception as e:
            print(f"[Relay] Connection lost. Reconnecting... {e}")
            await asyncio.sleep(3)


async def request_work_loop():

    while True:

        await asyncio.sleep(2)

        if websocket_connection is None:
            continue

        request = {
            "type": "request_chunk",
            "source": NODE_ID,
                    }

        try:
            await websocket_connection.send(json.dumps(request))
        except:
            pass
