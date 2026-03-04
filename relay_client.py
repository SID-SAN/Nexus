import asyncio
import websockets
from config import NODE_ID

RELAY_URL = f"ws://127.0.0.1:9000/ws/{NODE_ID}"

async def connect_to_relay():
    while True:
        try:
            async with websockets.connect(RELAY_URL) as websocket:
                print(f"[Relay] Connected as {NODE_ID}")

                while True:
                    message = await websocket.recv()
                    print(f"[Relay] Received: {message}")

        except Exception as e:
            print(f"[Relay] Connection lost, retrying... {e}")
            await asyncio.sleep(3)