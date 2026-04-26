import argparse
import os
import asyncio
import uuid
import sys

from relay_client import connect_to_relay
from resource_monitor import resource_monitor_loop
from logger import setup_logger


def start_node(node_id, api_key):
    os.environ["NODE_ID"] = node_id
    os.environ["API_KEY"] = api_key
    logger = setup_logger(node_id)
    logger.info("=====================================")
    logger.info("NEXUS NODE STARTING")
    logger.info("=====================================")
    logger.info(f"Node ID: {node_id}")
    logger.info(f"API Key: {api_key[:6]}***")
    logger.info("=====================================")

    async def runner():
        await asyncio.gather(
            connect_to_relay(),
            resource_monitor_loop()
        )

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        logger.info("[CLI] Node stopped manually")


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

    if args.command == "start":

        node_id = args.node_id or f"node_{uuid.uuid4().hex[:6]}"

        start_node(node_id, args.api_key)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
