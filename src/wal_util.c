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
** A rejection that was decided by looking at balances or at the
** idempotency table (INSUFFICIENT_FUNDS, id used with other values).
** What it looked at may still be in the log buffer, so the answer is
** held back like a read: the caller notes the log end here, under the
** locks it decided under, and lg_settle waits for it.
*/
t_lg_rc	lg_reject(t_ledger *l, t_lg_rc rc, uint64_t *lsn)
{
	*lsn = lg_tail(l);
	return (rc);
}

/*
** Last step of every operation, called with no ledger lock held.
** An answer that tells the caller "this is applied" (OK, DUPLICATE),
** and any answer derived from state (lsn != 0), is held back until
** the log is durable up to lsn.
*/
t_lg_rc	lg_settle(t_ledger *l, t_lg_rc rc, uint64_t lsn)
{
	if (!l->wal)
		return (rc);
	if (rc == LG_OK || rc == LG_DUPLICATE || lsn != 0)
	{
		if (lg_wal_sync(l->wal, lsn) != 0)
			return (LG_ERROR);
	}
	if (lg_dead(l))
		return (LG_ERROR);
	return (rc);
}
