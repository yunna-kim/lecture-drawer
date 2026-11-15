"""One service per state directory; never read a byte another instance locks."""
import errno
import os


def acquire(home):
    lock = (home / "service.lock").open("a+b")
    if os.name != "nt":
        return lock
    import msvcrt
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        lock.close()
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return None
        raise
    if os.fstat(lock.fileno()).st_size == 0:
        lock.write(b"1")
        lock.flush()
    return lock
