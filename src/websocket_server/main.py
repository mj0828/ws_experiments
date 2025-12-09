"""Entry point for the WebSocket server."""

import asyncio
import logging
import signal
import sys

from aiohttp import web
from websockets.asyncio.server import serve

from websocket_server.config import get_settings
from websocket_server.http import create_http_app
from websocket_server.registry import ConnectionRegistry
from websocket_server.server import WebSocketHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def run_server() -> None:
    """Initialize and run the WebSocket server and HTTP API server."""
    settings = get_settings()

    logger.info(f"Starting WebSocket server on {settings.host}:{settings.port}")
    logger.info(f"Starting HTTP server on {settings.host}:{settings.http_port}")

    if settings.is_local_dev:
        logger.info(f"Running in local development mode with endpoint: {settings.dynamodb_endpoint_url}")

    # Initialize the connection registry
    registry = ConnectionRegistry(settings)

    # Create table if running in local dev mode
    if settings.is_local_dev:
        logger.info("Creating DynamoDB table if not exists...")
        registry.create_table_if_not_exists()

    # Create the WebSocket handler
    handler = WebSocketHandler(registry, settings)

    # Create HTTP app with access to WebSocket handler
    http_app = create_http_app(handler)

    # Set up graceful shutdown
    stop_event = asyncio.Event()

    def handle_shutdown():
        logger.info("Shutdown signal received, stopping servers...")
        stop_event.set()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown)

    # Start the HTTP server
    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http_site = web.TCPSite(http_runner, settings.host, settings.http_port)
    await http_site.start()

    logger.info(f"HTTP server listening on http://{settings.host}:{settings.http_port}")
    logger.info(f"Health endpoint: http://{settings.host}:{settings.http_port}/health")

    # Start the WebSocket server
    async with serve(
        handler.handle_connection,
        settings.host,
        settings.port,
    ) as server:
        # Build example URL with first supported version
        example_version = settings.supported_versions_list[0]
        example_path = settings.ws_path_template.format(version=example_version)
        base_url = f"ws://{settings.host}:{settings.port}"

        logger.info(f"WebSocket server listening on {base_url}")
        logger.info(f"Supported versions: {', '.join(settings.supported_versions_list)}")
        logger.info(
            f"Example connection URL: {base_url}{example_path}"
            f"?tenant=<name>&deployment_id=<id>&connection_type=<type>"
        )

        # Wait for shutdown signal
        await stop_event.wait()

        # Gracefully close all active connections and clean up registry
        logger.info("Initiating graceful shutdown...")
        await handler.close_all_connections(timeout=settings.shutdown_timeout)

    # Clean up HTTP server
    await http_runner.cleanup()

    logger.info("Servers stopped")


def main() -> None:
    """Main entry point."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server interrupted")


if __name__ == "__main__":
    main()

