import requests

RELAY_HTTP = "https://nexus-relay-5wog.onrender.com"

def fetch_nodes():
    try:
        r = requests.get(f"{RELAY_HTTP}/nodes", timeout=5)
        return r.json()["nodes"]
    except Exception as e:
        print("Failed to fetch nodes:", e)
        return []