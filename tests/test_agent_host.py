"""Tests for RdpNet Agent Host mode."""

import asyncio
import pytest

from client.rdp_agent import RdpAgent
from server.models import NetworkRegistry
from server.relay import RelayServer
from server.protocol import (
    MSG_HELLO,
    MSG_WELCOME,
    MSG_TUNNEL_REQUEST,
    MSG_TUNNEL_START,
    encode_frame,
    decode_frame,
)


@pytest.mark.asyncio
async def test_host_agent_registration_and_tunnel_bridge(tmp_path):
    storage = str(tmp_path / "test_host_reg.json")
    registry = NetworkRegistry(storage)
    net = registry.create_network("HostTestNet", "token_host_123")

    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    # 1. Mock RDP Server on dynamic port
    mock_rdp_received = bytearray()
    async def mock_rdp_handler(r, w):
        data = await r.read(1024)
        mock_rdp_received.extend(data)
        w.write(b"MOCK_RDP_REPLY:" + data)
        await w.drain()
        w.close()
        await w.wait_closed()

    mock_rdp = await asyncio.start_server(mock_rdp_handler, "127.0.0.1", 0)
    mock_rdp_port = mock_rdp.sockets[0].getsockname()[1]

    # 2. Start Host Agent pointing to Mock RDP
    agent = RdpAgent(
        server_host="127.0.0.1",
        server_port=relay.port,
        network_id=net.id,
        auth_token="token_host_123",
        role="host",
        node_name="Production-Server-1",
        rdp_port=mock_rdp_port,
        heartbeat_interval=0.5,
    )
    agent_task = asyncio.create_task(agent.start())

    try:
        # Give agent 0.1s to connect & register
        await asyncio.sleep(0.1)

        # Verify host registered in relay
        assert "Production-Server-1" in relay.active_hosts[net.id]
        host_active = relay.active_hosts[net.id]["Production-Server-1"]
        assert host_active.role == "host"

        # 3. Connect a real Viewer control socket to request tunnel
        v_ctl_r, v_ctl_w = await asyncio.open_connection("127.0.0.1", relay.port)
        v_ctl_w.write(encode_frame(MSG_HELLO, {
            "network_id": net.id,
            "auth_token": "token_host_123",
            "role": "viewer",
            "name": "ControlViewer",
        }))
        await v_ctl_w.drain()

        # Read welcome
        welcome_raw = await v_ctl_r.read(1024)
        m_type, welcome_p, _ = decode_frame(welcome_raw)
        assert m_type == MSG_WELCOME and welcome_p["status"] == "ok"

        # Send MSG_TUNNEL_REQUEST targeting Production-Server-1
        v_ctl_w.write(encode_frame(MSG_TUNNEL_REQUEST, {"target_host": "Production-Server-1"}))
        await v_ctl_w.drain()

        # Read MSG_TUNNEL_START
        start_raw = await v_ctl_r.read(1024)
        m_type, start_p, _ = decode_frame(start_raw)
        assert m_type == MSG_TUNNEL_START
        session_token = start_p["session_token"]

        # Wait a moment for host agent to receive MSG_TUNNEL_ASSIGN and open data socket
        await asyncio.sleep(0.15)

        # 4. Connect Viewer data socket to relay
        v_data_r, v_data_w = await asyncio.open_connection("127.0.0.1", relay.port)
        v_data_w.write(f"SESSION_AUTH {session_token} viewer\n".encode("utf-8"))
        await v_data_w.drain()

        # Check OK ack
        ack = await v_data_r.readline()
        assert ack.strip() == b"OK"

        # 5. Send RDP test payload from viewer
        test_payload = b"RDP_CLIENT_INITIAL_HANDSHAKE_PACKET"
        v_data_w.write(test_payload)
        await v_data_w.drain()

        # Read reply from mock RDP through relay and host agent
        reply = await asyncio.wait_for(v_data_r.read(1024), timeout=3.0)
        assert reply == b"MOCK_RDP_REPLY:" + test_payload
        assert mock_rdp_received == test_payload

        v_data_w.close()
        await v_data_w.wait_closed()
        v_ctl_w.close()
        await v_ctl_w.wait_closed()
    finally:
        await agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        mock_rdp.close()
        await mock_rdp.wait_closed()
        await relay.stop()
