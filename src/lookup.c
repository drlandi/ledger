#include "ledger_int.h"

/*
** LG_OK and *out filled if a transfer with this id was applied.
*/
t_lg_rc	lg_lookup(t_ledger *l, uint64_t id, t_lg_xfer *out)
{
	t_lg_xfer	*slot;
	t_lg_rc		rc;

	rc = LG_INVALID;
	lg_lock_id(l, id);
	slot = lg_idem_find(lg_shard(l, id), id);
	if (slot->amount != 0)
	{
		*out = *slot;
		rc = LG_OK;
	}
	lg_unlock_id(l, id);
	return (rc);
}
