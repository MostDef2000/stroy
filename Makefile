.PHONY: infra-up infra-down infra-logs check

infra-up:
	docker compose up -d postgres redis minio

infra-down:
	docker compose down

infra-logs:
	docker compose logs -f postgres redis minio

check:
	python3 scripts/check_json.py
