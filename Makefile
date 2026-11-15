PY		= python3

all:

test-model:
	cd tests && $(PY) -m unittest test_model test_invariants

test: test-model

clean:
	rm -rf build tests/__pycache__

.PHONY: all test test-model clean
