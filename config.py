import os
import socket

def get_free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

# Unique node id
NODE_ID = os.getenv("NODE_ID", "node_default")

# Node server settings
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 5001))

RELAY_URLS = ["https://nexus-relay-5wog.onrender.com"]

PEER_PORT = int(
    os.getenv("PEER_PORT", get_free_port())
)

PACKAGE_SERVER_PORT = int(
    os.getenv("PACKAGE_SERVER_PORT", get_free_port())
)

REQUEST_TIMEOUT = 5

