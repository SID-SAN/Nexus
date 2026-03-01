import requests
from config import REQUEST_TIMEOUT

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