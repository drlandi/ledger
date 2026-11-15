/* Workload process for the crash harness. It is meant to be killed.
 *
 *   crash_worker LOG ACKDIR THREADS ACCOUNTS ITER SEED full|process OPS
 *
 * Opens the ledger (recovering whatever the previous victim left),
 * creates accounts up to ACCOUNTS + 1 if any are missing, then runs
 * THREADS threads of transfers until killed (OPS = 0) or until each
 * thread has done OPS operations.
 *
 * After every lg_transfer that returns, the thread appends one binary
 * record to ACKDIR/ack.<thread>:
 *     id u64, from u64, to u64, amount i64, durable u64, rc u32, pad u32
 * "durable" is the log offset the ledger reported as safely on disk at
 * that moment. An ack on disk therefore proves the call returned; the
 * harness holds the ledger to every one of them.
 *
 * Transfer parameters are a pure function of the id. Ids are
 * (ITER << 32) + n, where n comes from a shared counter divided by 2,
 * so every id is submitted twice at almost the same time; some
 * submissions go back to ids of earlier iterations (whose fate was
 * decided by an earlier crash), and a few of those change the amount.
 *
 * Exit codes: 0 done, 3 setup failed, 4 the ledger reported LG_ERROR
 * (and kept reporting it), 5 it reported LG_ERROR and then worked again.
 */
#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "ledger.h"

typedef struct s_ack
{
	t_lg_xfer	x;
	uint64_t	durable;
	uint32_t	rc;
	uint32_t	pad;
}	t_ack;

static t_ledger			*g_l;
static uint64_t			g_accounts;
static uint64_t			g_iter;
static uint64_t			g_seed;
static uint64_t			g_ops;
static const char		*g_ackdir;
static atomic_ullong	g_ctr;

static uint64_t	mix(uint64_t x)
{
	x += 0x9e3779b97f4a7c15ULL;
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
	x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
	return (x ^ (x >> 31));
}

static void	derive(uint64_t id, t_lg_xfer *x)
{
	uint64_t	h;

	h = mix(id ^ g_seed);
	x->id = id;
	x->from = h % (g_accounts + 1);
	if (h % 24 == 0)
		x->from = 0;
	x->to = mix(h) % (g_accounts + 1);
	if (x->to == x->from)
		x->to = (x->from + 1) % (g_accounts + 1);
	x->amount = 1 + (int64_t)(mix(h + 1) % 300);
}

static void	*worker(void *arg)
{
	uint64_t	rng = mix(g_seed + g_iter * 1000 + (uintptr_t)arg);
	char		path[4096];
	t_lg_stats	st;
	t_ack		a;
	uint64_t	i;
	uint64_t	n;
	int			fd;

	snprintf(path, sizeof(path), "%s/ack.%d", g_ackdir, (int)(uintptr_t)arg);
	fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (fd < 0)
		exit(3);
	memset(&a, 0, sizeof(a));
	for (i = 0; g_ops == 0 || i < g_ops; i++)
	{
		rng = mix(rng);
		n = atomic_fetch_add(&g_ctr, 1) / 2;
		if (rng % 10 == 0 && g_iter > 0)
			derive(((rng >> 8) % g_iter << 32) + (rng >> 40) % 3000, &a.x);
		else
			derive((g_iter << 32) + n, &a.x);
		if (rng % 10 == 0 && (rng >> 20) % 8 == 0)
			a.x.amount += 1;
		a.rc = lg_transfer(g_l, &a.x);
		if (a.rc == LG_ERROR)
		{
			/* Fail-stop: once the log failed, nothing may succeed. */
			if (lg_transfer(g_l, &a.x) != LG_ERROR
				|| lg_balance(g_l, 0, &a.x.amount) != LG_ERROR
				|| lg_create(g_l, 0, 0, &n) != LG_ERROR)
				_exit(5);
			fprintf(stderr, "worker: LG_ERROR\n");
			_exit(4);
		}
		lg_stats(g_l, &st);
		a.durable = st.durable;
		if (write(fd, &a, sizeof(a)) != sizeof(a))
			_exit(3);
	}
	return (NULL);
}

int	main(int argc, char **argv)
{
	t_lg_config	cfg;
	pthread_t	th[256];
	t_lg_stats	st;
	uint64_t	id;
	t_lg_rc		rc;
	int			threads;
	int			t;

	if (argc != 9)
		return (2);
	memset(&cfg, 0, sizeof(cfg));
	cfg.wal_path = argv[1];
	g_ackdir = argv[2];
	threads = atoi(argv[3]);
	g_accounts = strtoull(argv[4], NULL, 10);
	g_iter = strtoull(argv[5], NULL, 10);
	g_seed = strtoull(argv[6], NULL, 10);
	g_ops = strtoull(argv[8], NULL, 10);
	cfg.max_accounts = g_accounts + 1;
	if (!strcmp(argv[7], "process"))
		cfg.sync_mode = LG_SYNC_PROCESS;
	if (threads < 1 || threads > 256 || g_accounts < 3)
		return (2);
	rc = lg_open(&cfg, &g_l);
	if (rc != LG_OK)
	{
		fprintf(stderr, "worker: open: %s\n", lg_rc_name(rc));
		return (3);
	}
	while (lg_count(g_l) < g_accounts + 1)
	{
		if (lg_count(g_l) == 0)
			rc = lg_create(g_l, 0, LG_ACC_OVERDRAFT, &id);
		else
			rc = lg_create(g_l, 1000, 0, &id);
		if (rc == LG_ERROR)
			return (4);
		if (rc != LG_OK)
			return (3);
	}
	for (t = 0; t < threads; t++)
		pthread_create(&th[t], NULL, worker, (void *)(uintptr_t)t);
	for (t = 0; t < threads; t++)
		pthread_join(th[t], NULL);
	/* Only reached with OPS > 0: report how many write batches the
	 * log made, so a test can compare that with the system calls. */
	lg_stats(g_l, &st);
	printf("flushes=%" PRIu64 "\n", st.flushes);
	lg_close(g_l);
	return (0);
}
