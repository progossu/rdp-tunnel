import os
import json
import tempfile
import threading
import pytest

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
from server.models import NetworkRegistry, Network, Node


# ---------------------------------------------------------------------------
# Protocol Framing Tests
# ---------------------------------------------------------------------------

def test_protocol_constants():
    assert MAGIC == b"RN"
    assert MSG_HELLO == 0x01
    assert MSG_WELCOME == 0x02
    assert MSG_HEARTBEAT == 0x03
    assert MSG_TUNNEL_REQUEST == 0x10
    assert MSG_TUNNEL_ASSIGN == 0x11
    assert MSG_TUNNEL_START == 0x12
    assert MSG_TUNNEL_ERROR == 0x13


def test_frame_encoding_and_decoding():
    payload = {"network_id": "net_alpha", "role": "host", "name": "PC-1"}
    frame = encode_frame(MSG_HELLO, payload)

    # Header checks
    assert frame[:2] == MAGIC
    assert frame[2] == MSG_HELLO
    payload_len = int.from_bytes(frame[3:5], byteorder="big")
    assert payload_len == len(frame) - 5
    assert frame[5:] == json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    # Decoding
    msg_type, decoded_payload, consumed = decode_frame(frame)
    assert msg_type == MSG_HELLO
    assert decoded_payload == payload
    assert consumed == len(frame)


def test_empty_payload_encoding_and_decoding():
    frame = encode_frame(MSG_HEARTBEAT, {})
    assert len(frame) == 5 + len(b"{}")
    msg_type, decoded_payload, consumed = decode_frame(frame)
    assert msg_type == MSG_HEARTBEAT
    assert decoded_payload == {}
    assert consumed == len(frame)


def test_unicode_payload_handling():
    payload = {
        "name": "ПК-Бухгалтерия-1",
        "greeting": "Привет мир 🚀",
        "symbols": "日本語 / Español",
    }
    frame = encode_frame(MSG_WELCOME, payload)
    msg_type, decoded_payload, consumed = decode_frame(frame)
    assert msg_type == MSG_WELCOME
    assert decoded_payload == payload
    assert consumed == len(frame)


def test_decode_incomplete_buffer():
    payload = {"key": "value"}
    full_frame = encode_frame(MSG_HELLO, payload)

    # Empty buffer
    assert decode_frame(b"") == (None, None, 0)

    # Partial header (< 5 bytes)
    assert decode_frame(b"R") == (None, None, 0)
    assert decode_frame(b"RN") == (None, None, 0)
    assert decode_frame(b"RN\x01") == (None, None, 0)
    assert decode_frame(b"RN\x01\x00") == (None, None, 0)

    # Full header but incomplete payload
    header_only = full_frame[:5]
    assert decode_frame(header_only) == (None, None, 0)

    half_frame = full_frame[: len(full_frame) - 2]
    assert decode_frame(half_frame) == (None, None, 0)


def test_decode_invalid_magic():
    bad_data = b"XX\x01\x00\x02{}"
    with pytest.raises(ValueError, match="Invalid magic"):
        decode_frame(bad_data)


def test_decode_stream_multiple_frames():
    p1 = {"step": 1}
    p2 = {"step": 2, "extra": "data"}
    f1 = encode_frame(MSG_TUNNEL_REQUEST, p1)
    f2 = encode_frame(MSG_TUNNEL_ASSIGN, p2)

    stream = f1 + f2

    # First frame
    t1, dec1, consumed1 = decode_frame(stream)
    assert t1 == MSG_TUNNEL_REQUEST
    assert dec1 == p1
    assert consumed1 == len(f1)

    # Second frame from remainder
    stream_rem = stream[consumed1:]
    t2, dec2, consumed2 = decode_frame(stream_rem)
    assert t2 == MSG_TUNNEL_ASSIGN
    assert dec2 == p2
    assert consumed2 == len(f2)


# ---------------------------------------------------------------------------
# Data Models and NetworkRegistry Tests
# ---------------------------------------------------------------------------

def test_models_dataclasses():
    net = Network(id="net_123", name="HQ", auth_token="tok_abc", created_at=1000.0)
    assert net.id == "net_123"
    assert net.name == "HQ"
    assert net.auth_token == "tok_abc"
    assert net.created_at == 1000.0

    node = Node(
        id="node_456",
        network_id="net_123",
        name="Server-1",
        role="host",
        remote_ip="192.168.1.10",
        status="online",
        last_seen=1005.0,
    )
    assert node.id == "node_456"
    assert node.network_id == "net_123"
    assert node.name == "Server-1"
    assert node.role == "host"
    assert node.remote_ip == "192.168.1.10"
    assert node.status == "online"
    assert node.last_seen == 1005.0


