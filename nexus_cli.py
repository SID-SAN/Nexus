import argparse
import os
import uvicorn
import uuid
import socket

def get_free_port():
    s = socket.socket()
    s.bind(('', 0))   # 🔥 auto-assign
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

    args = parser.parse_args()

    # -----------------------------
    # START NODE
    # -----------------------------
    if args.command == "start":

        node_id = args.node_id or f"node_{uuid.uuid4().hex[:6]}"
        PORT = get_free_port()
        os.environ["PORT"] = str(PORT)
        os.environ["NODE_ID"] = node_id

        print("\n=====================================")
        print("        🚀 NEXUS NODE STARTING       ")
        print("=====================================")
        print(f"Node ID   : {node_id}")
        print(f"Port      : {PORT}")
        print("=====================================\n")

        # 🔥 IMPORTANT: correct import
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