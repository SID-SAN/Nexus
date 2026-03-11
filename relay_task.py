import json
import asyncio
import relay_client
from config import NODE_ID


async def send_task_to_node(target_node, task, start, end):

    print(f"[Task] Sending task '{task}' chunk {start}-{end} to {target_node}")

    # wait until websocket connected
    while relay_client.websocket_connection is None:
        print("[Task] Waiting for relay connection...")
        await asyncio.sleep(0.2)

    message = {
        "target": target_node,
        "type": "execute_task",
        "source": NODE_ID,
        "payload": {
            "task": task,
            "start": start,
            "end": end
        }
    }

    ws = relay_client.websocket_connection

    if ws is None:
        return None

    await ws.send(json.dumps(message))
    print(f"[Task] Task sent to {target_node}, waiting for result...")

    timeout = 20
    waited = 0

    while waited < timeout:

        if target_node in relay_client.pending_results:
            result = relay_client.pending_results.pop(target_node)
            print(f"[Task] Result received from {target_node}: {result}")
            return result

        await asyncio.sleep(0.1)
        waited += 0.1

    print(f"[Task] Timeout waiting for result from {target_node}")
    raise TimeoutError(f"Node {target_node} did not respond")