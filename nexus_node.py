import argparse
import os
import subprocess
import socket


def get_free_port():
    s = socket.socket()
    s.bind(('', 0))  # auto-assign free port
    port = s.getsockname()[1]
    s.close()
    return port

def start_node(node_id, api_key):
    os.environ["NODE_ID"] = node_id
    os.environ["API_KEY"] = api_key

    port = get_free_port()
    os.environ["PORT"] = str(port)
    print(f"[CLI] Starting node {node_id} on port {port}")

    subprocess.run([
        "uvicorn",
        "node_server:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port)
    ])

def main():

    parser = argparse.ArgumentParser(prog="nexus-node")

    parser.add_argument(
        "command",
        choices=["start"]
    )

    parser.add_argument(
        "--node-id",
        default="node_default"
    )

    parser.add_argument(
        "--api-key",
        required=True,
        help="API key to authenticate node"
    )

    args = parser.parse_args()

    if args.command == "start":
        start_node(args.node_id, args.api_key)


if __name__ == "__main__":
    main()