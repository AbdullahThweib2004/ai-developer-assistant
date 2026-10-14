"""Application settings, read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    jwt_algorithm: str
    access_token_max_age_minutes: int
    database_url: str


def load_settings() -> Settings:
    return Settings(
        jwt_secret=os.getenv("JWT_SECRET", "dev-secret"),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        access_token_max_age_minutes=int(os.getenv("ACCESS_TOKEN_MAX_AGE_MINUTES", "60")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./demo.db"),
    )


settings = load_settings()
