/* Multi-threaded stress and benchmark tool for the ledger core.
 *
 * Every transfer's parameters are a pure function of its id, so any
 * thread can re-submit any id and it is a true duplicate. Fresh ids
 * come from one shared counter divided by --dup, so each id is handed
 * to --dup threads at almost the same moment. Some submissions replay
 * an old id, and a few replay one with a changed amount.
 *
 * Benchmark use (--dup 1, no --replay-pct, no --out): ids come from
 * per-thread ranges and progress counters are per-thread, so the tool
 * itself shares no written cache line between threads and the numbers
 * measure the ledger, not the harness.
 *
 * With --out FILE the tool records what it saw, for the Python checker:
 *   A n                        accounts at the end
 *   I id initial flags         one per account
 *   S count b0 b1 ...          consistent snapshot taken while running
 *   F b0 b1 ...                final balances
 *   T id from to amount rc     every submission and its result code
 *
 * A watchdog aborts with exit code 42 if no thread makes progress for
 * --watchdog seconds: that is what a deadlock looks like from outside.
 */
#define _GNU_SOURCE
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "ledger.h"

#define INITIAL 1000

typedef struct s_rec
{
	t_lg_xfer	x;
	int			rc;
}	t_rec;

typedef struct s_opt
{
	int			threads;
	uint64_t	accounts;
	uint64_t	hot;
	unsigned	hot_pct;
	uint64_t	ops;
	uint64_t	dup;
	unsigned	replay_pct;
	uint64_t	seed;
	int			mode;
	int			watchdog;
	int			grow;
	const char	*out;
}	t_opt;

typedef struct s_worker
{
	pthread_t	th;
	int			idx;
	t_rec		*recs;
	uint64_t	nrec;
	atomic_ullong	done;
	char		pad[64];
}	t_worker;

static t_opt		g_o = {8, 64, 4, 50, 100000, 1, 0, 1, LG_LOCK_FINE,
	10, 0, NULL};
static t_ledger		*g_l;
static atomic_ullong	g_ctr;
static t_worker		*g_w;
static atomic_int	g_stop;
static FILE			*g_out;
static pthread_mutex_t	g_out_lock = PTHREAD_MUTEX_INITIALIZER;

static uint64_t	mix(uint64_t x)
{
	x += 0x9e3779b97f4a7c15ULL;
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
	x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
	return (x ^ (x >> 31));
}

static uint64_t	pick(uint64_t h)
{
	if (h % 100 < g_o.hot_pct)
		return ((h >> 8) % g_o.hot);
	return ((h >> 8) % g_o.accounts);
}

/* The transfer that belongs to an id. Always the same answer. */
static void	derive(uint64_t id, t_lg_xfer *x)
{
	uint64_t	h;

	h = mix(id ^ g_o.seed);
	x->id = id;
	x->from = pick(h);
	x->to = pick(mix(h));
	if (x->to == x->from)
		x->to = (x->from + 1) % g_o.accounts;
	x->amount = 1 + (int64_t)(mix(h + 1) % 100);
}

static void	*worker(void *arg)
{
	t_worker	*w = arg;
	uint64_t	rng = mix(g_o.seed + 1000 + (uint64_t)w->idx);
	uint64_t	base = g_o.seed << 32;
	uint64_t	i;
	uint64_t	n;
	t_lg_xfer	x;
	int			rc;

	for (i = 0; i < g_o.ops && !atomic_load(&g_stop); i++)
	{
		rng = mix(rng);
		if (g_o.dup == 1 && g_o.replay_pct == 0)
			n = ((uint64_t)w->idx << 40) + i;
		else
			n = atomic_fetch_add(&g_ctr, 1) / g_o.dup;
		if (rng % 100 < g_o.replay_pct)
			n = (rng >> 16) % (n + 1);
		derive(base + n, &x);
		if (g_o.replay_pct && (rng >> 40) % 64 == 0)
			x.amount += 1;
		rc = lg_transfer(g_l, &x);
		if (w->recs)
		{
			w->recs[w->nrec].x = x;
			w->recs[w->nrec++].rc = rc;
		}
		atomic_store_explicit(&w->done, i + 1, memory_order_relaxed);
	}
	return (NULL);
}

