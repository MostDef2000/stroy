.PHONY: infra-up infra-down infra-logs install lint test check api web worker-fake password-hash

infra-up:
	docker compose up -d postgres redis minio

infra-down:
	docker compose down

infra-logs:
	docker compose logs -f postgres redis minio

install:
	python3 -m pip install -e ".[dev]"
	cd apps/web && npm install

lint:
	ruff check src tests

test:
	pytest -q

check:
	python3 scripts/check_json.py
	ruff check src tests
	pytest -q
	cd apps/web && npm run build

password-hash:
	python3 scripts/hash_password.py

api:
	uvicorn stroy.api.main:app --host 127.0.0.1 --port 8000 --reload

web:
	cd apps/web && npm run dev

worker-fake:
	stroy-worker
