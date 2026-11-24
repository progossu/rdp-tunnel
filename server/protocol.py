"""RdpNet Binary Framing Protocol.

Header format:
  +---------------+---------------+--------------------+------------------------+
  | Magic (2B)    | MsgType (1B)  | PayloadLen (2B)    | Payload (JSON format)  |
  | b'RN'         | uint8         | Big-Endian uint16  | UTF-8 JSON payload     |
  +---------------+---------------+--------------------+------------------------+
"""

import json
import struct
from typing import Any, Dict, Optional, Tuple

MAGIC: bytes = b"RN"

MSG_HELLO: int = 0x01
MSG_WELCOME: int = 0x02
MSG_HEARTBEAT: int = 0x03
MSG_TUNNEL_REQUEST: int = 0x10
MSG_TUNNEL_ASSIGN: int = 0x11
MSG_TUNNEL_START: int = 0x12
MSG_TUNNEL_ERROR: int = 0x13

_HEADER_FORMAT: str = ">2sBH"
HEADER_SIZE: int = struct.calcsize(_HEADER_FORMAT)  # 5 bytes


def encode_frame(msg_type: int, payload: Dict[str, Any]) -> bytes:
    """Encodes a message into binary frame format.

    Args:
        msg_type: Message type identifier (1 byte uint8).
        payload: JSON-serializable dictionary.

    Returns:
        Encoded bytes matching the binary frame format.
    """
    json_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_len = len(json_bytes)
    if payload_len > 0xFFFF:
        raise ValueError(f"Payload size ({payload_len} bytes) exceeds uint16 maximum (65535)")

    header = struct.pack(_HEADER_FORMAT, MAGIC, msg_type, payload_len)
    return header + json_bytes


def decode_frame(buffer: bytes) -> Tuple[Optional[int], Optional[Dict[str, Any]], int]:
    """Decodes a frame from the given buffer.

    Args:
        buffer: Raw incoming byte buffer.

    Returns:
        (msg_type, payload_dict, bytes_consumed)
        If the buffer contains less than a full frame, returns (None, None, 0).

    Raises:
        ValueError: If magic bytes are invalid.
    """
    if len(buffer) < HEADER_SIZE:
        return None, None, 0

    magic = buffer[:2]
    if magic != MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}, expected {MAGIC!r}")

    _, msg_type, payload_len = struct.unpack_from(_HEADER_FORMAT, buffer, 0)
    total_frame_len = HEADER_SIZE + payload_len

    if len(buffer) < total_frame_len:
        return None, None, 0

    payload_bytes = buffer[HEADER_SIZE:total_frame_len]
    payload = json.loads(payload_bytes.decode("utf-8"))
    return msg_type, payload, total_frame_len
