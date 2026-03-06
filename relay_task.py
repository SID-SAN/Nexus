import json
import asyncio
import relay_client
from config import NODE_ID


async def send_chunk_to_node(target_node, task_name, start, end):

    print(f"[Task] Sending task '{task_name}' chunk {start}-{end} to {target_node}")

    # Wait until relay connection is ready
    while relay_client.websocket_connection is None:
        print("[Task] Waiting for relay connection...")
        await asyncio.sleep(0.2)

    # Prepare message for relay routing
    message = {
        "target": target_node,
        "source": NODE_ID,
        "type": "execute_chunk",
        "payload": {
            "task": task_name,
            "start": start,
            "end": end
        }
    }

    # Send task to worker node
    await relay_client.websocket_connection.send(json.dumps(message))

    print(f"[Task] Task sent to {target_node}, waiting for result...")

    # Wait for worker result
    while target_node not in relay_client.pending_results:
        await asyncio.sleep(0.1)

    result = relay_client.pending_results.pop(target_node)

    print(f"[Task] Result received from {target_node}: {result}")

    return result