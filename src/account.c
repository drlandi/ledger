#include "ledger_int.h"

/*
** Caller holds create_lock. The slot is filled in first and count is
** raised last, so a transfer that sees the new count sees a complete
** account.
*/
static t_lg_rc	create_locked(t_ledger *l, int64_t initial, uint32_t flags,
					uint64_t *id)
{
	uint64_t	n;

	n = atomic_load(&l->count);
	if (l->seeded > INT64_MAX - initial)
		return (LG_INVALID);
	if (n >= l->cfg.max_accounts)
		return (LG_FULL);
	l->acc[n].balance = initial;
	l->acc[n].flags = flags;
	l->seeded += initial;
	*id = n;
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
	t_lg_rc	rc;

	if (initial < 0 || (flags & ~LG_ACC_OVERDRAFT) != 0)
		return (LG_INVALID);
	pthread_mutex_lock(&l->create_lock);
	rc = create_locked(l, initial, flags, id);
	pthread_mutex_unlock(&l->create_lock);
	return (rc);
}

t_lg_rc	lg_balance(t_ledger *l, uint64_t id, int64_t *out)
{
	if (id >= atomic_load(&l->count))
		return (LG_UNKNOWN_ACCOUNT);
	lg_lock_acc(l, id);
	*out = l->acc[id].balance;
	lg_unlock_acc(l, id);
	return (LG_OK);
}
