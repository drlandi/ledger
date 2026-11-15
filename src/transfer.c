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

t_lg_rc	lg_transfer(t_ledger *l, const t_lg_xfer *x)
{
	t_shard		*s;
	t_lg_xfer	*old;

	if (x->amount <= 0 || x->from == x->to)
		return (LG_INVALID);
	s = lg_shard(l, x->id);
	if (lg_idem_reserve(s) != 0)
		return (LG_ERROR);
	old = lg_idem_find(s, x->id);
	if (old->amount != 0 && xfer_same(old, x))
		return (LG_DUPLICATE);
	if (old->amount != 0)
		return (LG_INVALID);
	if (x->from >= l->count || x->to >= l->count)
		return (LG_UNKNOWN_ACCOUNT);
	return (xfer_apply(l, s, x));
}

/*
** LG_OK and *out filled if a transfer with this id was applied.
*/
t_lg_rc	lg_lookup(t_ledger *l, uint64_t id, t_lg_xfer *out)
{
	t_lg_xfer	*slot;

	slot = lg_idem_find(lg_shard(l, id), id);
	if (slot->amount == 0)
		return (LG_INVALID);
	*out = *slot;
	return (LG_OK);
}
