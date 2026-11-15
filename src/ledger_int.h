#ifndef LEDGER_INT_H
# define LEDGER_INT_H

# include <stdlib.h>
# include <string.h>
# include "ledger.h"

/*
** The idempotency table is split in LG_SHARDS independent hash tables.
** The top LG_SHARD_BITS bits of the hash pick the shard, the low bits
** pick the slot inside it.
*/
# define LG_SHARD_BITS 10
# define LG_SHARDS 1024
# define LG_SHARD_INIT 64

typedef struct s_account
{
	int64_t		balance;
	uint32_t	flags;
}	t_account;

/*
** Open addressing, linear probing. A slot with amount == 0 is empty
** (a stored transfer always has amount > 0).
*/
typedef struct s_shard
{
	t_lg_xfer	*slots;
	uint64_t	cap;
	uint64_t	used;
}	t_shard;

struct s_ledger
{
	t_lg_config	cfg;
	t_account	*acc;
	uint64_t	count;
	int64_t		seeded;
	t_shard		shards[LG_SHARDS];
};

uint64_t	lg_hash(uint64_t x);
t_shard		*lg_shard(t_ledger *l, uint64_t id);
t_lg_xfer	*lg_idem_find(t_shard *s, uint64_t id);
int			lg_idem_reserve(t_shard *s);

#endif
