# On Windows without make, run the same targets directly:
#   python scripts/check.py        (make check)
#   python -m pytest               (make test)
PY ?= python

.PHONY: install dev lock test check

install:
	$(PY) -m pip install --require-hashes -r requirements.txt
	$(PY) -m pip install --no-deps -e .

dev: install
	$(PY) -m pip install --require-hashes -r requirements-dev.txt

lock:
	$(PY) -m piptools compile --generate-hashes --allow-unsafe --strip-extras -o requirements.txt requirements.in
	$(PY) -m piptools compile --generate-hashes --allow-unsafe --strip-extras -o requirements-dev.txt requirements-dev.in

test:
	$(PY) -m pytest

check:
	$(PY) scripts/check.py
