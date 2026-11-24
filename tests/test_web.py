"""Tests for RdpNet Web Management Dashboard and REST API using async client."""

import asyncio
import json
import pytest

from server.models import NetworkRegistry
from server.relay import RelayServer
from server.web_server import WebServer


async def async_http(port: int, method: str, path: str, body: bytes = None, headers: dict = None) -> tuple[int, dict, bytes]:
    """Helper for non-blocking HTTP requests against the asyncio web server."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        req_headers = {"Host": f"127.0.0.1:{port}", "Connection": "close"}
        if headers:
            req_headers.update(headers)
        if body:
            req_headers["Content-Length"] = str(len(body))

        lines = [f"{method} {path} HTTP/1.1"]
        for k, v in req_headers.items():
            lines.append(f"{k}: {v}")
        lines.append("\r\n")
        req_bytes = "\r\n".join(lines).encode("utf-8")
        if body:
            req_bytes += body

        writer.write(req_bytes)
        await writer.drain()

        resp = await reader.read()
        header_part, _, body_part = resp.partition(b"\r\n\r\n")
        header_lines = header_part.decode("utf-8", errors="replace").split("\r\n")
        status_line = header_lines[0].split(" ")
        status_code = int(status_line[1])

        resp_headers = {}
        for h in header_lines[1:]:
            if ":" in h:
                hk, hv = h.split(":", 1)
                resp_headers[hk.strip().lower()] = hv.strip()

        return status_code, resp_headers, body_part
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_web_api_networks_crud(tmp_path):
    storage = str(tmp_path / "test_networks.json")
    registry = NetworkRegistry(storage)
    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    web = WebServer("127.0.0.1", 0, registry, relay)
    await web.start()

    try:
        # 1. Initially empty
        status, _, body = await async_http(web.port, "GET", "/api/networks")
        assert status == 200
        nets = json.loads(body.decode())
        assert len(nets) == 0

        # 2. Create network
        create_data = json.dumps({"name": "HQ Office"}).encode()
        status, _, body = await async_http(
            web.port, "POST", "/api/networks", body=create_data, headers={"Content-Type": "application/json"}
        )
        assert status == 201
        net_info = json.loads(body.decode())
        assert net_info["name"] == "HQ Office"
        net_id = net_info["id"]

        # 3. Verify in list
        status, _, body = await async_http(web.port, "GET", "/api/networks")
        assert status == 200
        nets = json.loads(body.decode())
        assert len(nets) == 1
        assert nets[0]["id"] == net_id
        assert nets[0]["name"] == "HQ Office"

        # 4. Delete network
        status, _, body = await async_http(web.port, "DELETE", f"/api/networks/{net_id}")
        assert status == 200
        del_res = json.loads(body.decode())
        assert del_res["status"] == "ok"

        # 5. Verify empty again
        status, _, body = await async_http(web.port, "GET", "/api/networks")
        assert status == 200
        assert len(json.loads(body.decode())) == 0
    finally:
        await web.stop()
        await relay.stop()


@pytest.mark.asyncio
async def test_web_api_nodes_and_snippets(tmp_path):
    storage = str(tmp_path / "test_nodes.json")
    registry = NetworkRegistry(storage)
    net = registry.create_network("Branch Office", "sec_token_99")
    registry.register_node(net.id, "Accounting-PC", "host", "192.168.1.50")

    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    web = WebServer("127.0.0.1", 0, registry, relay)
    await web.start()

    try:
        # 1. Get nodes list
        status, _, body = await async_http(web.port, "GET", "/api/nodes")
        assert status == 200
        nodes = json.loads(body.decode())
        assert len(nodes) == 1
        assert nodes[0]["name"] == "Accounting-PC"
        assert nodes[0]["network_id"] == net.id

        # 2. Get connection snippets
        status, _, body = await async_http(web.port, "GET", f"/api/connect-snippet?net={net.id}&host=Accounting-PC")
        assert status == 200
        snippet = json.loads(body.decode())
        assert "host_cmd" in snippet
        assert "viewer_cmd" in snippet
        assert "ini_content" in snippet
        assert "Accounting-PC" in snippet["viewer_cmd"]
        assert net.id in snippet["viewer_cmd"]
        assert "[server]" in snippet["ini_content"]
    finally:
        await web.stop()
        await relay.stop()


@pytest.mark.asyncio
async def test_web_static_file_serving(tmp_path):
    storage = str(tmp_path / "test_static.json")
    registry = NetworkRegistry(storage)
    relay = RelayServer("127.0.0.1", 0, registry)
    await relay.start()

    web = WebServer("127.0.0.1", 0, registry, relay)
    await web.start()

    try:
        # Root / -> index.html
        status, headers, body = await async_http(web.port, "GET", "/")
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        assert b"RdpNet" in body

        # CSS
        status, headers, body = await async_http(web.port, "GET", "/style.css")
        assert status == 200
        assert "text/css" in headers.get("content-type", "")

        # JS
        status, headers, body = await async_http(web.port, "GET", "/app.js")
        assert status == 200
        assert "javascript" in headers.get("content-type", "")
    finally:
        await web.stop()
        await relay.stop()


@pytest.mark.asyncio
async def test_web_basic_auth_and_host_autodetect_and_zip_bundle(tmp_path):
    import base64
    import io
    import zipfile

    storage = str(tmp_path / "test_auth_bundle.json")
    registry = NetworkRegistry(storage)
    net = registry.create_network("AuthNet", "secret_key_777")

    relay = RelayServer("0.0.0.0", 0, registry)
    await relay.start()

    web = WebServer("127.0.0.1", 0, registry, relay, admin_password="super_secret_password")
    await web.start()

    try:
        # 1. Unauthenticated request returns 401
        status, headers, _ = await async_http(web.port, "GET", "/api/networks")
        assert status == 401
        assert "Basic" in headers.get("www-authenticate", "")

        # 2. Wrong password returns 401
        bad_creds = base64.b64encode(b"admin:wrong").decode()
        status, _, _ = await async_http(web.port, "GET", "/api/networks", headers={"Authorization": f"Basic {bad_creds}"})
        assert status == 401

        # 3. Valid Basic Auth + custom Host header auto-detects VPS IP
        good_creds = base64.b64encode(b"admin:super_secret_password").decode()
        auth_hdrs = {
            "Authorization": f"Basic {good_creds}",
            "Host": "45.67.89.10:8080",
        }
        status, _, body = await async_http(
            web.port,
            "GET",
            f"/api/connect-snippet?net={net.id}&host=PC-5555&role=host&rdp_port=5555",
            headers=auth_hdrs,
        )
        assert status == 200
        snippet = json.loads(body.decode())
        assert f"45.67.89.10:{relay.port}" in snippet["host_cmd"]
        assert "-rdp-port 5555" in snippet["host_cmd"]
        assert "rdp_port=5555" in snippet["ini_content"]

        # 4. Download Host ZIP bundle
        status, headers, zip_bytes = await async_http(
            web.port,
            "GET",
            f"/api/download-bundle?net={net.id}&role=host&host=PC-5555&rdp_port=auto",
            headers=auth_hdrs,
        )
        assert status == 200
        assert "application/zip" in headers.get("content-type", "")
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            assert "rdp-agent.exe" in names
            assert "rdp-agent.ini" in names
            assert "start_host.bat" in names
            assert "install_autostart.bat" in names
            ini_text = zf.read("rdp-agent.ini").decode("utf-8")
            assert f"address=45.67.89.10:{relay.port}" in ini_text
            assert "rdp_port=auto" in ini_text

        # 5. Download Viewer ZIP bundle
        status, _, zip_bytes = await async_http(
            web.port,
            "GET",
            f"/api/download-bundle?net={net.id}&role=viewer&host=PC-5555",
            headers=auth_hdrs,
        )
        assert status == 200
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            assert "rdp-agent.exe" in names
            assert "rdp-agent.ini" in names
            assert "start_rdp.bat" in names
            ini_text = zf.read("rdp-agent.ini").decode("utf-8")
            assert "role=viewer" in ini_text
            assert "target=PC-5555" in ini_text
            assert "mstsc=1" in ini_text
    finally:
        await web.stop()
        await relay.stop()

