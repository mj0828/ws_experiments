"""Data models for the WebSocket server."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass
class Connection:
    """Represents a WebSocket connection stored in the registry."""

    tenant: str
    deployment_id: str
    connection_type: str
    api_version: str = "v1"  # API version the client connected with
    connection_id: UUID = field(default_factory=uuid4)
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl: Optional[int] = None  # Unix timestamp for DynamoDB TTL

    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        item = {
            "connection_id": {"S": str(self.connection_id)},
            "tenant": {"S": self.tenant},
            "deployment_id": {"S": self.deployment_id},
            "connection_type": {"S": self.connection_type},
            "api_version": {"S": self.api_version},
            "connected_at": {"S": self.connected_at.isoformat()},
        }
        if self.ttl is not None:
            item["ttl"] = {"N": str(self.ttl)}
        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> "Connection":
        """Create Connection from DynamoDB item."""
        return cls(
            connection_id=UUID(item["connection_id"]["S"]),
            tenant=item["tenant"]["S"],
            deployment_id=item["deployment_id"]["S"],
            connection_type=item["connection_type"]["S"],
            api_version=item.get("api_version", {}).get("S", "v1"),
            connected_at=datetime.fromisoformat(item["connected_at"]["S"]),
            ttl=int(item["ttl"]["N"]) if "ttl" in item else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "connection_id": str(self.connection_id),
            "tenant": self.tenant,
            "deployment_id": self.deployment_id,
            "connection_type": self.connection_type,
            "api_version": self.api_version,
            "connected_at": self.connected_at.isoformat(),
        }

