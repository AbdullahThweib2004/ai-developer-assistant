# Troubleshooting

## DatabaseConnectionError: Could not connect

The service could not reach PostgreSQL. Check that the database is running
and that `database.host` and `database.port` in `config.yaml` are correct.
The client retries three times with exponential backoff before giving up.

## Login always fails

If every login is rejected, make sure passwords were hashed with the same
PBKDF2 iteration count that `auth.py` uses. Changing the iteration count
invalidates existing password hashes.
