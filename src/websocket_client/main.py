"""Entry point for the WebSocket client."""

import asyncio
import logging
import signal
import sys
from typing import Any, Optional

from websocket_client.config import get_client_settings
from websocket_client.instance import get_client, init_client, reset_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def example_message_handler(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Example message handler that processes server messages and returns responses.

    Note: Pong messages are handled internally by the client for keepalive monitoring,
    but are still passed to this handler for logging if needed.

    Args:
        message: The received message from the server.

    Returns:
        A response message to send back, or None for no response.
    """
    msg_type = message.get("type", "unknown")

    if msg_type == "pong":
        # Pong is handled internally by client for keepalive, just log
        logger.info("Received pong response from server")
        return None

    elif msg_type == "info":
        connection_info = message.get("connection", {})
        logger.info(f"Connection info: {connection_info}")
        return None

    elif msg_type == "echo":
        logger.info(f"Received echo: {message}")
        return None

    elif msg_type == "connected":
        logger.info(f"Connection confirmed: {message.get('connection_id')}")
        return None

    else:
        # For other message types, log and optionally respond
        logger.info(f"Received message: {message}")
        # Example: acknowledge receipt
        return {
            "type": "ack",
            "received_type": msg_type,
            "status": "processed",
        }


async def run_client() -> None:
    """Initialize and run the WebSocket client using the global instance."""
    settings = get_client_settings()

    logger.info("Starting WebSocket client")
    logger.info(f"Server URL: {settings.server_url}")
    logger.info(
        f"Retry settings: max_retries={'∞' if settings.max_retries < 0 else settings.max_retries}, "
        f"initial_delay={settings.initial_retry_delay}s, "
        f"max_delay={settings.max_retry_delay}s"
    )
    if settings.keepalive_enabled:
        logger.info(
            f"Keepalive: enabled (interval={settings.keepalive_interval}s, "
            f"timeout={settings.keepalive_timeout}s)"
        )
    else:
        logger.info("Keepalive: disabled")

    # Initialize the global client instance
    client = init_client(
        settings=settings,
        message_handler=example_message_handler,
    )

    # Set up graceful shutdown
    async def handle_shutdown_async():
        logger.info("Shutdown signal received, stopping client...")
        await reset_client()

    def handle_shutdown():
        asyncio.create_task(handle_shutdown_async())

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown)

    # Run the client
    await client.run()

    logger.info("Client exited")


async def run_interactive_client() -> None:
    """
    Run an interactive client that sends periodic pings and handles user input.

    This is useful for testing and debugging. Uses the global client instance
    so other parts of the application can also send messages.
    """
    settings = get_client_settings()

    logger.info("Starting interactive WebSocket client")
    logger.info(f"Server URL: {settings.server_url}")
    logger.info(
        f"Retry settings: max_retries={'∞' if settings.max_retries < 0 else settings.max_retries}"
    )
    logger.info(
        f"Keepalive: {'enabled' if settings.keepalive_enabled else 'disabled'}"
    )

    # Initialize the global client instance
    client = init_client(
        settings=settings,
        message_handler=example_message_handler,
    )

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_shutdown():
        logger.info("Shutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown)

    # Start the client in background
    client_task = asyncio.create_task(client.run())

    # Wait for initial connection
    await asyncio.sleep(2)

    # Send periodic pings while running
    # Note: Other parts of the app can now use get_client() to send messages
    ping_count = 0
    try:
        while not shutdown_event.is_set() and not client_task.done():
            # Get the client (demonstrates global access pattern)
            ws_client = get_client()

            if ws_client.is_connected:
                ping_count += 1
                logger.info(f"Sending ping #{ping_count}")
                await ws_client.send_ping()

                # Occasionally request info
                if ping_count % 5 == 0:
                    await ws_client.request_info()

            # Wait before next ping or check for shutdown
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=10.0)
                break
            except asyncio.TimeoutError:
                continue

    except asyncio.CancelledError:
        pass

    # Reset the global client (stops and clears it)
    await reset_client()

    # Wait for client task to complete
    try:
        await asyncio.wait_for(client_task, timeout=5.0)
    except asyncio.TimeoutError:
        client_task.cancel()

    logger.info("Interactive client exited")


def main() -> None:
    """Main entry point."""
    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        logger.info("Client interrupted")


def main_interactive() -> None:
    """Interactive mode entry point."""
    try:
        asyncio.run(run_interactive_client())
    except KeyboardInterrupt:
        logger.info("Client interrupted")


if __name__ == "__main__":
    # Check for interactive mode flag
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        main_interactive()
    else:
        main()

