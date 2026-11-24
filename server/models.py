"""RdpNet Data Models and Multi-Tenant Network Registry."""

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class Network:
    id: str
    name: str
    auth_token: str
    created_at: float = field(default_factory=time.time)


@dataclass
class Node:
    id: str
    network_id: str
    name: str
    role: str  # 'host' or 'viewer'
    remote_ip: str
    status: str = "online"
    last_seen: float = field(default_factory=time.time)


class NetworkRegistry:
    """Thread-safe registry for virtual networks and connected nodes."""

    def __init__(self, storage_path: Optional[str] = "networks.json") -> None:
        self.storage_path: Optional[str] = storage_path
        self._lock: threading.RLock = threading.RLock()
        self._networks: Dict[str, Network] = {}
        self._nodes: Dict[str, Node] = {}
        self._load()

    def _load(self) -> None:
        if not self.storage_path or not os.path.exists(self.storage_path):
            return

        with self._lock:
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            net = Network(
                                id=item["id"],
                                name=item["name"],
                                auth_token=item["auth_token"],
                                created_at=item.get("created_at", time.time()),
                            )
                            self._networks[net.id] = net
                    elif isinstance(data, dict):
                        for net_id, item in data.items():
                            net = Network(
                                id=item.get("id", net_id),
                                name=item["name"],
                                auth_token=item["auth_token"],
                                created_at=item.get("created_at", time.time()),
                            )
                            self._networks[net.id] = net
            except Exception:
                # Corrupted or unreadable file: keep empty networks
                pass

    def _save(self) -> None:
        if not self.storage_path:
            return

        dir_name = os.path.dirname(os.path.abspath(self.storage_path))
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        temp_path = f"{self.storage_path}.tmp"
        data = [asdict(net) for net in self._networks.values()]
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.storage_path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

    def create_network(self, name: str, auth_token: Optional[str] = None) -> Network:
        """Creates and stores a new isolated network."""
        with self._lock:
            net_id = f"net_{secrets.token_hex(6)}"
            token = auth_token if auth_token else secrets.token_hex(16)
            net = Network(
                id=net_id,
                name=name,
                auth_token=token,
                created_at=time.time(),
            )
            self._networks[net.id] = net
            self._save()
            return net

    def get_network(self, network_id: str) -> Optional[Network]:
        """Retrieves a network by ID."""
        with self._lock:
            return self._networks.get(network_id)

    def list_networks(self) -> List[Network]:
        """Lists all registered networks."""
        with self._lock:
            return list(self._networks.values())

    def delete_network(self, network_id: str) -> bool:
        """Deletes a network and evicts all connected nodes in it."""
        with self._lock:
            if network_id in self._networks:
                del self._networks[network_id]
                # Purge any nodes belonging to this network
                to_remove = [
                    nid for nid, node in self._nodes.items() if node.network_id == network_id
                ]
                for nid in to_remove:
                    del self._nodes[nid]
                self._save()
                return True
            return False

    def authenticate(self, network_id: str, auth_token: str) -> bool:
        """Validates credentials for a network using constant-time comparison."""
        with self._lock:
            net = self._networks.get(network_id)
            if not net:
                return False
            if not auth_token or not isinstance(auth_token, str):
                return False
            return secrets.compare_digest(net.auth_token, auth_token)

    def register_node(
        self,
        network_id: str,
        name: str,
        role: str,
        remote_ip: str,
        node_id: Optional[str] = None,
    ) -> Node:
        """Registers a new active node or updates an existing one."""
        with self._lock:
            existing_node: Optional[Node] = None
            if node_id and node_id in self._nodes:
                existing_node = self._nodes[node_id]
            else:
                for n in self._nodes.values():
                    if n.network_id == network_id and n.name == name:
                        existing_node = n
                        break

            now = time.time()
            if existing_node:
                existing_node.role = role
                existing_node.remote_ip = remote_ip
                existing_node.status = "online"
                existing_node.last_seen = now
                return existing_node

            nid = node_id or f"node_{secrets.token_hex(6)}"
            node = Node(
                id=nid,
                network_id=network_id,
                name=name,
                role=role,
                remote_ip=remote_ip,
                status="online",
                last_seen=now,
            )
            self._nodes[nid] = node
            return node

    def remove_node(self, node_id: str) -> None:
        """Removes a node from active registration."""
        with self._lock:
            self._nodes.pop(node_id, None)

    def get_nodes_in_network(self, network_id: str) -> List[Node]:
        """Returns all registered nodes within a specific network."""
        with self._lock:
            return [node for node in self._nodes.values() if node.network_id == network_id]

    def get_all_nodes(self) -> List[Node]:
        """Returns all registered nodes across all networks."""
        with self._lock:
            return list(self._nodes.values())
