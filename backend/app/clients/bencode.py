"""bencode 编解码 + info_hash 计算(下载器无关,qB/BitComet 都本地算 hash)。"""
import hashlib


def _bdecode(data: bytes, i: int = 0):
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    colon = data.index(b":", i)
    length = int(data[i:colon])
    start = colon + 1
    return data[start:start + length], start + length


def _bencode(obj) -> bytes:
    if isinstance(obj, int):
        return b"i%de" % obj
    if isinstance(obj, bytes):
        return b"%d:%s" % (len(obj), obj)
    if isinstance(obj, list):
        return b"l" + b"".join(_bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(v) for k, v in sorted(obj.items())) + b"e"
    raise TypeError(type(obj))


def info_hash_of(torrent_bytes: bytes) -> str:
    meta, _ = _bdecode(torrent_bytes)
    return hashlib.sha1(_bencode(meta[b"info"])).hexdigest()
