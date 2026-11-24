# RdpNet: Isolated Multi-Tenant RDP Tunnel with Web Management UI

**Date:** 2026-11-24  
**Status:** Approved by Builder Review / In User Review  
**Target Architecture:** Windows Client (C++ / WinSock2 / ASM) + Ubuntu VPS Server (Relay Core + Web Management Dashboard)

---

## 1. Intent and The Why

The goal of **RdpNet** is to provide seamless, secure Remote Desktop (RDP) connectivity between computers over the internet behind NAT and firewalls, using an Ubuntu VPS as a coordination relay.

Unlike heavy VPN solutions (WireGuard, OpenVPN, ZeroTier) that require administrative privileges, kernel drivers (`wintun.sys`/`tap-windows`), virtual network interfaces, and IP routing configuration, RdpNet is purpose-built **specifically for RDP**:
1. **Zero-Driver, Zero-Admin:** Runs as a standard unprivileged user-mode application on Windows.
2. **100% NAT Traversal:** Both target (Host) and controlling (Viewer) machines establish outbound TCP connections to the VPS, effortlessly traversing strict corporate firewalls, NATs, and cellular connections.
3. **Multi-Tenant Network Isolation:** Users can create multiple completely isolated networks (e.g., "Office", "Home", "Production Servers"). Devices in Network A cannot see, access, or communicate with Network B.
4. **Web Management Dashboard on VPS:** A modern, intuitive browser-based UI to create networks, manage access tokens, inspect connected nodes in real time (status, ping, IP, uptime), and copy one-click launch commands or download configuration files.

---

## 2. Goals, Non-Goals, and Anti-Goals

### Goals
- **Multi-Network Support:** Arbitrary number of isolated virtual networks, each identified by a unique `NetworkID` and secured with a pre-shared authentication key (`AuthToken`).
- **Multi-Host Routing:** Support multiple Hosts per network with explicit `-target <HostName>` addressing (or auto-targeting if only one Host is online).
- **Web UI on Ubuntu VPS:**
  - View real-time status of all networks and connected nodes.
  - Create, modify, and delete networks.
  - Generate connection parameters (ready-to-run CLI commands, `rdp-agent.ini`).
  - Web UI password protection.
- **Native Windows Agent (`rdp-agent.exe`):**
  - Ultra-lightweight native executable (C++ / Win32 WinSock2).
  - Dual-mode:
    - **Host Mode (`-host`)**: Connects to VPS and forwards incoming tunnel sessions to local port 3389 (Windows Remote Desktop).
    - **Viewer Mode (`-viewer` or `-client`)**: Connects to VPS and opens a local port (e.g., `127.0.0.1:33890`) for `mstsc.exe` to connect to.
  - Automatic reconnection upon network interruptions.
  - Background/silent execution option.
- **Performance & Reliability:**
  - Dedicated per-session pairing sockets to avoid head-of-line blocking and achieve wire-speed throughput.
  - Robust state machine handling `HOST_OFFLINE` errors and clean socket closure.

### Non-Goals
- Full Layer-3 IP networking: RdpNet does not route ICMP pings, broadcast packets, or arbitrary network traffic outside of RDP streams.
- Direct P2P hole-punching (STUN/ICE): Relying on the VPS relay guarantees 100% reliability regardless of symmetric NAT or ISP carrier-grade NAT.

### Anti-Goals
- **No kernel-level drivers:** No installation of `wintun.dll`, OpenVPN TAP, or NDIS drivers.
- **No administrative requirements:** Normal user accounts can run the Windows client immediately.
- **No heavy client runtime:** No Electron, .NET runtime dependencies, or multi-megabyte frameworks on the Windows agent.

---

## 3. Architecture and Component Breakdown

