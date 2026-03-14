import argparse
import os
import subprocess


def start_node(node_id, port):

    os.environ["NODE_ID"] = node_id

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
        "--port",
        default=5001
    )

    args = parser.parse_args()

    if args.command == "start":
        start_node(args.node_id, args.port)


if __name__ == "__main__":
    main()