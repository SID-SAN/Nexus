import os

BOOTSTRAP_URL = "http://127.0.0.1:8000"
NODE_ID = os.getenv("NODE_ID", "node_default")
HOST = "127.0.0.1"
PORT = 5001

PEERS = [
    "http://127.0.0.1:5002",
    "http://127.0.0.1:5003",
]

REQUEST_TIMEOUT = 10