"""HTTP endpoints using aiohttp."""

import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from websocket_server.server import WebSocketHandler

logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """
    Health check endpoint.

    Returns server status and active connection count.
    """
    ws_handler: WebSocketHandler = request.app["ws_handler"]

    return web.json_response(
        {
            "status": "healthy",
            "active_connections": ws_handler.active_connection_count,
        }
    )


def create_http_app(ws_handler: "WebSocketHandler") -> web.Application:
    """
    Create and configure the aiohttp application.

    Args:
        ws_handler: WebSocket handler instance for accessing server state.

    Returns:
        Configured aiohttp Application.
    """
    app = web.Application()

    # Store ws_handler in app for access in request handlers
    app["ws_handler"] = ws_handler

    # Register routes
    app.router.add_get("/health", health_handler)

    logger.info("HTTP routes registered: GET /health")

    return app