```mermaid
flowchart TD
    subgraph Windows_Host["Windows PC 1 (Target / Host: 'Office-PC-1')"]
        RDP_SRV["Windows RDP Service (Port 3389)"]
        AGENT_HOST["rdp-agent.exe -host\nNetwork: 'Office'\nName: 'Office-PC-1'"]
        RDP_SRV <-->|Local TCP 127.0.0.1:3389| AGENT_HOST
    end

    subgraph Ubuntu_VPS["Ubuntu VPS (Public IP: X.X.X.X)"]
        RELAY_CTL["Relay Control Server (:8443)\n- Network Isolation & Auth\n- Node Registry & Heartbeats\n- Session Dispatcher"]
        RELAY_DATA["Relay Data Bridge (:8444 or multiplexed)\n- High-Speed Socket Pairing\n- Zero-Copy Wire Streaming"]
        WEB_UI["RdpNet Web Dashboard (:8080)\n- Networks & Devices View\n- One-Click Connect Commands\n- Config Generator"]
        DB[("Network Registry\n(JSON file / SQLite)")]
        
        WEB_UI <--> DB
        RELAY_CTL <--> DB
        RELAY_CTL --> RELAY_DATA
    end

    subgraph Windows_Viewer["Windows PC 2 (Client / Viewer)"]
        MSTSC["Remote Desktop (mstsc.exe)\nConnect to: 127.0.0.1:33890"]
        AGENT_VIEW["rdp-agent.exe -viewer\nNetwork: 'Office'\nTarget: 'Office-PC-1'"]
        MSTSC <-->|Local TCP 127.0.0.1:33890| AGENT_VIEW
    end

    AGENT_HOST == 1. Outbound Control TCP ==> RELAY_CTL
    AGENT_VIEW == 1. Outbound Control TCP ==> RELAY_CTL
    
    AGENT_HOST -. 2. Dedicated Data TCP (Token) .-> RELAY_DATA
    AGENT_VIEW -. 2. Dedicated Data TCP (Token) .-> RELAY_DATA
    
    Admin["Administrator Browser"] == HTTP/HTTPS :8080 ==> WEB_UI
```

---

## 4. Relay Protocol & Lifecycle State Machine

### 4.1 Connection Architecture: Control Socket + Dedicated Data Sockets
To ensure maximum RDP responsiveness without head-of-line blocking:
1. **Control Connection:** Each agent maintains one persistent TCP connection to `VPS:8443` for authentication, registration, heartbeat pings, and session signaling.
2. **Data Connection:** When a Viewer requests a connection, the relay generates a unique 16-byte `SessionToken`. Both the Viewer and the Host establish a dedicated TCP connection to the relay passing this token. The relay pairs the two sockets and pipes the raw TCP stream at wire speed.

### 4.2 Control Packet Header
```
+---------------+---------------+--------------------+------------------------+
| Magic (2B)    | MsgType (1B)  | PayloadLen (2B)    | Payload (JSON format)  |
| 0x52 0x4E     | (uint8)       | Big-Endian uint16  | UTF-8 JSON payload     |
+---------------+---------------+--------------------+------------------------+
```

- **Magic:** `0x52 0x4E` ('RN')
- **MsgType Values:**
  - `0x01` `MSG_HELLO` (Agent -> Server):
    Payload: `{"network_id": "...", "auth_token": "...", "role": "host|viewer", "node_name": "..."}`
  - `0x02` `MSG_WELCOME` (Server -> Agent):
    Payload: `{"status": "ok|error", "error_msg": "", "node_id": "..."}`
  - `0x03` `MSG_HEARTBEAT` (Agent <-> Server):
    Payload: `{}`
  - `0x10` `MSG_TUNNEL_REQUEST` (Viewer -> Server):
    Payload: `{"target_host": "Office-PC-1"}` (or empty if single host)
  - `0x11` `MSG_TUNNEL_ASSIGN` (Server -> Host):
    Payload: `{"session_token": "<hex16>", "viewer_name": "..."}`
  - `0x12` `MSG_TUNNEL_START` (Server -> Viewer):
    Payload: `{"status": "ok", "session_token": "<hex16>"}`
  - `0x13` `MSG_TUNNEL_ERROR` (Server -> Viewer):
    Payload: `{"error_code": "HOST_OFFLINE|HOST_NOT_FOUND|AUTH_FAILED", "message": "..."}`

### 4.3 End-to-End Tunnel Lifecycle

1. **Host Registration:**
   - Host connects to `VPS:8443` -> sends `MSG_HELLO {role: "host", network_id: "office", node_name: "PC-1"}`.
   - VPS checks secret -> replies `MSG_WELCOME {status: "ok"}`.
   - Host sends periodic `MSG_HEARTBEAT` every 15 seconds.

2. **Viewer Launch:**
   - Viewer connects to `VPS:8443` -> sends `MSG_HELLO {role: "viewer", network_id: "office"}`.
   - Viewer starts listening on `127.0.0.1:33890`.

3. **Session Initiation:**
   - User opens `mstsc.exe 127.0.0.1:33890`.
   - Viewer agent accepts local client socket and sends `MSG_TUNNEL_REQUEST {target_host: "PC-1"}` to VPS.
   - **Case A: Host is offline:** VPS replies `MSG_TUNNEL_ERROR {error_code: "HOST_OFFLINE"}` -> Viewer closes local socket.
   - **Case B: Host is online:**
     - VPS generates `SessionToken = 16-byte random hex`.
     - VPS sends `MSG_TUNNEL_ASSIGN {session_token}` to Host.
     - VPS sends `MSG_TUNNEL_START {status: "ok", session_token}` to Viewer.
     - Both Host and Viewer open a new TCP connection to VPS data port with payload `SESSION_AUTH <token>`.
     - Host connects to local `127.0.0.1:3389` (Windows RDP).
     - VPS pairs the two sockets and relays traffic directly.
     - When `mstsc` disconnects, sockets close cleanly with zero resource leaks.

