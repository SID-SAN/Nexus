import asyncio
import json
import os
import psutil
import relay_client
from logger import setup_logger


def get_node_id():
    return os.getenv("NODE_ID", "node_default")

logger = setup_logger("resource-monitor")


async def resource_monitor_loop():
    """
    Continuously monitor CPU and RAM usage and send updates
    to the relay server so the scheduler can make decisions.
    """
    psutil.cpu_percent(interval=None)
    node_id = get_node_id()

    while True:
        try:
            cpu_usage = psutil.cpu_percent(interval=None)
            ram_usage = psutil.virtual_memory().percent

            logger.info(f"[Resource] CPU: {cpu_usage}% | RAM: {ram_usage}%")

            ws = getattr(relay_client, "websocket_connection", None)

            if ws is None:
                logger.warning("[Resource] No active connection, waiting...")
                await asyncio.sleep(5)
                continue

            message = {
                "type": "resource_update",
                "source": node_id,
                "payload": {
                    "cpu": cpu_usage,
                    "ram": ram_usage
                }
            }

            try:
                await ws.send(json.dumps(message))
                logger.debug("[Resource] Sent update to relay")

            except Exception as e:
                # connection probably dropped
                logger.warning(f"[Resource] Failed to send update: {e}")
                setattr(relay_client, "websocket_connection", None)

        except Exception:
            logger.exception("[Resource] Monitor error")

        await asyncio.sleep(5)
