import argparse
import os
import asyncio
import uuid
from relay_client import connect_to_relay
from resource_monitor import resource_monitor_loop


def start_node(node_id, api_key):
    os.environ["NODE_ID"] = node_id
    os.environ["API_KEY"] = api_key

    print(f"[CLI] Starting node {node_id}")

    async def runner():
        await asyncio.gather(
            connect_to_relay(),
            resource_monitor_loop()
        )

    asyncio.run(runner())


def main():
    parser = argparse.ArgumentParser(prog="nexus-node")

    parser.add_argument(
        "command",
        choices=["start"]
    )

    parser.add_argument(
        "--node-id",
        default=f"node_{uuid.uuid4().hex[:6]}"
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