---

## 5. Security & Isolation Model

1. **Network Namespace Isolation:**
   - All networks exist in strictly isolated namespaces in the relay server.
   - An agent cannot discover or connect to any node outside its authenticated `network_id`.
   - Wrong `auth_token` immediately drops connection.
2. **Encryption:**
   - **RDP Traffic:** Windows RDP uses TLS 1.2/1.3 and CredSSP (Network Level Authentication) natively end-to-end. All credentials, keystrokes, and screen frames are strongly encrypted by Microsoft's TLS subsystem before ever leaving the local machine.
   - **Tunnel Authentication:** Control channel handshakes are authenticated with SHA-256 HMAC tokens.
   - **Transport Encryption:** Support optional ChaCha20-Poly1305 stream cipher keyed by `auth_token` for double-encryption across the relay.

---

## 6. Web Management Interface (Ubuntu VPS)

The Web UI is served directly on `:8080` (protected by admin password):

1. **Networks Tab:**
   - Table of all virtual networks (Name, ID, Secret Key, Online Hosts, Online Viewers).
   - "New Network" button: creates a network with a custom name and auto-generated secure token.
   - "Delete Network" button.
2. **Live Nodes / Devices Tab:**
   - Real-time monitor of connected machines:
     - Network
     - Node Name (e.g. `Office-PC-1`, `Work-Laptop`)
     - Role (Host / Viewer)
     - Remote IP
     - Latency (ms)
     - Connected time & status (Online / Idle)
3. **One-Click Connection Generator:**
   - For any Host in the list, clicking **"Connect via RDP"** displays:
     - Exact Windows command to run on the Viewer machine:
       ```cmd
       rdp-agent.exe -server <VPS_IP>:8443 -net <NET_ID> -key <KEY> -viewer -target "Office-PC-1"
       ```
     - Button: **"Download rdp-agent.ini"** pre-filled with server, network ID, token, and target.
     - Button: **"Launch mstsc.exe"** (`mstsc /v:127.0.0.1:33890`).

---

## 7. Windows Client (`rdp-agent.exe`) Specification

- **Implementation:** C++ / WinSock2 (or FASM x64).
- **Executable Footprint:** Standalone `.exe` (< 100 KB), zero external DLL dependencies.
- **Command-line Interface:**
  ```
  Usage: rdp-agent.exe [options]
  
  Options:
    -server <ip:port>      VPS Relay address (e.g., 1.2.3.4:8443)
    -net <network_id>      Virtual Network ID
    -key <auth_token>      Network authentication secret
    -host                  Run in Host mode (exposes local RDP 3389)
    -viewer                Run in Viewer mode (listens for mstsc.exe)
    -name <node_name>      Node name (for Host mode, e.g. "Work-PC")
    -target <host_name>    Target Host name (for Viewer mode, optional if only 1 host)
    -local <port>          Local listening port for Viewer (default: 33890)
    -config <path>         Load settings from INI file (default: rdp-agent.ini)
    -silent                Run silently in background without console window
  ```
- **INI Configuration (`rdp-agent.ini`):**
  ```ini
  [server]
  address=1.2.3.4:8443

  [network]
  id=office_net
  key=secret_token_123

  [node]
  role=host
  name=Accounting-PC
  rdp_port=3389
  ```

---

## 8. VPS Deployment & Installation

Provided as a turnkey script `install_vps.sh` for Ubuntu:
- Sets up systemd service `/etc/systemd/system/rdpnet.service`.
- Opens required firewall ports via `ufw allow 8443/tcp` and `ufw allow 8080/tcp`.
- Auto-starts on server reboot.
- Prints the Web UI URL and initial admin login/password.

---

## 9. Builder Review Summary (Calls Made)

1. **Per-Session Socket Architecture:** Decided on dedicated paired data sockets with a one-time 16-byte session token instead of multiplexing over the control socket. This eliminates head-of-line blocking and delivers full wire-speed RDP performance.
2. **Multi-Host Targeting:** Added `-target <HostName>` to the Viewer. If a network has only 1 Host online, `-target` is optional and defaults to the active Host.
3. **Session Lifecycle & Error Codes:** Added `MSG_TUNNEL_ERROR` (`HOST_OFFLINE`, `HOST_NOT_FOUND`) so the Viewer UI immediately alerts the user instead of hanging `mstsc`.
4. **Encryption:** Natively utilizes RDP's end-to-end TLS 1.2/1.3 + CredSSP encryption, with HMAC-SHA256 control plane authentication, avoiding external OpenSSL bloat to strictly maintain the < 100 KB footprint.
