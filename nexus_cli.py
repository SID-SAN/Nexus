import argparse
import os
import uvicorn
import uuid


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
        "--port",
        type=int,
        default=5001,
        help="Port to run node (default: 5001)"
    )

    args = parser.parse_args()

    # -----------------------------
    # START NODE
    # -----------------------------
    if args.command == "start":

        node_id = args.node_id or f"node_{uuid.uuid4().hex[:6]}"

        os.environ["NODE_ID"] = node_id

        print("\n=====================================")
        print("        🚀 NEXUS NODE STARTING       ")
        print("=====================================")
        print(f"Node ID   : {node_id}")
        print(f"Port      : {args.port}")
        print("=====================================\n")

        # 🔥 IMPORTANT: correct import
        from node_server import app

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=args.port
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()