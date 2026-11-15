#include "ledger_int.h"

/*
** 1: the file starts with the magic.
** 0: no usable header yet, because a crash interrupted the creation of
**    the file: it is empty, or at most a header long and either a
**    prefix of the magic or all zeros (size on disk, data not).
** -1: anything else. Not ours; must not be touched.
*/
int	lg_hdr_check(const t_lg_img *img)
{
	uint64_t	i;

	if (img->size >= LG_WAL_HDR
		&& memcmp(img->p, LG_WAL_MAGIC, LG_WAL_HDR) == 0)
		return (1);
	if (img->size > LG_WAL_HDR)
		return (-1);
	if (memcmp(img->p, LG_WAL_MAGIC, img->size) == 0)
		return (0);
	i = 0;
	while (i < img->size && img->p[i] == 0)
		i++;
	if (i == img->size)
		return (0);
	return (-1);
}

/*
** Writes one record into out (at least LG_REC_MAX bytes) and returns
** its size. A create record travels in a t_lg_xfer too: id = account
** id, from = initial balance, to = flags; its body is the first 20
** bytes of that struct. Byte order is the host's: little-endian only
** (lg_wal_new refuses to run elsewhere).
*/
size_t	lg_rec_encode(const uint32_t *tab, uint8_t *out, uint8_t type,
			const t_lg_xfer *x)
{
	uint32_t	len;
	uint32_t	crc;

	len = LG_LEN_XFER;
	if (type == LG_REC_CREATE)
		len = LG_LEN_CREATE;
	memcpy(out, &len, 4);
	out[LG_REC_HDR] = type;
	memcpy(out + LG_REC_HDR + 1, x, len - 1);
	crc = lg_crc32(tab, 0, out, 4);
	crc = lg_crc32(tab, crc, out + LG_REC_HDR, len);
	memcpy(out + 4, &crc, 4);
	return (LG_REC_HDR + len);
}

static int	rec_shape_ok(uint32_t len, uint8_t type)
{
	if (type == LG_REC_CREATE)
		return (len == LG_LEN_CREATE);
	if (type == LG_REC_XFER)
		return (len == LG_LEN_XFER);
	return (0);
}

/*
** Reads the record at p, of which avail bytes exist. Returns its size,
** or 0 if there is no complete, intact record here: too few bytes,
** impossible length, checksum mismatch, or unknown type. The length
** is validated before it is used, so garbage cannot make us read
** past avail.
*/
size_t	lg_rec_decode(const uint32_t *tab, const uint8_t *p, uint64_t avail,
			t_lg_rec *r)
{
	uint32_t	len;
	uint32_t	crc;
	uint32_t	want;

	if (avail < LG_REC_HDR)
		return (0);
	memcpy(&len, p, 4);
	memcpy(&want, p + 4, 4);
	if (len != LG_LEN_CREATE && len != LG_LEN_XFER)
		return (0);
	if (avail < LG_REC_HDR + len)
		return (0);
	crc = lg_crc32(tab, 0, p, 4);
	crc = lg_crc32(tab, crc, p + LG_REC_HDR, len);
	if (crc != want || !rec_shape_ok(len, p[LG_REC_HDR]))
		return (0);
	r->type = p[LG_REC_HDR];
	memset(&r->x, 0, sizeof(r->x));
	memcpy(&r->x, p + LG_REC_HDR + 1, len - 1);
	return (LG_REC_HDR + len);
}
