#include "ledger_int.h"

/*
** Called without the log lock. Any failure is final: after a failed
** write or fdatasync the kernel may have dropped the data while
** reporting later calls as successful, so retrying proves nothing.
*/
static int	wal_write(t_wal *w, const uint8_t *p, uint64_t n)
{
	ssize_t	k;

	while (n > 0)
	{
		k = write(w->fd, p, n);
		if (k < 0 && errno == EINTR)
			continue ;
		if (k <= 0)
			return (-1);
		p += k;
		n -= (uint64_t)k;
	}
	if (w->sync_mode == LG_SYNC_FULL && fdatasync(w->fd) != 0)
		return (-1);
	return (0);
}

/*
** Group commit. Caller holds the log lock and becomes the leader: it
** takes the whole buffer (its own record and everybody else's), swaps
** in the empty spare so others can keep appending, and does one
** write + fdatasync for the batch with the lock released. The batch
** is closed with its commit record first (lg_wal_seal).
*/
static void	wal_flush(t_wal *w)
{
	uint8_t		*batch;
	uint64_t	n;
	uint64_t	target;
	int			err;

	w->flushing = 1;
	lg_wal_seal(w);
	batch = w->buf;
	w->buf = w->spare;
	w->spare = batch;
	n = w->len;
	w->len = 0;
	target = w->tail;
	pthread_mutex_unlock(&w->lock);
	err = wal_write(w, batch, n);
	pthread_mutex_lock(&w->lock);
	w->flushing = 0;
	w->flushes++;
	if (err != 0)
		atomic_store(&w->failed, 1);
	else
		w->durable = target;
	pthread_cond_broadcast(&w->cond);
}

/*
** Caller holds the log lock. Either waits for the current leader to
** finish or becomes the leader.
*/
static void	wal_step(t_wal *w)
{
	if (w->flushing)
		pthread_cond_wait(&w->cond, &w->lock);
	else
		wal_flush(w);
}

/*
** Copies one record into the buffer and returns its LSN: the file
** offset just past it. Nothing touches the disk here. If the buffer
** is full (room is always kept for the commit record) the caller
** helps to empty it first. It does so while still holding its account
** locks: that is what keeps log order equal to the order in which
** transfers touched an account. The loop re-checks after every wait,
** because the buffer it finds afterwards is a different one.
*/
uint64_t	lg_wal_append(t_wal *w, const uint8_t *rec, size_t n)
{
	uint64_t	lsn;

	pthread_mutex_lock(&w->lock);
	while (w->len + n > LG_WAL_BUF - LG_COMMIT && !atomic_load(&w->failed))
	{
		w->full_waits++;
		wal_step(w);
	}
	if (!atomic_load(&w->failed))
	{
		memcpy(w->buf + w->len, rec, n);
		w->len += n;
		w->tail += n;
	}
	lsn = w->tail;
	pthread_mutex_unlock(&w->lock);
	return (lsn);
}

/*
** Returns 0 once everything up to lsn is durable, -1 if the log failed.
*/
int	lg_wal_sync(t_wal *w, uint64_t lsn)
{
	int	failed;

	pthread_mutex_lock(&w->lock);
	while (w->durable < lsn && !atomic_load(&w->failed))
		wal_step(w);
	failed = atomic_load(&w->failed);
	pthread_mutex_unlock(&w->lock);
	return (-failed);
}
