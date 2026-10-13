# Sample Project

A tiny fake repository used by the ingestion tests. It models a small web
service with user login and a PostgreSQL database.

## Setup

1. Create a virtual environment: `python -m venv .venv`
2. Activate it: `source .venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in `DATABASE_PASSWORD`.
5. Start PostgreSQL locally (see `config.yaml` for the expected host and port).

## Usage

Run `python main.py` to print a greeting.

## Running tests

Run `pytest` from the project root.
