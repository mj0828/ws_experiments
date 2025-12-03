"""WebSocket client package."""

from websocket_client.client import SendResult, SendStatus, WebSocketClient
from websocket_client.config import ClientSettings, get_client_settings
from websocket_client.instance import (
    get_client,
    init_client,
    is_client_initialized,
    reset_client,
    send,
)

__all__ = [
    # Client class
    "WebSocketClient",
    # Config
    "ClientSettings",
    "get_client_settings",
    # Send result types
    "SendResult",
    "SendStatus",
    # Global instance management
    "get_client",
    "init_client",
    "is_client_initialized",
    "reset_client",
    # Convenience function
    "send",
]

