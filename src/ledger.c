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

static int	lg_alloc(t_ledger *l)
{
	uint64_t	i;

	l->acc = calloc(l->cfg.max_accounts + 1, sizeof(t_account));
	if (!l->acc)
		return (-1);
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
	if (cfg->max_accounts == 0 || cfg->max_accounts > UINT32_MAX)
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
	*out = l;
	return (LG_OK);
}
