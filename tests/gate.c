/* Deterministic test of "nothing observable before it is durable" and
 * of the buffer-full path of group commit (gaps 1 and 3).
 *
 *   gate LOG
 *
 * Built with -Wl,--wrap=fdatasync and -DLG_WAL_BUF=128. The linker
 * sends the ledger's fdatasync calls to __wrap_fdatasync below, which
 * blocks until the test hands out a "permit". The test therefore
 * decides when each batch becomes durable, and can look at who has
 * returned in between. No timing luck is involved in what is checked:
 *
 *   - "X must not have returned yet" is checked after waiting SETTLE
 *     ms while the disk is held shut: a correct ledger cannot return
 *     however long we wait; a broken one returns at once.
 *   - "X must return now" waits up to LIMIT seconds.
 *
 * A 128-byte commit buffer holds two transfer records plus the commit
 * record, so the third appender finds it full.
 *
 * Exit 0 and "PASS" on success; exit 1 with the reason otherwise.
 */
#include <inttypes.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "ledger.h"

#define SETTLE 150
#define LIMIT 10
#define REC 41
#define COMMIT 17

enum { XFER, BALANCE, LOOKUP, SNAPSHOT };

typedef struct s_actor
{
	const char	*name;
	int			op;
	t_lg_xfer	x;
	pthread_t	th;
	atomic_int	done;
	t_lg_rc		rc;
	int64_t		bal;
	t_lg_xfer	out;
	t_lg_snap	snap;
}	t_actor;

static t_ledger		*g_l;
static sem_t		g_permit;
static atomic_int	g_closed;
static atomic_int	g_blocked;

int	__real_fdatasync(int fd);

int	__wrap_fdatasync(int fd)
{
	if (atomic_load(&g_closed))
	{
		atomic_fetch_add(&g_blocked, 1);
		sem_wait(&g_permit);
		atomic_fetch_sub(&g_blocked, 1);
	}
	return (__real_fdatasync(fd));
}

static void	fail(const char *fmt, ...)
{
	va_list	ap;

	va_start(ap, fmt);
	printf("FAIL: ");
	vprintf(fmt, ap);
	printf("\n");
	fflush(stdout);
	_exit(1);
}

static void	*run(void *p)
{
	t_actor	*a;

	a = p;
	if (a->op == XFER)
		a->rc = lg_transfer(g_l, &a->x);
	else if (a->op == BALANCE)
		a->rc = lg_balance(g_l, a->x.from, &a->bal);
	else if (a->op == LOOKUP)
		a->rc = lg_lookup(g_l, a->x.id, &a->out);
	else
		a->rc = lg_snapshot(g_l, &a->snap);
	atomic_store(&a->done, 1);
	return (NULL);
}

static t_actor	*go(const char *name, int op, t_lg_xfer x)
{
	t_actor	*a;

	a = calloc(1, sizeof(*a));
	a->name = name;
	a->op = op;
	a->x = x;
	pthread_create(&a->th, NULL, run, a);
	return (a);
}

static t_lg_xfer	X(uint64_t id, uint64_t from, uint64_t to, int64_t amount)
{
	t_lg_xfer	x;

	x.id = id;
	x.from = from;
	x.to = to;
	x.amount = amount;
	return (x);
}

static t_lg_stats	stats(void)
{
	t_lg_stats	st;

	lg_stats(g_l, &st);
	return (st);
}

/* Polls `cond` (re-evaluated) for up to LIMIT seconds. */
#define AWAIT(cond, ...) \
	do { \
		int	w_; \
		for (w_ = 0; !(cond) && w_ < LIMIT * 1000; w_++) \
			usleep(1000); \
		if (!(cond)) \
			fail(__VA_ARGS__); \
	} while (0)

/* The disk is shut: none of these may have returned. */
static void	pending(const char *when, int n, ...)
{
	va_list	ap;
	t_actor	*a;

	usleep(SETTLE * 1000);
	va_start(ap, n);
	while (n-- > 0)
	{
		a = va_arg(ap, t_actor *);
		if (atomic_load(&a->done))
			fail("%s: '%s' returned %s before the data it depends on "
				"was durable", when, a->name, lg_rc_name(a->rc));
	}
	va_end(ap);
}

static void	finished(t_actor *a, t_lg_rc want)
{
	AWAIT(atomic_load(&a->done), "'%s' never returned", a->name);
	pthread_join(a->th, NULL);
	if (a->rc != want)
		fail("'%s' returned %s, expected %s", a->name, lg_rc_name(a->rc),
			lg_rc_name(want));
}

static void	permit(void)
{
	sem_post(&g_permit);
}

static void	blocked(const char *when)
{
	AWAIT(atomic_load(&g_blocked) == 1,
		"%s: expected exactly one thread in fdatasync, saw %d", when,
		atomic_load(&g_blocked));
}

