/* Command-line front end. Every invocation opens the log (which
 * recovers the ledger), performs one command, and exits.
 *
 *   ledger LOG create INITIAL_CENTS [overdraft]
 *   ledger LOG transfer ID FROM TO AMOUNT_CENTS
 *   ledger LOG balance ACCOUNT
 *   ledger LOG verify
 *   ledger LOG dump
 *
 * Exit status: 0 = OK or DUPLICATE, 1 = request refused, 2 = usage,
 * 3 = the ledger could not be opened or failed its self-check.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ledger.h"

#define MAX_ACCOUNTS 65536

static int	usage(void)
{
	fprintf(stderr,
		"usage: ledger LOG create INITIAL_CENTS [overdraft]\n"
		"       ledger LOG transfer ID FROM TO AMOUNT_CENTS\n"
		"       ledger LOG balance ACCOUNT\n"
		"       ledger LOG verify\n"
		"       ledger LOG dump\n"
		"environment: LEDGER_SYNC=process skips fdatasync\n");
	return (2);
}

/* Strict parsers: the whole string must be a number in range. Money
 * is never parsed as floating point. */
static int	parse_u64(const char *s, uint64_t *out)
{
	char	*end;

	errno = 0;
	if (*s < '0' || *s > '9')
		return (-1);
	*out = strtoull(s, &end, 10);
	return (-(errno != 0 || *end != '\0'));
}

static int	parse_i64(const char *s, int64_t *out)
{
	char	*end;

	errno = 0;
	if (*s == '\0' || *s == ' ' || *s == '+')
		return (-1);
	*out = strtoll(s, &end, 10);
	return (-(errno != 0 || *end != '\0'));
}

static int	cmd_create(t_ledger *l, int argc, char **argv)
{
	int64_t		initial;
	uint64_t	id;
	uint32_t	flags;
	t_lg_rc		rc;

	flags = 0;
	if (argc == 5 && !strcmp(argv[4], "overdraft"))
		flags = LG_ACC_OVERDRAFT;
	else if (argc != 4)
		return (usage());
	if (parse_i64(argv[3], &initial) != 0)
		return (usage());
	rc = lg_create(l, initial, flags, &id);
	if (rc == LG_OK)
		printf("OK %" PRIu64 "\n", id);
	else
		printf("%s\n", lg_rc_name(rc));
	return (rc != LG_OK);
}

static int	cmd_transfer(t_ledger *l, int argc, char **argv)
{
	t_lg_xfer	x;
	t_lg_rc		rc;

	if (argc != 7 || parse_u64(argv[3], &x.id) != 0
		|| parse_u64(argv[4], &x.from) != 0
		|| parse_u64(argv[5], &x.to) != 0
		|| parse_i64(argv[6], &x.amount) != 0)
		return (usage());
	rc = lg_transfer(l, &x);
	printf("%s\n", lg_rc_name(rc));
	return (rc != LG_OK && rc != LG_DUPLICATE);
}

static int	cmd_balance(t_ledger *l, int argc, char **argv)
{
	uint64_t	id;
	int64_t		bal;
	t_lg_rc		rc;

	if (argc != 4 || parse_u64(argv[3], &id) != 0)
		return (usage());
	rc = lg_balance(l, id, &bal);
	if (rc == LG_OK)
		printf("OK %" PRId64 "\n", bal);
	else
		printf("%s\n", lg_rc_name(rc));
	return (rc != LG_OK);
}

/* verify: human-readable. dump: one machine-readable line per fact. */
static int	cmd_report(t_ledger *l, int dump)
{
	t_lg_snap	s;
	t_lg_stats	st;
	t_lg_rc		rc;
	uint64_t	i;

	rc = lg_verify(l);
	lg_stats(l, &st);
	if (lg_snapshot(l, &s) != LG_OK)
		return (3);
	if (!dump)
		printf("%s: %" PRIu64 " accounts, %" PRId64 " cents seeded, "
			"%" PRIu64 " log records replayed, %" PRIu64
			" torn bytes discarded\n", rc == LG_OK ? "OK" : "CORRUPT",
			s.count, s.seeded, st.recovered, st.truncated);
	else
	{
		printf("R %" PRIu64 " %" PRIu64 " %" PRIu64 "\n", st.recovered,
			st.truncated, st.durable);
		printf("D %" PRIu64 " %" PRId64 " %s", s.count, s.seeded,
			lg_rc_name(rc));
		for (i = 0; i < s.count; i++)
			printf(" %" PRId64 ":%u", s.balance[i], s.flags[i]);
		printf("\n");
	}
	lg_snap_free(&s);
	return (rc == LG_OK ? 0 : 3);
}

/* Says why a log was refused; nothing in the file was changed. */
static const char	*why_text(int why)
{
	if (why == LG_WHY_FOREIGN)
		return (" (not a ledger log; file left untouched)");
	if (why == LG_WHY_LONG_TAIL)
		return (" (more unreadable bytes at the end than one interrupted"
			" write can explain; file left untouched)");
	if (why == LG_WHY_BEHIND)
		return (" (damage in front of data that was written later:"
			" acknowledged transfers would be lost by cutting;"
			" file left untouched)");
	if (why == LG_WHY_RULES)
		return (" (a committed record breaks the ledger's rules;"
			" file left untouched)");
	return ("");
}

int	main(int argc, char **argv)
{
	t_lg_config	cfg;
	t_ledger	*l;
	t_lg_rc		rc;
	int			ret;
	int			why;
	const char	*sync;

	if (argc < 3)
		return (usage());
	memset(&cfg, 0, sizeof(cfg));
	cfg.max_accounts = MAX_ACCOUNTS;
	cfg.wal_path = argv[1];
	why = LG_WHY_NONE;
	cfg.why = &why;
	sync = getenv("LEDGER_SYNC");
	if (sync && !strcmp(sync, "process"))
		cfg.sync_mode = LG_SYNC_PROCESS;
	rc = lg_open(&cfg, &l);
	if (rc != LG_OK)
	{
		fprintf(stderr, "ledger: cannot open %s: %s%s\n", argv[1],
			lg_rc_name(rc), why_text(why));
		return (3);
	}
	if (!strcmp(argv[2], "create"))
		ret = cmd_create(l, argc, argv);
	else if (!strcmp(argv[2], "transfer"))
		ret = cmd_transfer(l, argc, argv);
	else if (!strcmp(argv[2], "balance"))
		ret = cmd_balance(l, argc, argv);
	else if (!strcmp(argv[2], "verify") && argc == 3)
		ret = cmd_report(l, 0);
	else if (!strcmp(argv[2], "dump") && argc == 3)
		ret = cmd_report(l, 1);
	else
		ret = usage();
	lg_close(l);
	return (ret);
}
