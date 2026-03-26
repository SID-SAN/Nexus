import requests
from config import RELAY_URL

def select_best_nodes(peer_ids, max_nodes=3):

    try:
        res = requests.get(f"{RELAY_URL}/resources", timeout=3)
        resources = res.json()

    except Exception as e:
        print("Scheduler failed to fetch resources:", e)
        return peer_ids

    node_scores = []

    for node in peer_ids:

        if node not in resources:
            node_scores.append((node, 1000))
            continue

        cpu = resources[node].get("cpu", 100)
        ram = resources[node].get("ram", 100)

        score = cpu * 0.7 + ram * 0.3

        node_scores.append((node, score))

    node_scores.sort(key=lambda x: x[1])

    best_nodes = [node for node, _ in node_scores[:max_nodes]]

    return best_nodes