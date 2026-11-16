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
	$(B)/crash_worker_tiny $(B)/crash_worker_tsan $(B)/gate $(B)/gate_tsan

# The gate tool decides when fdatasync returns (see tests/gate.c).
GATE	= -DLG_WAL_BUF=128 -Wl,--wrap=fdatasync

$(B)/gate: tests/gate.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(GATE) -o $@ tests/gate.c $(LIB)

$(B)/gate_tsan: tests/gate.c $(LIB) $(HDR)
	@mkdir -p $(B)
	@echo "  CC  $@"
	@$(CC) $(CFLAGS) $(TSAN) $(GATE) -o $@ tests/gate.c $(LIB)

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
	$(UT) 'test_torn.py'
	LEDGER=$(B)/ledger_asan $(UT) 'test_torn.py'

# Gaps 1 and 3: fdatasync held shut by the test; buffer-full storms
# under ThreadSanitizer and kill -9; reads checked against the log.
test-groupcommit: all
	$(UT) 'test_groupcommit.py'

test-crash: all
	$(UT) 'test_crash.py'

# The headline result: about two million durable transfers, 400 kills.
# DEMO_KILLS=40 make demo gives a 20-second version.
DEMO_KILLS ?= 400
demo: all
	@echo "Running $(DEMO_KILLS) rounds of: 64 threads transfer money," \
		"kill -9, damage the log tail, recover, check."
	@$(PY) tests/crash_harness.py --iterations $(DEMO_KILLS) --threads 64 \
		--min-ms 150 --max-ms 450 --epoch 10 --seed 7

# The long version of the crash test: 1000 kills.
crash-long: all
	$(PY) tests/crash_harness.py --iterations 1000 --threads 64

bench: $(B)/stress
	$(PY) tests/bench.py

# Optional (needs `pip install hypothesis`): shrinks failing cases.
test-property: $(B)/driver
	$(UT) 'test_property.py'

mutants: all
	$(PY) tests/mutants.py

# 42 Norm. The 42 file header carries a student login and e-mail, so it
# is left for the author to stamp; every other rule must pass.
norm:
	@norminette src include | grep -v -e INVALID_HEADER -e ': OK!' \
		-e GLOBAL_VAR_DETECTED | grep -v -x -e '.*: Error!' \
		&& exit 1 || echo "norm: OK (42 header not stamped)"

test: test-model test-diff test-stress test-recovery test-crash \
	test-groupcommit

clean:
	rm -rf $(B) tests/__pycache__

.PHONY: all test test-model test-diff test-stress test-recovery test-crash test-groupcommit crash-long demo bench test-property mutants norm clean
