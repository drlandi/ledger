/* ************************************************************************** */
/*                                                                            */
/*                                                        :::      ::::::::   */
/*   wal_batch.c                                        :+:      :+:    :+:   */
/*                                                    +:+ +:+         +:+     */
/*   By: dlandi <dlandi@student.42sp.org.br>        +#+  +:+       +#+        */
/*                                                +#+#+#+#+#+   +#+           */
/*   Created: 2026/11/16 20:24:00 by dlandi            #+#    #+#             */
/*   Updated: 2026/11/16 20:24:00 by dlandi           ###   ########.fr       */
/*                                                                            */
/* ************************************************************************** */

#include "ledger_int.h"

/*
** Caller holds the log lock and is about to write the buffer. Closes
** the batch with a commit record that names the file offset where the
** batch began. lg_wal_append always leaves room for it.
*/
void	lg_wal_seal(t_wal *w)
{
	t_lg_xfer	c;
	size_t		n;

	memset(&c, 0, sizeof(c));
	c.id = w->tail - w->len;
	n = lg_rec_encode(w->crc, w->buf + w->len, LG_REC_COMMIT, &c);
	w->len += n;
	w->tail += n;
}

/*
** Returns the offset just past the last complete batch: intact
** records followed by a commit record that names the batch's own
** start. Batches are written strictly one after another, each only
** after the previous one was durable, so everything before that
** offset was written completely.
*/
uint64_t	lg_scan_commits(const uint32_t *tab, const t_lg_img *img)
{
	t_lg_rec	r;
	size_t		n;
	uint64_t	off;
	uint64_t	end;

	end = LG_WAL_HDR;
	off = end;
	n = lg_rec_decode(tab, img->p + off, img->size - off, &r);
	while (n != 0)
	{
		off += n;
		if (r.type == LG_REC_COMMIT && r.x.id != end)
			break ;
		if (r.type == LG_REC_COMMIT)
			end = off;
		n = lg_rec_decode(tab, img->p + off, img->size - off, &r);
	}
	return (end);
}

/*
** The bytes from end on are not a complete batch. Is "one batch was
** being written when the machine died" a possible explanation?
** Its blocks may have reached the disk in any order, so intact
** records, and even the batch's own commit record, may sit behind a
** hole. But a commit record of another batch, or anything at all
** behind this batch's commit record, was written later, and a later
** write only starts after this batch was durable and acknowledged.
** Returns 0 if the tail may be cut, -1 if it must not be.
*/
int	lg_scan_tail(const uint32_t *tab, const t_lg_img *img, uint64_t end)
{
	t_lg_rec	r;
	size_t		n;
	uint64_t	p;

	p = end;
	while (p < img->size)
	{
		n = lg_rec_decode(tab, img->p + p, img->size - p, &r);
		if (n != 0 && r.type == LG_REC_COMMIT)
			return (-(r.x.id != end || p + n != img->size));
		p++;
	}
	return (0);
}

/*
** Records why the log is refused, for the caller of lg_open.
*/
t_lg_rc	lg_why(t_ledger *l, int why)
{
	if (l->cfg.why)
		*l->cfg.why = why;
	return (LG_CORRUPT);
}
