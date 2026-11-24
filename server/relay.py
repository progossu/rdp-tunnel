"""RdpNet VPS Relay Core: Control Plane and Wire-Speed Socket Pairing."""

import asyncio
import collections
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from server.models import NetworkRegistry, Node
from server.protocol import (
    MAGIC,
    MSG_HELLO,
    MSG_WELCOME,
    MSG_HEARTBEAT,
    MSG_TUNNEL_REQUEST,
    MSG_TUNNEL_ASSIGN,
    MSG_TUNNEL_START,
    MSG_TUNNEL_ERROR,
    decode_frame,
    encode_frame,
)

logger = logging.getLogger(__name__)


class ActiveNode:
    """Wrapper for a connected control node holding network node metadata and streams."""

    def __init__(self, node: Node, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.node = node
        self.reader = reader
        self.writer = writer
        self.id = node.id
        self.name = node.name
        self.role = node.role
        self.network_id = node.network_id

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        if hasattr(self.node, item):
            return getattr(self.node, item)
        raise KeyError(item)

    async def send_frame(self, msg_type: int, payload: Dict[str, Any]) -> None:
        """Encodes and sends a framed message to this node's control connection."""
        frame = encode_frame(msg_type, payload)
        self.writer.write(frame)
        await self.writer.drain()


class RelayServer:
    """Asynchronous Relay Server managing control connections and data socket pairing."""

    def __init__(
        self,
        host: str,
        port: int,
        registry: NetworkRegistry,
        heartbeat_timeout: float = 45.0,
        data_port: Optional[int] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.registry = registry
        self.heartbeat_timeout = heartbeat_timeout
        self.data_port = data_port

        # Tracking: network_id -> {host_name: ActiveNode}
        self.active_hosts: Dict[str, Dict[str, ActiveNode]] = collections.defaultdict(dict)
        # Tracking: network_id -> {viewer_id: ActiveNode}
        self.active_viewers: Dict[str, Dict[str, ActiveNode]] = collections.defaultdict(dict)

        # Pending data session pairing slots: session_token -> slot dict
        self.pending_sessions: Dict[str, Dict[str, Any]] = {}

        self._server: Optional[asyncio.Server] = None
        self._data_server: Optional[asyncio.Server] = None
        self._active_connections: set = set()

    async def start(self) -> None:
        """Starts the control and optional data servers."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self.host,
            port=self.port,
        )
        if self.port == 0 and self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]

        if self.data_port is not None and self.data_port != self.port:
            self._data_server = await asyncio.start_server(
                self._handle_connection,
                host=self.host,
                port=self.data_port,
            )
            if self.data_port == 0 and self._data_server.sockets:
                self.data_port = self._data_server.sockets[0].getsockname()[1]

        logger.info(f"RelayServer started on {self.host}:{self.port} (data_port: {self.data_port})")

    async def stop(self) -> None:
        """Stops servers and closes all active connections and sessions."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._data_server:
            self._data_server.close()
            await self._data_server.wait_closed()
            self._data_server = None

        # Close all active hosts
        for net_hosts in list(self.active_hosts.values()):
            for host_conn in list(net_hosts.values()):
                try:
                    host_conn.writer.close()
                except Exception:
                    pass

        # Close all active viewers
        for net_viewers in list(self.active_viewers.values()):
            for viewer_conn in list(net_viewers.values()):
                try:
                    viewer_conn.writer.close()
                except Exception:
                    pass

        # Clean up pending sessions and wake up waiting handlers
        for slot in list(self.pending_sessions.values()):
            for sock_key in ("first_socket", "second_socket", "host_socket", "viewer_socket"):
                sock = slot.get(sock_key)
                if sock and isinstance(sock, tuple):
                    try:
                        sock[1].close()
                    except Exception:
                        pass
            if "ready_event" in slot:
                slot["ready_event"].set()
            if "done_event" in slot:
                slot["done_event"].set()

        self.pending_sessions.clear()
        self.active_hosts.clear()
        self.active_viewers.clear()

        # Close any lingering raw connections
        for w in list(self._active_connections):
            try:
                w.close()
            except Exception:
                pass
        self._active_connections.clear()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Dispatches connection based on initial handshake bytes (Magic vs SESSION_AUTH)."""
        self._active_connections.add(writer)
        try:
            head = await reader.read(2)
            if not head:
                return

            if head == MAGIC:
                await self._handle_control_connection(reader, writer, initial_bytes=head)
            else:
                rest = await reader.readline()
                line = (head + rest).decode("utf-8", errors="ignore").strip()
                await self._handle_data_auth(reader, writer, line)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._active_connections.discard(writer)
            try:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
            except Exception:
                pass

    async def _handle_control_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, initial_bytes: bytes
    ) -> None:
        """Handles an authenticated control connection lifetime."""
        buffer = initial_bytes
        active_node: Optional[ActiveNode] = None

        try:
            # Wait for MSG_HELLO
            while True:
                msg_type, payload, consumed = decode_frame(buffer)
                if msg_type is not None:
                    buffer = buffer[consumed:]
                    break
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buffer += chunk

            if msg_type != MSG_HELLO or not isinstance(payload, dict):
                return

            network_id = payload.get("network_id", "")
            auth_token = payload.get("auth_token", "")
            role = payload.get("role", "viewer")
            name = payload.get("name") or payload.get("node_name", "node")

            if not self.registry.authenticate(network_id, auth_token):
                err_frame = encode_frame(
                    MSG_WELCOME,
                    {
                        "status": "error",
                        "error_msg": "Authentication failed",
                        "message": "Authentication failed",
                    },
                )
                writer.write(err_frame)
                await writer.drain()
                return

            remote_ip = "127.0.0.1"
            peername = writer.get_extra_info("peername")
            if peername and isinstance(peername, tuple):
                remote_ip = str(peername[0])

            node = self.registry.register_node(
                network_id=network_id,
                name=name,
                role=role,
                remote_ip=remote_ip,
            )
            active_node = ActiveNode(node=node, reader=reader, writer=writer)

            welcome_frame = encode_frame(
                MSG_WELCOME,
                {"status": "ok", "node_id": node.id},
            )
            writer.write(welcome_frame)
            await writer.drain()

            if role == "host":
                self.active_hosts[network_id][name] = active_node
            else:
                self.active_viewers[network_id][node.id] = active_node

            # Event loop for incoming control messages
            while True:
                while True:
                    msg_type, msg_payload, consumed = decode_frame(buffer)
                    if msg_type is None:
                        break
                    buffer = buffer[consumed:]
                    await self._dispatch_control_message(active_node, msg_type, msg_payload or {})

                if self.heartbeat_timeout and self.heartbeat_timeout > 0:
                    try:
                        chunk = await asyncio.wait_for(reader.read(4096), timeout=self.heartbeat_timeout)
                    except asyncio.TimeoutError:
                        break
                else:
                    chunk = await reader.read(4096)

                if not chunk:
                    break
                buffer += chunk

        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if active_node:
                self._cleanup_control_node(active_node)

    async def _dispatch_control_message(
        self, active_node: ActiveNode, msg_type: int, payload: Dict[str, Any]
    ) -> None:
        """Processes a single decoded control message."""
        if msg_type == MSG_HEARTBEAT:
            active_node.node.last_seen = time.time()
            await active_node.send_frame(MSG_HEARTBEAT, {})

        elif msg_type == MSG_TUNNEL_REQUEST:
            target_host = (payload.get("target_host") or payload.get("host") or "").strip()
            hosts_in_net = self.active_hosts.get(active_node.network_id, {})

            chosen_host: Optional[ActiveNode] = None
            if target_host and target_host.lower() != "auto":
                chosen_host = hosts_in_net.get(target_host)
                if chosen_host is None:
                    # Case-insensitive fallback lookup
                    target_lower = target_host.lower()
                    for h_name, h_node in hosts_in_net.items():
                        if h_name.lower() == target_lower:
                            chosen_host = h_node
                            break
            else:
                if len(hosts_in_net) == 1:
                    chosen_host = next(iter(hosts_in_net.values()))

            if chosen_host is None:
                if not hosts_in_net:
                    err_msg = f"Target host '{target_host or 'auto'}' is offline (no hosts online in network)"
                elif not target_host or target_host.lower() == "auto":
                    online_names = ", ".join(sorted(hosts_in_net.keys()))
                    err_msg = f"Multiple hosts online ({online_names}); specify -target <name>"
                else:
                    online_names = ", ".join(sorted(hosts_in_net.keys()))
                    err_msg = f"Target host '{target_host}' is offline (online hosts: {online_names})"
                await active_node.send_frame(
                    MSG_TUNNEL_ERROR,
                    {"error_code": "HOST_OFFLINE", "message": err_msg},
                )
            else:
                session_token = secrets.token_hex(16)
                self.pending_sessions[session_token] = {
                    "network_id": active_node.network_id,
                    "created_at": time.time(),
                    "host_socket": None,
                    "viewer_socket": None,
                    "first_socket": None,
                    "second_socket": None,
                    "ready_event": asyncio.Event(),
                    "done_event": asyncio.Event(),
                    "paired": False,
                    "target_host": chosen_host.name,
                    "viewer_name": active_node.name,
                }

                # Send MSG_TUNNEL_ASSIGN to Host
                await chosen_host.send_frame(
                    MSG_TUNNEL_ASSIGN,
                    {"session_token": session_token, "viewer_name": active_node.name},
                )
                # Send MSG_TUNNEL_START to Viewer
                await active_node.send_frame(
                    MSG_TUNNEL_START,
                    {"status": "ok", "session_token": session_token},
                )

    def _cleanup_control_node(self, active_node: ActiveNode) -> None:
        """Cleans up disconnected control node from tracking and registry."""
        net_id = active_node.network_id
        if active_node.role == "host":
            if net_id in self.active_hosts:
                # Only remove if this exact ActiveNode instance is still the registered one
                if self.active_hosts[net_id].get(active_node.name) is active_node:
                    self.active_hosts[net_id].pop(active_node.name, None)
                if not self.active_hosts[net_id]:
                    self.active_hosts.pop(net_id, None)
        elif active_node.role == "viewer":
            if net_id in self.active_viewers:
                self.active_viewers[net_id].pop(active_node.id, None)
                if not self.active_viewers[net_id]:
                    self.active_viewers.pop(net_id, None)

        self.registry.remove_node(active_node.id)

    async def _handle_data_auth(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, line: str
    ) -> None:
        """Handles data socket connection pairing via SESSION_AUTH."""
        parts = line.strip().split()
        if len(parts) < 2 or parts[0] != "SESSION_AUTH":
            try:
                writer.write(b"ERROR Invalid command\n")
                await writer.drain()
            except Exception:
                pass
            return

        session_token = parts[1]
        slot = self.pending_sessions.get(session_token)
        if not slot or slot.get("paired"):
            try:
                writer.write(b"ERROR Invalid session\n")
                await writer.drain()
            except Exception:
                pass
            return

        role_hint = parts[2].lower() if len(parts) >= 3 else None

        if slot["first_socket"] is None:
            # First socket of the pair arrives
            slot["first_socket"] = (reader, writer)
            if role_hint == "viewer":
                slot["viewer_socket"] = (reader, writer)
            elif role_hint == "host":
                slot["host_socket"] = (reader, writer)
            else:
                slot["host_socket"] = (reader, writer)

            ready_event: asyncio.Event = slot["ready_event"]
            done_event: asyncio.Event = slot["done_event"]

            try:
                await ready_event.wait()
                await done_event.wait()
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
        else:
            # Second socket completes the pair
            reader1, writer1 = slot["first_socket"]
            slot["second_socket"] = (reader, writer)
            if slot["host_socket"] is None:
                slot["host_socket"] = (reader, writer)
            elif slot["viewer_socket"] is None:
                slot["viewer_socket"] = (reader, writer)

            slot["paired"] = True

            try:
                writer1.write(b"OK\n")
                writer.write(b"OK\n")
                await writer1.drain()
                await writer.drain()
            except Exception:
                slot["ready_event"].set()
                slot["done_event"].set()
                self.pending_sessions.pop(session_token, None)
                return

            slot["ready_event"].set()

            try:
                await self._pipe_bidirectional(reader1, writer1, reader, writer)
            finally:
                slot["done_event"].set()
                self.pending_sessions.pop(session_token, None)

    async def _pipe_bidirectional(
        self,
        reader_a: asyncio.StreamReader,
        writer_a: asyncio.StreamWriter,
        reader_b: asyncio.StreamReader,
        writer_b: asyncio.StreamWriter,
    ) -> None:
        """Pipes byte streams bidirectionally at wire speed with 64KB buffers."""
        chunk_size = 65536

        async def forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(chunk_size)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                try:
                    if not dst.is_closing():
                        dst.close()
                except Exception:
                    pass

        task_a = asyncio.create_task(forward(reader_a, writer_b))
        task_b = asyncio.create_task(forward(reader_b, writer_a))

        try:
            await asyncio.wait([task_a, task_b], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (task_a, task_b):
                if not task.done():
                    task.cancel()
            await asyncio.gather(task_a, task_b, return_exceptions=True)

            for w in (writer_a, writer_b):
                try:
                    if not w.is_closing():
                        w.close()
                        await w.wait_closed()
                except Exception:
                    pass
