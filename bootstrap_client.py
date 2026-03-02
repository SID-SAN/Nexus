import requests
from config import BOOTSTRAP_URL, NODE_ID


def register_self(address: str):
    try:
        requests.post(
            f"{BOOTSTRAP_URL}/register",
            json={
                "node_id": NODE_ID,
                "address": address
            },
            timeout=5
        )
    except Exception as e:
        print("Bootstrap registration failed:", e)


def fetch_peers():
    try:
        response = requests.get(
            f"{BOOTSTRAP_URL}/peers",
            timeout=5
        )
        nodes = response.json()

        # Remove self
        return [
            node["address"]
            for node in nodes
            if node["node_id"] != NODE_ID
        ]
    except Exception as e:
        print("Failed to fetch peers:", e)
        return []
    
def send_heartbeat(address: str):
    try:
        requests.post(
            f"{BOOTSTRAP_URL}/heartbeat",
            json={
                "node_id": NODE_ID,
                "address": address
            },
            timeout=5
        )
    except Exception:
        pass