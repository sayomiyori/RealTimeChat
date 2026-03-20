# realtime-chat

FastAPI + WebSocket + Redis + PostgreSQL (SQLAlchemy 2 async) + JWT authentication.

## Local run with Docker

1. Create `.env` from `.env.example`
2. Start services:
   - `docker compose up --build`

API will be available at `http://localhost:8000`.

## Notes

- JWT secret and token TTL are configured via environment variables.
- Redis connection is initialized on application startup.

