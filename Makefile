PYTHON ?= python3

.PHONY: run test verify docker
run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

test:
	$(PYTHON) -m unittest discover -s tests -v

verify:
	$(PYTHON) scripts/verify_api.py --base-url http://localhost:8000

docker:
	docker build -t travel-python-fastapi .
