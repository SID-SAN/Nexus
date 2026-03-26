import requests
from config import RELAY_URL

def fetch_nodes():
    try:
        r = requests.get(f"{RELAY_URL}/nodes", timeout=5)
        data = r.json()

        return data.get("nodes", [])

    except Exception as e:
        print("Failed to fetch nodes:", e)
        return []