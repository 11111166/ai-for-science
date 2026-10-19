.PHONY: install api web seed test build

install:
	npm install
	pip install -r services/api/requirements.txt

api:
	cd services/api && uvicorn app.main:app --reload --port 8000

web:
	npm run dev:web

seed:
	cd services/api && python seed_local_demo.py

test:
	cd services/api && pytest -q tests

build:
	npm run build:web
