#include "ledger_int.h"

static const char *const	g_rc_names[] = {
	"OK", "DUPLICATE", "INSUFFICIENT_FUNDS", "UNKNOWN_ACCOUNT", "INVALID",
	"FULL", "ERROR", "CORRUPT"
};

const char	*lg_rc_name(t_lg_rc rc)
{
	if ((unsigned int)rc > LG_CORRUPT)
		return ("?");
	return (g_rc_names[rc]);
}

void	lg_close(t_ledger *l)
{
	uint64_t	i;

	if (!l)
		return ;
	i = 0;
	while (i < LG_SHARDS)
	{
		free(l->shards[i].slots);
		i++;
	}
	free(l->acc);
	free(l);
}

/*
** Default mutexes: initialising and destroying them cannot fail on
** Linux and needs no cleanup, so lg_close only frees memory.
*/
static void	lg_init_locks(t_ledger *l)
{
	uint64_t	i;

	pthread_mutex_init(&l->create_lock, NULL);
	pthread_mutex_init(&l->global_lock, NULL);
	i = 0;
	while (i < LG_SHARDS)
	{
		pthread_mutex_init(&l->shards[i].lock, NULL);
		i++;
	}
	i = 0;
	while (i < l->cfg.max_accounts)
	{
		pthread_mutex_init(&l->acc[i].lock, NULL);
		i++;
	}
}

static int	lg_alloc(t_ledger *l)
{
	uint64_t	i;
	size_t		size;

	size = (l->cfg.max_accounts + 1) * sizeof(t_account);
	l->acc = aligned_alloc(64, size);
	if (!l->acc)
		return (-1);
	memset(l->acc, 0, size);
	i = 0;
	while (i < LG_SHARDS)
	{
		l->shards[i].slots = calloc(LG_SHARD_INIT, sizeof(t_lg_xfer));
		if (!l->shards[i].slots)
			return (-1);
		l->shards[i].cap = LG_SHARD_INIT;
		i++;
	}
	return (0);
}

t_lg_rc	lg_open(const t_lg_config *cfg, t_ledger **out)
{
	t_ledger	*l;

	*out = NULL;
	if (cfg->max_accounts == 0 || cfg->max_accounts > LG_MAX_ACCOUNTS)
		return (LG_INVALID);
	if (cfg->lock_mode < LG_LOCK_FINE || cfg->lock_mode > LG_LOCK_NAIVE)
		return (LG_INVALID);
	l = calloc(1, sizeof(t_ledger));
	if (!l)
		return (LG_ERROR);
	l->cfg = *cfg;
	if (lg_alloc(l) != 0)
	{
		lg_close(l);
		return (LG_ERROR);
	}
	lg_init_locks(l);
	*out = l;
	return (LG_OK);
}
