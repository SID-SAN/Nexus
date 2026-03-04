import json
import asyncio
import relay_client
from config import NODE_ID


async def send_chunk_to_node(target_node, start, end):

    print(f"[Task] Sending chunk {start}-{end} to {target_node}")

    while relay_client.websocket_connection is None:
        print("[Task] Waiting for relay connection...")
        await asyncio.sleep(0.2)

    message = {
        "target": target_node,
        "type": "execute_chunk",
        "source": NODE_ID,
        "payload": {
            "start": start,
            "end": end
        }
    }

    await relay_client.websocket_connection.send(json.dumps(message))

    print("[Task] Waiting for result...")

    while target_node not in relay_client.pending_results:
        await asyncio.sleep(0.1)

    result = relay_client.pending_results.pop(target_node)

    return result