# Cross-platform note: on Windows without `make`, run the plain commands shown after each target.
PY ?= python

.PHONY: setup data docs load index api ui web test lint eval gate demo smoke

setup:   ## python -m scripts.setup_models
	$(PY) -m scripts.setup_models

data:    ## python -m scripts.generate_data
	$(PY) -m scripts.generate_data

docs:    ## python -m scripts.make_documents
	$(PY) -m scripts.make_documents

load:    ## python -m scripts.load_db
	$(PY) -m scripts.load_db

index:   ## python -m scripts.build_index
	$(PY) -m scripts.build_index

api:     ## python -m uvicorn nexora.api.main:app --port 8000
	$(PY) -m uvicorn nexora.api.main:app --port 8000

ui:      ## python -m streamlit run app/Home.py
	$(PY) -m streamlit run app/Home.py

test:    ## python -m pytest
	$(PY) -m pytest

lint:    ## python -m ruff check .
	$(PY) -m ruff check .

eval:    ## python -m evaluation.eval_rules ; eval_detection ; eval_time
	$(PY) -m evaluation.eval_retrieval
	$(PY) -m evaluation.eval_rules
	$(PY) -m evaluation.eval_detection
	$(PY) -m evaluation.eval_understanding
	$(PY) -m evaluation.eval_data_answers
	$(PY) -m evaluation.eval_safety
	$(PY) -m evaluation.eval_faithfulness
	$(PY) -m evaluation.eval_production
	$(PY) -m evaluation.eval_statistics

demo: data docs load index test
	$(PY) -m scripts.run_demo

smoke:   ## python -m scripts.smoke_test (API must be running)
	$(PY) -m scripts.smoke_test

web:     ## React front end: cd web && npm install && npm run dev  (http://localhost:5173, API must run)
	cd web && npm run dev

gate:    ## fail if a key evaluation number got worse (run `make eval` first)
	$(PY) -m evaluation.check_gate
