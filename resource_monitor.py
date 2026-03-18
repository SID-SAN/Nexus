import asyncio
import json
import psutil
import relay_client
from config import NODE_ID


async def resource_monitor_loop():
    """
    Continuously monitor CPU and RAM usage and send updates
    to the relay server so the scheduler can make decisions.
    """

    while True:

        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            ram_usage = psutil.virtual_memory().percent

            print(f"[Resource] CPU: {cpu_usage}% | RAM: {ram_usage}%")

            ws = relay_client.websocket_connection

            if ws is None:
                print("[Resource] No active connection, waiting...")
                await asyncio.sleep(5)
                continue

            message = {
                "type": "resource_update",
                "source": NODE_ID,
                "payload": {
                    "cpu": cpu_usage,
                    "ram": ram_usage
                }
            }

            try:
                await ws.send(json.dumps(message))
                print("[Resource] Sent update to relay")

            except Exception as e:
                # connection probably dropped
                print(f"[Resource] Failed to send update: {e}")
                relay_client.websocket_connection = None

        except Exception as e:
            print(f"[Resource] Monitor error: {e}")

        await asyncio.sleep(5)