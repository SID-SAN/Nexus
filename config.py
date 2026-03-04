import os

NODE_ID = os.getenv("NODE_ID", "node_default")

HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", 5001))

BOOTSTRAP_URL = "http://127.0.0.1:8000"

REQUEST_TIMEOUT = 5

PEERS = [
    "http://127.0.0.1:5002",
    "http://127.0.0.1:5003",
]