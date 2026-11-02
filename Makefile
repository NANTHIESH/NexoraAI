# Cross-platform note: on Windows without `make`, run the plain commands shown after each target.
PY ?= python

.PHONY: setup data docs load index api ui test lint eval demo smoke

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

demo: data docs load index test
	$(PY) -m scripts.run_demo

smoke:   ## python -m scripts.smoke_test (API must be running)
	$(PY) -m scripts.smoke_test
