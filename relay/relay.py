from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json

app = FastAPI()
node_resources = {}

# active node connections
connected_nodes = {}

@app.get("/health")
def health():
    return {
        "status": "relay_alive",
        "connected_nodes": list(connected_nodes.keys())
    }

@app.get("/")
def root():
    return {"message": "Relay server running"}


# NEW: node registry endpoint
@app.get("/nodes")
def get_nodes():
    return {"nodes": list(connected_nodes.keys())}


@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):

    await websocket.accept()

    connected_nodes[node_id] = websocket
    print(f"Node connected: {node_id}")

    try:
        while True:

            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") == "resource_update":
                node_resources[node_id] = message["payload"]
                return

            target = message.get("target")

            if target in connected_nodes:
                target_socket = connected_nodes[target]
                await target_socket.send_text(json.dumps(message))
            else:
                print(f"Target {target} not connected")

    except WebSocketDisconnect:

        print(f"Node disconnected: {node_id}")
        connected_nodes.pop(node_id, None)

@app.get("/resources")
def get_resources():
    return node_resources