static void	print_balances(const char *tag, t_lg_snap *s, int with_count)
{
	uint64_t	i;

	pthread_mutex_lock(&g_out_lock);
	fputs(tag, g_out);
	if (with_count)
		fprintf(g_out, " %" PRIu64, s->count);
	for (i = 0; i < s->count; i++)
		fprintf(g_out, " %" PRId64, s->balance[i]);
	fputc('\n', g_out);
	pthread_mutex_unlock(&g_out_lock);
}

/* Takes consistent snapshots while the workers are running. */
static void	*auditor(void *arg)
{
	t_lg_snap	s;
	int			n;

	(void)arg;
	n = 0;
	while (!atomic_load(&g_stop) && n < 3000)
	{
		if (lg_snapshot(g_l, &s) != LG_OK)
			exit(3);
		print_balances("S", &s, 1);
		lg_snap_free(&s);
		n++;
		usleep(300);
	}
	return (NULL);
}

/* Creates the second half of the accounts while transfers are running. */
static void	*creator(void *arg)
{
	uint64_t	id;

	(void)arg;
	while (!atomic_load(&g_stop))
	{
		if (lg_create(g_l, INITIAL, 0, &id) != LG_OK)
			break ;
		usleep(200);
	}
	return (NULL);
}

static double	now(void)
{
	struct timespec	ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (ts.tv_sec + ts.tv_nsec / 1e9);
}

/* Returns when all work is done; exits 42 if progress stops. */
static void	watch(uint64_t total)
{
	uint64_t	last = 0;
	uint64_t	cur;
	double		since = now();

	while (1)
	{
		cur = 0;
		for (int t = 0; t < g_o.threads; t++)
			cur += atomic_load(&g_w[t].done);
		if (cur >= total)
			return ;
		if (cur != last)
		{
			last = cur;
			since = now();
		}
		else if (now() - since > g_o.watchdog)
		{
			fprintf(stderr, "WATCHDOG: no progress for %d s at %" PRIu64
				"/%" PRIu64 " operations: deadlock\n", g_o.watchdog, cur,
				total);
			_exit(42);
		}
		usleep(2000);
	}
}

static void	write_results(t_worker *w)
{
	t_lg_snap	s;
	uint64_t	i;
	int			t;

	if (lg_snapshot(g_l, &s) != LG_OK)
		exit(3);
	fprintf(g_out, "A %" PRIu64 "\n", s.count);
	for (i = 0; i < s.count; i++)
		fprintf(g_out, "I %" PRIu64 " %d %u\n", i,
			i == 0 ? 0 : INITIAL, s.flags[i]);
	print_balances("F", &s, 0);
	lg_snap_free(&s);
	for (t = 0; t < g_o.threads; t++)
		for (i = 0; i < w[t].nrec; i++)
			fprintf(g_out, "T %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRId64
				" %s\n", w[t].recs[i].x.id, w[t].recs[i].x.from,
				w[t].recs[i].x.to, w[t].recs[i].x.amount,
				lg_rc_name(w[t].recs[i].rc));
}

