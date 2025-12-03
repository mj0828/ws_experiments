"""WebSocket server with DynamoDB connection registry."""

from websocket_server.handlers import (
    BaseMessageHandler,
    MessageHandlerRegistry,
    V1MessageHandler,
    V2MessageHandler,
)
from websocket_server.models import Connection
from websocket_server.registry import ConnectionRegistry
from websocket_server.server import WebSocketHandler

__version__ = "0.1.0"

__all__ = [
    "Connection",
    "ConnectionRegistry",
    "WebSocketHandler",
    "BaseMessageHandler",
    "MessageHandlerRegistry",
    "V1MessageHandler",
    "V2MessageHandler",
]

