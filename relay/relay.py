from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json

app = FastAPI()

connected_nodes = {}
node_resources = {}


@app.get("/")
def root():
    return {"message": "Relay server running"}


@app.get("/health")
def health():
    return {
        "status": "relay_alive",
        "connected_nodes": list(connected_nodes.keys())
    }


@app.get("/nodes")
def get_nodes():
    return {"nodes": list(connected_nodes.keys())}


@app.get("/resources")
def get_resources():
    return node_resources


@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):

    await websocket.accept()

    connected_nodes[node_id] = websocket
    print(f"Node connected: {node_id}")

    try:
        while True:

            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            # handle resource updates
            if msg_type == "resource_update":
                node_resources[node_id] = message["payload"]
                continue

            # route task messages
            target = message.get("target")

            if target in connected_nodes:

                target_socket = connected_nodes[target]

                await target_socket.send_text(json.dumps(message))

                print(f"Routed message {msg_type} from {node_id} → {target}")

            else:

                print(f"Target {target} not connected")

    except WebSocketDisconnect:

        print(f"Node disconnected: {node_id}")

        connected_nodes.pop(node_id, None)
        node_resources.pop(node_id, None)