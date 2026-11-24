# RdpNet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build RdpNet, a lightweight, driverless, multi-tenant reverse RDP tunnel connecting remote Windows machines over the internet via an Ubuntu VPS, featuring network isolation, a web management dashboard, and a native Windows agent.

**Architecture:** An Ubuntu VPS hosts a high-performance Python/Linux async TCP relay core (`:8443`) and an embedded Web management dashboard (`:8080`). Remote Windows machines run a native standalone agent (`rdp-agent.exe`) establishing outbound TCP connections to the relay in either Host mode (bridging to local RDP port 3389) or Viewer mode (listening on local port 33890 for `mstsc.exe`). Communication uses a binary framing protocol with HMAC-authenticated network tokens and dedicated per-session wire-speed data sockets.

**Tech Stack:**
- VPS Server: Python 3.10+ async I/O (`asyncio`, standard library only, zero heavy external packages), HTML5/CSS3/Vanilla JS for Web UI.
- Windows Agent: Native C++ / WinSock2 (or FASM x64) compiled to a single standalone `.exe` (< 100 KB, zero external DLLs).
- Testing: `pytest` and Python socket test harnesses.

**Spec:** [rdpnet-design.md](file:///C:/Users/progo/.gemini/antigravity/scratch/rdp-tunnel/docs/superpowers/specs/2026-11-24-rdp-tunnel-design.md)

## Global Constraints

- **Zero-Driver, Zero-Admin:** The Windows agent must run completely in user mode without requiring Windows Administrator privileges or virtual network drivers (`wintun.sys`/TAP).
- **Binary Footprint:** The compiled `rdp-agent.exe` must be under 100 KB and depend only on standard Windows system libraries (`ws2_32.dll`, `kernel32.dll`, `user32.dll`, `advapi32.dll`).
- **Network Isolation:** Sessions and node discovery must be strictly segregated by `network_id` and verified by `auth_token`; cross-network discovery or connection must be rejected at the relay core.
- **Port Defaults:** Control & Data relay on `8443`, Web Dashboard on `8080`, local RDP service on `3389`, Viewer local listener on `33890`.

## Review Focus

1. **Viewer targets an offline or non-existent Host:** Relay immediately responds with `MSG_TUNNEL_ERROR {error_code: "HOST_OFFLINE"}` so the Viewer client resets the local socket and does not leave `mstsc.exe` hanging indefinitely.
2. **Invalid or mismatched `auth_token` during handshake:** Relay instantly closes the connection with an error frame and does not leak registered network existence or node names.
3. **Abrupt network loss on Host or Viewer during active RDP session:** Relay detects socket EOF/reset, closes the paired peer socket immediately, and frees session descriptors without resource leaks.
4. **Heartbeat timeout handling:** Relay drops unresponsive agent control connections after 45 seconds of silence, updating the node state to `offline` in the Web UI.
5. **Multiple Hosts in the same network:** Viewer specifying `-target <HostName>` routes exclusively to the requested Host; if no target is specified in a single-host network, it automatically resolves to the sole active Host.

---

### Task 1: Protocol Framing, Network Data Model & Registry

**Files:**
- Create: `server/protocol.py`
- Create: `server/models.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: Python 3 standard library (`struct`, `json`, `dataclasses`, `secrets`, `hashlib`)
- Produces:
  - `encode_frame(msg_type: int, payload: dict) -> bytes`
  - `decode_frame(buffer: bytes) -> tuple[int, dict, int]` (returns msg_type, payload, bytes_consumed)
  - `Network(id: str, name: str, auth_token: str, created_at: float)`
  - `Node(id: str, network_id: str, name: str, role: str, remote_ip: str, last_seen: float)`
  - `NetworkRegistry`: `create_network()`, `get_network()`, `delete_network()`, `register_node()`, `remove_node()`, `get_nodes_in_network()`

- [ ] **Step 1: Write the failing test for binary framing and registry isolation**

```python
# tests/test_protocol.py
import pytest
from server.protocol import encode_frame, decode_frame, MSG_HELLO, MSG_WELCOME, MAGIC
from server.models import NetworkRegistry, Network, Node

def test_frame_encoding_and_decoding():
    payload = {"network_id": "net_alpha", "role": "host", "name": "PC-1"}
    frame = encode_frame(MSG_HELLO, payload)
    assert frame[:2] == MAGIC
    assert frame[2] == MSG_HELLO
    
    msg_type, decoded_payload, consumed = decode_frame(frame)
    assert msg_type == MSG_HELLO
    assert decoded_payload == payload
    assert consumed == len(frame)

def test_network_isolation_and_auth():
    reg = NetworkRegistry()
    net = reg.create_network("Office", "secret_pass")
    
    assert reg.authenticate("invalid_id", "secret_pass") is False
    assert reg.authenticate(net.id, "wrong_pass") is False
    assert reg.authenticate(net.id, "secret_pass") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_protocol.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Implement `server/protocol.py` and `server/models.py`**

Implement `encode_frame` using `struct.pack('>2sBH', b'RN', msg_type, len(json_bytes)) + json_bytes` and buffer decoding with bounds checking. Implement thread-safe `NetworkRegistry` supporting multi-tenant storage in `networks.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/protocol.py server/models.py tests/test_protocol.py
git commit -m "feat(protocol): implement binary framing and network registry with tenant isolation"
```

---

### Task 2: VPS Relay Core (Control Plane & Wire-Speed Socket Pairing)

**Files:**
- Create: `server/relay.py`
- Test: `tests/test_relay.py`

**Interfaces:**
- Consumes: `server.protocol.*`, `server.models.NetworkRegistry`
- Produces:
  - `RelayServer(host: str, port: int, registry: NetworkRegistry)`
  - Methods: `start()`, `stop()`, `handle_control_client()`, `handle_data_client()`, `dispatch_tunnel()`

- [ ] **Step 1: Write the failing test for control handshake and data pairing**

```python
# tests/test_relay.py
import asyncio
import pytest
from server.protocol import encode_frame, decode_frame, MSG_HELLO, MSG_WELCOME, MSG_TUNNEL_REQUEST, MSG_TUNNEL_ASSIGN, MSG_TUNNEL_START
from server.models import NetworkRegistry
from server.relay import RelayServer

@pytest.mark.asyncio
async def test_relay_control_and_tunnel_pairing():
    registry = NetworkRegistry()
    net = registry.create_network("TestNet", "pass123")
    relay = RelayServer("127.0.0.1", 18443, registry)
    await relay.start()
    try:
        # 1. Connect Host
        h_r, h_w = await asyncio.open_connection("127.0.0.1", 18443)
        h_w.write(encode_frame(MSG_HELLO, {"network_id": net.id, "auth_token": "pass123", "role": "host", "name": "Host-1"}))
        await h_w.drain()
        resp = await h_r.read(1024)
        m_type, p, _ = decode_frame(resp)
        assert m_type == MSG_WELCOME and p["status"] == "ok"
        
        # 2. Connect Viewer
        v_r, v_w = await asyncio.open_connection("127.0.0.1", 18443)
        v_w.write(encode_frame(MSG_HELLO, {"network_id": net.id, "auth_token": "pass123", "role": "viewer", "name": "Viewer-1"}))
        await v_w.drain()
        resp = await v_r.read(1024)
        m_type, p, _ = decode_frame(resp)
        assert m_type == MSG_WELCOME and p["status"] == "ok"
        
        # 3. Request Tunnel to Host-1
        v_w.write(encode_frame(MSG_TUNNEL_REQUEST, {"target_host": "Host-1"}))
        await v_w.drain()
        
        # Host receives MSG_TUNNEL_ASSIGN
        h_msg = await h_r.read(1024)
        m_type, h_payload, _ = decode_frame(h_msg)
        assert m_type == MSG_TUNNEL_ASSIGN
        token = h_payload["session_token"]
        
        # Viewer receives MSG_TUNNEL_START
        v_msg = await v_r.read(1024)
        m_type, v_payload, _ = decode_frame(v_msg)
        assert m_type == MSG_TUNNEL_START
        assert v_payload["session_token"] == token
    finally:
        await relay.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_relay.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.relay'`

- [ ] **Step 3: Implement `server/relay.py`**

Implement `RelayServer` using `asyncio.start_server`. Control connections authenticate and keep state in `self.active_hosts[network_id][host_name]` and `self.active_viewers[network_id]`. Implement dedicated data socket pairing using `SESSION_AUTH <token>` preamble, followed by continuous bidirectional `asyncio.copy_bytes` / `reader.read` to pipe streams with zero framing overhead.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_relay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/relay.py tests/test_relay.py
git commit -m "feat(relay): implement async control plane and dedicated wire-speed data pairing"
```

---

### Task 3: Web Management Dashboard & REST API

**Files:**
- Create: `server/web_server.py`
- Create: `server/static/index.html`
- Create: `server/static/app.js`
- Create: `server/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `server.models.NetworkRegistry`, `server.relay.RelayServer`
- Produces:
  - `start_web_server(host: str, port: int, registry: NetworkRegistry, relay: RelayServer)`
  - REST endpoints:
    - `GET /api/networks` -> List networks & host counts
    - `POST /api/networks` -> Create network `{name: "..."}`
    - `DELETE /api/networks/<id>` -> Delete network
    - `GET /api/nodes` -> Real-time online nodes with latency, IP, status
    - `GET /api/connect-snippet?net=<id>&host=<name>` -> CLI string & `rdp-agent.ini`

- [ ] **Step 1: Write the failing test for Web REST API**

```python
# tests/test_web.py
import pytest
from urllib.request import urlopen, Request
import json
from server.models import NetworkRegistry
from server.relay import RelayServer
from server.web_server import WebServer

@pytest.mark.asyncio
async def test_web_api_endpoints():
    registry = NetworkRegistry()
    relay = RelayServer("127.0.0.1", 18444, registry)
    web = WebServer("127.0.0.1", 18080, registry, relay)
    await web.start()
    try:
        # Create network via API
        req = Request("http://127.0.0.1:18080/api/networks", data=json.dumps({"name": "HQ"}).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["name"] == "HQ"
            net_id = data["id"]
            
        # Get networks list
        with urlopen("http://127.0.0.1:18080/api/networks") as resp:
            data = json.loads(resp.read().decode())
            assert len(data) == 1
            assert data[0]["id"] == net_id
    finally:
        await web.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.web_server'`

- [ ] **Step 3: Implement Web Server and HTML/CSS/JS Dashboard**

Implement lightweight asynchronous HTTP server using standard `asyncio` or `http.server`. Serve modern responsive dark/light dashboard in `server/static/` with live polling for node states, modal dialogs for network creation, and one-click copyable CLI / INI download.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/web_server.py server/static/ tests/test_web.py
git commit -m "feat(web): add web management dashboard and REST API for networks and nodes"
```

---

### Task 4: Native Windows Client (`rdp-agent`) - Control Plane & Host Mode

**Files:**
- Create: `client/protocol.h`
- Create: `client/protocol.cpp`
- Create: `client/host.cpp`
- Create: `client/build.py` (compilation harness)
- Test: `tests/test_agent_host.py`

**Interfaces:**
- Consumes: WinSock2 (`ws2_32.lib`), Windows Sockets API
- Produces:
  - `rdp-agent.exe -server <ip:port> -net <id> -key <token> -host -name <HostName>`
  - Host loop: registers with VPS, responds to heartbeats, accepts `MSG_TUNNEL_ASSIGN`, opens data socket to VPS, bridges to `127.0.0.1:3389`.

- [ ] **Step 1: Write the integration test for Host Mode**

```python
# tests/test_agent_host.py
import asyncio
import subprocess
import pytest
from server.models import NetworkRegistry
from server.relay import RelayServer

@pytest.mark.asyncio
async def test_host_agent_connection(tmp_path):
    registry = NetworkRegistry()
    net = registry.create_network("TestHostNet", "token_abc")
    relay = RelayServer("127.0.0.1", 18445, registry)
    await relay.start()
    
    # Launch agent in host mode
    proc = subprocess.Popen([
        "client/bin/rdp-agent.exe",
        "-server", "127.0.0.1:18445",
        "-net", net.id,
        "-key", "token_abc",
        "-host",
        "-name", "WinTestHost"
    ])
    try:
        await asyncio.sleep(1.0)
        nodes = registry.get_nodes_in_network(net.id)
        assert any(n.name == "WinTestHost" and n.role == "host" for n in nodes)
    finally:
        proc.terminate()
        await relay.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_host.py`
Expected: FAIL (binary `rdp-agent.exe` does not exist yet)

- [ ] **Step 3: Implement client protocol, host logic, and build harness**

Implement WinSock2 client in C++ / Win32. Implement socket non-blocking multiplexer or thread-per-stream pipe bridging VPS data socket to `127.0.0.1:3389`. Compile cleanly with MSVC / MinGW / Clang / FASM to a single `.exe` under 100 KB.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_host.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add client/ tests/test_agent_host.py
git commit -m "feat(client): implement Windows native rdp-agent host mode and protocol engine"
```

---

### Task 5: Native Windows Client (`rdp-agent`) - Viewer Mode & Full End-to-End Tunneling

**Files:**
- Create: `client/viewer.cpp`
- Create: `client/main.cpp`
- Create: `client/config.cpp`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `client/protocol.h`, `client/host.cpp`
- Produces:
  - Viewer mode (`-viewer`, `-target <HostName>`, `-local <Port>`)
  - INI file reader (`rdp-agent.ini`)
  - Complete standalone `rdp-agent.exe` executable

- [ ] **Step 1: Write full End-to-End RDP simulation test**

```python
# tests/test_end_to_end.py
import asyncio
import subprocess
import pytest
from server.models import NetworkRegistry
from server.relay import RelayServer

@pytest.mark.asyncio
async def test_full_rdp_tunnel_pipeline():
    registry = NetworkRegistry()
    net = registry.create_network("E2ENet", "secret_e2e")
    relay = RelayServer("127.0.0.1", 18446, registry)
    await relay.start()
    
    # 1. Start Mock RDP Server on 13389
    rdp_received = bytearray()
    async def mock_rdp_handler(reader, writer):
        data = await reader.read(1024)
        rdp_received.extend(data)
        writer.write(b"RDP_ACK:" + data)
        await writer.drain()
        writer.close()
    
    mock_rdp = await asyncio.start_server(mock_rdp_handler, "127.0.0.1", 13389)
    
    # 2. Start Host Agent pointing to mock RDP port
    host_proc = subprocess.Popen([
        "client/bin/rdp-agent.exe",
        "-server", "127.0.0.1:18446",
        "-net", net.id,
        "-key", "secret_e2e",
        "-host",
        "-name", "HostA",
        "-rdp-port", "13389"
    ])
    
    # 3. Start Viewer Agent targeting HostA on local port 33891
    viewer_proc = subprocess.Popen([
        "client/bin/rdp-agent.exe",
        "-server", "127.0.0.1:18446",
        "-net", net.id,
        "-key", "secret_e2e",
        "-viewer",
        "-target", "HostA",
        "-local", "33891"
    ])
    
    try:
        await asyncio.sleep(1.0)
        # 4. Connect simulated mstsc client to local 33891
        c_r, c_w = await asyncio.open_connection("127.0.0.1", 33891)
        test_payload = b"RDP_NEGOTIATION_REQUEST_FRAME_DATA"
        c_w.write(test_payload)
        await c_w.drain()
        
        reply = await asyncio.wait_for(c_r.read(1024), timeout=5.0)
        assert reply == b"RDP_ACK:" + test_payload
        assert rdp_received == test_payload
    finally:
        host_proc.terminate()
        viewer_proc.terminate()
        mock_rdp.close()
        await relay.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_end_to_end.py`
Expected: FAIL

- [ ] **Step 3: Implement Viewer mode, CLI parser, and INI configuration loader**

Implement Viewer local listening loop on `127.0.0.1:<local_port>`, target selection logic (`-target <name>` or auto-resolve if 1 host), and INI parsing. Recompile `rdp-agent.exe`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_end_to_end.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add client/ tests/test_end_to_end.py
git commit -m "feat(client): complete viewer mode, INI configuration, and end-to-end tunneling"
```

---

### Task 6: VPS Turnkey Installer & Production Packaging

**Files:**
- Create: `scripts/install_vps.sh`
- Create: `scripts/rdpnet.service`
- Create: `README.md`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `server/` complete package
- Produces:
  - Turnkey `install_vps.sh` bash script for Ubuntu 20.04/22.04/24.04 LTS
  - Systemd service configuration
  - Ready-to-deploy archive and distribution instructions

- [ ] **Step 1: Write verification test for installation scripts and systemd syntax**

```python
# tests/test_packaging.py
import os
import subprocess

def test_packaging_assets_exist():
    assert os.path.exists("scripts/install_vps.sh")
    assert os.path.exists("scripts/rdpnet.service")
    assert os.path.exists("README.md")
    
    with open("scripts/install_vps.sh", "r", encoding="utf-8") as f:
        content = f.read()
        assert "systemctl enable rdpnet" in content
        assert "ufw allow 8443/tcp" in content
        assert "ufw allow 8080/tcp" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_packaging.py`
Expected: FAIL

- [ ] **Step 3: Implement `install_vps.sh`, `rdpnet.service`, and documentation**

Create automated installation script that checks Python 3, copies files to `/opt/rdpnet`, installs systemd service, opens ports, and generates admin password. Write clear `README.md` with visual diagrams and quickstart steps.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_packaging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ README.md tests/test_packaging.py
git commit -m "chore(deploy): add Ubuntu VPS turnkey installer and systemd service"
```
