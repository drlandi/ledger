#include "ledger_int.h"

/*
** Lock order (a thread only ever takes locks left to right):
**
**   create_lock -> one shard lock -> account locks, lowest id first
**
** A deadlock needs a cycle of threads each waiting for a lock the next
** one holds. If every thread takes locks in one global order, no such
** cycle can exist. In GLOBAL mode the single global lock stands in for
** the shard lock and the account locks.
*/
void	lg_lock_id(t_ledger *l, uint64_t id)
{
	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		pthread_mutex_lock(&l->global_lock);
	else
		pthread_mutex_lock(&lg_shard(l, id)->lock);
}

void	lg_unlock_id(t_ledger *l, uint64_t id)
{
	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		pthread_mutex_unlock(&l->global_lock);
	else
		pthread_mutex_unlock(&lg_shard(l, id)->lock);
}

/*
** a != b. FINE sorts the pair; NAIVE (tests only) does not, so two
** threads moving money A->B and B->A can each hold one lock and wait
** forever for the other.
*/
void	lg_lock_pair(t_ledger *l, uint64_t a, uint64_t b)
{
	uint64_t	tmp;

	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		return ;
	if (l->cfg.lock_mode == LG_LOCK_FINE && a > b)
	{
		tmp = a;
		a = b;
		b = tmp;
	}
	pthread_mutex_lock(&l->acc[a].lock);
	pthread_mutex_lock(&l->acc[b].lock);
}

void	lg_unlock_pair(t_ledger *l, uint64_t a, uint64_t b)
{
	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		return ;
	pthread_mutex_unlock(&l->acc[a].lock);
	pthread_mutex_unlock(&l->acc[b].lock);
}
