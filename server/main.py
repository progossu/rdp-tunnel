"""RdpNet Server Entry Point: Runs Relay Core (:8443) and Web Dashboard (:8080)."""

import argparse
import asyncio
import logging
import os
import signal
import sys

from server.models import NetworkRegistry
from server.relay import RelayServer
from server.web_server import WebServer

logger = logging.getLogger("rdpnet-server")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RdpNet Server Daemon")
    parser.add_argument("--host", default="0.0.0.0", help="Binding IP address (default: 0.0.0.0)")
    parser.add_argument("--relay-port", type=int, default=8443, help="Relay control & data port (default: 8443)")
    parser.add_argument("--web-port", type=int, default=8080, help="Web management dashboard port (default: 8080)")
    parser.add_argument("--data-file", default="networks.json", help="Path to network storage JSON file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    )

    registry = NetworkRegistry(args.data_file)
    # Ensure at least one default network exists on first run
    if not registry.list_networks():
        default_net = registry.create_network("Default Network")
        logger.info("Created initial network '%s' (ID: %s, Key: %s)", default_net.name, default_net.id, default_net.auth_token)

    relay = RelayServer(
        host=args.host,
        port=args.relay_port,
        registry=registry,
    )
    await relay.start()
    logger.info("RdpNet Relay Server listening on %s:%d", args.host, args.relay_port)

    web = WebServer(
        host=args.host,
        port=args.web_port,
        registry=registry,
        relay=relay,
    )
    await web.start()
    logger.info("RdpNet Web Dashboard listening on http://%s:%d", args.host, args.web_port)

    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows signal handling fallback
                pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Shutting down RdpNet services...")
        await web.stop()
        await relay.stop()
        logger.info("RdpNet Server terminated cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
