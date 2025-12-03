"""WebSocket client implementation with exponential backoff retry."""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional

from websockets.asyncio.client import connect, ClientConnection
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidStatusCode,
)

from websocket_client.config import ClientSettings

logger = logging.getLogger(__name__)

# Type alias for message handlers
MessageHandler = Callable[[dict[str, Any]], Optional[dict[str, Any]]]


class SendStatus(Enum):
    """Status of a send operation."""

    SUCCESS = auto()
    """Message was sent successfully."""

    RETRYABLE_ERROR = auto()
    """Send failed due to a retryable error (e.g., connection closed)."""

    NON_RETRYABLE_ERROR = auto()
    """Send failed due to a non-retryable error (e.g., serialization error)."""


@dataclass
class SendResult:
    """Result of a send operation."""

    status: SendStatus
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if the send was successful."""
        return self.status == SendStatus.SUCCESS

    @property
    def retryable(self) -> bool:
        """Check if the error is retryable."""
        return self.status == SendStatus.RETRYABLE_ERROR


class WebSocketClient:
    """
    WebSocket client with automatic reconnection and exponential backoff.

    Handles:
    - Connection establishment with retry logic
    - Message sending with retry and reconnection on failure
    - Server shutdown message handling with automatic reconnection
    - Application-level keepalive (ping/pong) with reconnection on timeout
    - Exponential backoff with jitter for retries
    """

    def __init__(
        self,
        settings: ClientSettings,
        message_handler: Optional[MessageHandler] = None,
    ):
        """
        Initialize the WebSocket client.

        Args:
            settings: Client configuration settings.
            message_handler: Optional callback for handling incoming messages.
                            Should return a dict to send as response, or None.
        """
        self.settings = settings
        self.message_handler = message_handler or self._default_message_handler

        self._websocket: Optional[ClientConnection] = None
        self._connection_id: Optional[str] = None
        self._running = False
        self._should_reconnect = True
        self._retry_count = 0

        # Keepalive state
        self._keepalive_task: Optional[asyncio.Task] = None
        self._last_pong_time: float = 0
        self._pending_ping: bool = False
        self._reconnect_requested: bool = False

        # Connection event - set when connected, cleared when disconnected
        self._connected_event = asyncio.Event()

        # Lock for send operations to prevent concurrent sends during reconnection
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to the server."""
        return self._websocket is not None and self._websocket.state.name == "OPEN"

    @property
    def connection_id(self) -> Optional[str]:
        """Get the current connection ID assigned by the server."""
        return self._connection_id

    def _calculate_retry_delay(self) -> float:
        """
        Calculate the next retry delay using exponential backoff with jitter.

        Returns:
            Delay in seconds before the next retry attempt.
        """
        # Base delay with exponential backoff
        delay = self.settings.initial_retry_delay * (
            self.settings.retry_multiplier ** self._retry_count
        )

        # Cap at maximum delay
        delay = min(delay, self.settings.max_retry_delay)

        # Add random jitter to prevent thundering herd
        jitter = delay * self.settings.retry_jitter * random.random()
        delay += jitter

        return delay

    def _should_continue_retrying(self) -> bool:
        """Check if we should continue retrying based on configuration."""
        if self.settings.max_retries < 0:
            # Infinite retries
            return True
        return self._retry_count < self.settings.max_retries

    async def _connect(self) -> bool:
        """
        Attempt to establish a connection to the server.

        Returns:
            True if connection was successful, False otherwise.
        """
        try:
            logger.info(f"Connecting to {self.settings.server_url}")

            self._websocket = await asyncio.wait_for(
                connect(
                    self.settings.server_url,
                    ping_interval=self.settings.ws_ping_interval,
                    ping_timeout=self.settings.ws_ping_timeout,
                ),
                timeout=self.settings.connection_timeout,
            )

            # Wait for the connection confirmation from server
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=self.settings.connection_timeout,
            )

            data = json.loads(response)
            if data.get("type") == "connected":
                self._connection_id = data.get("connection_id")
                self._retry_count = 0  # Reset retry count on successful connection
                self._last_pong_time = time.monotonic()  # Initialize pong time
                self._pending_ping = False
                self._reconnect_requested = False
                self._connected_event.set()  # Signal that we're connected
                logger.info(f"Connected successfully. Connection ID: {self._connection_id}")
                return True
            else:
                logger.warning(f"Unexpected connection response: {data}")
                await self._websocket.close()
                return False

        except asyncio.TimeoutError:
            logger.warning("Connection attempt timed out")
            return False
        except InvalidStatusCode as e:
            logger.warning(f"Server rejected connection: {e}")
            return False
        except ConnectionRefusedError:
            logger.warning("Connection refused - server may be down")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    async def connect_with_retry(self) -> bool:
        """
        Connect to the server with exponential backoff retry.

        Returns:
            True if connection was eventually successful, False if max retries exceeded.
        """
        self._retry_count = 0

        while self._should_continue_retrying() and self._should_reconnect:
            if await self._connect():
                return True

            self._retry_count += 1

            if not self._should_continue_retrying():
                logger.error(
                    f"Max retries ({self.settings.max_retries}) exceeded. Giving up."
                )
                return False

            delay = self._calculate_retry_delay()
            logger.info(
                f"Retry {self._retry_count}/{self.settings.max_retries if self.settings.max_retries >= 0 else '∞'} "
                f"in {delay:.2f} seconds..."
            )
            await asyncio.sleep(delay)

        return False

    async def _send_raw(self, message: dict[str, Any]) -> SendResult:
        """
        Send a message without retry logic.

        Args:
            message: Dictionary to send as JSON.

        Returns:
            SendResult indicating success, retryable error, or non-retryable error.
        """
        if not self.is_connected:
            return SendResult(
                status=SendStatus.RETRYABLE_ERROR,
                error="Not connected",
            )

        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as e:
            # Serialization errors are not retryable
            logger.error(f"Failed to serialize message: {e}")
            return SendResult(
                status=SendStatus.NON_RETRYABLE_ERROR,
                error=f"Serialization error: {e}",
            )

        try:
            await self._websocket.send(payload)
            logger.debug(f"Sent message: {message}")
            return SendResult(status=SendStatus.SUCCESS)
        except ConnectionClosed as e:
            # Connection closed is retryable (can reconnect and retry)
            logger.warning(f"Connection closed while sending message: {e}")
            return SendResult(
                status=SendStatus.RETRYABLE_ERROR,
                error=f"Connection closed: {e}",
            )
        except ConnectionClosedError as e:
            # Connection closed error is also retryable
            logger.warning(f"Connection closed error while sending: {e}")
            return SendResult(
                status=SendStatus.RETRYABLE_ERROR,
                error=f"Connection closed error: {e}",
            )
        except Exception as e:
            # Other exceptions are generally not retryable
            logger.error(f"Unexpected error sending message: {e}")
            return SendResult(
                status=SendStatus.NON_RETRYABLE_ERROR,
                error=f"Unexpected error: {e}",
            )

    async def _wait_for_connection(self, timeout: float) -> bool:
        """
        Wait for the connection to be established.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if connected within timeout, False otherwise.
        """
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def send(self, message: dict[str, Any], retry: bool = True) -> SendResult:
        """
        Send a message to the server with optional retry on failure.

        If not connected and retry is enabled, waits for reconnection before
        attempting to send. Will retry up to send_max_retries times for
        retryable errors only. Non-retryable errors (e.g., serialization)
        fail immediately.

        Args:
            message: Dictionary to send as JSON.
            retry: Whether to retry on retryable failures (default: True).

        Returns:
            SendResult with status indicating success or failure type.
            On failure, the caller can inspect result.error and result.retryable
            to decide what to do with the message.
        """
        async with self._send_lock:
            if not retry:
                return await self._send_raw(message)

            last_result: SendResult = SendResult(
                status=SendStatus.RETRYABLE_ERROR,
                error="No send attempt made",
            )

            # Attempt to send with retries
            for attempt in range(self.settings.send_max_retries + 1):
                # If not connected, wait for reconnection
                if not self.is_connected:
                    if not self._running:
                        logger.warning("Cannot send message: client is stopped")
                        return SendResult(
                            status=SendStatus.RETRYABLE_ERROR,
                            error="Client is stopped",
                        )

                    logger.info(
                        f"Not connected, waiting for reconnection "
                        f"(attempt {attempt + 1}/{self.settings.send_max_retries + 1})..."
                    )
                    self._request_reconnect()

                    # Wait for reconnection with timeout
                    connected = await self._wait_for_connection(
                        self.settings.send_wait_for_connection_timeout
                    )

                    if not connected:
                        if attempt < self.settings.send_max_retries:
                            logger.warning(
                                f"Reconnection timeout, will retry "
                                f"({attempt + 1}/{self.settings.send_max_retries})..."
                            )
                            continue
                        else:
                            logger.error(
                                f"Failed to reconnect after {self.settings.send_max_retries + 1} attempts, "
                                "message not sent"
                            )
                            return SendResult(
                                status=SendStatus.RETRYABLE_ERROR,
                                error=f"Failed to reconnect after {self.settings.send_max_retries + 1} attempts",
                            )

                # Try to send the message
                result = await self._send_raw(message)

                if result.success:
                    return result

                last_result = result

                # Check if error is retryable
                if not result.retryable:
                    logger.error(
                        f"Non-retryable send error: {result.error}"
                    )
                    return result

                # Retryable error - decide whether to retry
                if attempt < self.settings.send_max_retries:
                    logger.warning(
                        f"Retryable send error ({result.error}), "
                        f"retrying ({attempt + 1}/{self.settings.send_max_retries})..."
                    )
                    await asyncio.sleep(self.settings.send_retry_delay)
                else:
                    logger.error(
                        f"Send failed after {self.settings.send_max_retries + 1} attempts: {result.error}"
                    )
                    self._request_reconnect()

            return last_result

    def _request_reconnect(self) -> None:
        """Signal that a reconnection is needed."""
        if not self._reconnect_requested:
            self._reconnect_requested = True
            logger.info("Reconnection requested due to connection issues")

    async def _keepalive_loop(self) -> None:
        """
        Background task that sends periodic keepalive pings and monitors responses.

        If a pong response is not received within the timeout, triggers reconnection.
        """
        logger.debug("Keepalive loop started")

        while self._running and self.is_connected:
            try:
                # Wait for the keepalive interval
                await asyncio.sleep(self.settings.keepalive_interval)

                if not self._running or not self.is_connected:
                    break

                # Check if we're still waiting for a pong from previous ping
                if self._pending_ping:
                    elapsed = time.monotonic() - self._last_pong_time
                    if elapsed > self.settings.keepalive_timeout:
                        logger.warning(
                            f"No pong response received in {elapsed:.1f}s "
                            f"(timeout: {self.settings.keepalive_timeout}s), "
                            "requesting reconnection"
                        )
                        self._request_reconnect()
                        break

                # Send keepalive ping
                self._pending_ping = True
                logger.debug("Sending keepalive ping")
                result = await self._send_raw({"type": "ping"})

                if not result.success:
                    logger.warning(
                        f"Failed to send keepalive ping ({result.error}), "
                        "requesting reconnection"
                    )
                    self._request_reconnect()
                    break

            except asyncio.CancelledError:
                logger.debug("Keepalive loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in keepalive loop: {e}")
                self._request_reconnect()
                break

        logger.debug("Keepalive loop ended")

    def _start_keepalive(self) -> None:
        """Start the keepalive background task."""
        if self.settings.keepalive_enabled and self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            logger.debug("Keepalive task started")

    async def _stop_keepalive(self) -> None:
        """Stop the keepalive background task."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
            logger.debug("Keepalive task stopped")

    def _default_message_handler(self, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Default message handler that logs messages.

        Args:
            message: The received message.

        Returns:
            None (no response).
        """
        msg_type = message.get("type", "unknown")
        logger.info(f"Received message of type '{msg_type}': {message}")
        return None

    async def _handle_message(self, raw_message: str | bytes) -> bool:
        """
        Handle an incoming message from the server.

        Args:
            raw_message: The raw message received.

        Returns:
            True to continue processing, False if shutdown was received.
        """
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON message: {raw_message}")
            return True

        msg_type = message.get("type", "unknown")

        # Handle pong response (update keepalive state)
        if msg_type == "pong":
            self._last_pong_time = time.monotonic()
            self._pending_ping = False
            logger.debug("Received pong response")
            # Still pass to message handler in case user wants to handle it
            try:
                self.message_handler(message)
            except Exception:
                pass
            return True

        # Handle shutdown message from server
        if msg_type == "shutdown":
            logger.info(
                f"Received shutdown message from server: {message.get('message', 'No reason given')}"
            )
            # Signal to reconnect
            return False

        # Call the message handler and send response if any
        try:
            response = await asyncio.wait_for(
                asyncio.coroutine(lambda: self.message_handler(message))()
                if not asyncio.iscoroutinefunction(self.message_handler)
                else self.message_handler(message),
                timeout=self.settings.message_handler_timeout,
            )

            if response is not None:
                await self.send(response)

        except asyncio.TimeoutError:
            logger.warning(f"Message handler timed out for message type: {msg_type}")
        except Exception as e:
            logger.error(f"Error in message handler: {e}")

        return True

    async def run(self) -> None:
        """
        Main client loop: connect, receive messages, and handle reconnection.

        This method runs indefinitely (with infinite retries by default) until
        stop() is called. It automatically handles:
        - Initial connection with retry
        - Message processing
        - Keepalive monitoring
        - Automatic reconnection on any connection issue
        """
        self._running = True
        self._should_reconnect = True

        while self._running:
            # Reset reconnect flag
            self._reconnect_requested = False

            # Attempt to connect
            if not await self.connect_with_retry():
                logger.error("Failed to establish connection. Exiting.")
                break

            # Start keepalive monitoring
            self._start_keepalive()

            # Process messages
            try:
                async for message in self._websocket:
                    if not self._running:
                        break

                    # Check if reconnection was requested
                    if self._reconnect_requested:
                        logger.info("Breaking message loop due to reconnection request")
                        break

                    should_continue = await self._handle_message(message)
                    if not should_continue:
                        # Server shutdown received, reconnect
                        logger.info("Server initiated shutdown, will attempt to reconnect...")
                        break

            except ConnectionClosedError as e:
                logger.warning(f"Connection closed unexpectedly: {e}")
            except ConnectionClosed as e:
                logger.info(f"Connection closed: {e}")
            except Exception as e:
                logger.error(f"Error in message loop: {e}")

            # Stop keepalive
            await self._stop_keepalive()

            # Clean up current connection
            self._connected_event.clear()  # Signal that we're disconnected
            if self._websocket:
                try:
                    await self._websocket.close()
                except Exception:
                    pass
                self._websocket = None
                self._connection_id = None

            # Check if we should reconnect
            if self._running and self._should_reconnect:
                logger.info("Attempting to reconnect...")
            else:
                break

        self._running = False
        logger.info("Client stopped")

    async def stop(self) -> None:
        """Stop the client and close the connection gracefully."""
        logger.info("Stopping client...")
        self._running = False
        self._should_reconnect = False

        # Stop keepalive
        await self._stop_keepalive()

        # Clear connection state
        self._connected_event.clear()

        if self._websocket:
            try:
                await self._websocket.close(1000, "Client shutting down")
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")
            finally:
                self._websocket = None
                self._connection_id = None

    async def send_ping(self) -> SendResult:
        """
        Send a ping message to the server.

        Returns:
            SendResult indicating success or failure.
        """
        return await self.send({"type": "ping"})

    async def request_info(self) -> SendResult:
        """
        Request connection info from the server.

        Returns:
            SendResult indicating success or failure.
        """
        return await self.send({"type": "info"})
