import os

# Unique node id
NODE_ID = os.getenv("NODE_ID", "node_default")

# Node server settings
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 5001))

# Bootstrap server (local or deployed)
BOOTSTRAP_URL = os.getenv(
    "BOOTSTRAP_URL",
    "http://127.0.0.1:8000"
)

RELAY_URL = "https://nexus-relay-5wog.onrender.com"


# Request timeout
REQUEST_TIMEOUT = 5

