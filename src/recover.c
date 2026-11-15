#include "ledger_int.h"

static uint8_t	*read_all(int fd, uint64_t *size)
{
	struct stat	st;
	uint8_t		*p;
	uint64_t	got;
	ssize_t		k;

	if (fstat(fd, &st) != 0)
		return (NULL);
	p = malloc((size_t)st.st_size + 1);
	got = 0;
	k = 1;
	while (p && k > 0 && got < (uint64_t)st.st_size)
	{
		k = pread(fd, p + got, (size_t)st.st_size - got, (off_t)got);
		if (k > 0)
			got += (uint64_t)k;
	}
	if (p && got != (uint64_t)st.st_size)
	{
		free(p);
		return (NULL);
	}
	*size = got;
	return (p);
}

/*
** Replay goes through lg_create and lg_transfer themselves: recovery
** has no second implementation of the rules that could disagree with
** the first. l->wal is still NULL here, so nothing is logged again.
*/
static t_lg_rc	replay_one(t_ledger *l, const t_lg_rec *r)
{
	uint64_t	id;

	if (r->type == LG_REC_XFER)
		return (lg_transfer(l, &r->x));
	if (r->x.from > INT64_MAX || r->x.to > UINT32_MAX)
		return (LG_CORRUPT);
	if (lg_create(l, (int64_t)r->x.from, (uint32_t)r->x.to, &id) != LG_OK)
		return (LG_CORRUPT);
	if (id != r->x.id)
		return (LG_CORRUPT);
	return (LG_OK);
}

/*
** Applies records until the first one that is incomplete or fails its
** checksum; img->end is left there. A record that is intact but that
** the ledger rejects (overdraw, repeated id, ...) was never written by
** this program in this position: that is corruption, not a torn tail.
*/
static t_lg_rc	replay(t_ledger *l, const uint32_t *tab, t_lg_img *img)
{
	t_lg_rec	r;
	size_t		n;

	img->end = LG_WAL_HDR;
	n = lg_rec_decode(tab, img->p + img->end, img->size - img->end, &r);
	while (n != 0)
	{
		if (replay_one(l, &r) != LG_OK)
			return (LG_CORRUPT);
		img->end += n;
		l->recovered++;
		n = lg_rec_decode(tab, img->p + img->end, img->size - img->end, &r);
	}
	return (LG_OK);
}

/*
** A crash can tear at most the one batch that was being written, and
** a batch is at most LG_WAL_BUF bytes. More invalid bytes than that
** cannot be a torn tail, so the file is left alone and open fails.
*/
static t_lg_rc	recover_img(t_ledger *l, t_wal *w, t_lg_img *img)
{
	int	hdr;

	hdr = lg_hdr_check(img);
	if (hdr < 0)
		return (LG_CORRUPT);
	img->end = 0;
	if (hdr == 1 && replay(l, w->crc, img) != LG_OK)
		return (LG_CORRUPT);
	if (img->size - img->end > LG_WAL_BUF)
		return (LG_CORRUPT);
	l->truncated = img->size - img->end;
	return (lg_wal_start(w, l->cfg.wal_path, img));
}

t_lg_rc	lg_recover(t_ledger *l)
{
	t_wal		*w;
	t_lg_img	img;
	t_lg_rc		rc;

	w = lg_wal_new(&l->cfg);
	if (!w)
		return (LG_ERROR);
	img.p = read_all(w->fd, &img.size);
	rc = LG_ERROR;
	if (img.p)
		rc = recover_img(l, w, &img);
	free(img.p);
	if (rc != LG_OK)
	{
		lg_wal_free(w);
		return (rc);
	}
	l->wal = w;
	return (LG_OK);
}
