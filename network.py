import requests
from config import REQUEST_TIMEOUT, BOOTSTRAP_URL, NODE_ID


# ----------------------------
# Send distributed task
# ----------------------------
def send_task(peer_url: str, payload: dict):
    try:
        response = requests.post(
            f"{peer_url}/execute_chunk",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "error": str(e),
            "result": 0
        }


# ----------------------------
# Register node to bootstrap
# ----------------------------
def register_self(address: str):
    try:
        requests.post(
            f"{BOOTSTRAP_URL}/register",
            json={
                "node_id": NODE_ID,
                "address": address
            },
            timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException:
        pass


# ----------------------------
# Send heartbeat to bootstrap
# ----------------------------
def send_heartbeat(address: str):
    try:
        requests.post(
            f"{BOOTSTRAP_URL}/heartbeat",
            json={
                "node_id": NODE_ID,
                "address": address
            },
            timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException:
        pass