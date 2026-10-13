"""Database connection handling."""

import time


class DatabaseConnectionError(Exception):
    """Raised when the database cannot be reached after all retries."""


def connect(host: str, port: int, retries: int = 3, backoff_seconds: float = 0.5):
    """Open a connection, retrying with exponential backoff.

    Raises DatabaseConnectionError if every attempt fails, for example when
    PostgreSQL is not running or the port in config.yaml is wrong.
    """
    for attempt in range(retries):
        try:
            return _open_socket(host, port)
        except ConnectionRefusedError:
            time.sleep(backoff_seconds * 2**attempt)
    raise DatabaseConnectionError(f"Could not connect to {host}:{port} after {retries} attempts")


def _open_socket(host: str, port: int):
    raise ConnectionRefusedError  # placeholder: no real database in the fixture
