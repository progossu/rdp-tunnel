"""RdpNet Server Entry Point: Runs Relay Core (:8443) and Web Dashboard (:8080)."""

import argparse
import asyncio
import logging
import os
import signal

from server.models import NetworkRegistry
from server.relay import RelayServer
from server.web_server import WebServer

logger = logging.getLogger("rdpnet-server")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RdpNet Server Daemon")
    parser.add_argument("--host", default=os.environ.get("RDPNET_HOST", "0.0.0.0"), help="Binding IP address")
    parser.add_argument("--relay-port", type=int, default=int(os.environ.get("RDPNET_RELAY_PORT", "8443")), help="Relay port")
    parser.add_argument("--web-port", type=int, default=int(os.environ.get("RDPNET_WEB_PORT", "8080")), help="Web UI port")
    parser.add_argument("--data-file", default=os.environ.get("RDPNET_DATA_FILE", "networks.json"), help="JSON storage path")
    parser.add_argument("--web-user", default=os.environ.get("RDPNET_WEB_USER", "admin"), help="Web UI username")
    parser.add_argument("--web-pass", default=os.environ.get("RDPNET_WEB_PASS", ""), help="Web UI password (empty = open)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    )

    registry = NetworkRegistry(args.data_file)
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

    web_pass = args.web_pass.strip() or None
    web = WebServer(
        host=args.host,
        port=args.web_port,
        registry=registry,
        relay=relay,
        admin_user=args.web_user,
        admin_password=web_pass,
    )
    await web.start()
    auth_mode = f"protected (user={args.web_user})" if web_pass else "open (no password set)"
    logger.info("RdpNet Web Dashboard listening on http://%s:%d [%s]", args.host, args.web_port, auth_mode)

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
