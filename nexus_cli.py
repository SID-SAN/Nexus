import argparse
import os
import uvicorn


def main():

    parser = argparse.ArgumentParser(description="Nexus Node CLI")

    parser.add_argument(
        "command",
        choices=["start"],
        help="Command to run"
    )

    parser.add_argument(
        "--node-id",
        required=True,
        help="Node ID"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5001,
        help="Port to run node"
    )

    args = parser.parse_args()

    if args.command == "start":

        os.environ["NODE_ID"] = args.node_id

    from node import app

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port
    )


if __name__ == "__main__":
    main()