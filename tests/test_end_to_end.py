"""Full End-to-End integration tests for RdpNet tunnel pipeline."""

import asyncio
import pytest

from client.rdp_agent import RdpAgent
from server.models import NetworkRegistry
from server.relay import RelayServer


@pytest.mark.asyncio
async def test_full_rdp_tunnel_pipeline(tmp_path):
    storage = str(tmp_path / "test_e2e.json")
    registry = NetworkRegistry(storage)
    net = registry.create_network("E2ENetwork", "secret_e2e_key")

    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    # 1. Start Mock RDP Server (simulating local Windows RDP on Host)
    mock_rdp_received = bytearray()
    async def mock_rdp_handler(reader, writer):
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            mock_rdp_received.extend(chunk)
            writer.write(b"RDP_SERVER_ACK:" + chunk)
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    mock_rdp = await asyncio.start_server(mock_rdp_handler, "127.0.0.1", 0)
    mock_rdp_port = mock_rdp.sockets[0].getsockname()[1]

    # 2. Start Host Agent pointing to Mock RDP
    host_agent = RdpAgent(
        server_host="127.0.0.1",
        server_port=relay.port,
        network_id=net.id,
        auth_token="secret_e2e_key",
        role="host",
        node_name="Main-Office-PC",
        rdp_port=mock_rdp_port,
        heartbeat_interval=1.0,
    )
    host_task = asyncio.create_task(host_agent.start())

    # 3. Start Viewer Agent listening on dynamic local port
    viewer_agent = RdpAgent(
        server_host="127.0.0.1",
        server_port=relay.port,
        network_id=net.id,
        auth_token="secret_e2e_key",
        role="viewer",
        node_name="Laptop-Viewer",
        target_host="Main-Office-PC",
        local_port=0,  # dynamic port
        heartbeat_interval=1.0,
    )
    viewer_task = asyncio.create_task(viewer_agent.start())

    try:
        # Wait for both agents to connect & register
        await asyncio.sleep(0.15)
        assert viewer_agent.local_port > 0

        # 4. Simulate mstsc.exe connecting to Viewer Agent's local port
        mstsc_r, mstsc_w = await asyncio.open_connection("127.0.0.1", viewer_agent.local_port)

        test_payload = b"CRACKING_TLS_RDP_INITIAL_CONNECTION_REQUEST"
        mstsc_w.write(test_payload)
        await mstsc_w.drain()

        # Read response through the entire pipeline:
        # mstsc -> Viewer Agent -> Relay -> Host Agent -> Mock RDP -> Host Agent -> Relay -> Viewer Agent -> mstsc
        reply = await asyncio.wait_for(mstsc_r.read(1024), timeout=4.0)
        assert reply == b"RDP_SERVER_ACK:" + test_payload
        assert mock_rdp_received == test_payload

        # Send second frame to verify continuous bidirectional streaming
        test_payload_2 = b"SECOND_RDP_GRAPHICS_UPDATE_FRAME"
        mstsc_w.write(test_payload_2)
        await mstsc_w.drain()

        reply_2 = await asyncio.wait_for(mstsc_r.read(1024), timeout=4.0)
        assert reply_2 == b"RDP_SERVER_ACK:" + test_payload_2

        # 5. Close client connection
        mstsc_w.close()
        await mstsc_w.wait_closed()
        await asyncio.sleep(0.1)
    finally:
        await viewer_agent.stop()
        await host_agent.stop()
        viewer_task.cancel()
        host_task.cancel()
        for t in (viewer_task, host_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        mock_rdp.close()
        await mock_rdp.wait_closed()
        await relay.stop()


@pytest.mark.asyncio
async def test_viewer_auto_target_single_host(tmp_path):
    storage = str(tmp_path / "test_auto_target.json")
    registry = NetworkRegistry(storage)
    net = registry.create_network("AutoNet", "key_auto")

    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    # Mock RDP
    async def mock_rdp_handler(r, w):
        data = await r.read(1024)
        w.write(b"SINGLE_HOST_ACK:" + data)
        await w.drain()
        w.close()
        await w.wait_closed()

    mock_rdp = await asyncio.start_server(mock_rdp_handler, "127.0.0.1", 0)
    mock_rdp_port = mock_rdp.sockets[0].getsockname()[1]

    # Host Agent
    host_agent = RdpAgent(
        server_host="127.0.0.1",
        server_port=relay.port,
        network_id=net.id,
        auth_token="key_auto",
        role="host",
        node_name="Sole-Host",
        rdp_port=mock_rdp_port,
    )
    host_task = asyncio.create_task(host_agent.start())

    # Viewer Agent without target_host specified (None)
    viewer_agent = RdpAgent(
        server_host="127.0.0.1",
        server_port=relay.port,
        network_id=net.id,
        auth_token="key_auto",
        role="viewer",
        node_name="Auto-Viewer",
        target_host=None,  # No target host specified
        local_port=0,
    )
    viewer_task = asyncio.create_task(viewer_agent.start())

    try:
        await asyncio.sleep(0.15)
        # Connect mstsc
        c_r, c_w = await asyncio.open_connection("127.0.0.1", viewer_agent.local_port)
        c_w.write(b"PING")
        await c_w.drain()

        resp = await asyncio.wait_for(c_r.read(1024), timeout=4.0)
        assert resp == b"SINGLE_HOST_ACK:PING"
        c_w.close()
        await c_w.wait_closed()
    finally:
        await viewer_agent.stop()
        await host_agent.stop()
        viewer_task.cancel()
        host_task.cancel()
        for t in (viewer_task, host_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        mock_rdp.close()
        await mock_rdp.wait_closed()
        await relay.stop()
