#include "ledger_int.h"

void	lg_snap_free(t_lg_snap *s)
{
	free(s->balance);
	free(s->flags);
	s->balance = NULL;
	s->flags = NULL;
}

t_lg_rc	lg_snapshot(t_ledger *l, t_lg_snap *s)
{
	uint64_t	i;

	s->count = l->count;
	s->seeded = l->seeded;
	s->balance = malloc((s->count + 1) * sizeof(int64_t));
	s->flags = malloc((s->count + 1) * sizeof(uint32_t));
	if (!s->balance || !s->flags)
	{
		lg_snap_free(s);
		return (LG_ERROR);
	}
	i = 0;
	while (i < s->count)
	{
		s->balance[i] = l->acc[i].balance;
		s->flags[i] = l->acc[i].flags;
		i++;
	}
	return (LG_OK);
}

/*
** Self-check for the CLI: conservation (I1) and no negative normal
** account (I2). The sum is done in uint64 because unsigned wrap-around
** is defined in C; it is exact whenever the true sum fits in an int64,
** which lg_create guarantees for a healthy ledger.
*/
t_lg_rc	lg_verify(t_ledger *l)
{
	t_lg_snap	s;
	uint64_t	sum;
	uint64_t	i;
	t_lg_rc		rc;

	if (lg_snapshot(l, &s) != LG_OK)
		return (LG_ERROR);
	rc = LG_OK;
	sum = 0;
	i = 0;
	while (i < s.count)
	{
		sum += (uint64_t)s.balance[i];
		if (s.balance[i] < 0 && !(s.flags[i] & LG_ACC_OVERDRAFT))
			rc = LG_CORRUPT;
		i++;
	}
	if (sum != (uint64_t)s.seeded)
		rc = LG_CORRUPT;
	lg_snap_free(&s);
	return (rc);
}
