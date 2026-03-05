import requests

RELAY_HTTP = "https://nexus-relay-5wog.onrender.com"

def fetch_nodes():
    try:
        r = requests.get(f"{RELAY_HTTP}/nodes", timeout=5)
        data = r.json()

        if "nodes" in data:
            return data["nodes"]

        return []

    except Exception as e:
        print("[REGISTRY] Failed to fetch nodes:", e)
        return []