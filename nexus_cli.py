import argparse
import os
import uvicorn
import uuid
import socket


def get_free_port():
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    parser = argparse.ArgumentParser(
        prog="nexus-node",
        description="🚀 Nexus Distributed Node CLI"
    )

    subparsers = parser.add_subparsers(dest="command")

    # -----------------------------
    # START COMMAND
    # -----------------------------
    start_parser = subparsers.add_parser("start", help="Start a Nexus node")

    start_parser.add_argument(
        "--node-id",
        default=None,
        help="Custom Node ID (default: auto-generated)"
    )

    start_parser.add_argument(
        "--api-key",
        required=True,
        help="User API key (required)"
    )

    args = parser.parse_args()

    # -----------------------------
    # START NODE
    # -----------------------------
    if args.command == "start":

        node_id = args.node_id or f"node_{uuid.uuid4().hex[:6]}"
        PORT = get_free_port()

        # 🔥 ENV SETUP
        os.environ["PORT"] = str(PORT)
        os.environ["NODE_ID"] = node_id
        os.environ["API_KEY"] = args.api_key

        print("\n=====================================")
        print("        🚀 NEXUS NODE STARTING       ")
        print("=====================================")
        print(f"Node ID   : {node_id}")
        print(f"Port      : {PORT}")
        print(f"API Key   : {args.api_key[:6]}***")
        print("=====================================\n")

        # 🔥 import AFTER env is set
        from node_server import app

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=PORT
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()