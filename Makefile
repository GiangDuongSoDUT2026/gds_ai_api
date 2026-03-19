.PHONY: setup up down migrate test lint format worker api

setup:
	uv sync --all-packages

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd migrations && alembic upgrade head

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

worker:
	uv run --package worker celery -A worker.app worker --loglevel=info -Q gpu.high,gpu.medium,db -c 4

api:
	uv run --package api uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
