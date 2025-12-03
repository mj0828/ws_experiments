"""DynamoDB-backed connection registry."""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import boto3
from botocore.exceptions import ClientError

from websocket_server.config import Settings, get_settings
from websocket_server.models import Connection

logger = logging.getLogger(__name__)


class ConnectionRegistry:
    """Manages WebSocket connections in DynamoDB."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize the registry with DynamoDB client."""
        self.settings = settings or get_settings()
        self._client = self._create_client()

    def _create_client(self):
        """Create DynamoDB client with appropriate configuration."""
        client_kwargs = {
            "service_name": "dynamodb",
            "region_name": self.settings.aws_region,
        }

        # Use LocalStack endpoint if configured
        if self.settings.dynamodb_endpoint_url:
            client_kwargs["endpoint_url"] = self.settings.dynamodb_endpoint_url

        # Use explicit credentials if provided
        if self.settings.aws_access_key_id and self.settings.aws_secret_access_key:
            client_kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
            client_kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key

        return boto3.client(**client_kwargs)

    def create_table_if_not_exists(self) -> None:
        """Create the DynamoDB table if it doesn't exist (for local dev)."""
        try:
            self._client.describe_table(TableName=self.settings.dynamodb_table_name)
            logger.info(f"Table {self.settings.dynamodb_table_name} already exists")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.info(f"Creating table {self.settings.dynamodb_table_name}")
                self._client.create_table(
                    TableName=self.settings.dynamodb_table_name,
                    KeySchema=[
                        {"AttributeName": "connection_id", "KeyType": "HASH"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "connection_id", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                # Wait for table to be active
                waiter = self._client.get_waiter("table_exists")
                waiter.wait(TableName=self.settings.dynamodb_table_name)

                # Enable TTL on the table
                self._client.update_time_to_live(
                    TableName=self.settings.dynamodb_table_name,
                    TimeToLiveSpecification={
                        "Enabled": True,
                        "AttributeName": "ttl",
                    },
                )
                logger.info(f"Table {self.settings.dynamodb_table_name} created with TTL enabled")
            else:
                raise

    def register(
        self,
        tenant: str,
        deployment_id: str,
        connection_type: str,
        api_version: str = "v1",
    ) -> Connection:
        """
        Register a new connection in the registry.

        Args:
            tenant: The tenant name
            deployment_id: The deployment identifier
            connection_type: Type of connection
            api_version: API version the client connected with

        Returns:
            The created Connection object
        """
        # Calculate TTL timestamp
        ttl_timestamp = int(
            datetime.now(timezone.utc).timestamp() + self.settings.connection_ttl_seconds
        )

        connection = Connection(
            tenant=tenant,
            deployment_id=deployment_id,
            connection_type=connection_type,
            api_version=api_version,
            ttl=ttl_timestamp,
        )

        self._client.put_item(
            TableName=self.settings.dynamodb_table_name,
            Item=connection.to_dynamodb_item(),
        )

        logger.info(f"Registered connection {connection.connection_id}")
        return connection

    def unregister(self, connection_id: UUID) -> bool:
        """
        Remove a connection from the registry.

        Args:
            connection_id: The connection ID to remove

        Returns:
            True if the connection was removed, False if it didn't exist
        """
        try:
            response = self._client.delete_item(
                TableName=self.settings.dynamodb_table_name,
                Key={"connection_id": {"S": str(connection_id)}},
                ReturnValues="ALL_OLD",
            )
            existed = "Attributes" in response
            if existed:
                logger.info(f"Unregistered connection {connection_id}")
            else:
                logger.warning(f"Connection {connection_id} not found for unregistration")
            return existed
        except ClientError as e:
            logger.error(f"Error unregistering connection {connection_id}: {e}")
            raise

    def get(self, connection_id: UUID) -> Optional[Connection]:
        """
        Retrieve a connection from the registry.

        Args:
            connection_id: The connection ID to retrieve

        Returns:
            The Connection object if found, None otherwise
        """
        try:
            response = self._client.get_item(
                TableName=self.settings.dynamodb_table_name,
                Key={"connection_id": {"S": str(connection_id)}},
            )
            if "Item" in response:
                return Connection.from_dynamodb_item(response["Item"])
            return None
        except ClientError as e:
            logger.error(f"Error retrieving connection {connection_id}: {e}")
            raise

    def refresh_ttl(self, connection_id: UUID) -> bool:
        """
        Refresh the TTL for an active connection.

        Args:
            connection_id: The connection ID to refresh

        Returns:
            True if the TTL was refreshed, False if connection not found
        """
        new_ttl = int(
            datetime.now(timezone.utc).timestamp() + self.settings.connection_ttl_seconds
        )

        try:
            self._client.update_item(
                TableName=self.settings.dynamodb_table_name,
                Key={"connection_id": {"S": str(connection_id)}},
                UpdateExpression="SET #ttl = :ttl",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":ttl": {"N": str(new_ttl)}},
                ConditionExpression="attribute_exists(connection_id)",
            )
            logger.debug(f"Refreshed TTL for connection {connection_id}")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.warning(f"Connection {connection_id} not found for TTL refresh")
                return False
            raise

