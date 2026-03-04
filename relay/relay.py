from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

# store active node connections
connected_nodes = {}

@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):
    await websocket.accept()

    connected_nodes[node_id] = websocket
    print(f"Node connected: {node_id}")

    try:
        while True:
            data = await websocket.receive_text()
            print(f"Message from {node_id}: {data}")

            # simple echo for now
            await websocket.send_text(f"Relay received: {data}")

    except WebSocketDisconnect:
        print(f"Node disconnected: {node_id}")
        connected_nodes.pop(node_id, None)