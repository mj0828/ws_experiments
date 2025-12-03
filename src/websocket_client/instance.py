"""Global WebSocket client instance management."""

import logging
from typing import Optional

from websocket_client.client import MessageHandler, SendResult, WebSocketClient
from websocket_client.config import ClientSettings, get_client_settings

logger = logging.getLogger(__name__)

# Global client instance
_client: Optional[WebSocketClient] = None


def get_client() -> WebSocketClient:
    """
    Get the global WebSocket client instance.

    Returns:
        The global WebSocketClient instance.

    Raises:
        RuntimeError: If the client has not been initialized.
            Call init_client() first.

    Example:
        ```python
        from websocket_client import get_client

        async def send_data(data: dict):
            client = get_client()
            result = await client.send(data)
            return result.success
        ```
    """
    if _client is None:
        raise RuntimeError(
            "WebSocket client not initialized. Call init_client() first."
        )
    return _client


def init_client(
    settings: Optional[ClientSettings] = None,
    message_handler: Optional[MessageHandler] = None,
) -> WebSocketClient:
    """
    Initialize the global WebSocket client instance.

    This should be called once during application startup, before any
    other code tries to use get_client().

    Args:
        settings: Client settings. If None, uses get_client_settings().
        message_handler: Optional message handler callback.

    Returns:
        The initialized WebSocketClient instance.

    Raises:
        RuntimeError: If the client has already been initialized.
            Call reset_client() first if you need to reinitialize.

    Example:
        ```python
        from websocket_client import init_client, get_client_settings

        # In your application startup
        settings = get_client_settings()
        client = init_client(settings, my_message_handler)

        # Start the client
        await client.run()
        ```
    """
    global _client

    if _client is not None:
        raise RuntimeError(
            "WebSocket client already initialized. "
            "Call reset_client() first if you need to reinitialize."
        )

    if settings is None:
        settings = get_client_settings()

    _client = WebSocketClient(settings=settings, message_handler=message_handler)
    logger.info("Global WebSocket client initialized")

    return _client


async def reset_client() -> None:
    """
    Reset the global WebSocket client instance.

    Stops the current client (if running) and clears the global instance.
    This is useful for testing or if you need to reinitialize with
    different settings.

    Example:
        ```python
        from websocket_client import reset_client, init_client

        # Stop and clear the current client
        await reset_client()

        # Reinitialize with new settings
        new_settings = ClientSettings(server_host="new-server.com")
        init_client(new_settings)
        ```
    """
    global _client

    if _client is not None:
        logger.info("Resetting global WebSocket client")
        await _client.stop()
        _client = None


def is_client_initialized() -> bool:
    """
    Check if the global client has been initialized.

    Returns:
        True if init_client() has been called and reset_client() has not.
    """
    return _client is not None


async def send(message: dict, retry: bool = True) -> SendResult:
    """
    Send a message using the global WebSocket client.

    Convenience function that gets the global client and sends a message.

    Args:
        message: The message to send.
        retry: Whether to retry on failure.

    Returns:
        SendResult indicating success or failure.

    Raises:
        RuntimeError: If the client has not been initialized.

    Example:
        ```python
        from websocket_client import send

        async def notify_server(event: str, data: dict):
            result = await send({"type": event, "data": data})
            if not result.success:
                logger.error(f"Failed to send {event}: {result.error}")
        ```
    """
    client = get_client()
    return await client.send(message, retry=retry)

