#ifndef LEDGER_INT_H
# define LEDGER_INT_H

# include <errno.h>
# include <fcntl.h>
# include <pthread.h>
# include <stdatomic.h>
# include <sys/stat.h>
# include <unistd.h>
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
** Log file: 8 bytes of magic, then records.
** Record: len u32 | crc u32 | type u8 | body      (little-endian)
**   len  = size of type + body
**   crc  = CRC-32 of the len field followed by type + body
**   type = 1 create:   id u64, initial i64, flags u32      (len 21)
**          2 transfer: id u64, from u64, to u64, amount i64 (len 33)
**          3 commit:   start u64                             (len 9)
** Every write batch ends with one commit record; start is the file
** offset where that batch began. Records are only believed once the
** commit record of their batch is there. LG_COMMIT is its size on disk.
** LG_WAL_BUF is the size of the group-commit buffer (commit record
** included), so it is also the most that one interrupted write can tear.
*/
# define LG_WAL_MAGIC "LGWAL001"
# define LG_WAL_HDR 8
# define LG_REC_HDR 8
# define LG_REC_MAX 41
# define LG_REC_CREATE 1
# define LG_REC_XFER 2
# define LG_REC_COMMIT 3
# define LG_LEN_CREATE 21
# define LG_LEN_XFER 33
# define LG_LEN_COMMIT 9
# define LG_COMMIT 17
# ifndef LG_WAL_BUF
#  define LG_WAL_BUF 1048576
# endif

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
** tail: file offset just past the last record handed to the log.
** durable: file offset up to which records are safely written.
** buf collects records; spare is the buffer being written to disk.
** flushing: some thread (the "leader") is writing spare right now.
** failed: a write or fdatasync failed; the ledger stops for good.
*/
typedef struct s_wal
{
	pthread_mutex_t	lock;
	pthread_cond_t	cond;
	int				fd;
	int				sync_mode;
	int				flushing;
	atomic_int		failed;
	uint8_t			*buf;
	uint8_t			*spare;
	uint64_t		len;
	uint64_t		tail;
	uint64_t		durable;
	uint64_t		flushes;
	uint64_t		full_waits;
	uint32_t		crc[256];
}	t_wal;

typedef struct s_lg_rec
{
	uint8_t		type;
	t_lg_xfer	x;
}	t_lg_rec;

/*
** The log file read into memory. end: where the valid records stop.
*/
typedef struct s_lg_img
{
	uint8_t		*p;
	uint64_t	size;
	uint64_t	end;
}	t_lg_img;

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
	t_wal					*wal;
	uint64_t				recovered;
	uint64_t				truncated;
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
void		lg_crc_init(uint32_t *tab);
uint32_t	lg_crc32(const uint32_t *tab, uint32_t crc, const uint8_t *p,
				size_t n);
int			lg_hdr_check(const t_lg_img *img);
size_t		lg_rec_encode(const uint32_t *tab, uint8_t *out, uint8_t type,
				const t_lg_xfer *x);
size_t		lg_rec_decode(const uint32_t *tab, const uint8_t *p,
				uint64_t avail, t_lg_rec *r);
uint64_t	lg_wal_append(t_wal *w, const uint8_t *rec, size_t n);
int			lg_wal_sync(t_wal *w, uint64_t lsn);
t_wal		*lg_wal_new(const t_lg_config *cfg);
void		lg_wal_free(t_wal *w);
int			lg_wal_fd(const char *path);
int			lg_wal_header(int fd, const char *path);
t_lg_rc		lg_wal_start(t_wal *w, const char *path, t_lg_img *img);
t_lg_rc		lg_recover(t_ledger *l);
int			lg_dead(t_ledger *l);
uint64_t	lg_tail(t_ledger *l);
uint64_t	lg_log(t_ledger *l, uint8_t type, const t_lg_xfer *x);
t_lg_rc		lg_settle(t_ledger *l, t_lg_rc rc, uint64_t lsn);
t_lg_rc		lg_reject(t_ledger *l, t_lg_rc rc, uint64_t *lsn);
void		lg_wal_seal(t_wal *w);
uint64_t	lg_scan_commits(const uint32_t *tab, const t_lg_img *img);
int			lg_scan_tail(const uint32_t *tab, const t_lg_img *img,
				uint64_t end);
t_lg_rc		lg_why(t_ledger *l, int why);

#endif
