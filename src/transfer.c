#include "ledger_int.h"

/*
** The id was applied before as old. Same request: DUPLICATE, and the
** caller must wait until the original is durable before saying so
** (hence the LSN). Anything else under the same id is an error, and
** that answer waits too: it also reveals that the id is taken.
*/
static t_lg_rc	xfer_replay(t_ledger *l, const t_lg_xfer *old,
					const t_lg_xfer *x, uint64_t *lsn)
{
	if (old->from != x->from || old->to != x->to)
		return (lg_reject(l, LG_INVALID, lsn));
	if (old->amount != x->amount)
		return (lg_reject(l, LG_INVALID, lsn));
	*lsn = lg_tail(l);
	return (LG_DUPLICATE);
}

/*
** Funds first, then int64 range on both sides. Written so that the
** comparisons themselves cannot overflow (amount > 0 here).
*/
static t_lg_rc	xfer_check(t_ledger *l, const t_lg_xfer *x)
{
	t_account	*src;
	t_account	*dst;

	src = &l->acc[x->from];
	dst = &l->acc[x->to];
	if (!(src->flags & LG_ACC_OVERDRAFT) && src->balance < x->amount)
		return (LG_INSUFFICIENT_FUNDS);
	if (src->balance < INT64_MIN + x->amount)
		return (LG_INVALID);
	if (dst->balance > INT64_MAX - x->amount)
		return (LG_INVALID);
	return (LG_OK);
}

/*
** Caller holds the id's shard lock and both account locks. The record
** goes into the log buffer before the balances change and while the
** account locks are held: for any one account, log order is therefore
** the order in which transfers really touched it.
*/
static t_lg_rc	xfer_apply(t_ledger *l, const t_lg_xfer *x, uint64_t *lsn)
{
	t_shard	*s;
	t_lg_rc	rc;

	rc = xfer_check(l, x);
	if (rc != LG_OK)
		return (lg_reject(l, rc, lsn));
	s = lg_shard(l, x->id);
	*lsn = lg_log(l, LG_REC_XFER, x);
	l->acc[x->from].balance -= x->amount;
	l->acc[x->to].balance += x->amount;
	*lg_idem_find(s, x->id) = *x;
	s->used++;
	return (LG_OK);
}

/*
** Caller holds the id's shard lock, so no other thread can look up or
** insert this id until we return: "check, then apply, then remember"
** is one atomic step as far as this id is concerned.
*/
static t_lg_rc	xfer_locked(t_ledger *l, const t_lg_xfer *x, uint64_t *lsn)
{
	t_lg_xfer	*old;
	t_lg_rc		rc;
	uint64_t	n;

	if (lg_idem_reserve(lg_shard(l, x->id)) != 0)
		return (LG_ERROR);
	old = lg_idem_find(lg_shard(l, x->id), x->id);
	if (old->amount != 0)
		return (xfer_replay(l, old, x, lsn));
	n = atomic_load(&l->count);
	if (x->from >= n || x->to >= n)
		return (LG_UNKNOWN_ACCOUNT);
	lg_lock_pair(l, x->from, x->to);
	rc = xfer_apply(l, x, lsn);
	lg_unlock_pair(l, x->from, x->to);
	return (rc);
}

/*
** The locks are released before waiting for the disk (lg_settle), so
** other transfers can proceed and share the same fdatasync. Nothing
** is reported to the caller until its record is durable.
*/
t_lg_rc	lg_transfer(t_ledger *l, const t_lg_xfer *x)
{
	t_lg_rc		rc;
	uint64_t	lsn;

	if (x->amount <= 0 || x->from == x->to)
		return (LG_INVALID);
	if (lg_dead(l))
		return (LG_ERROR);
	lsn = 0;
	lg_lock_id(l, x->id);
	rc = xfer_locked(l, x, &lsn);
	lg_unlock_id(l, x->id);
	return (lg_settle(l, rc, lsn));
}
