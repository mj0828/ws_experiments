"""Configuration management using pydantic-settings."""

import re
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8765

    # WebSocket path settings
    # Context path pattern: /{version}/mai/ws/stream
    # Example: /v1/mai/ws/stream, /v2/mai/ws/stream
    ws_path_template: str = "/{version}/mai/ws/stream"
    supported_versions: str = "v1,v2"  # Comma-separated list of supported versions

    @property
    def supported_versions_list(self) -> list[str]:
        """Get list of supported API versions."""
        return [v.strip() for v in self.supported_versions.split(",") if v.strip()]

    def get_path_pattern(self) -> re.Pattern:
        """
        Get compiled regex pattern for matching WebSocket paths.

        The pattern captures the version from the path.
        """
        # Escape the template and replace {version} with a capture group
        escaped = re.escape(self.ws_path_template)
        pattern = escaped.replace(r"\{version\}", r"(?P<version>[^/]+)")
        return re.compile(f"^{pattern}$")

    def is_valid_path(self, path: str) -> tuple[bool, Optional[str]]:
        """
        Check if a path matches the expected pattern and version.

        Args:
            path: The URL path (without query string).

        Returns:
            Tuple of (is_valid, version). version is None if invalid.
        """
        pattern = self.get_path_pattern()
        match = pattern.match(path)

        if not match:
            return False, None

        version = match.group("version")
        if version not in self.supported_versions_list:
            return False, None

        return True, version

    # AWS settings
    aws_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # DynamoDB settings
    dynamodb_table_name: str = "websocket_connections"
    dynamodb_endpoint_url: Optional[str] = None  # Set for LocalStack, None for real AWS

    # Connection settings
    connection_ttl_seconds: int = 3600  # 1 hour default TTL for orphaned connections

    # Shutdown settings
    shutdown_timeout: float = 5.0  # Seconds to wait for connections to close gracefully

    @property
    def is_local_dev(self) -> bool:
        """Check if running in local development mode (using LocalStack)."""
        return self.dynamodb_endpoint_url is not None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