/* One batch is in fdatasync; six observers of its effect must wait. */
static void	observers_wait_for_the_batch_in_flight(void)
{
	t_actor	*t1 = go("transfer 1", XFER, X(1, 1, 2, 100));
	t_actor	*bal;
	t_actor	*look;
	t_actor	*dup;
	t_actor	*conf;
	t_actor	*ins;
	t_actor	*snap;

	blocked("first transfer");
	bal = go("balance of the credited account", BALANCE, X(0, 2, 0, 0));
	look = go("lookup of transfer 1", LOOKUP, X(1, 0, 0, 0));
	dup = go("retry of transfer 1 (DUPLICATE)", XFER, X(1, 1, 2, 100));
	conf = go("id 1 with another amount (INVALID)", XFER, X(1, 1, 2, 99));
	ins = go("transfer from the emptied account (INSUFFICIENT_FUNDS)",
			XFER, X(2, 1, 3, 50));
	snap = go("snapshot", SNAPSHOT, X(0, 0, 0, 0));
	pending("batch 1 in fdatasync", 7, t1, bal, look, dup, conf, ins, snap);
	permit();
	finished(t1, LG_OK);
	finished(bal, LG_OK);
	finished(look, LG_OK);
	finished(dup, LG_DUPLICATE);
	finished(conf, LG_INVALID);
	finished(ins, LG_INSUFFICIENT_FUNDS);
	finished(snap, LG_OK);
	if (bal->bal != 100 || look->out.amount != 100 || look->out.to != 2)
		fail("wrong values after batch 1: balance %" PRId64, bal->bal);
	if (snap->snap.balance[1] != 0 || snap->snap.balance[2] != 100)
		fail("snapshot after batch 1 is wrong");
	printf("ok   6 observers waited for the batch in flight\n");
}

/* Transfer 4 sits in the buffer BEHIND the batch in flight. A reader
 * that saw it must wait for the next flush, not just the current one. */
static void	observers_wait_for_the_batch_still_in_the_buffer(void)
{
	uint64_t	base = stats().tail;
	t_actor		*t3 = go("transfer 3", XFER, X(3, 0, 3, 10));
	t_actor		*t4;
	t_actor		*bal;
	t_actor		*look;

	blocked("transfer 3");
	t4 = go("transfer 4", XFER, X(4, 0, 2, 5));
	AWAIT(stats().tail == base + REC + COMMIT + REC,
		"transfer 4 did not reach the log buffer");
	bal = go("balance that includes transfer 4", BALANCE, X(0, 2, 0, 0));
	look = go("lookup of transfer 4", LOOKUP, X(4, 0, 0, 0));
	pending("batch [3] in fdatasync", 4, t3, t4, bal, look);
	permit();
	finished(t3, LG_OK);
	blocked("batch [4]");
	pending("batch [3] durable, batch [4] not", 3, t4, bal, look);
	permit();
	finished(t4, LG_OK);
	finished(bal, LG_OK);
	finished(look, LG_OK);
	if (bal->bal != 105)
		fail("balance after transfer 4 is %" PRId64, bal->bal);
	printf("ok   readers waited for the batch behind the one in flight\n");
}

/* The buffer-full path: D finds the buffer full while it holds the
 * locks of accounts 4 and 5, and has to get it flushed first. */
static void	appender_finds_the_buffer_full(void)
{
	uint64_t	base;
	uint64_t	full = stats().full_waits;
	t_actor		*a = go("A", XFER, X(10, 0, 1, 1));
	t_actor		*b;
	t_actor		*c;
	t_actor		*d;
	t_actor		*r;
	t_actor		*e;
	t_actor		*f;

	blocked("A");
	base = stats().tail;
	b = go("B", XFER, X(11, 0, 2, 1));
	AWAIT(stats().tail == base + REC, "B did not reach the buffer");
	c = go("C", XFER, X(12, 0, 3, 1));
	AWAIT(stats().tail == base + 2 * REC, "C did not reach the buffer");
	d = go("D (buffer full)", XFER, X(13, 4, 5, 50));
	AWAIT(stats().full_waits > full, "D did not find the buffer full");
	r = go("balance of D's destination", BALANCE, X(0, 5, 0, 0));
	e = go("E (spends what D delivers)", XFER, X(14, 5, 6, 50));
	f = go("retry of D", XFER, X(13, 4, 5, 50));
	pending("batch [A] in fdatasync", 7, a, b, c, d, r, e, f);
	if (stats().tail != base + 2 * REC)
		fail("a record was put into a full buffer");
	permit();
	finished(a, LG_OK);
	blocked("batch [B C]");
	pending("batch [A] durable, [B C] not", 6, b, c, d, r, e, f);
	permit();
	finished(b, LG_OK);
	finished(c, LG_OK);
	blocked("batch with D");
	pending("[B C] durable, D not", 4, d, r, e, f);
	atomic_store(&g_closed, 0);
	permit();
	finished(d, LG_OK);
	finished(e, LG_OK);
	finished(f, LG_DUPLICATE);
	finished(r, LG_OK);
	if (r->bal != 50 && r->bal != 0)
		fail("balance of account 5 was %" PRId64, r->bal);
	printf("ok   buffer-full appender: nobody returned early, nothing "
		"lost (full_waits=%" PRIu64 ")\n", stats().full_waits - full);
}

int	main(int argc, char **argv)
{
	t_lg_config	cfg;
	uint64_t	id;
	int64_t		init[7] = {0, 100, 0, 0, 50, 0, 0};
	int			i;

	if (argc != 2 || LG_WAL_BUF != 128)
		return (2);
	alarm(120);
	sem_init(&g_permit, 0, 0);
	memset(&cfg, 0, sizeof(cfg));
	cfg.max_accounts = 16;
	cfg.wal_path = argv[1];
	if (lg_open(&cfg, &g_l) != LG_OK)
		return (3);
	for (i = 0; i < 7; i++)
		if (lg_create(g_l, init[i], i == 0, &id) != LG_OK)
			return (3);
	atomic_store(&g_closed, 1);
	observers_wait_for_the_batch_in_flight();
	observers_wait_for_the_batch_still_in_the_buffer();
	appender_finds_the_buffer_full();
	if (lg_verify(g_l) != LG_OK)
		fail("lg_verify");
	lg_close(g_l);
	printf("PASS\n");
	return (0);
}
