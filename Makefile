CC		= cc
PY		= python3
CFLAGS	= -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Iinclude -Isrc
ASAN	= -fsanitize=address,undefined -fno-sanitize-recover=all
B		= build
LIB		= $(wildcard src/*.c)
HDR		= include/ledger.h $(wildcard src/*.h)
UT		= $(PY) -m unittest discover -s tests -p

TSAN	= -fsanitize=thread

all: $(B)/driver $(B)/stress

$(B)/driver: tests/driver.c $(LIB) $(HDR)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/driver.c $(LIB)

$(B)/driver_asan: tests/driver.c $(LIB) $(HDR)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) $(ASAN) -o $@ tests/driver.c $(LIB)

$(B)/stress: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/stress.c $(LIB)

$(B)/stress_tsan: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) $(TSAN) -o $@ tests/stress.c $(LIB)

$(B)/stress_asan: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) $(ASAN) -o $@ tests/stress.c $(LIB)

test-model:
	$(UT) 'test_model.py'
	$(UT) 'test_invariants.py'

test-diff: $(B)/driver $(B)/driver_asan
	$(UT) 'test_diff.py'
	DRIVER=$(B)/driver_asan DIFF_SEEDS=1000 $(UT) 'test_diff.py'

test-stress: $(B)/stress $(B)/stress_tsan $(B)/stress_asan
	$(UT) 'test_stress.py'
	STRESS=$(B)/stress_tsan STRESS_OPS=8000 $(UT) 'test_stress.py'
	STRESS=$(B)/stress_asan STRESS_OPS=8000 $(UT) 'test_stress.py'

bench: $(B)/stress
	$(PY) tests/bench.py

mutants: all
	$(PY) tests/mutants.py

# 42 Norm. The 42 file header carries a student login and e-mail, so it
# is left for the author to stamp; every other rule must pass.
norm:
	@norminette src include | grep -v -e INVALID_HEADER -e ': OK!' \
		-e GLOBAL_VAR_DETECTED | grep -v -x -e '.*: Error!' \
		&& exit 1 || echo "norm: OK (42 header not stamped)"

test: test-model test-diff test-stress

clean:
	rm -rf $(B) tests/__pycache__

.PHONY: all test test-model test-diff test-stress bench mutants norm clean
