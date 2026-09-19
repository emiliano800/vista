.PHONY: setup check test test-py test-js lint fmt demo start run clean

setup:            ## install everything and run the tests
	./setup.sh

check:            ## only report which prerequisites are installed
	./setup.sh --check

test: test-py test-js

test-py:
	uv run ruff check . && uv run ruff format --check .
	uv run pytest -q tests/test_pipeline.py

test-js:
	cd src/recorder && npm test

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
	rm -rf out/ .pytest_cache .ruff_cache src/recorder/node_modules
