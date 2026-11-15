#include "ledger_int.h"

/*
** True once the log has failed. Every public operation checks this
** first and reports LG_ERROR: the ledger is fail-stop.
*/
int	lg_dead(t_ledger *l)
{
	return (l->wal && atomic_load(&l->wal->failed));
}

/*
** Current end of the log, buffered records included. A reader that
** waits for this LSN has waited for everything it could have seen.
*/
uint64_t	lg_tail(t_ledger *l)
{
	uint64_t	lsn;

	if (!l->wal)
		return (0);
	pthread_mutex_lock(&l->wal->lock);
	lsn = l->wal->tail;
	pthread_mutex_unlock(&l->wal->lock);
	return (lsn);
}

/*
** Appends one record; returns its LSN (0 without a log, which is also
** the case while the log is being replayed at open).
*/
uint64_t	lg_log(t_ledger *l, uint8_t type, const t_lg_xfer *x)
{
	uint8_t	rec[LG_REC_MAX];
	size_t	n;

	if (!l->wal)
		return (0);
	n = lg_rec_encode(l->wal->crc, rec, type, x);
	return (lg_wal_append(l->wal, rec, n));
}

/*
** Last step of every operation, called with no ledger lock held.
** An answer that tells the caller "this is applied" (OK, DUPLICATE)
** is held back until the log is durable up to lsn.
*/
t_lg_rc	lg_settle(t_ledger *l, t_lg_rc rc, uint64_t lsn)
{
	if (!l->wal)
		return (rc);
	if (rc == LG_OK || rc == LG_DUPLICATE)
	{
		if (lg_wal_sync(l->wal, lsn) != 0)
			return (LG_ERROR);
	}
	if (lg_dead(l))
		return (LG_ERROR);
	return (rc);
}
