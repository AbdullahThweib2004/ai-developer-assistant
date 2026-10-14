# Authentication

Clients obtain an access token from the central auth service and send it with every
request:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## Token format

Access tokens are JWTs signed with HMAC (`HS256` by default) using the shared secret
configured as `JWT_SECRET_KEY`. Required claims:

| Claim | Meaning |
|---|---|
| `sub` | User id |
| `exp` | Expiry (Unix time) |
| `iat` | Issued at (Unix time) |
| `roles` | List of roles, e.g. `["user"]` or `["user", "admin"]` |

## Request flow

1. `get_current_user` (a FastAPI dependency) reads the `Authorization` header.
2. The header is split into scheme and token; only bearer tokens are accepted.
3. The token signature, expiry and age are verified with the configured secret.
4. The `sub` claim is looked up in the user store.

Any failure in steps 1–4 returns **401 Unauthorized** with a `WWW-Authenticate: Bearer`
header. A valid token for a user without the required role returns **403 Forbidden**.
