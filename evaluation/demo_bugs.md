# Deliberate bugs in `examples/fastapi-auth-demo`

The demo project is used for live demos and orchestration evaluation. It contains two
realistic bugs that both cause **401 Unauthorized even with a valid bearer token**. They
are intentionally not mentioned anywhere inside the demo folder, so the assistant has to
find them from the code. This file lives outside the demo folder (and `evaluation/` is in
`.assistantignore`), so it is never indexed.

## 1. Case-sensitive scheme check (`app/auth.py`, `parse_authorization_header`)

```python
scheme, _, token = value.strip().partition(" ")
if scheme != "bearer" or not token:
    raise AuthError("Invalid authentication scheme")
```

The scheme is compared case-sensitively against lowercase `"bearer"`. Standard clients
(and the README/docs) send `Authorization: Bearer <token>`, so every normal request fails
with `401 Invalid authentication scheme`. Auth schemes are case-insensitive (RFC 7235).
Fix: `if scheme.lower() != "bearer" ...`.

## 2. Secret read from the wrong environment variable (`app/config.py`)

```python
jwt_secret=os.getenv("JWT_SECRET", "dev-secret"),
```

The README and `.env.example` document `JWT_SECRET_KEY` (the secret shared with the
auth service). The code reads `JWT_SECRET`, which isn't set, so it silently falls back
to `"dev-secret"`. Tokens signed by the auth service then fail signature verification
(`401 Invalid token`). Fix: read `JWT_SECRET_KEY` and fail at startup if it's missing.

Either bug alone produces the symptom; fixing only one still leaves 401s.
