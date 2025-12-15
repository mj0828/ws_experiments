"""Version-specific message handlers for the WebSocket server."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from websockets.asyncio.server import ServerConnection

from websocket_server.models import Connection
from websocket_server.registry import ConnectionRegistry

logger = logging.getLogger(__name__)


class BaseMessageHandler(ABC):
    """
    Abstract base class for version-specific message handlers.

    Each API version can have its own handler implementation with
    different message formats, validation rules, and business logic.
    """

    version: str = "base"

    def __init__(self, registry: ConnectionRegistry):
        """
        Initialize the message handler.

        Args:
            registry: Connection registry for TTL refresh and other operations.
        """
        self.registry = registry

    @abstractmethod
    async def handle_info(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Handle info request and return connection information."""
        pass

    @abstractmethod
    async def handle_unknown(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Handle unknown message types (default behavior)."""
        pass

    async def handle_non_json(
        self,
        websocket: ServerConnection,
        connection: Connection,
        message: str,
    ) -> None:
        """Handle non-JSON messages. Can be overridden by version handlers."""
        await websocket.send(
            json.dumps(
                {
                    "type": "echo",
                    "connection_id": str(connection.connection_id),
                    "message": message,
                }
            )
        )

    async def handle(
        self,
        websocket: ServerConnection,
        connection: Connection,
        message: str | bytes,
    ) -> None:
        """
        Main entry point for handling messages.

        Routes to appropriate handler based on message type.
        """
        if isinstance(message, bytes):
            message = message.decode("utf-8")

        logger.debug(
            f"[{self.version}] Received message from {connection.connection_id}: "
            f"{message[:100]}"
        )

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            await self.handle_non_json(websocket, connection, message)
            return

        msg_type = data.get("type", "unknown")

        # Route to appropriate handler
        if msg_type == "info":
            await self.handle_info(websocket, connection, data)
        else:
            await self.handle_unknown(websocket, connection, data)


class V1MessageHandler(BaseMessageHandler):
    """
    V1 API message handler.

    Simple response format with minimal metadata.
    """

    version = "v1"

    async def handle_info(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Handle info request with connection details."""
        await websocket.send(
            json.dumps(
                {
                    "type": "info",
                    "connection": connection.to_dict(),
                }
            )
        )

    async def handle_unknown(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Echo back unknown messages."""
        await websocket.send(
            json.dumps(
                {
                    "type": "echo",
                    "connection_id": str(connection.connection_id),
                    "received": data,
                }
            )
        )


class V2MessageHandler(BaseMessageHandler):
    """
    V2 API message handler.

    Enhanced response format with additional metadata:
    - Timestamps on all responses
    - Server version information
    - Request IDs for correlation
    """

    version = "v2"
    server_version = "1.0.0"

    def _build_response(
        self,
        response_type: str,
        connection: Connection,
        data: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build a standardized V2 response with metadata."""
        response = {
            "type": response_type,
            "api_version": "v2",
            "server_version": self.server_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connection_id": str(connection.connection_id),
        }

        if request_id:
            response["request_id"] = request_id

        if data:
            response["data"] = data

        return response

    async def handle_info(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Handle info request with enhanced connection details."""
        request_id = data.get("request_id")

        response = self._build_response(
            "info",
            connection,
            data={
                "connection": connection.to_dict(),
                "capabilities": ["info", "echo"],
            },
            request_id=request_id,
        )

        await websocket.send(json.dumps(response))

    async def handle_unknown(
        self,
        websocket: ServerConnection,
        connection: Connection,
        data: dict[str, Any],
    ) -> None:
        """Echo back unknown messages with V2 response format."""
        request_id = data.get("request_id")
        msg_type = data.get("type", "unknown")

        response = self._build_response(
            "echo",
            connection,
            data={
                "original_type": msg_type,
                "received": data,
            },
            request_id=request_id,
        )

        await websocket.send(json.dumps(response))

    async def handle_non_json(
        self,
        websocket: ServerConnection,
        connection: Connection,
        message: str,
    ) -> None:
        """Handle non-JSON messages with V2 error response."""
        response = self._build_response(
            "error",
            connection,
            data={
                "code": "INVALID_JSON",
                "message": "Message must be valid JSON",
                "received": message[:100],
            },
        )

        await websocket.send(json.dumps(response))


class MessageHandlerRegistry:
    """
    Registry for version-specific message handlers.

    Manages handler instances and provides lookup by version.
    """

    def __init__(self, registry: ConnectionRegistry):
        """
        Initialize the handler registry with all supported versions.

        Args:
            registry: Connection registry to pass to handlers.
        """
        self._handlers: dict[str, BaseMessageHandler] = {}
        self._connection_registry = registry

        # Register built-in handlers
        self.register_handler(V1MessageHandler(registry))
        self.register_handler(V2MessageHandler(registry))

    def register_handler(self, handler: BaseMessageHandler) -> None:
        """
        Register a message handler for a specific version.

        Args:
            handler: The handler instance to register.
        """
        self._handlers[handler.version] = handler
        logger.info(f"Registered message handler for version: {handler.version}")

    def get_handler(self, version: str) -> BaseMessageHandler:
        """
        Get the handler for a specific version.

        Args:
            version: API version string (e.g., "v1", "v2").

        Returns:
            The handler for the specified version.

        Raises:
            KeyError: If no handler is registered for the version.
        """
        if version not in self._handlers:
            raise KeyError(
                f"No handler registered for version '{version}'. "
                f"Available versions: {list(self._handlers.keys())}"
            )
        return self._handlers[version]

    def get_handler_or_default(
        self, version: str, default_version: str = "v1"
    ) -> BaseMessageHandler:
        """
        Get the handler for a version, falling back to default if not found.

        Args:
            version: API version string.
            default_version: Fallback version if requested version not found.

        Returns:
            The handler for the specified or default version.
        """
        return self._handlers.get(version, self._handlers.get(default_version))

    @property
    def supported_versions(self) -> list[str]:
        """Get list of all supported versions."""
        return list(self._handlers.keys())

