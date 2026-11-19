"""在媒体卷内原子地发布新文件。"""
import ctypes
import errno
import os
from pathlib import Path


def rename_new(src: Path, dest: Path) -> None:
    """原子发布到新名字。Windows rename 和 Linux RENAME_NOREPLACE 均不会覆盖同名文件。"""
    if os.name == "nt":
        os.rename(src, dest)
        return
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is not None:
        # AT_FDCWD=-100，RENAME_NOREPLACE=1。两端路径均相对当前工作目录或为绝对路径。
        if rename(-100, os.fsencode(src), -100, os.fsencode(dest), 1) == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise OSError(err, os.strerror(err), str(dest))
    # 老文件系统可用同卷硬链接完成无覆盖发布。两者均不支持时让调用方保留源文件。
    os.link(src, dest)
    src.unlink()
