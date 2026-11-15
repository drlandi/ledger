#include "ledger_int.h"
#include <sys/file.h>

/*
** Opens (or creates) the log and takes an exclusive advisory lock on
** it. Two processes appending to one log would interleave records and
** each would trust a state the other is changing, so the second one
** is turned away. The kernel drops the lock when the process dies,
** kill -9 included.
*/
int	lg_wal_fd(const char *path)
{
	int	fd;

	fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
	if (fd >= 0 && flock(fd, LOCK_EX | LOCK_NB) != 0)
	{
		close(fd);
		return (-1);
	}
	return (fd);
}

/*
** A new file only exists for sure once its directory entry is on
** disk, and that takes an fsync of the directory itself.
*/
static int	sync_dir(const char *path)
{
	char	dir[4096];
	char	*slash;
	int		fd;
	int		rc;

	if (strlen(path) >= sizeof(dir))
		return (-1);
	strcpy(dir, path);
	slash = strrchr(dir, '/');
	if (!slash)
		strcpy(dir, ".");
	else if (slash == dir)
		dir[1] = '\0';
	else
		*slash = '\0';
	fd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (fd < 0)
		return (-1);
	rc = fsync(fd);
	close(fd);
	return (rc);
}

int	lg_wal_header(int fd, const char *path)
{
	if (ftruncate(fd, 0) != 0)
		return (-1);
	if (pwrite(fd, LG_WAL_MAGIC, LG_WAL_HDR, 0) != LG_WAL_HDR)
		return (-1);
	if (fsync(fd) != 0)
		return (-1);
	return (sync_dir(path));
}
