<!--
  Technical README for realtime-chat.
-->

# realtime-chat

[![CI](https://github.com/sayomiyori/RealTimeChat/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/sayomiyori/RealTimeChat/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sayomiyori/RealTimeChat/branch/master/graph/badge.svg)](https://codecov.io/gh/sayomiyori/RealTimeChat)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue)](#local-development)
[![WebSocket](https://img.shields.io/badge/websocket-FastAPI-2F80ED?logo=websocket)](#websocket-client-example)
[![Docker](https://img.shields.io/badge/docker-2496ED?logo=docker&logoColor=fff)](#local-development)

Real-time chat API with WebSockets, JWT authentication, PostgreSQL persistence, and Redis Pub/Sub for scalable broadcasts.

## Stack

- FastAPI + WebSocket
- Redis Pub/Sub (async: `redis.asyncio`)
- PostgreSQL (async: `asyncpg`)
- JWT authentication (`python-jose`, HS256)
- Docker / Docker Compose

## Architecture

High-level message flow for a room:

1. Client connects to `GET /ws/{room_id}` and provides `token` as a query parameter.
2. Server authenticates the token (`get_current_user_ws`) and registers the WebSocket in `ConnectionManager` (in-memory list per room, per instance).
3. Server loads the last `50` messages for that room from PostgreSQL and sends them as:
   - `{"type":"history","messages":[...]}`
4. Server subscribes to the Redis channel `chat:room:{room_id}` and starts a listener task.
5. When the client sends:
   - `{"type":"message","data":{"content":"..."}}`
   the server validates it via `MessageCreate`, persists it to PostgreSQL, and publishes the full message payload to Redis.
6. Redis delivers the published event to **all API instances** subscribed to that channel.
7. Each instance forwards the event to its connected clients via `ConnectionManager.broadcast(...)`.

### Why Redis Pub/Sub (not only in-memory connections)

In-memory connection lists only work inside a single process. With multiple API replicas, each instance would only know about its own WebSockets, so broadcasts would be incomplete.

Redis Pub/Sub provides a shared distribution mechanism:
- each instance subscribes to the room channel
- only one event needs to be published
- every replica that has active clients for the room will broadcast it locally

This enables horizontal scaling without maintaining cross-instance WebSocket state.

## Features

- Rooms with message history (last `50` messages sent immediately on connect)
- JWT authentication via query parameter (`?token=...`) for WebSocket endpoints
- Typing indicator (`type=typing`) is published to Redis for real-time UI updates but **not** persisted in PostgreSQL
- Horizontal scaling: Redis Pub/Sub fan-out across multiple API replicas

## Local development

Prerequisites: Docker + Docker Compose.

1. Create environment file:
   - `cp .env.example .env`
2. Start services:
   - `docker compose up --build -d`
3. Run migrations:
   - `docker compose exec api alembic upgrade head`
4. Run tests (inside the API container):
   - `docker compose exec api python -m pytest -v --cov=app --cov-report=term-missing --cov-report=xml`

API:
- REST: `http://localhost:8010`
- WebSocket: `ws://localhost:8010/ws/{room_id}?token=...`

## WebSocket client example (JavaScript)

```js
const roomId = "PUT_ROOM_UUID_HERE";
const token = "PUT_JWT_HERE";

const ws = new WebSocket(
  `ws://localhost:8010/ws/${roomId}?token=${encodeURIComponent(token)}`
);

ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  if (payload.type === "history") {
    console.log("history:", payload.messages);
  } else if (payload.type === "message") {
    console.log("message:", payload.data);
  }
};

// Send a chat message
ws.send(
  JSON.stringify({
    type: "message",
    data: { content: "hello" },
  })
);

// Typing indicator (not stored in DB)
ws.send(
  JSON.stringify({
    type: "typing",
    data: { is_typing: true },
  })
);
```

## API endpoints

All authenticated endpoints require a valid JWT.

### Auth

- `POST /auth/register`
  - body: `{ "username": "...", "email": "...", "password": "..." }`
- `POST /auth/token`
  - body: OAuth2 form (`username`, `password`)

### Rooms (REST)

- `POST /rooms`
  - body: `{ "name": "...", "description": "..." }`
- `GET /rooms`
  - lists rooms with `online_count` (derived from active WebSockets on the current instance)
- `GET /rooms/{room_id}/history?limit=50&offset=0`
  - paginated message history

### WebSocket

- `GET /ws/{room_id}?token=...`
  - Sends initial history: `{ "type":"history", "messages":[...] }`
  - Accepts:
    - `{"type":"message","data":{"content":"..."}}`
    - `{"type":"typing","data":{"is_typing":true}}`


