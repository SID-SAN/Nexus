import os

# Unique node id
NODE_ID = os.getenv("NODE_ID", "node_default")

# Node server settings
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 5001))

RELAY_URLS = ["https://nexus-relay-5wog.onrender.com"]

# Request timeout
REQUEST_TIMEOUT = 5

