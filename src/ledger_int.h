#ifndef LEDGER_INT_H
# define LEDGER_INT_H

# include <pthread.h>
# include <stdatomic.h>
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
# define LG_MAX_ACCOUNTS 16777216

/*
** Padded to 64 bytes (one cache line) so that two threads working on
** neighbouring accounts do not fight over the same line.
*/
typedef struct s_account
{
	pthread_mutex_t	lock;
	int64_t			balance;
	uint32_t		flags;
	char			pad[12];
}	t_account;

/*
** Open addressing, linear probing. A slot with amount == 0 is empty
** (a stored transfer always has amount > 0).
*/
typedef struct s_shard
{
	pthread_mutex_t	lock;
	t_lg_xfer		*slots;
	uint64_t		cap;
	uint64_t		used;
}	t_shard;

/*
** count is atomic: transfers read it without a lock. An account slot
** is fully initialised before count is raised to include it.
** seeded and account creation are protected by create_lock.
*/
struct s_ledger
{
	t_lg_config				cfg;
	t_account				*acc;
	atomic_uint_fast64_t	count;
	int64_t					seeded;
	pthread_mutex_t			create_lock;
	pthread_mutex_t			global_lock;
	t_shard					shards[LG_SHARDS];
};

uint64_t	lg_hash(uint64_t x);
t_shard		*lg_shard(t_ledger *l, uint64_t id);
t_lg_xfer	*lg_idem_find(t_shard *s, uint64_t id);
int			lg_idem_reserve(t_shard *s);
void		lg_lock_id(t_ledger *l, uint64_t id);
void		lg_unlock_id(t_ledger *l, uint64_t id);
void		lg_lock_pair(t_ledger *l, uint64_t a, uint64_t b);
void		lg_unlock_pair(t_ledger *l, uint64_t a, uint64_t b);
void		lg_lock_acc(t_ledger *l, uint64_t id);
void		lg_unlock_acc(t_ledger *l, uint64_t id);
void		lg_lock_all(t_ledger *l, uint64_t n);
void		lg_unlock_all(t_ledger *l, uint64_t n);

#endif
