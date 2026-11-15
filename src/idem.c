#include "ledger_int.h"

/*
** splitmix64 finalizer: every input bit affects every output bit, so
** sequential ids and ids that differ only in high bits both spread.
*/
uint64_t	lg_hash(uint64_t x)
{
	x ^= x >> 30;
	x *= 0xbf58476d1ce4e5b9ULL;
	x ^= x >> 27;
	x *= 0x94d049bb133111ebULL;
	x ^= x >> 31;
	return (x);
}

t_shard	*lg_shard(t_ledger *l, uint64_t id)
{
	return (&l->shards[lg_hash(id) >> (64 - LG_SHARD_BITS)]);
}

/*
** Returns the slot that holds id, or the empty slot where id belongs.
** Terminates because lg_idem_reserve keeps the table under 70% full.
*/
t_lg_xfer	*lg_idem_find(t_shard *s, uint64_t id)
{
	uint64_t	i;

	i = lg_hash(id) & (s->cap - 1);
	while (s->slots[i].amount != 0 && s->slots[i].id != id)
		i = (i + 1) & (s->cap - 1);
	return (&s->slots[i]);
}

/*
** Makes room for one more entry, doubling the table if needed.
** Called before a transfer has any effect, so failure (-1) is clean.
*/
int	lg_idem_reserve(t_shard *s)
{
	t_lg_xfer	*old;
	uint64_t	i;

	if ((s->used + 1) * 10 <= s->cap * 7)
		return (0);
	old = s->slots;
	s->slots = calloc(s->cap * 2, sizeof(t_lg_xfer));
	if (!s->slots)
	{
		s->slots = old;
		return (-1);
	}
	s->cap *= 2;
	i = 0;
	while (i < s->cap / 2)
	{
		if (old[i].amount != 0)
			*lg_idem_find(s, old[i].id) = old[i];
		i++;
	}
	free(old);
	return (0);
}
