"""Tests for RdpNet Relay Server (Control Plane & Wire-Speed Socket Pairing)."""

import asyncio
import os
import secrets
import pytest

from server.protocol import (
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
from server.models import NetworkRegistry
from server.relay import RelayServer


async def send_msg(writer: asyncio.StreamWriter, msg_type: int, payload: dict) -> None:
    frame = encode_frame(msg_type, payload)
    writer.write(frame)
    await writer.drain()


async def recv_msg(reader: asyncio.StreamReader, timeout: float = 3.0) -> tuple[int, dict]:
    buffer = b""
    async def _read():
        nonlocal buffer
        while True:
            msg_type, payload, consumed = decode_frame(buffer)
            if msg_type is not None:
                return msg_type, payload
            chunk = await reader.read(4096)
            if not chunk:
                raise ConnectionResetError("Connection closed before complete frame was received")
            buffer += chunk

    return await asyncio.wait_for(_read(), timeout=timeout)


@pytest.fixture
def registry():
    # In-memory registry for testing
    return NetworkRegistry(storage_path=None)


@pytest.mark.asyncio
async def test_invalid_auth_rejection(registry):
    net = registry.create_network("TenantA", "correct_token")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        # Send HELLO with bad token
        await send_msg(
            writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "wrong_token", "role": "host", "name": "Host-1"},
        )

        msg_type, payload = await recv_msg(reader)
        assert msg_type == MSG_WELCOME
        assert payload.get("status") == "error"
        assert "Authentication failed" in payload.get("error_msg", "")

        # Verify connection closed by server
        data = await reader.read(100)
        assert data == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_successful_host_and_viewer_registration(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # Register Host
        h_reader, h_writer = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(
            h_writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "PC-Host"},
        )
        msg_type, payload = await recv_msg(h_reader)
        assert msg_type == MSG_WELCOME
        assert payload.get("status") == "ok"
        host_node_id = payload.get("node_id")
        assert host_node_id is not None

        # Verify host tracking
        assert "PC-Host" in server.active_hosts[net.id]

        # Register Viewer
        v_reader, v_writer = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(
            v_writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "Laptop-Viewer"},
        )
        msg_type, payload = await recv_msg(v_reader)
        assert msg_type == MSG_WELCOME
        assert payload.get("status") == "ok"
        viewer_node_id = payload.get("node_id")
        assert viewer_node_id is not None

        # Verify viewer tracking
        assert viewer_node_id in server.active_viewers[net.id]

        # Verify registry state
        nodes = registry.get_nodes_in_network(net.id)
        assert len(nodes) == 2
        assert {n.name for n in nodes} == {"PC-Host", "Laptop-Viewer"}

        h_writer.close()
        v_writer.close()
        await h_writer.wait_closed()
        await v_writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_heartbeat_exchange(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(
            writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "PC-1"},
        )
        _, welcome = await recv_msg(reader)
        node_id = welcome["node_id"]

        node = registry._nodes[node_id]
        prev_last_seen = node.last_seen

        await asyncio.sleep(0.05)
        # Send heartbeat
        await send_msg(writer, MSG_HEARTBEAT, {})
        msg_type, payload = await recv_msg(reader)
        assert msg_type == MSG_HEARTBEAT
        assert payload == {}
        assert node.last_seen >= prev_last_seen

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_viewer_tunnel_request_host_offline(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(
            writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "V-1"},
        )
        await recv_msg(reader)

        # 1. Request non-existent host explicitly
        await send_msg(writer, MSG_TUNNEL_REQUEST, {"target_host": "GhostHost"})
        msg_type, payload = await recv_msg(reader)
        assert msg_type == MSG_TUNNEL_ERROR
        assert payload.get("error_code") == "HOST_OFFLINE"

        # 2. Request default host when no hosts online
        await send_msg(writer, MSG_TUNNEL_REQUEST, {})
        msg_type, payload = await recv_msg(reader)
        assert msg_type == MSG_TUNNEL_ERROR
        assert payload.get("error_code") == "HOST_OFFLINE"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_successful_tunnel_request_dispatch(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # Connect Host 1
        h1_r, h1_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h1_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Host-1"})
        await recv_msg(h1_r)

        # Connect Host 2
        h2_r, h2_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h2_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Host-2"})
        await recv_msg(h2_r)

        # Connect Viewer
        v_r, v_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(v_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "Viewer-Alice"})
        await recv_msg(v_r)

        # Request tunnel to Host-2
        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "Host-2"})

        # Viewer receives MSG_TUNNEL_START
        v_msg, v_pay = await recv_msg(v_r)
        assert v_msg == MSG_TUNNEL_START
        assert v_pay.get("status") == "ok"
        session_token = v_pay.get("session_token")
        assert session_token is not None
        assert len(session_token) == 32  # 16 bytes hex

        # Host-2 receives MSG_TUNNEL_ASSIGN
        h2_msg, h2_pay = await recv_msg(h2_r)
        assert h2_msg == MSG_TUNNEL_ASSIGN
        assert h2_pay.get("session_token") == session_token
        assert h2_pay.get("viewer_name") == "Viewer-Alice"

        # Verify session is registered in pending_sessions
        assert session_token in server.pending_sessions
        slot = server.pending_sessions[session_token]
        assert slot["network_id"] == net.id

        h1_w.close()
        h2_w.close()
        v_w.close()
        await h1_w.wait_closed()
        await h2_w.wait_closed()
        await v_w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_single_host_automatic_selection(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # Only 1 host online
        h_r, h_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Sole-Host"})
        await recv_msg(h_r)

        v_r, v_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(v_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "V-Solo"})
        await recv_msg(v_r)

        # Request with empty target_host
        await send_msg(v_w, MSG_TUNNEL_REQUEST, {})

        v_msg, v_pay = await recv_msg(v_r)
        assert v_msg == MSG_TUNNEL_START
        assert v_pay.get("status") == "ok"
        token = v_pay.get("session_token")

        h_msg, h_pay = await recv_msg(h_r)
        assert h_msg == MSG_TUNNEL_ASSIGN
        assert h_pay.get("session_token") == token

        h_w.close()
        v_w.close()
        await h_w.wait_closed()
        await v_w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_data_pairing_and_wire_speed_pipe(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # 1. Establish control session
        h_r, h_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "H-Data"})
        await recv_msg(h_r)

        v_r, v_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(v_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "V-Data"})
        await recv_msg(v_r)

        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "H-Data"})
        _, v_start = await recv_msg(v_r)
        _, h_assign = await recv_msg(h_r)
        session_token = v_start["session_token"]

        # 2. Connect Viewer data socket
        v_data_r, v_data_w = await asyncio.open_connection("127.0.0.1", server.port)
        v_data_w.write(f"SESSION_AUTH {session_token}\n".encode("utf-8"))
        await v_data_w.drain()

        # 3. Connect Host data socket
        h_data_r, h_data_w = await asyncio.open_connection("127.0.0.1", server.port)
        h_data_w.write(f"SESSION_AUTH {session_token}\n".encode("utf-8"))
        await h_data_w.drain()

        # Both should receive OK\n
        v_ok = await asyncio.wait_for(v_data_r.readline(), timeout=2.0)
        h_ok = await asyncio.wait_for(h_data_r.readline(), timeout=2.0)
        assert v_ok == b"OK\n"
        assert h_ok == b"OK\n"

        # 4. Bidirectional data transfer
        v_data_w.write(b"PING from viewer to host\n")
        await v_data_w.drain()
        h_rec = await asyncio.wait_for(h_data_r.readline(), timeout=2.0)
        assert h_rec == b"PING from viewer to host\n"

        h_data_w.write(b"PONG from host to viewer\n")
        await h_data_w.drain()
        v_rec = await asyncio.wait_for(v_data_r.readline(), timeout=2.0)
        assert v_rec == b"PONG from host to viewer\n"

        # 5. High-volume binary payload streaming (64 KB)
        random_payload = secrets.token_bytes(65536)
        v_data_w.write(random_payload)
        await v_data_w.drain()

        received_by_host = await asyncio.wait_for(h_data_r.readexactly(len(random_payload)), timeout=3.0)
        assert received_by_host == random_payload

        h_data_w.write(random_payload)
        await h_data_w.drain()

        received_by_viewer = await asyncio.wait_for(v_data_r.readexactly(len(random_payload)), timeout=3.0)
        assert received_by_viewer == random_payload

        # 6. Teardown: close viewer data socket, host should see EOF
        v_data_w.close()
        await v_data_w.wait_closed()

        eof_check = await asyncio.wait_for(h_data_r.read(100), timeout=2.0)
        assert eof_check == b""

        h_data_w.close()
        await h_data_w.wait_closed()

        # Session token should be cleaned up from pending_sessions
        assert session_token not in server.pending_sessions

        h_w.close()
        v_w.close()
        await h_w.wait_closed()
        await v_w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_invalid_session_token_rejected(registry):
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(b"SESSION_AUTH fake_token_9999\n")
        await writer.drain()

        # Server should reject and close
        res = await reader.read(100)
        # Should be closed (empty) or error response then closed
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_control_node_cleanup_on_disconnect(registry):
    net = registry.create_network("TenantA", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(
            writer,
            MSG_HELLO,
            {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Disconnect-Me"},
        )
        _, welcome = await recv_msg(reader)
        node_id = welcome["node_id"]

        assert "Disconnect-Me" in server.active_hosts[net.id]
        assert len(registry.get_nodes_in_network(net.id)) == 1

        # Disconnect client
        writer.close()
        await writer.wait_closed()

        # Allow event loop to process disconnect
        await asyncio.sleep(0.1)

        assert "Disconnect-Me" not in server.active_hosts.get(net.id, {})
        assert len(registry.get_nodes_in_network(net.id)) == 0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_multi_tenant_isolation(registry):
    net_a = registry.create_network("TenantA", "tokenA")
    net_b = registry.create_network("TenantB", "tokenB")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # Tenant A host
        ha_r, ha_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(ha_w, MSG_HELLO, {"network_id": net_a.id, "auth_token": "tokenA", "role": "host", "name": "Host-A"})
        await recv_msg(ha_r)

        # Tenant B viewer
        vb_r, vb_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(vb_w, MSG_HELLO, {"network_id": net_b.id, "auth_token": "tokenB", "role": "viewer", "name": "Viewer-B"})
        await recv_msg(vb_r)

        # Viewer B tries to request Host-A
        await send_msg(vb_w, MSG_TUNNEL_REQUEST, {"target_host": "Host-A"})
        msg_type, payload = await recv_msg(vb_r)
        assert msg_type == MSG_TUNNEL_ERROR
        assert payload.get("error_code") == "HOST_OFFLINE"

        ha_w.close()
        vb_w.close()
        await ha_w.wait_closed()
        await vb_w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dual_port_configuration(registry):
    net = registry.create_network("TenantA", "token123")
    # Start with separate control and data ports
    server = RelayServer(host="127.0.0.1", port=0, registry=registry, data_port=0)
    await server.start()

    try:
        assert server.port != server.data_port

        # Control connection on server.port
        h_r, h_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Dual-Host"})
        await recv_msg(h_r)

        v_r, v_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(v_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "Dual-Viewer"})
        await recv_msg(v_r)

        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "Dual-Host"})
        _, v_start = await recv_msg(v_r)
        _, h_assign = await recv_msg(h_r)
        token = v_start["session_token"]

        # Data connections on server.data_port
        v_d_r, v_d_w = await asyncio.open_connection("127.0.0.1", server.data_port)
        v_d_w.write(f"SESSION_AUTH {token}\n".encode("utf-8"))
        await v_d_w.drain()

        h_d_r, h_d_w = await asyncio.open_connection("127.0.0.1", server.data_port)
        h_d_w.write(f"SESSION_AUTH {token}\n".encode("utf-8"))
        await h_d_w.drain()

        v_ok = await asyncio.wait_for(v_d_r.readline(), timeout=2.0)
        h_ok = await asyncio.wait_for(h_d_r.readline(), timeout=2.0)
        assert v_ok == b"OK\n"
        assert h_ok == b"OK\n"

        v_d_w.write(b"Separate data port test\n")
        await v_d_w.drain()
        rec = await asyncio.wait_for(h_d_r.readline(), timeout=2.0)
        assert rec == b"Separate data port test\n"

        v_d_w.close()
        h_d_w.close()
        h_w.close()
        v_w.close()
        await v_d_w.wait_closed()
        await h_d_w.wait_closed()
        await h_w.wait_closed()
        await v_w.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_reconnect_does_not_evict_and_case_insensitive_auto_target(registry):
    net = registry.create_network("ReconnectNet", "token123")
    server = RelayServer(host="127.0.0.1", port=0, registry=registry)
    await server.start()

    try:
        # 1. Host connects first time
        h1_r, h1_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h1_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Office-PC"})
        await recv_msg(h1_r)

        # 2. Same Host reconnects before h1 closes
        h2_r, h2_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(h2_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "host", "name": "Office-PC"})
        await recv_msg(h2_r)

        # 3. Old connection closes -> must NOT evict h2!
        h1_w.close()
        await h1_w.wait_closed()
        await asyncio.sleep(0.1)
        assert "Office-PC" in server.active_hosts[net.id]

        # 4. Viewer connects and tests case-insensitive, 'auto', and missing-host error message
        v_r, v_w = await asyncio.open_connection("127.0.0.1", server.port)
        await send_msg(v_w, MSG_HELLO, {"network_id": net.id, "auth_token": "token123", "role": "viewer", "name": "ELKAR"})
        await recv_msg(v_r)

        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "office-pc"})
        m_type, p = await recv_msg(v_r)
        assert m_type == MSG_TUNNEL_START
        await recv_msg(h2_r)  # drain assign

        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "auto"})
        m_type, p = await recv_msg(v_r)
        assert m_type == MSG_TUNNEL_START
        await recv_msg(h2_r)  # drain assign

        await send_msg(v_w, MSG_TUNNEL_REQUEST, {"target_host": "WrongName"})
        m_type, p = await recv_msg(v_r)
        assert m_type == MSG_TUNNEL_ERROR
        assert "Office-PC" in p["message"]

        h2_w.close()
        v_w.close()
        await h2_w.wait_closed()
        await v_w.wait_closed()
    finally:
        await server.stop()
