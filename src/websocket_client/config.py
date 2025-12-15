"""Configuration management for WebSocket client."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientSettings(BaseSettings):
    """Client settings loaded from environment variables."""

    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="CLIENT_",
    )
    """
    # Server connection settings
    server_host: str = "localhost"
    server_port: int = 8765
    server_scheme: str = "ws"  # "ws" or "wss"

    # Context path settings
    # Path template with {version} placeholder
    server_path_template: str = "/{version}/mai/ws/stream"
    api_version: str = "v1"  # API version to use (v1, v2, etc.)

    # Connection parameters (required for server registration)
    tenant: str = "default"
    deployment_id: str = "deployment-1"
    connection_type: str = "client"

    # Retry settings with exponential backoff
    max_retries: int = -1  # -1 for infinite retries (default: stay connected forever)
    initial_retry_delay: float = 1.0  # Initial delay in seconds
    max_retry_delay: float = 60.0  # Maximum delay between retries
    retry_multiplier: float = 2.0  # Multiplier for exponential backoff
    retry_jitter: float = 0.1  # Random jitter factor (0-1) to prevent thundering herd

    # Send retry settings
    send_max_retries: int = 3  # Max retries for sending a message before giving up
    send_retry_delay: float = 0.5  # Delay between send retries
    send_wait_for_connection_timeout: float = 30.0  # Max time to wait for reconnection per retry

    # Connection settings
    connection_timeout: float = 10.0  # Timeout for establishing connection

    # WebSocket protocol-level ping (handled by websockets library)
    # Note: Server handles pings, so disable client-side pings to avoid conflicts
    ws_ping_interval: Optional[float] = None  # Disable client-initiated protocol pings
    ws_ping_timeout: Optional[float] = None   # Server handles ping/pong

    # Application-level keepalive settings (client-initiated pings)
    # Note: With server-initiated pings, this can be disabled
    keepalive_enabled: bool = False  # Disable client-initiated keepalive (server now sends pings)
    keepalive_interval: float = 30.0  # Interval for sending keepalive pings
    keepalive_timeout: float = 15.0  # Timeout waiting for pong response

    # Server heartbeat monitoring
    # The server sends application-level heartbeat messages that are visible to the client
    # This allows detection of dead connections through proxies/NAT gateways
    server_heartbeat_timeout: float = 60.0  # Reconnect if no heartbeat received in this many seconds

    # Message handler settings
    message_handler_timeout: float = 30.0  # Timeout for processing a message

    @property
    def server_path(self) -> str:
        """Build the context path with the configured API version."""
        return self.server_path_template.format(version=self.api_version)

    @property
    def server_url(self) -> str:
        """Build the full WebSocket server URL with path and query parameters."""
        base_url = f"{self.server_scheme}://{self.server_host}:{self.server_port}"
        path = self.server_path
        query_params = (
            f"?tenant={self.tenant}"
            f"&deployment_id={self.deployment_id}"
            f"&connection_type={self.connection_type}"
        )
        return f"{base_url}{path}{query_params}"


@lru_cache
def get_client_settings() -> ClientSettings:
    """Get cached client settings instance."""
    return ClientSettings()

