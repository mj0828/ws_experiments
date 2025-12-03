# WebSocket Server with DynamoDB Registry

A Python 3.12 WebSocket server that maintains a connection registry in DynamoDB. Each connection is identified by a unique `connection_id` and associated with tenant, deployment, and connection type metadata.

## Features

- WebSocket server using the `websockets` library
- DynamoDB-backed connection registry with TTL for automatic cleanup
- Connection metadata: tenant, deployment_id, connection_type
- LocalStack support for local development
- Automatic table creation in development mode

## Prerequisites

- Python 3.12+
- Docker (for LocalStack)

## Installation

1. Clone the repository and navigate to the project directory:

```bash
cd websocket_server
```

2. Create and activate a virtual environment:

```bash
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Install the package in development mode:

```bash
pip install -e .
```

## Local Development Setup

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Start LocalStack:

```bash
docker-compose up -d
```

3. Run the server:

```bash
python -m websocket_server.main
```

Or using the installed script:

```bash
websocket-server
```

## Connecting to the Server

Connect using a WebSocket client with query parameters:

```
ws://localhost:8765/?tenant=my-tenant&deployment_id=deploy-123&connection_type=client
```

### Required Query Parameters

| Parameter | Description |
|-----------|-------------|
| `tenant` | The tenant name |
| `deployment_id` | The deployment identifier |
| `connection_type` | Type of connection (e.g., client, worker, admin) |

### Example using websocat

```bash
# Install websocat
brew install websocat  # macOS

# Connect
websocat "ws://localhost:8765/?tenant=acme&deployment_id=prod-1&connection_type=client"
```

### Example using Python

```python
import asyncio
import websockets

async def connect():
    uri = "ws://localhost:8765/?tenant=acme&deployment_id=prod-1&connection_type=client"
    async with websockets.connect(uri) as ws:
        # Receive connection confirmation
        response = await ws.recv()
        print(f"Connected: {response}")

        # Send a ping
        await ws.send('{"type": "ping"}')
        pong = await ws.recv()
        print(f"Pong: {pong}")

        # Get connection info
        await ws.send('{"type": "info"}')
        info = await ws.recv()
        print(f"Info: {info}")

asyncio.run(connect())
```

## Message Types

### Outgoing (Server to Client)

| Type | Description |
|------|-------------|
| `connected` | Sent on successful connection with `connection_id` |
| `pong` | Response to ping message |
| `info` | Connection details |
| `echo` | Echo of received messages |

### Incoming (Client to Server)

| Type | Description |
|------|-------------|
| `ping` | Heartbeat (refreshes TTL) |
| `info` | Request connection information |
| Other | Echoed back to client |

## Configuration

Environment variables (can be set in `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8765` | Server port |
| `AWS_REGION` | `us-east-1` | AWS region |
| `AWS_ACCESS_KEY_ID` | - | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | - | AWS secret key |
| `DYNAMODB_TABLE_NAME` | `websocket_connections` | DynamoDB table name |
| `DYNAMODB_ENDPOINT_URL` | - | LocalStack endpoint (set for local dev) |
| `CONNECTION_TTL_SECONDS` | `3600` | TTL for orphaned connections (1 hour) |

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## AWS Deployment

For production deployment:

1. Remove `DYNAMODB_ENDPOINT_URL` from environment (or don't set it)
2. Create the DynamoDB table manually or via IaC (Terraform/CloudFormation)
3. Configure proper AWS credentials via IAM roles or environment variables
4. Ensure the table has TTL enabled on the `ttl` attribute

### DynamoDB Table Schema

- **Table Name**: `websocket_connections` (configurable)
- **Partition Key**: `connection_id` (String)
- **TTL Attribute**: `ttl`

## Project Structure

```
websocket_server/
├── pyproject.toml              # Project configuration
├── requirements.txt            # Pinned dependencies
├── docker-compose.yml          # LocalStack setup
├── .env.example                # Example environment config
├── README.md                   # This file
├── src/
│   └── websocket_server/
│       ├── __init__.py
│       ├── main.py             # Entry point
│       ├── server.py           # WebSocket handler
│       ├── registry.py         # DynamoDB registry
│       ├── config.py           # Configuration
│       └── models.py           # Data models
└── tests/
    └── test_registry.py        # Unit tests
```

