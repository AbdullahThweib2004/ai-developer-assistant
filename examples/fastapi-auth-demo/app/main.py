"""FastAPI application entry point."""

from fastapi import FastAPI

from app.routes import users

app = FastAPI(title="FastAPI Auth Demo", version="0.3.1")
app.include_router(users.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
