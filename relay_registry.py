import requests

RELAY_URL = "https://nexus-relay-5wog.onrender.com"


def fetch_nodes():
    try:
        r = requests.get(f"{RELAY_URL}/nodes", timeout=5)
        data = r.json()

        return data.get("nodes", [])

    except Exception as e:
        print("Failed to fetch nodes:", e)
        return []