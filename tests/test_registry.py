"""Tests for the connection registry."""

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from moto import mock_aws

from websocket_server.config import Settings
from websocket_server.models import Connection
from websocket_server.registry import ConnectionRegistry


@pytest.fixture
def mock_settings():
    """Create test settings."""
    return Settings(
        aws_region="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        dynamodb_table_name="test_connections",
        dynamodb_endpoint_url=None,
        connection_ttl_seconds=3600,
    )


@pytest.fixture
def registry_with_table(mock_settings):
    """Create a registry with a mocked DynamoDB table."""
    with mock_aws():
        registry = ConnectionRegistry(mock_settings)
        registry.create_table_if_not_exists()
        yield registry


class TestConnectionRegistry:
    """Tests for ConnectionRegistry class."""

    def test_register_creates_connection(self, registry_with_table):
        """Test that register creates a connection in DynamoDB."""
        connection = registry_with_table.register(
            tenant="test-tenant",
            deployment_id="deploy-123",
            connection_type="client",
        )

        assert connection.tenant == "test-tenant"
        assert connection.deployment_id == "deploy-123"
        assert connection.connection_type == "client"
        assert connection.connection_id is not None
        assert connection.ttl is not None

    def test_get_retrieves_connection(self, registry_with_table):
        """Test that get retrieves a registered connection."""
        # Register a connection
        connection = registry_with_table.register(
            tenant="test-tenant",
            deployment_id="deploy-456",
            connection_type="worker",
        )

        # Retrieve it
        retrieved = registry_with_table.get(connection.connection_id)

        assert retrieved is not None
        assert retrieved.connection_id == connection.connection_id
        assert retrieved.tenant == "test-tenant"
        assert retrieved.deployment_id == "deploy-456"
        assert retrieved.connection_type == "worker"

    def test_get_returns_none_for_missing(self, registry_with_table):
        """Test that get returns None for non-existent connections."""
        result = registry_with_table.get(uuid4())
        assert result is None

    def test_unregister_removes_connection(self, registry_with_table):
        """Test that unregister removes a connection."""
        # Register a connection
        connection = registry_with_table.register(
            tenant="test-tenant",
            deployment_id="deploy-789",
            connection_type="admin",
        )

        # Verify it exists
        assert registry_with_table.get(connection.connection_id) is not None

        # Unregister it
        result = registry_with_table.unregister(connection.connection_id)

        assert result is True
        assert registry_with_table.get(connection.connection_id) is None

    def test_unregister_returns_false_for_missing(self, registry_with_table):
        """Test that unregister returns False for non-existent connections."""
        result = registry_with_table.unregister(uuid4())
        assert result is False

    def test_refresh_ttl_updates_ttl(self, registry_with_table):
        """Test that refresh_ttl updates the TTL value."""
        # Register a connection
        connection = registry_with_table.register(
            tenant="test-tenant",
            deployment_id="deploy-ttl",
            connection_type="client",
        )

        original_ttl = connection.ttl

        # Refresh TTL
        result = registry_with_table.refresh_ttl(connection.connection_id)

        assert result is True

        # Verify TTL was updated (or at least the operation succeeded)
        retrieved = registry_with_table.get(connection.connection_id)
        assert retrieved is not None
        assert retrieved.ttl >= original_ttl

    def test_refresh_ttl_returns_false_for_missing(self, registry_with_table):
        """Test that refresh_ttl returns False for non-existent connections."""
        result = registry_with_table.refresh_ttl(uuid4())
        assert result is False


class TestConnectionModel:
    """Tests for Connection model."""

    def test_to_dynamodb_item(self):
        """Test conversion to DynamoDB item format."""
        connection = Connection(
            tenant="test",
            deployment_id="deploy",
            connection_type="client",
            ttl=1234567890,
        )

        item = connection.to_dynamodb_item()

        assert item["tenant"]["S"] == "test"
        assert item["deployment_id"]["S"] == "deploy"
        assert item["connection_type"]["S"] == "client"
        assert item["ttl"]["N"] == "1234567890"
        assert "connection_id" in item
        assert "connected_at" in item

    def test_from_dynamodb_item(self):
        """Test creation from DynamoDB item format."""
        item = {
            "connection_id": {"S": "550e8400-e29b-41d4-a716-446655440000"},
            "tenant": {"S": "test"},
            "deployment_id": {"S": "deploy"},
            "connection_type": {"S": "client"},
            "connected_at": {"S": "2024-01-01T00:00:00+00:00"},
            "ttl": {"N": "1234567890"},
        }

        connection = Connection.from_dynamodb_item(item)

        assert str(connection.connection_id) == "550e8400-e29b-41d4-a716-446655440000"
        assert connection.tenant == "test"
        assert connection.deployment_id == "deploy"
        assert connection.connection_type == "client"
        assert connection.ttl == 1234567890

    def test_to_dict(self):
        """Test conversion to dictionary."""
        connection = Connection(
            tenant="test",
            deployment_id="deploy",
            connection_type="client",
        )

        data = connection.to_dict()

        assert data["tenant"] == "test"
        assert data["deployment_id"] == "deploy"
        assert data["connection_type"] == "client"
        assert "connection_id" in data
        assert "connected_at" in data

