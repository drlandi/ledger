#include "ledger_int.h"

void	lg_wal_free(t_wal *w)
{
	if (!w)
		return ;
	if (w->fd >= 0)
		close(w->fd);
	free(w->buf);
	free(w->spare);
	free(w);
}

t_wal	*lg_wal_new(const t_lg_config *cfg)
{
	t_wal		*w;
	uint16_t	probe;

	probe = 1;
	w = calloc(1, sizeof(t_wal));
	if (!w || *(uint8_t *)&probe != 1)
	{
		free(w);
		return (NULL);
	}
	pthread_mutex_init(&w->lock, NULL);
	pthread_cond_init(&w->cond, NULL);
	lg_crc_init(w->crc);
	w->sync_mode = cfg->sync_mode;
	w->buf = malloc(LG_WAL_BUF);
	w->spare = malloc(LG_WAL_BUF);
	w->fd = lg_wal_fd(cfg->wal_path);
	if (!w->buf || !w->spare || w->fd < 0)
	{
		lg_wal_free(w);
		return (NULL);
	}
	return (w);
}

/*
** Makes the file end exactly at the last valid record and positions
** the log there. The cut is fsync'ed before anything is appended, so
** the discarded bytes can never come back behind new records.
*/
t_lg_rc	lg_wal_start(t_wal *w, const char *path, t_lg_img *img)
{
	if (img->end < LG_WAL_HDR)
	{
		if (lg_wal_header(w->fd, path) != 0)
			return (LG_ERROR);
		img->end = LG_WAL_HDR;
	}
	else if (img->end < img->size)
	{
		if (ftruncate(w->fd, (off_t)img->end) != 0 || fsync(w->fd) != 0)
			return (LG_ERROR);
	}
	if (lseek(w->fd, (off_t)img->end, SEEK_SET) < 0)
		return (LG_ERROR);
	w->tail = img->end;
	w->durable = img->end;
	return (LG_OK);
}
