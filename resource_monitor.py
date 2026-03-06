import psutil
import asyncio
import json
import relay_client
from config import NODE_ID


async def start_resource_monitor():

    while True:

        if relay_client.websocket_connection is not None:

            cpu = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory().percent

            message = {
                "type": "resource_update",
                "source": NODE_ID,
                "payload": {
                    "cpu": cpu,
                    "memory": memory
                }
            }

            try:
                await relay_client.websocket_connection.send(json.dumps(message))
                print(f"[Resource] CPU: {cpu}% | RAM: {memory}%")

            except Exception as e:
                print(f"[Resource] Failed to send update: {e}")

        await asyncio.sleep(5)