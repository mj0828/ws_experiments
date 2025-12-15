"""WebSocket server handler implementation."""

import asyncio
import json
import logging
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from websocket_server.config import Settings, get_settings
from websocket_server.handlers import MessageHandlerRegistry
from websocket_server.models import Connection
from websocket_server.registry import ConnectionRegistry

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles WebSocket connections and messages."""

    def __init__(self, registry: ConnectionRegistry, settings: Optional[Settings] = None):
        """Initialize handler with connection registry."""
        self.registry = registry
        self.settings = settings or get_settings()
        # Map websocket connections to their connection IDs
        self._connections: dict[ServerConnection, Connection] = {}
        # Version-specific message handlers
        self._message_handlers = MessageHandlerRegistry(registry)
        # Ping/Pong settings
        self._pong_timeout: float = 10.0   # Wait 10 seconds for pong response
        self._recv_timeout: float = 25.0   # Wait 25 seconds for next message

    @property
    def active_connection_count(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)

    async def close_all_connections(self, timeout: float = 5.0) -> None:
        """
        Gracefully close all active WebSocket connections and clean up registry.

        Sends a shutdown message to each client, closes the websocket,
        and unregisters from the registry.

        Args:
            timeout: Maximum time in seconds to wait for all connections to close.
        """
        if not self._connections:
            logger.info("No active connections to close")
            return

        logger.info(f"Closing {len(self._connections)} active connection(s)...")

        # Create a snapshot of connections to avoid modification during iteration
        connections_to_close = list(self._connections.items())

        async def close_single_connection(
            websocket: ServerConnection, connection: Connection
        ) -> None:
            """Close a single connection gracefully."""
            try:
                # Send shutdown notification to client
                await websocket.send(
                    json.dumps(
                        {
                            "type": "shutdown",
                            "connection_id": str(connection.connection_id),
                            "message": "Server is shutting down",
                        }
                    )
                )
                # Close with 1001 (Going Away) status code
                await websocket.close(1001, "Server shutting down")
                logger.debug(f"Closed connection: {connection.connection_id}")
            except ConnectionClosed:
                # Connection already closed by client
                logger.debug(f"Connection already closed: {connection.connection_id}")
            except Exception as e:
                logger.warning(
                    f"Error closing connection {connection.connection_id}: {e}"
                )

        # Close all connections concurrently with timeout
        close_tasks = [
            close_single_connection(ws, conn) for ws, conn in connections_to_close
        ]

        try:
            await asyncio.wait_for(
                asyncio.gather(*close_tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout after {timeout}s while closing connections, "
                f"forcing cleanup of remaining connections"
            )

        # Unregister all connections from registry (run in thread pool to avoid blocking)
        async def unregister_connection(conn: Connection) -> None:
            try:
                await asyncio.to_thread(self.registry.unregister, conn.connection_id)
            except Exception as e:
                logger.warning(
                    f"Error unregistering connection {conn.connection_id}: {e}"
                )

        unregister_tasks = [
            unregister_connection(conn) for _, conn in connections_to_close
        ]
        await asyncio.gather(*unregister_tasks, return_exceptions=True)

        # Clear the connections dict
        self._connections.clear()

        logger.info("All connections closed and registry cleaned up")

    def _parse_path_and_params(
        self, full_path: str
    ) -> tuple[Optional[str], dict[str, Optional[str]]]:
        """
        Parse the WebSocket path and query parameters.

        Args:
            full_path: Full request path including query string.

        Returns:
            Tuple of (api_version, params_dict).
            api_version is None if path doesn't match expected pattern.
        """
        parsed = urlparse(full_path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Validate path and extract version
        is_valid, version = self.settings.is_valid_path(path)

        query_params = {
            "tenant": params.get("tenant", [None])[0],
            "deployment_id": params.get("deployment_id", [None])[0],
            "connection_type": params.get("connection_type", [None])[0],
        }

        return version, query_params

    async def handle_connection(self, websocket: ServerConnection) -> None:
        """
        Handle a WebSocket connection lifecycle.

        Validates the request path, parses connection parameters from query string,
        registers the connection, processes messages, and cleans up on disconnect.
        """
        connection: Optional[Connection] = None

        try:
            # Parse path and connection parameters
            api_version, params = self._parse_path_and_params(websocket.request.path)

            # Validate path matches expected pattern
            if api_version is None:
                expected_path = self.settings.ws_path_template.format(
                    version=self.settings.supported_versions_list[0]
                )
                error_msg = (
                    f"Invalid path. Expected: {expected_path} "
                    f"(supported versions: {', '.join(self.settings.supported_versions_list)})"
                )
                logger.warning(f"Connection rejected: {error_msg}")
                await websocket.close(1008, error_msg)
                return

            # Validate required parameters
            missing = [k for k, v in params.items() if v is None]
            if missing:
                error_msg = f"Missing required parameters: {', '.join(missing)}"
                logger.warning(f"Connection rejected: {error_msg}")
                await websocket.close(1008, error_msg)
                return

            # Register connection in DynamoDB (run in thread pool to avoid blocking)
            connection = await asyncio.to_thread(
                self.registry.register,
                tenant=params["tenant"],
                deployment_id=params["deployment_id"],
                connection_type=params["connection_type"],
                api_version=api_version,
            )

            self._connections[websocket] = connection

            logger.info(
                f"Connection established: {connection.connection_id} "
                f"(version={api_version}, tenant={params['tenant']}, "
                f"deployment={params['deployment_id']}, type={params['connection_type']})"
            )

            # Send connection ID to client
            await websocket.send(
                json.dumps(
                    {
                        "type": "connected",
                        "connection_id": str(connection.connection_id),
                        "api_version": api_version,
                        "message": "Connection established successfully",
                    }
                )
            )

            # Process incoming messages with integrated ping/pong
            while True:
                try:
                    # Send protocol-level ping and wait for pong with timeout
                    logger.info(f"Sending protocol ping over {connection.connection_id}")
                    pong_waiter = await websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=self._pong_timeout)
                    logger.info(f"Received protocol pong from {connection.connection_id}")
                    
                    # Send application-level heartbeat so client can detect dead connections
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "connection_id": str(connection.connection_id),
                                "timestamp": time.time(),
                            }
                        )
                    )
                    logger.debug(f"Sent heartbeat to {connection.connection_id}")
                    
                except asyncio.TimeoutError:
                    # No pong in time -> close connection
                    logger.warning(
                        f"No pong response from {connection.connection_id} in "
                        f"{self._pong_timeout}s, closing connection"
                    )
                    await websocket.close(1008, "Pong timeout")
                    break

                try:
                    # Wait for next message with timeout
                    message = await asyncio.wait_for(
                        websocket.recv(), timeout=self._recv_timeout
                    )
                    await self._handle_message(websocket, connection, message)
                except asyncio.TimeoutError:
                    # No message received - this is OK, just continue to next ping
                    logger.debug(
                        f"No message from {connection.connection_id} in "
                        f"{self._recv_timeout}s, continuing"
                    )
                    continue
                except ConnectionClosed:
                    logger.info(f"Connection closed by client: {connection.connection_id}")
                    break

        except Exception as e:
            logger.error(f"Error handling connection: {e}", exc_info=True)
            raise

        finally:
            # Clean up on disconnect (run in thread pool to avoid blocking)
            if connection:
                await asyncio.to_thread(
                    self.registry.unregister, connection.connection_id
                )
                self._connections.pop(websocket, None)
                logger.info(f"Connection closed: {connection.connection_id}")

    async def _handle_message(
        self,
        websocket: ServerConnection,
        connection: Connection,
        message: str | bytes,
    ) -> None:
        """
        Handle an incoming WebSocket message.

        Routes to version-specific handler based on connection's API version.
        """
        # Get the appropriate handler for this connection's API version
        handler = self._message_handlers.get_handler_or_default(
            connection.api_version
        )

        # Delegate to the version-specific handler
        await handler.handle(websocket, connection, message)

