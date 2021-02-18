SHA = $(shell git rev-parse HEAD)

build:
	docker build . -t minimalci:${SHA}

test:
	python3 -u tests/test_executors.py
	python3 -u tests/test_taskrunner.py
	python3 -u tests/test_failed_import.py
	python3 -u tests/test_statesnapshot.py
	python3 -u tests/test_semaphore.py
	python3 -u tests/test_util.py

tasks:
	python3 -m minimalci.taskrunner --commit $(SHA)

check:
	mypy minimalci --strict
	mypy server --strict
	mypy tests --strict
	mypy . --strict

dev:
	docker-compose build && docker-compose kill && docker-compose up -d && docker-compose logs -f

clean:
	@rm **/.mypy_cache -r || true
	@rm **/__pycache__ -r || true
	@rm .mypy_cache -r || true
	@rm __pycache__ -r || true
