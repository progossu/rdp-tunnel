"""RdpNet Web Management Dashboard and REST API Server with Session Authentication."""

import asyncio
import json
import logging
import mimetypes
import os
import secrets
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from server.models import NetworkRegistry
from server.relay import RelayServer

logger = logging.getLogger(__name__)


class WebServer:
    """Asynchronous HTTP Web Server serving REST API and Static Management UI with Session Authentication."""

    def __init__(
        self,
        host: str,
        port: int,
        registry: NetworkRegistry,
        relay: RelayServer,
        admin_password: Optional[str] = None,
        static_dir: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.registry = registry
        self.relay = relay
        self.admin_password = admin_password
        self.static_dir = static_dir or os.path.join(os.path.dirname(__file__), "static")

        self._server: Optional[asyncio.Server] = None
        self._active_connections: set = set()
        self._sessions: Dict[str, float] = {}  # token -> login timestamp

    async def start(self) -> None:
        """Starts the HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        if self.port == 0 and self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]
        logger.info("WebServer listening on http://%s:%d (auth=%s)", self.host, self.port, bool(self.admin_password))

    async def stop(self) -> None:
        """Stops the HTTP server and terminates active client sockets."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for writer in list(self._active_connections):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._active_connections.clear()
        logger.info("WebServer stopped.")

    def _extract_cookie(self, headers: Dict[str, str], name: str) -> Optional[str]:
        cookie_header = headers.get("cookie", "")
        for item in cookie_header.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() == name:
                    return v.strip()
        return None

    def _is_authenticated(self, headers: Dict[str, str]) -> bool:
        if not self.admin_password:
            # If no password is set on server, allow open access
            return True

        token = self._extract_cookie(headers, "rdpnet_session")
        if not token:
            return False

        # Session token validity (7 days timeout)
        login_time = self._sessions.get(token)
        if login_time is None:
            return False
        if time.time() - login_time > 7 * 86400:
            self._sessions.pop(token, None)
            return False

        return True

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._active_connections.add(writer)
        try:
            # Read HTTP request header
            request_data = b""
            while b"\r\n\r\n" not in request_data and len(request_data) < 65536:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                request_data += chunk

            if not request_data:
                return

            header_part, _, body_prefix = request_data.partition(b"\r\n\r\n")
            lines = header_part.decode("utf-8", errors="replace").split("\r\n")
            if not lines:
                return

            request_line = lines[0].split(" ")
            if len(request_line) < 2:
                return

            method, raw_path = request_line[0], request_line[1]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_length = int(headers.get("content-length", 0))
            body = body_prefix
            if len(body) < content_length:
                remaining = content_length - len(body)
                while remaining > 0:
                    chunk = await reader.read(min(remaining, 4096))
                    if not chunk:
                        break
                    body += chunk
                    remaining -= len(chunk)

            parsed_url = urllib.parse.urlparse(raw_path)
            path = parsed_url.path
            query_params = urllib.parse.parse_qs(parsed_url.query)

            status_code, resp_headers, resp_body = await self._route(
                method, path, query_params, headers, body, writer.get_extra_info("peername")
            )

            status_text = {
                200: "OK",
                201: "Created",
                400: "Bad Request",
                401: "Unauthorized",
                404: "Not Found",
                405: "Method Not Allowed",
            }.get(status_code, "OK")

            response_header = [
                f"HTTP/1.1 {status_code} {status_text}",
                f"Content-Length: {len(resp_body)}",
                "Connection: close",
            ]
            for hk, hv in resp_headers.items():
                response_header.append(f"{hk}: {hv}")
            response_header.append("\r\n")

            writer.write("\r\n".join(response_header).encode("utf-8") + resp_body)
            await writer.drain()

        except Exception as e:
            logger.debug("HTTP handling error: %s", e)
        finally:
            self._active_connections.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _route(
        self,
        method: str,
        path: str,
        query: Dict[str, list],
        headers: Dict[str, str],
        body: bytes,
        peername: Any,
    ) -> Tuple[int, Dict[str, str], bytes]:
        if path.startswith("/api/"):
            return await self._handle_api(method, path, query, headers, body)

        return self._handle_static(path)

    async def _handle_api(
        self,
        method: str,
        path: str,
        query: Dict[str, list],
        headers: Dict[str, str],
        body: bytes,
    ) -> Tuple[int, Dict[str, str], bytes]:
        json_headers = {"Content-Type": "application/json; charset=utf-8"}

        # Public auth endpoints:
        # GET /api/auth-status
        if path == "/api/auth-status" and method == "GET":
            is_auth = self._is_authenticated(headers)
            return 200, json_headers, json.dumps({
                "authenticated": is_auth,
                "auth_required": bool(self.admin_password),
            }).encode("utf-8")

        # POST /api/login
        if path == "/api/login" and method == "POST":
            try:
                data = json.loads(body.decode("utf-8") if body else "{}")
            except Exception:
                return 400, json_headers, json.dumps({"error": "Invalid JSON"}).encode("utf-8")

            password = data.get("password", "")
            if not self.admin_password:
                return 200, json_headers, json.dumps({"status": "ok", "message": "No password configured"}).encode("utf-8")

            if secrets.compare_digest(password, self.admin_password):
                token = secrets.token_hex(24)
                self._sessions[token] = time.time()
                resp_headers = dict(json_headers)
                resp_headers["Set-Cookie"] = f"rdpnet_session={token}; Path=/; HttpOnly; SameSite=Lax"
                return 200, resp_headers, json.dumps({"status": "ok"}).encode("utf-8")

            return 401, json_headers, json.dumps({"error": "Invalid password"}).encode("utf-8")

        # POST /api/logout
        if path == "/api/logout" and method == "POST":
            token = self._extract_cookie(headers, "rdpnet_session")
            if token:
                self._sessions.pop(token, None)
            resp_headers = dict(json_headers)
            resp_headers["Set-Cookie"] = "rdpnet_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            return 200, resp_headers, json.dumps({"status": "ok"}).encode("utf-8")

        # All other API routes require authentication:
        if not self._is_authenticated(headers):
            return 401, json_headers, json.dumps({"error": "Authentication required"}).encode("utf-8")

        # GET /api/networks
        if path == "/api/networks" and method == "GET":
            networks = self.registry.list_networks()
            result = []
            for net in networks:
                hosts_count = len(self.relay.active_hosts.get(net.id, {}))
                viewers_count = len(self.relay.active_viewers.get(net.id, {}))
                result.append({
                    "id": net.id,
                    "name": net.name,
                    "auth_token": net.auth_token,
                    "created_at": net.created_at,
                    "hosts_count": hosts_count,
                    "viewers_count": viewers_count,
                })
            return 200, json_headers, json.dumps(result).encode("utf-8")

        # POST /api/networks
        if path == "/api/networks" and method == "POST":
            try:
                data = json.loads(body.decode("utf-8") if body else "{}")
            except Exception:
                return 400, json_headers, json.dumps({"error": "Invalid JSON"}).encode("utf-8")

            name = data.get("name", "").strip()
            if not name:
                return 400, json_headers, json.dumps({"error": "Network name is required"}).encode("utf-8")

            auth_token = data.get("auth_token", "").strip() or None
            net = self.registry.create_network(name, auth_token)
            return 201, json_headers, json.dumps({
                "id": net.id,
                "name": net.name,
                "auth_token": net.auth_token,
                "created_at": net.created_at,
                "hosts_count": 0,
                "viewers_count": 0,
            }).encode("utf-8")

        # DELETE /api/networks/<id>
        if path.startswith("/api/networks/") and method == "DELETE":
            net_id = path[len("/api/networks/") :].strip()
            deleted = self.registry.delete_network(net_id)
            if deleted:
                self.relay.active_hosts.pop(net_id, None)
                self.relay.active_viewers.pop(net_id, None)
                return 200, json_headers, json.dumps({"status": "ok", "deleted": net_id}).encode("utf-8")
            return 404, json_headers, json.dumps({"error": "Network not found"}).encode("utf-8")

        # GET /api/nodes
        if path == "/api/nodes" and method == "GET":
            nodes = self.registry.get_all_nodes()
            result = []
            for n in nodes:
                net = self.registry.get_network(n.network_id)
                net_name = net.name if net else n.network_id
                result.append({
                    "id": n.id,
                    "name": n.name,
                    "network_id": n.network_id,
                    "network_name": net_name,
                    "role": n.role,
                    "remote_ip": n.remote_ip,
                    "status": n.status,
                    "last_seen": n.last_seen,
                })
            return 200, json_headers, json.dumps(result).encode("utf-8")

        # GET /api/connect-snippet?net=<id>&host=<name>
        if path == "/api/connect-snippet" and method == "GET":
            net_id = query.get("net", [""])[0]
            host_name = query.get("host", [""])[0] or "Work-PC"

            net = self.registry.get_network(net_id)
            if not net:
                return 404, json_headers, json.dumps({"error": "Network not found"}).encode("utf-8")

            server_host = self.relay.host if self.relay.host not in ("0.0.0.0", "127.0.0.1") else "<VPS_IP>"
            server_addr = f"{server_host}:{self.relay.port}"

            host_cmd = f'rdp-agent.exe -server {server_addr} -net {net.id} -key {net.auth_token} -host -name "{host_name}"'
            viewer_cmd = f'rdp-agent.exe -server {server_addr} -net {net.id} -key {net.auth_token} -viewer -target "{host_name}"'

            ini_content = (
                f"[server]\n"
                f"address={server_addr}\n\n"
                f"[network]\n"
                f"id={net.id}\n"
                f"key={net.auth_token}\n\n"
                f"[node]\n"
                f"role=host\n"
                f"name={host_name}\n"
                f"rdp_port=3389\n"
            )

            return 200, json_headers, json.dumps({
                "host_cmd": host_cmd,
                "viewer_cmd": viewer_cmd,
                "ini_content": ini_content,
            }).encode("utf-8")

        return 404, json_headers, json.dumps({"error": "Endpoint not found"}).encode("utf-8")

    def _handle_static(self, path: str) -> Tuple[int, Dict[str, str], bytes]:
        if path == "/" or not path:
            filename = "index.html"
        else:
            filename = path.lstrip("/")

        file_path = os.path.abspath(os.path.join(self.static_dir, filename))
        if not file_path.startswith(os.path.abspath(self.static_dir)):
            return 404, {"Content-Type": "text/plain"}, b"Not Found"

        if not os.path.isfile(file_path):
            return 404, {"Content-Type": "text/plain"}, b"Not Found"

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            if file_path.endswith(".js"):
                mime_type = "application/javascript"
            elif file_path.endswith(".css"):
                mime_type = "text/css"
            elif file_path.endswith(".html"):
                mime_type = "text/html"
            else:
                mime_type = "application/octet-stream"

        with open(file_path, "rb") as f:
            content = f.read()

        return 200, {"Content-Type": f"{mime_type}; charset=utf-8"}, content
