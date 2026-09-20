.PHONY: setup check test test-py test-js test-agents llm-smoke eval db lint fmt demo start run clean

COMPANY ?= ridgeway
PHASE ?= discover
DIVISION ?=
SECTOR ?= industrial_goods

setup:            ## install everything and run the tests
	./setup.sh

check:            ## only report which prerequisites are installed
	./setup.sh --check

test: test-py test-js

test-py:          ## ruff + pytest (backend tests skip unless Postgres is up: docker compose up -d)
	uv run ruff check . && uv run ruff format --check .
	uv run pytest -q

test-js:          ## recorder + web app + Cloudflare worker tests
	npm test

test-agents:      ## tier 1: agent phases with stubbed model, no DB, no tokens
	uv run pytest -q tests/test_agents.py

llm-smoke:        ## tier 0: prove the configured provider/model works (spends a few hundred tokens)
	uv run python scripts/llm_smoke.py

eval:             ## tier 3: score a phase against synthetic_data/answer_key.json (make eval COMPANY=ridgeway PHASE=discover DIVISION=11_billing_ar)
	uv run python scripts/eval_agents.py --phase $(PHASE) $(if $(filter analyze,$(PHASE)),--sector $(SECTOR),--company $(COMPANY) $(if $(DIVISION),--division $(DIVISION),)) $(if $(TENANT),--tenant $(TENANT),)

db:               ## local Postgres + MinIO for the backend tests and scripts/demo.py
	docker compose up -d

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

demo:             ## recorder with simulated apps, no OS permissions needed
	cd src/recorder && npm run start:demo

start:            ## real recorder
	cd src/recorder && npm start

run:              ## engine on synthetic data -> out/
	uv run python -m taskmining run --synthetic 40 --annotations auto --out out/

clean:
	rm -rf out/ .pytest_cache .ruff_cache node_modules src/recorder/node_modules
