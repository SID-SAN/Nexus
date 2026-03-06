import asyncio
import websockets
import json

from config import NODE_ID
from compute import compute_range_sum

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

                        if task == "sum":
                            result = compute_range_sum(start, end)
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