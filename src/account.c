#include "ledger_int.h"

/*
** The initial balance is the only way money enters the ledger.
** l->seeded is the running total and is itself overflow-checked, so
** the sum of all balances always fits in an int64.
*/
t_lg_rc	lg_create(t_ledger *l, int64_t initial, uint32_t flags, uint64_t *id)
{
	if (initial < 0 || (flags & ~LG_ACC_OVERDRAFT) != 0)
		return (LG_INVALID);
	if (l->seeded > INT64_MAX - initial)
		return (LG_INVALID);
	if (l->count >= l->cfg.max_accounts)
		return (LG_FULL);
	l->acc[l->count].balance = initial;
	l->acc[l->count].flags = flags;
	l->seeded += initial;
	*id = l->count;
	l->count++;
	return (LG_OK);
}

t_lg_rc	lg_balance(t_ledger *l, uint64_t id, int64_t *out)
{
	if (id >= l->count)
		return (LG_UNKNOWN_ACCOUNT);
	*out = l->acc[id].balance;
	return (LG_OK);
}