static void	parse(int argc, char **argv)
{
	int	i;

	for (i = 1; i + 1 < argc; i += 2)
	{
		if (!strcmp(argv[i], "--threads"))
			g_o.threads = atoi(argv[i + 1]);
		else if (!strcmp(argv[i], "--accounts"))
			g_o.accounts = strtoull(argv[i + 1], NULL, 10);
		else if (!strcmp(argv[i], "--hot"))
			g_o.hot = strtoull(argv[i + 1], NULL, 10);
		else if (!strcmp(argv[i], "--hot-pct"))
			g_o.hot_pct = atoi(argv[i + 1]);
		else if (!strcmp(argv[i], "--ops"))
			g_o.ops = strtoull(argv[i + 1], NULL, 10);
		else if (!strcmp(argv[i], "--dup"))
			g_o.dup = strtoull(argv[i + 1], NULL, 10);
		else if (!strcmp(argv[i], "--replay-pct"))
			g_o.replay_pct = atoi(argv[i + 1]);
		else if (!strcmp(argv[i], "--seed"))
			g_o.seed = strtoull(argv[i + 1], NULL, 10);
		else if (!strcmp(argv[i], "--watchdog"))
			g_o.watchdog = atoi(argv[i + 1]);
		else if (!strcmp(argv[i], "--grow"))
			g_o.grow = atoi(argv[i + 1]);
		else if (!strcmp(argv[i], "--out"))
			g_o.out = argv[i + 1];
		else if (!strcmp(argv[i], "--mode") && !strcmp(argv[i + 1], "fine"))
			g_o.mode = LG_LOCK_FINE;
		else if (!strcmp(argv[i], "--mode") && !strcmp(argv[i + 1], "global"))
			g_o.mode = LG_LOCK_GLOBAL;
		else if (!strcmp(argv[i], "--mode") && !strcmp(argv[i + 1], "naive"))
			g_o.mode = LG_LOCK_NAIVE;
		else
			break ;
	}
	if (i < argc || g_o.threads < 1 || g_o.accounts < 2 || g_o.hot < 1
		|| g_o.hot > g_o.accounts || g_o.dup < 1)
	{
		fprintf(stderr, "bad arguments (see tests/stress.c)\n");
		exit(2);
	}
}

static void	setup(void)
{
	t_lg_config	cfg;
	uint64_t	id;
	uint64_t	first;

	memset(&cfg, 0, sizeof(cfg));
	cfg.max_accounts = g_o.accounts;
	cfg.lock_mode = g_o.mode;
	if (lg_open(&cfg, &g_l) != LG_OK)
		exit(3);
	first = g_o.accounts;
	if (g_o.grow)
		first = g_o.accounts / 2;
	if (lg_create(g_l, 0, LG_ACC_OVERDRAFT, &id) != LG_OK)
		exit(3);
	while (id + 1 < first)
		if (lg_create(g_l, INITIAL, 0, &id) != LG_OK)
			exit(3);
}

int	main(int argc, char **argv)
{
	t_worker	*w;
	pthread_t	aud;
	pthread_t	cre;
	double		t0;
	double		dt;
	int			t;

	parse(argc, argv);
	setup();
	w = aligned_alloc(64, g_o.threads * sizeof(*w));
	memset(w, 0, g_o.threads * sizeof(*w));
	g_w = w;
	if (g_o.out)
		g_out = fopen(g_o.out, "w");
	if (g_o.out && !g_out)
		exit(3);
	t0 = now();
	for (t = 0; t < g_o.threads; t++)
	{
		w[t].idx = t;
		if (g_out)
			w[t].recs = malloc(g_o.ops * sizeof(t_rec));
		pthread_create(&w[t].th, NULL, worker, &w[t]);
	}
	if (g_out)
		pthread_create(&aud, NULL, auditor, NULL);
	if (g_o.grow)
		pthread_create(&cre, NULL, creator, NULL);
	watch(g_o.ops * g_o.threads);
	for (t = 0; t < g_o.threads; t++)
		pthread_join(w[t].th, NULL);
	dt = now() - t0;
	atomic_store(&g_stop, 1);
	if (g_out)
		pthread_join(aud, NULL);
	if (g_o.grow)
		pthread_join(cre, NULL);
	if (g_out)
		write_results(w);
	printf("threads=%d ops=%" PRIu64 " seconds=%.3f ops_per_sec=%.0f"
		" verify=%s\n", g_o.threads, g_o.ops * g_o.threads, dt,
		g_o.ops * g_o.threads / dt, lg_rc_name(lg_verify(g_l)));
	for (t = 0; t < g_o.threads; t++)
		free(w[t].recs);
	free(w);
	if (g_out)
		fclose(g_out);
	lg_close(g_l);
	return (0);
}
