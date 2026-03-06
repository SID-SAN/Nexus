import requests
from config import RELAY_URL


def get_cluster_resources():

    try:
        response = requests.get(f"{RELAY_URL}/resources")
        return response.json()

    except Exception as e:
        print("Failed to fetch resources:", e)
        return {}


def select_best_nodes(peers):

    resources = get_cluster_resources()

    scored_nodes = []

    for node in peers:

        if node in resources:

            cpu = resources[node].get("cpu", 100)
            memory = resources[node].get("ram", 100)

            score = cpu + memory
            scored_nodes.append((node, score))

        else:
            scored_nodes.append((node, 200))

    scored_nodes.sort(key=lambda x: x[1])

    return [node for node, score in scored_nodes]