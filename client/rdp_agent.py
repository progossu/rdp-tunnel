"""RdpNet Client Agent: High-performance reverse tunnel agent for Host and Viewer roles."""

import argparse
import asyncio
import configparser
import logging
import os
import socket
import sys
from typing import Optional

from server.protocol import (
    MAGIC,
    MSG_HELLO,
    MSG_WELCOME,
    MSG_HEARTBEAT,
    MSG_TUNNEL_REQUEST,
    MSG_TUNNEL_ASSIGN,
    MSG_TUNNEL_START,
    MSG_TUNNEL_ERROR,
    encode_frame,
    decode_frame,
)

logger = logging.getLogger("rdp-agent")


class RdpAgent:
    """Unified RdpNet agent supporting Host (server) and Viewer (client) modes."""

    def __init__(
        self,
        server_host: str,
        server_port: int,
        network_id: str,
        auth_token: str,
        role: str,
        node_name: str,
        target_host: Optional[str] = None,
        local_port: int = 33890,
        rdp_port: int = 3389,
        heartbeat_interval: float = 15.0,
        reconnect_delay: float = 3.0,
    ) -> None:
        self.server_host = server_host
        self.server_port = server_port
        self.network_id = network_id
        self.auth_token = auth_token
        self.role = role.lower()  # 'host' or 'viewer'
        self.node_name = node_name
        self.target_host = target_host
        self.local_port = local_port
        self.rdp_port = rdp_port
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delay = reconnect_delay

        self._running = False
        self._control_reader: Optional[asyncio.StreamReader] = None
        self._control_writer: Optional[asyncio.StreamWriter] = None
        self._control_buf = bytearray()
        self._active_sessions = set()
        self._local_server: Optional[asyncio.Server] = None
        self._tunnel_response_queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        """Starts the agent control loop and role listeners."""
        self._running = True
        logger.info(
            "Starting RdpNet Agent: role=%s, network=%s, name=%s, server=%s:%d",
            self.role,
            self.network_id,
            self.node_name,
            self.server_host,
            self.server_port,
        )

        if self.role == "viewer":
            self._local_server = await asyncio.start_server(
                self._handle_local_rdp_client,
                host="127.0.0.1",
                port=self.local_port,
            )
            if self.local_port == 0 and self._local_server.sockets:
                self.local_port = self._local_server.sockets[0].getsockname()[1]
            logger.info("Viewer listening for RDP clients (mstsc) on 127.0.0.1:%d", self.local_port)

        while self._running:
            try:
                await self._run_control_session()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.warning("Connection lost to relay (%s). Reconnecting in %.1fs...", e, self.reconnect_delay)
                    await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        """Stops the agent and terminates open sockets."""
        self._running = False
        if self._local_server:
            try:
                self._local_server.close()
                await asyncio.wait_for(self._local_server.wait_closed(), timeout=0.2)
            except Exception:
                pass
            self._local_server = None

        if self._control_writer:
            try:
                self._control_writer.close()
                await asyncio.wait_for(self._control_writer.wait_closed(), timeout=0.2)
            except Exception:
                pass
            self._control_writer = None

        for task in list(self._active_sessions):
            task.cancel()
        self._active_sessions.clear()
        logger.info("RdpNet Agent stopped.")

    async def _run_control_session(self) -> None:
        """Maintains persistent control connection with the relay."""
        logger.info("Connecting to relay %s:%d...", self.server_host, self.server_port)
        self._control_buf.clear()
        self._control_reader, self._control_writer = await asyncio.open_connection(
            self.server_host, self.server_port
        )

        # 1. Handshake: Send MSG_HELLO
        hello_payload = {
            "network_id": self.network_id,
            "auth_token": self.auth_token,
            "role": self.role,
            "name": self.node_name,
        }
        self._control_writer.write(encode_frame(MSG_HELLO, hello_payload))
        await self._control_writer.drain()

        # 2. Wait for MSG_WELCOME
        welcome_frame = await self._read_next_frame(self._control_reader)
        if not welcome_frame:
            raise ConnectionError("Relay closed connection during handshake")

        msg_type, welcome_payload = welcome_frame
        if msg_type != MSG_WELCOME or welcome_payload.get("status") != "ok":
            err_msg = welcome_payload.get("error_msg", "Registration rejected")
            raise PermissionError(f"Handshake failed: {err_msg}")

        logger.info("Authenticated with relay! Assigned node ID: %s", welcome_payload.get("node_id"))

        # 3. Spawn heartbeat task
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            # 4. Message dispatch loop - Single reader coroutine
            while self._running:
                frame = await self._read_next_frame(self._control_reader)
                if not frame:
                    break
                m_type, payload = frame
                await self._handle_control_message(m_type, payload)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self) -> None:
        """Sends periodic MSG_HEARTBEAT to relay."""
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            if self._control_writer and not self._control_writer.is_closing():
                try:
                    self._control_writer.write(encode_frame(MSG_HEARTBEAT, {}))
                    await self._control_writer.drain()
                except Exception as e:
                    logger.debug("Heartbeat send error: %s", e)
                    break

    async def _handle_control_message(self, msg_type: int, payload: dict) -> None:
        """Handles server-pushed commands from the single reader loop."""
        if msg_type == MSG_HEARTBEAT:
            # Server responded to heartbeat
            pass

        elif msg_type == MSG_TUNNEL_ASSIGN and self.role == "host":
            # Incoming viewer connection request assigned by relay
            session_token = payload.get("session_token")
            viewer_name = payload.get("viewer_name", "Viewer")
            logger.info("Incoming RDP tunnel request from %s (token: %s)", viewer_name, session_token)
            task = asyncio.create_task(self._host_bridge_session(session_token))
            self._active_sessions.add(task)
            task.add_done_callback(self._active_sessions.discard)

        elif msg_type in (MSG_TUNNEL_START, MSG_TUNNEL_ERROR):
            # Dispatch to waiting viewer client coroutine
            await self._tunnel_response_queue.put((msg_type, payload))

    async def _host_bridge_session(self, session_token: str) -> None:
        """Host bridges the VPS data socket to local Windows RDP (3389)."""
        logger.info("Opening dedicated data pairing connection to relay for token %s", session_token)
        vps_writer = None
        rdp_writer = None
        try:
            # Connect to VPS relay data port
            vps_reader, vps_writer = await asyncio.open_connection(self.server_host, self.server_port)
            vps_writer.write(f"SESSION_AUTH {session_token} host\n".encode("utf-8"))
            await vps_writer.drain()

            # Wait for pairing OK\n
            auth_ack = await vps_reader.readline()
            if auth_ack.strip() != b"OK":
                logger.error("Data socket pairing rejected by relay: %r", auth_ack)
                return

            # Connect to local Windows RDP server
            logger.info("Connecting to local RDP port 127.0.0.1:%d...", self.rdp_port)
            rdp_reader, rdp_writer = await asyncio.open_connection("127.0.0.1", self.rdp_port)

            logger.info("RDP tunnel established! Streaming traffic...")
            await asyncio.gather(
                self._pipe_streams(vps_reader, rdp_writer, "VPS -> RDP"),
                self._pipe_streams(rdp_reader, vps_writer, "RDP -> VPS"),
            )
        except Exception as e:
            logger.error("Session bridge error: %s", e)
        finally:
            for w in (vps_writer, rdp_writer):
                if w:
                    try:
                        w.close()
                    except Exception:
                        pass
            logger.info("RDP tunnel session finished.")

    async def _handle_local_rdp_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        """Viewer accepts incoming connection from local mstsc.exe and requests VPS tunnel."""
        logger.info("Local RDP client connected on 127.0.0.1:%d", self.local_port)
        if not self._control_writer or self._control_writer.is_closing():
            logger.warning("Cannot start session: Not connected to relay")
            client_writer.close()
            await client_writer.wait_closed()
            return

        # Request tunnel from relay
        req_payload = {}
        if self.target_host:
            req_payload["target_host"] = self.target_host

        self._control_writer.write(encode_frame(MSG_TUNNEL_REQUEST, req_payload))
        await self._control_writer.drain()

        # Await response from the tunnel response queue
        try:
            m_type, payload = await asyncio.wait_for(self._tunnel_response_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for relay tunnel response")
            client_writer.close()
            await client_writer.wait_closed()
            return

        if m_type == MSG_TUNNEL_ERROR:
            logger.error("Tunnel request failed: %s (%s)", payload.get("error_code"), payload.get("message"))
            client_writer.close()
            await client_writer.wait_closed()
            return

        if m_type != MSG_TUNNEL_START:
            logger.error("Unexpected message type %d while awaiting tunnel start", m_type)
            client_writer.close()
            await client_writer.wait_closed()
            return

        session_token = payload.get("session_token")
        logger.info("Tunnel authorized! Session token: %s", session_token)

        # Connect to VPS data port and pair
        vps_writer = None
        try:
            vps_reader, vps_writer = await asyncio.open_connection(self.server_host, self.server_port)
            vps_writer.write(f"SESSION_AUTH {session_token} viewer\n".encode("utf-8"))
            await vps_writer.drain()

            ack = await vps_reader.readline()
            if ack.strip() != b"OK":
                logger.error("Viewer data socket pairing rejected: %r", ack)
                return

            logger.info("Viewer tunnel paired! Streaming mstsc <-> VPS...")
            await asyncio.gather(
                self._pipe_streams(client_reader, vps_writer, "mstsc -> VPS"),
                self._pipe_streams(vps_reader, client_writer, "VPS -> mstsc"),
            )
        except Exception as e:
            logger.error("Viewer tunnel error: %s", e)
        finally:
            for w in (client_writer, vps_writer):
                if w:
                    try:
                        w.close()
                    except Exception:
                        pass
            logger.info("Viewer RDP session closed.")

    async def _pipe_streams(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, label: str) -> None:
        """Pipes raw bytes between two streams until EOF or disconnect."""
        try:
            while self._running:
                data = await reader.read(65536)
                if not data:
                    logger.debug("Pipe %s got EOF", label)
                    break
                logger.debug("Pipe %s read %d bytes, writing to dest", label, len(data))
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.debug("Pipe %s error: %s", label, e)

    async def _read_next_frame(self, reader: asyncio.StreamReader) -> Optional[tuple[int, dict]]:
        """Reads a complete framed message from stream using persistent buffer."""
        while self._running:
            msg_type, payload, consumed = decode_frame(bytes(self._control_buf))
            if msg_type is not None:
                del self._control_buf[:consumed]
                return msg_type, payload
            chunk = await reader.read(4096)
            if not chunk:
                return None
            self._control_buf.extend(chunk)


def detect_windows_rdp_port() -> int:
    """Reads configured RDP listening port from Windows Registry, falling back to 3389."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as reg_key:
                port_val, _ = winreg.QueryValueEx(reg_key, "PortNumber")
                if isinstance(port_val, int) and 1 <= port_val <= 65535:
                    return port_val
        except Exception:
            pass
    return 3389


def load_config(config_path: str = "rdp-agent.ini") -> dict:
    """Loads configuration from INI file if present."""
    config = {}
    if os.path.exists(config_path):
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if parser.has_section("server"):
            config["server"] = parser.get("server", "address", fallback=None)
        if parser.has_section("network"):
            config["net"] = parser.get("network", "id", fallback=None)
            config["key"] = parser.get("network", "key", fallback=None)
        if parser.has_section("node"):
            config["role"] = parser.get("node", "role", fallback=None)
            config["name"] = parser.get("node", "name", fallback=None)
            config["target"] = parser.get("node", "target", fallback=None)
            config["local_port"] = parser.getint("node", "local_port", fallback=None)
            raw_rdp = parser.get("node", "rdp_port", fallback=None)
            if raw_rdp is not None:
                config["rdp_port"] = 0 if raw_rdp.strip().lower() == "auto" else int(raw_rdp)
            config["mstsc"] = parser.getboolean("node", "mstsc", fallback=False)
    return config


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="RdpNet Windows Client Agent")
    parser.add_argument("-server", help="VPS Relay address (e.g., 1.2.3.4:8443)")
    parser.add_argument("-net", help="Virtual Network ID")
    parser.add_argument("-key", help="Network authentication secret")
    parser.add_argument("-host", action="store_true", help="Run in Host mode (exposes local RDP)")
    parser.add_argument("-viewer", action="store_true", help="Run in Viewer mode (listens for mstsc.exe)")
    parser.add_argument("-name", help="Node name (for Host mode, e.g. Work-PC)")
    parser.add_argument("-target", help="Target Host name (for Viewer mode, optional if only 1 host)")
    parser.add_argument("-local", type=int, default=33890, help="Local listening port for Viewer (default: 33890)")
    parser.add_argument("-rdp-port", type=int, default=None, help="Local RDP port on Host (default: auto-detect)")
    parser.add_argument("-mstsc", action="store_true", help="Auto-launch mstsc.exe in Viewer mode")
    parser.add_argument("-config", default="rdp-agent.ini", help="Path to INI config file (default: rdp-agent.ini)")
    parser.add_argument("-silent", action="store_true", help="Run silently without console logging")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    ini_cfg = load_config(args.config)

    server = args.server or ini_cfg.get("server") or "127.0.0.1:8443"
    net_id = args.net or ini_cfg.get("net")
    key = args.key or ini_cfg.get("key")
    role = "host" if args.host else ("viewer" if args.viewer else ini_cfg.get("role", "host"))
    name = args.name or ini_cfg.get("name") or socket.gethostname()
    target = args.target or ini_cfg.get("target")
    local_port = args.local if args.local != 33890 else (ini_cfg.get("local_port") or 33890)
    rdp_port = args.rdp_port if args.rdp_port is not None else ini_cfg.get("rdp_port", 0)
    if rdp_port == 0:
        rdp_port = detect_windows_rdp_port()
    launch_mstsc = args.mstsc or ini_cfg.get("mstsc", False)

    if not net_id or not key:
        print("Error: Both -net <network_id> and -key <secret> are required.")
        sys.exit(1)

    if ":" in server:
        s_host, s_port_str = server.split(":", 1)
        s_port = int(s_port_str)
    else:
        s_host = server
        s_port = 8443

    if not args.silent:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if role == "viewer" and launch_mstsc and sys.platform == "win32":
        import subprocess
        subprocess.Popen(["mstsc.exe", f"/v:127.0.0.1:{local_port}"])

    agent = RdpAgent(
        server_host=s_host,
        server_port=s_port,
        network_id=net_id,
        auth_token=key,
        role=role,
        node_name=name,
        target_host=target,
        local_port=local_port,
        rdp_port=rdp_port,
    )
    await agent.start()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
