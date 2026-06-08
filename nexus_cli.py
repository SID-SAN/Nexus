import argparse
import os
import asyncio
import uuid
import sys

from relay_client import connect_to_relay
from resource_monitor import resource_monitor_loop
from logger import setup_logger
from chaos import chaos_enabled

APP_VERSION = "v6.0.0"


def generate_node_id():
    return f"node_{uuid.uuid4().hex[:6]}"


def print_banner(logger, node_id, api_key):
    logger.info("=====================================")
    logger.info("🚀 NEXUS NODE")
    logger.info("=====================================")
    logger.info(f"Version : {APP_VERSION}")
    logger.info(f"Node ID : {node_id}")
    logger.info(f"API Key : {api_key[:6]}***")
    logger.info(f"Python  : {sys.version.split()[0]}")
    logger.info(f"Platform: {sys.platform}")
    logger.info(f"[Chaos] Enabled={chaos_enabled()}")
    logger.info("=====================================")


def start_node(node_id, api_key):

    os.environ["NODE_ID"] = node_id
    os.environ["API_KEY"] = api_key

    logger = setup_logger(node_id)

    print_banner(
        logger,
        node_id,
        api_key
    )

    async def runner():
        await asyncio.gather(
            connect_to_relay(),
            resource_monitor_loop()
        )

    try:
        asyncio.run(runner())

    except KeyboardInterrupt:
        logger.info(
            "[CLI] Node stopped manually"
        )

    except Exception:
        logger.exception(
            "[CLI] Fatal node error"
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="nexus-node",
        description="Nexus Distributed Compute Node"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )

    # =====================================
    # START COMMAND
    # =====================================
    start_parser = subparsers.add_parser(
        "start",
        help="Start a Nexus node"
    )

    start_parser.add_argument(
        "--node-id",
        default=None,
        help="Node identifier"
    )

    start_parser.add_argument(
        "--api-key",
        required=True,
        help="User API key"
    )

    # =====================================
    # VERSION
    # =====================================
    subparsers.add_parser(
        "version",
        help="Show Nexus version"
    )

    args = parser.parse_args()

    if args.command == "start":

        node_id = (
            args.node_id
            or generate_node_id()
        )

        start_node(
            node_id=node_id,
            api_key=args.api_key
        )

    elif args.command == "version":
        print(
            f"Nexus Node {APP_VERSION}"
        )

if __name__ == "__main__":
    main()