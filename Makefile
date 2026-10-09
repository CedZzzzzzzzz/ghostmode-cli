.PHONY: test lint typecheck demo demo-reset

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy --strict ghostmode

demo:
	python -m ghostmode run "pytest demo_project -q"

demo-reset:
	cp demo_project/auth.py.orig demo_project/auth.py
