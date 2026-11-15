#include "ledger_int.h"

static int	xfer_same(const t_lg_xfer *a, const t_lg_xfer *b)
{
	return (a->from == b->from && a->to == b->to && a->amount == b->amount);
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
** Caller holds the id's shard lock and both account locks.
*/
static t_lg_rc	xfer_apply(t_ledger *l, t_shard *s, const t_lg_xfer *x)
{
	t_lg_rc	rc;

	rc = xfer_check(l, x);
	if (rc != LG_OK)
		return (rc);
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
static t_lg_rc	xfer_locked(t_ledger *l, t_shard *s, const t_lg_xfer *x)
{
	t_lg_xfer	*old;
	t_lg_rc		rc;
	uint64_t	n;

	if (lg_idem_reserve(s) != 0)
		return (LG_ERROR);
	old = lg_idem_find(s, x->id);
	if (old->amount != 0 && xfer_same(old, x))
		return (LG_DUPLICATE);
	if (old->amount != 0)
		return (LG_INVALID);
	n = atomic_load(&l->count);
	if (x->from >= n || x->to >= n)
		return (LG_UNKNOWN_ACCOUNT);
	lg_lock_pair(l, x->from, x->to);
	rc = xfer_apply(l, s, x);
	lg_unlock_pair(l, x->from, x->to);
	return (rc);
}

t_lg_rc	lg_transfer(t_ledger *l, const t_lg_xfer *x)
{
	t_lg_rc	rc;

	if (x->amount <= 0 || x->from == x->to)
		return (LG_INVALID);
	lg_lock_id(l, x->id);
	rc = xfer_locked(l, lg_shard(l, x->id), x);
	lg_unlock_id(l, x->id);
	return (rc);
}
