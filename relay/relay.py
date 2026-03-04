from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json

app = FastAPI()

# active node connections
connected_nodes = {}

@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):
    await websocket.accept()

    connected_nodes[node_id] = websocket
    print(f"Node connected: {node_id}")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            target = message.get("target")

            # route message to target node
            if target in connected_nodes:
                target_socket = connected_nodes[target]
                await target_socket.send_text(json.dumps(message))

            else:
                print(f"Target {target} not connected")

    except WebSocketDisconnect:
        print(f"Node disconnected: {node_id}")
        connected_nodes.pop(node_id, None)