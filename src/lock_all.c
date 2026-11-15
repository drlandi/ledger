#include "ledger_int.h"

void	lg_lock_acc(t_ledger *l, uint64_t id)
{
	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		pthread_mutex_lock(&l->global_lock);
	else
		pthread_mutex_lock(&l->acc[id].lock);
}

void	lg_unlock_acc(t_ledger *l, uint64_t id)
{
	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
		pthread_mutex_unlock(&l->global_lock);
	else
		pthread_mutex_unlock(&l->acc[id].lock);
}

/*
** Every account lock, lowest id first: the same order transfers use.
*/
void	lg_lock_all(t_ledger *l, uint64_t n)
{
	uint64_t	i;

	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
	{
		pthread_mutex_lock(&l->global_lock);
		return ;
	}
	i = 0;
	while (i < n)
	{
		pthread_mutex_lock(&l->acc[i].lock);
		i++;
	}
}

void	lg_unlock_all(t_ledger *l, uint64_t n)
{
	uint64_t	i;

	if (l->cfg.lock_mode == LG_LOCK_GLOBAL)
	{
		pthread_mutex_unlock(&l->global_lock);
		return ;
	}
	i = 0;
	while (i < n)
	{
		pthread_mutex_unlock(&l->acc[i].lock);
		i++;
	}
}
