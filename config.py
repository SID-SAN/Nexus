import os

BOOTSTRAP_URL = "https://nexus-wr6s.onrender.com"
NODE_ID = os.getenv("NODE_ID", "node_default")
HOST = "127.0.0.1"
PORT = os.getenv("PORT")

PEERS = [
    "http://127.0.0.1:5002",
    "http://127.0.0.1:5003",
]

REQUEST_TIMEOUT = 10