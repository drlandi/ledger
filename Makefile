CC		= cc
PY		= python3
CFLAGS	= -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -D_GNU_SOURCE \
		  -Iinclude -Isrc
ASAN	= -fsanitize=address,undefined -fno-sanitize-recover=all
B		= build
LIB		= $(wildcard src/*.c)
HDR		= include/ledger.h $(wildcard src/*.h)
UT		= $(PY) -m unittest discover -s tests -p

TSAN	= -fsanitize=thread

all: $(B)/driver $(B)/stress $(B)/ledger $(B)/crash_worker \
	$(B)/crash_worker_tiny $(B)/crash_worker_tsan

$(B)/crash_worker_tsan: tests/crash_worker.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(TSAN) -DLG_WAL_BUF=128 -o $@ tests/crash_worker.c $(LIB)

$(B)/crash_worker_tiny: tests/crash_worker.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) -DLG_WAL_BUF=128 -o $@ tests/crash_worker.c $(LIB)

$(B)/crash_worker: tests/crash_worker.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) -o $@ tests/crash_worker.c $(LIB)

$(B)/ledger: cli/ledger_cli.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) -o $@ cli/ledger_cli.c $(LIB)

$(B)/ledger_asan: cli/ledger_cli.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(ASAN) -o $@ cli/ledger_cli.c $(LIB)

$(B)/driver: tests/driver.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) -o $@ tests/driver.c $(LIB)

$(B)/driver_asan: tests/driver.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(ASAN) -o $@ tests/driver.c $(LIB)

$(B)/stress: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) -o $@ tests/stress.c $(LIB)

$(B)/stress_tsan: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(TSAN) -o $@ tests/stress.c $(LIB)

$(B)/stress_asan: tests/stress.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(ASAN) -o $@ tests/stress.c $(LIB)

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

test-recovery: $(B)/ledger $(B)/ledger_asan
	$(UT) 'test_recovery.py'
	LEDGER=$(B)/ledger_asan $(UT) 'test_recovery.py'

test-crash: all
	$(UT) 'test_crash.py'

# The long version of the crash test: 1000 kills.
crash-long: all
	$(PY) tests/crash_harness.py --iterations 1000 --threads 64

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

test: test-model test-diff test-stress test-recovery test-crash

clean:
	rm -rf $(B) tests/__pycache__

.PHONY: all test test-model test-diff test-stress test-recovery test-crash crash-long bench mutants norm clean
