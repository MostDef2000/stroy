.PHONY: infra-up infra-down infra-logs install lint test check api worker-fake

infra-up:
	docker compose up -d postgres redis minio

infra-down:
	docker compose down

infra-logs:
	docker compose logs -f postgres redis minio

install:
	python3 -m pip install -e ".[dev]"

lint:
	ruff check src tests

test:
	pytest -q

check:
	python3 scripts/check_json.py
	ruff check src tests
	pytest -q

api:
	uvicorn stroy.api.main:app --host 127.0.0.1 --port 8000 --reload

worker-fake:
	stroy-worker
