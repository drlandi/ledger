/* Differential-test driver: reads operations on stdin, one per line,
 * runs them against the C core, prints one result line per operation.
 *   r MAX             new ledger with MAX accounts
 *   c INITIAL FLAGS   create account
 *   t ID FROM TO AMT  transfer
 *   b ID              balance
 *   l ID              lookup applied transfer
 *   d                 dump: D count seeded verify bal:flags ...
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include "ledger.h"

static t_ledger	*g_l;

static void	dump(void)
{
	t_lg_snap	s;
	uint64_t	i;

	if (lg_snapshot(g_l, &s) != LG_OK)
		exit(3);
	printf("D %" PRIu64 " %" PRId64 " %s", s.count, s.seeded,
		lg_rc_name(lg_verify(g_l)));
	for (i = 0; i < s.count; i++)
		printf(" %" PRId64 ":%u", s.balance[i], s.flags[i]);
	printf("\n");
	lg_snap_free(&s);
}

static void	one(char *line)
{
	t_lg_config	cfg = {0};
	t_lg_xfer	x;
	uint64_t	id;
	int64_t		v;
	uint32_t	fl;
	t_lg_rc		rc;

	if (sscanf(line, "r %" SCNu64, &cfg.max_accounts) == 1)
	{
		lg_close(g_l);
		printf("%s\n", lg_rc_name(lg_open(&cfg, &g_l)));
	}
	else if (sscanf(line, "c %" SCNd64 " %" SCNu32, &v, &fl) == 2)
	{
		rc = lg_create(g_l, v, fl, &id);
		if (rc == LG_OK)
			printf("OK %" PRIu64 "\n", id);
		else
			printf("%s\n", lg_rc_name(rc));
	}
	else if (sscanf(line, "t %" SCNu64 " %" SCNu64 " %" SCNu64 " %" SCNd64,
			&x.id, &x.from, &x.to, &x.amount) == 4)
		printf("%s\n", lg_rc_name(lg_transfer(g_l, &x)));
	else if (sscanf(line, "b %" SCNu64, &id) == 1)
	{
		rc = lg_balance(g_l, id, &v);
		if (rc == LG_OK)
			printf("OK %" PRId64 "\n", v);
		else
			printf("%s\n", lg_rc_name(rc));
	}
	else if (sscanf(line, "l %" SCNu64, &id) == 1)
	{
		if (lg_lookup(g_l, id, &x) == LG_OK)
			printf("L %" PRIu64 " %" PRIu64 " %" PRId64 "\n",
				x.from, x.to, x.amount);
		else
			printf("L -\n");
	}
	else if (line[0] == 'd')
		dump();
	else
		exit(2);
}

int	main(void)
{
	char	line[256];

	while (fgets(line, sizeof(line), stdin))
		one(line);
	lg_close(g_l);
	return (0);
}
