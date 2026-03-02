from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict
from datetime import datetime, timedelta

app = FastAPI()

active_nodes: Dict[str, dict] = {}

HEARTBEAT_TIMEOUT = 15  # seconds


class RegisterRequest(BaseModel):
    node_id: str
    address: str


@app.post("/register")
def register_node(request: RegisterRequest):
    active_nodes[request.node_id] = {
        "address": request.address,
        "last_seen": datetime.utcnow()
    }
    return {"status": "registered"}


@app.post("/heartbeat")
def heartbeat(request: RegisterRequest):
    if request.node_id in active_nodes:
        active_nodes[request.node_id]["last_seen"] = datetime.utcnow()
    return {"status": "alive"}


@app.get("/peers")
def get_peers():
    now = datetime.utcnow()

    # Remove dead nodes
    dead_nodes = [
        node_id for node_id, data in active_nodes.items()
        if now - data["last_seen"] > timedelta(seconds=HEARTBEAT_TIMEOUT)
    ]

    for node_id in dead_nodes:
        del active_nodes[node_id]

    return [
        {"node_id": node_id, "address": data["address"]}
        for node_id, data in active_nodes.items()
    ]