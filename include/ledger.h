#ifndef LEDGER_H
# define LEDGER_H

# include <stdint.h>

/*
** Account flag: the balance may go below zero (bank / outside world).
*/
# define LG_ACC_OVERDRAFT 1u

/*
** The first five codes are the domain results of the brief.
** LG_FULL: the account table is full.
** LG_ERROR: out of memory, or the ledger has stopped after an I/O error.
** LG_CORRUPT: the log (or the in-memory state) fails validation.
*/
typedef enum e_lg_rc
{
	LG_OK = 0,
	LG_DUPLICATE,
	LG_INSUFFICIENT_FUNDS,
	LG_UNKNOWN_ACCOUNT,
	LG_INVALID,
	LG_FULL,
	LG_ERROR,
	LG_CORRUPT
}	t_lg_rc;

typedef struct s_ledger	t_ledger;

/*
** id is the idempotency key. amount is in cents and must be > 0.
*/
typedef struct s_lg_xfer
{
	uint64_t	id;
	uint64_t	from;
	uint64_t	to;
	int64_t		amount;
}	t_lg_xfer;

/*
** lock_mode. FINE is the real design: one lock per account, taken in
** id order. GLOBAL is the baseline: one lock for everything. NAIVE
** takes the two account locks in from/to order and can deadlock; it
** exists only so the tests can prove the watchdog notices a deadlock.
*/
# define LG_LOCK_FINE 0
# define LG_LOCK_GLOBAL 1
# define LG_LOCK_NAIVE 2

/*
** sync_mode, used when wal_path is set.
** FULL: an operation returns only after its log record was written
** and fdatasync'ed. Survives power loss (as far as the disk is honest).
** PROCESS: returns after write() without fdatasync. Survives a crash
** of the process (kill -9), not of the machine.
*/
# define LG_SYNC_FULL 0
# define LG_SYNC_PROCESS 1

/*
** Why lg_open refused a log with LG_CORRUPT (see t_lg_config.why).
** FOREIGN: the file does not start like a ledger log.
** LONG_TAIL: more unreadable bytes at the end than one batch can hold.
** BEHIND: data that was written after the damaged part exists, so the
** damaged part had been acknowledged; cutting it would lose transfers.
** RULES: a committed record that the ledger's own rules reject.
*/
# define LG_WHY_NONE 0
# define LG_WHY_FOREIGN 1
# define LG_WHY_LONG_TAIL 2
# define LG_WHY_BEHIND 3
# define LG_WHY_RULES 4

/*
** wal_path NULL: in-memory ledger, nothing survives the process.
** why: optional; receives one of LG_WHY_* when lg_open fails.
*/
typedef struct s_lg_config
{
	uint64_t	max_accounts;
	int			lock_mode;
	int			sync_mode;
	const char	*wal_path;
	int			*why;
}	t_lg_config;

/*
** A consistent copy of every account, taken at one instant.
*/
typedef struct s_lg_snap
{
	uint64_t	count;
	int64_t		seeded;
	int64_t		*balance;
	uint32_t	*flags;
}	t_lg_snap;

/*
** recovered: records replayed at open. truncated: bytes of torn tail
** cut off at open. tail: log size including buffered records.
** durable: log size known to be on disk. flushes: write batches.
** full_waits: times an appender found the commit buffer full and had
** to wait for, or perform, a flush while holding its account locks.
*/
typedef struct s_lg_stats
{
	uint64_t	recovered;
	uint64_t	truncated;
	uint64_t	tail;
	uint64_t	durable;
	uint64_t	flushes;
	uint64_t	full_waits;
}	t_lg_stats;

t_lg_rc		lg_open(const t_lg_config *cfg, t_ledger **out);
void		lg_close(t_ledger *l);
t_lg_rc		lg_create(t_ledger *l, int64_t initial, uint32_t flags,
				uint64_t *id);
t_lg_rc		lg_transfer(t_ledger *l, const t_lg_xfer *x);
t_lg_rc		lg_balance(t_ledger *l, uint64_t id, int64_t *out);
t_lg_rc		lg_lookup(t_ledger *l, uint64_t id, t_lg_xfer *out);
t_lg_rc		lg_snapshot(t_ledger *l, t_lg_snap *s);
void		lg_snap_free(t_lg_snap *s);
t_lg_rc		lg_verify(t_ledger *l);
uint64_t	lg_count(t_ledger *l);
void		lg_stats(t_ledger *l, t_lg_stats *s);
const char	*lg_rc_name(t_lg_rc rc);

#endif
