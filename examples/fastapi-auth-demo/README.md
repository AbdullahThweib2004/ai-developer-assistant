# FastAPI Auth Demo

A small orders-and-users API protected by JWT bearer tokens. Access tokens are issued
by the company's central auth service and signed with a shared HMAC secret; this API
only **verifies** them.

## Setup

1. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install fastapi uvicorn pyjwt
   ```

2. Copy `.env.example` to `.env` and set `JWT_SECRET_KEY` to the shared secret used by
   the auth service. `JWT_ALGORITHM` must match the algorithm the auth service signs with
   (default `HS256`).
3. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | Liveness check |
| GET | `/users/me` | bearer token | The current user (from the token's `sub` claim) |
| GET | `/users/{user_id}` | bearer token, role `admin` | Look up another user |

Send the token in the standard header:

```
Authorization: Bearer <access token>
```

See [docs/auth.md](docs/auth.md) for the token format and claims.