def test_network_creation_and_auth():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg = NetworkRegistry(storage_path=storage)

        # Explicit token
        net1 = reg.create_network("Office", "secret_pass")
        assert net1.id.startswith("net_")
        assert net1.name == "Office"
        assert net1.auth_token == "secret_pass"

        # Auto-generated token
        net2 = reg.create_network("Home")
        assert net2.id.startswith("net_")
        assert net2.name == "Home"
        assert len(net2.auth_token) >= 16

        # Authentication tests
        assert reg.authenticate("invalid_id", "secret_pass") is False
        assert reg.authenticate(net1.id, "wrong_pass") is False
        assert reg.authenticate(net1.id, "secret_pass") is True
        assert reg.authenticate(net2.id, net2.auth_token) is True


def test_network_retrieval_and_deletion():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg = NetworkRegistry(storage_path=storage)

        net = reg.create_network("Branch-1", "pass123")
        assert reg.get_network(net.id) == net
        assert reg.get_network("non_existent") is None

        networks = reg.list_networks()
        assert len(networks) == 1
        assert networks[0].id == net.id

        # Register a node before deletion
        reg.register_node(net.id, "PC-Branch", "host", "10.0.0.1")
        assert len(reg.get_nodes_in_network(net.id)) == 1

        # Delete network
        assert reg.delete_network(net.id) is True
        assert reg.get_network(net.id) is None
        assert len(reg.list_networks()) == 0
        # Associated nodes must be purged
        assert len(reg.get_nodes_in_network(net.id)) == 0

        # Deleting again returns False
        assert reg.delete_network(net.id) is False


def test_tenant_isolation():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg = NetworkRegistry(storage_path=storage)

        net_a = reg.create_network("TenantA", "passA")
        net_b = reg.create_network("TenantB", "passB")

        # Cross-tenant auth failure
        assert reg.authenticate(net_a.id, "passB") is False
        assert reg.authenticate(net_b.id, "passA") is False

        # Register nodes in Tenant A
        node_a1 = reg.register_node(net_a.id, "Host-A", "host", "1.1.1.1")
        node_a2 = reg.register_node(net_a.id, "Viewer-A", "viewer", "1.1.1.2")

        # Register node in Tenant B
        node_b1 = reg.register_node(net_b.id, "Host-B", "host", "2.2.2.1")

        nodes_a = reg.get_nodes_in_network(net_a.id)
        assert len(nodes_a) == 2
        assert {n.name for n in nodes_a} == {"Host-A", "Viewer-A"}

        nodes_b = reg.get_nodes_in_network(net_b.id)
        assert len(nodes_b) == 1
        assert nodes_b[0].name == "Host-B"

        # Cross-network node query
        all_nodes = reg.get_all_nodes()
        assert len(all_nodes) == 3


def test_node_registration_and_removal():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg = NetworkRegistry(storage_path=storage)
        net = reg.create_network("Corp", "pass")

        # Register node
        node = reg.register_node(net.id, "Workstation-1", "host", "192.168.0.50")
        assert node.id.startswith("node_")
        assert node.name == "Workstation-1"
        assert node.role == "host"
        assert node.status == "online"

        # Re-register / update existing node
        node_updated = reg.register_node(net.id, "Workstation-1", "host", "192.168.0.51")
        assert node_updated.id == node.id  # Same node ID updated
        assert node_updated.remote_ip == "192.168.0.51"
        assert len(reg.get_nodes_in_network(net.id)) == 1

        # Remove node
        reg.remove_node(node.id)
        assert len(reg.get_nodes_in_network(net.id)) == 0
        assert len(reg.get_all_nodes()) == 0


def test_registry_file_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg1 = NetworkRegistry(storage_path=storage)
        net = reg1.create_network("PersistentNet", "perm_key")

        # Create new registry pointing to the same file
        reg2 = NetworkRegistry(storage_path=storage)
        loaded_net = reg2.get_network(net.id)
        assert loaded_net is not None
        assert loaded_net.name == "PersistentNet"
        assert loaded_net.auth_token == "perm_key"
        assert reg2.authenticate(net.id, "perm_key") is True


def test_registry_thread_safety():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = os.path.join(tmpdir, "networks.json")
        reg = NetworkRegistry(storage_path=storage)
        net = reg.create_network("ThreadNet", "tok")

        def worker(idx):
            for i in range(20):
                node = reg.register_node(net.id, f"Node-{idx}-{i}", "host", f"10.0.{idx}.{i}")
                reg.authenticate(net.id, "tok")
                if i % 2 == 0:
                    reg.remove_node(node.id)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Check registry state is valid
        nodes = reg.get_nodes_in_network(net.id)
        assert isinstance(nodes, list)
