#include "ledger_int.h"

/*
** LG_OK and *out filled if a transfer with this id was applied.
*/
t_lg_rc	lg_lookup(t_ledger *l, uint64_t id, t_lg_xfer *out)
{
	t_lg_xfer	*slot;
	t_lg_rc		rc;
	uint64_t	lsn;

	if (lg_dead(l))
		return (LG_ERROR);
	rc = LG_INVALID;
	lg_lock_id(l, id);
	slot = lg_idem_find(lg_shard(l, id), id);
	if (slot->amount != 0)
	{
		*out = *slot;
		rc = LG_OK;
	}
	lsn = lg_tail(l);
	lg_unlock_id(l, id);
	return (lg_settle(l, rc, lsn));
}

uint64_t	lg_count(t_ledger *l)
{
	return (atomic_load(&l->count));
}

void	lg_stats(t_ledger *l, t_lg_stats *s)
{
	memset(s, 0, sizeof(*s));
	s->recovered = l->recovered;
	s->truncated = l->truncated;
	if (!l->wal)
		return ;
	pthread_mutex_lock(&l->wal->lock);
	s->tail = l->wal->tail;
	s->durable = l->wal->durable;
	s->flushes = l->wal->flushes;
	s->full_waits = l->wal->full_waits;
	pthread_mutex_unlock(&l->wal->lock);
}
