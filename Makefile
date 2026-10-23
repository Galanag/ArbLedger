.PHONY: install test coverage demo benchmark
install:
	python -m pip install -e '.[test]'

test:
	pytest -q

coverage:
	pytest --cov=arbledger --cov-branch --cov-report=term-missing --cov-fail-under=95

demo:
	python -m arbledger

benchmark:
	PYTHONPATH=src python benchmarks/benchmark_simulation.py --quotes 10000 --workers 4
