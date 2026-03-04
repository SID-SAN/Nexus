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
            async with websockets.connect(RELAY_URL) as websocket:

                websocket_connection = websocket
                print(f"[Relay] Connected as {NODE_ID}")

                while True:
                    message = await websocket.recv()
                    data = json.loads(message)

                    print("RELAY MESSAGE:", data)

                    msg_type = data.get("type")

                    # --------------------------------
                    # Execute compute chunk
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
                    # Receive compute result
                    # --------------------------------
                    elif msg_type == "chunk_result":

                        source = data["source"]
                        result = data["payload"]["result"]

                        print(f"[Relay] Received result from {source}: {result}")

                        pending_results[source] = result

        except Exception as e:
            print(f"[Relay] Connection lost. Reconnecting... {e}")
            await asyncio.sleep(3)