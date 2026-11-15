#include "ledger_int.h"

/*
** Caller holds create_lock. c carries the request the same way the
** log record does: id = new account id, from = initial, to = flags.
** The slot is filled in and the record logged first; count is raised
** last, so a transfer that sees the new count sees a complete account
** and its log record comes after the account's.
*/
static t_lg_rc	create_locked(t_ledger *l, t_lg_xfer *c, uint64_t *lsn)
{
	uint64_t	n;

	n = atomic_load(&l->count);
	if (l->seeded > INT64_MAX - (int64_t)c->from)
		return (LG_INVALID);
	if (n >= l->cfg.max_accounts)
		return (LG_FULL);
	c->id = n;
	*lsn = lg_log(l, LG_REC_CREATE, c);
	l->acc[n].balance = (int64_t)c->from;
	l->acc[n].flags = (uint32_t)c->to;
	l->seeded += (int64_t)c->from;
	atomic_store(&l->count, n + 1);
	return (LG_OK);
}

/*
** The initial balance is the only way money enters the ledger.
** l->seeded is the running total and is itself overflow-checked, so
** the sum of all balances always fits in an int64.
*/
t_lg_rc	lg_create(t_ledger *l, int64_t initial, uint32_t flags, uint64_t *id)
{
	t_lg_xfer	c;
	t_lg_rc		rc;
	uint64_t	lsn;

	if (initial < 0 || (flags & ~LG_ACC_OVERDRAFT) != 0)
		return (LG_INVALID);
	if (lg_dead(l))
		return (LG_ERROR);
	memset(&c, 0, sizeof(c));
	c.from = (uint64_t)initial;
	c.to = flags;
	lsn = 0;
	pthread_mutex_lock(&l->create_lock);
	rc = create_locked(l, &c, &lsn);
	pthread_mutex_unlock(&l->create_lock);
	*id = c.id;
	return (lg_settle(l, rc, lsn));
}

/*
** A balance may include transfers whose records are still in the log
** buffer. lg_settle waits until everything logged so far is durable,
** so a caller never sees money that a crash could take back.
*/
t_lg_rc	lg_balance(t_ledger *l, uint64_t id, int64_t *out)
{
	uint64_t	lsn;

	if (id >= atomic_load(&l->count))
		return (LG_UNKNOWN_ACCOUNT);
	if (lg_dead(l))
		return (LG_ERROR);
	lg_lock_acc(l, id);
	*out = l->acc[id].balance;
	lsn = lg_tail(l);
	lg_unlock_acc(l, id);
	return (lg_settle(l, LG_OK, lsn));
}
