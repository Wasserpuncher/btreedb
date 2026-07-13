"""On-disk B+ tree.

Layout: the file is a flat array of fixed-size pages. Page 0 is the meta page
and holds the root pointer; every other page is either an internal node or a
leaf. Keys and values live only in leaves, so a lookup always costs exactly
one root-to-leaf descent -- log_B(n) page reads, and B is large because a page
holds many keys.

Page formats (big-endian, offsets in bytes):

    meta      magic(8) root(4) npages(4)
    internal  type(1) nkeys(2) child0(4)  [ klen(2) key child(4) ] * nkeys
    leaf      type(1) nkeys(2) next(4)    [ klen(2) vlen(2) key value ] * nkeys

An internal node with n keys has n+1 children: child[i] holds every key
strictly below keys[i], and child[n] holds the rest.
"""

from __future__ import annotations

import os
import struct
from typing import Iterator

PAGE_SIZE = 4096
MAGIC = b"BTREEDB1"

_LEAF = 1
_INTERNAL = 2

_META_FMT = ">8sII"
_LEAF_HEADER = 1 + 2 + 4
_INTERNAL_HEADER = 1 + 2 + 4

# Splitting turns one overfull page into two, so *both* halves have to fit.
# That only works if no single entry can eat more than half a page: cap it
# there and a legal split point always exists. (With a full-page cap, one fat
# entry next to a full leaf could produce a half that still overflows -- which
# is exactly the bug test_overwrite_with_larger_value_can_split_the_page pins.)
MAX_ENTRY = (PAGE_SIZE - _LEAF_HEADER) // 2 - 4


class CorruptPage(Exception):
    """The bytes on disk are not a page we wrote."""


def _leaf_size(entries: list[tuple[bytes, bytes]]) -> int:
    return _LEAF_HEADER + sum(4 + len(k) + len(v) for k, v in entries)


def _internal_size(keys: list[bytes]) -> int:
    return _INTERNAL_HEADER + sum(2 + len(k) + 4 for k in keys)


class Leaf:
    __slots__ = ("entries", "next")

    def __init__(self, entries: list[tuple[bytes, bytes]], next_page: int = 0):
        self.entries = entries
        self.next = next_page

    def serialize(self) -> bytes:
        out = bytearray(PAGE_SIZE)
        out[0] = _LEAF
        struct.pack_into(">HI", out, 1, len(self.entries), self.next)
        pos = _LEAF_HEADER
        for key, value in self.entries:
            struct.pack_into(">HH", out, pos, len(key), len(value))
            pos += 4
            out[pos:pos + len(key)] = key
            pos += len(key)
            out[pos:pos + len(value)] = value
            pos += len(value)
        if pos > PAGE_SIZE:
            raise CorruptPage(f"leaf overflows page: {pos} > {PAGE_SIZE}")
        return bytes(out)

    @staticmethod
    def parse(buf: bytes) -> "Leaf":
        nkeys, next_page = struct.unpack_from(">HI", buf, 1)
        entries = []
        pos = _LEAF_HEADER
        for _ in range(nkeys):
            klen, vlen = struct.unpack_from(">HH", buf, pos)
            pos += 4
            key = bytes(buf[pos:pos + klen])
            pos += klen
            value = bytes(buf[pos:pos + vlen])
            pos += vlen
            entries.append((key, value))
        return Leaf(entries, next_page)


class Internal:
    __slots__ = ("keys", "children")

    def __init__(self, keys: list[bytes], children: list[int]):
        if len(children) != len(keys) + 1:
            raise CorruptPage(f"{len(keys)} keys need {len(keys) + 1} children, got {len(children)}")
        self.keys = keys
        self.children = children

    def serialize(self) -> bytes:
        out = bytearray(PAGE_SIZE)
        out[0] = _INTERNAL
        struct.pack_into(">HI", out, 1, len(self.keys), self.children[0])
        pos = _INTERNAL_HEADER
        for key, child in zip(self.keys, self.children[1:]):
            struct.pack_into(">H", out, pos, len(key))
            pos += 2
            out[pos:pos + len(key)] = key
            pos += len(key)
            struct.pack_into(">I", out, pos, child)
            pos += 4
        if pos > PAGE_SIZE:
            raise CorruptPage(f"internal node overflows page: {pos} > {PAGE_SIZE}")
        return bytes(out)

    @staticmethod
    def parse(buf: bytes) -> "Internal":
        nkeys, child0 = struct.unpack_from(">HI", buf, 1)
        keys: list[bytes] = []
        children = [child0]
        pos = _INTERNAL_HEADER
        for _ in range(nkeys):
            (klen,) = struct.unpack_from(">H", buf, pos)
            pos += 2
            keys.append(bytes(buf[pos:pos + klen]))
            pos += klen
            (child,) = struct.unpack_from(">I", buf, pos)
            pos += 4
            children.append(child)
        return Internal(keys, children)

    def route(self, key: bytes) -> int:
        """Which child holds `key`."""
        for i, sep in enumerate(self.keys):
            if key < sep:
                return self.children[i]
        return self.children[-1]


class Pager:
    """Reads and writes fixed-size pages, and hands out new page numbers.

    Keeps recently used pages parsed in memory. `reads` counts pages actually
    fetched from disk -- that counter is what the benchmark reports, because it
    is the cost model that matters and it does not depend on how fast the
    machine happens to be.
    """

    def __init__(self, path: str, cache_pages: int = 1024):
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.f = open(path, "w+b" if new else "r+b")
        self.cache: dict[int, object] = {}
        self.cache_pages = cache_pages
        self.reads = 0
        if new:
            self.root = 0
            self.npages = 1
            self._write_meta()
        else:
            self.f.seek(0)
            magic, self.root, self.npages = struct.unpack(_META_FMT, self.f.read(16))
            if magic != MAGIC:
                raise CorruptPage(f"not a btreedb file (magic {magic!r})")

    def _write_meta(self) -> None:
        buf = bytearray(PAGE_SIZE)
        struct.pack_into(_META_FMT, buf, 0, MAGIC, self.root, self.npages)
        self.f.seek(0)
        self.f.write(buf)

    def read(self, page: int) -> bytes:
        self.f.seek(page * PAGE_SIZE)
        buf = self.f.read(PAGE_SIZE)
        if len(buf) != PAGE_SIZE:
            raise CorruptPage(f"short read on page {page}")
        return buf

    def write(self, page: int, buf: bytes) -> None:
        self.f.seek(page * PAGE_SIZE)
        self.f.write(buf)
        self.cache.pop(page, None)

    def allocate(self) -> int:
        page = self.npages
        self.npages += 1
        return page

    def load(self, page: int):
        node = self.cache.get(page)
        if node is not None:
            return node
        buf = self.read(page)
        self.reads += 1
        kind = buf[0]
        if kind == _LEAF:
            node = Leaf.parse(buf)
        elif kind == _INTERNAL:
            node = Internal.parse(buf)
        else:
            raise CorruptPage(f"page {page} has unknown type {kind}")
        if len(self.cache) >= self.cache_pages:
            self.cache.pop(next(iter(self.cache)))
        self.cache[page] = node
        return node

    def commit(self) -> None:
        """Make everything written so far survive a crash.

        fsync costs milliseconds, so put() does not call this -- it only keeps
        the meta page current in the OS cache. Durability is an explicit act,
        the same bargain every real database makes.
        """
        self._write_meta()
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        self.commit()
        self.f.close()


class BTree:
    """A B+ tree. Keys and values are bytes; keys are ordered bytewise."""

    def __init__(self, path: str):
        self.pager = Pager(path)
        if self.pager.root == 0:
            root = self.pager.allocate()
            self.pager.write(root, Leaf([]).serialize())
            self.pager.root = root
            self.pager.commit()

    # -- reading ---------------------------------------------------------

    def get(self, key: bytes) -> bytes | None:
        page = self.pager.root
        while True:
            node = self.pager.load(page)
            if isinstance(node, Leaf):
                for k, v in node.entries:
                    if k == key:
                        return v
                return None
            page = node.route(key)

    def height(self) -> int:
        """Number of pages touched by one lookup. This is the number that matters."""
        page, levels = self.pager.root, 1
        while True:
            node = self.pager.load(page)
            if isinstance(node, Leaf):
                return levels
            page = node.children[0]
            levels += 1

    def items(self) -> Iterator[tuple[bytes, bytes]]:
        """Every pair in key order -- leaves are chained, so this is a linear walk."""
        page = self.pager.root
        while True:
            node = self.pager.load(page)
            if isinstance(node, Leaf):
                break
            page = node.children[0]
        while page:
            leaf = self.pager.load(page)
            yield from leaf.entries
            page = leaf.next

    # -- writing ---------------------------------------------------------

    def put(self, key: bytes, value: bytes) -> None:
        if len(key) + len(value) > MAX_ENTRY:
            raise ValueError(f"entry of {len(key) + len(value)} bytes exceeds {MAX_ENTRY}")
        split = self._insert(self.pager.root, key, value)
        if split is not None:
            sep, right = split
            left = self.pager.root
            new_root = self.pager.allocate()
            self.pager.write(new_root, Internal([sep], [left, right]).serialize())
            self.pager.root = new_root
        self.pager._write_meta()

    def commit(self) -> None:
        """fsync everything written so far. See Pager.commit."""
        self.pager.commit()

    def _insert(self, page: int, key: bytes, value: bytes) -> tuple[bytes, int] | None:
        """Insert below `page`. Returns (separator, new_right_page) if `page` split."""
        node = self.pager.load(page)

        if isinstance(node, Leaf):
            entries = node.entries
            lo, found = 0, -1
            for i, (k, _) in enumerate(entries):
                if k == key:
                    found = i
                    break
                if k < key:
                    lo = i + 1
            if found >= 0:
                # An overwrite can still overflow the page: the new value may be
                # larger than the old one. It has to go through the same size
                # check and split path as a fresh insert.
                entries[found] = (key, value)
            else:
                entries.insert(lo, (key, value))

            if _leaf_size(entries) <= PAGE_SIZE:
                self.pager.write(page, Leaf(entries, node.next).serialize())
                return None
            return self._split_leaf(page, entries, node.next)

        child = node.route(key)
        split = self._insert(child, key, value)
        if split is None:
            return None

        sep, right = split
        pos = 0
        for i, k in enumerate(node.keys):
            if sep < k:
                break
            pos = i + 1
        node.keys.insert(pos, sep)
        node.children.insert(pos + 1, right)

        if _internal_size(node.keys) <= PAGE_SIZE:
            self.pager.write(page, node.serialize())
            return None
        return self._split_internal(page, node)

    @staticmethod
    def _split_point(sizes: list[int], header: int) -> int:
        """Where to cut so that both halves fit in a page.

        Splitting by entry *count* is the obvious thing and it is wrong: with
        entries of unequal size, half the entries can still be more than half
        the bytes. Cut by bytes instead, and pick the most balanced cut among
        those where each side fits.
        """
        total = sum(sizes)
        best, best_balance = -1, None
        acc = 0
        for i in range(1, len(sizes)):
            acc += sizes[i - 1]
            left, right = header + acc, header + (total - acc)
            if left <= PAGE_SIZE and right <= PAGE_SIZE:
                balance = abs(left - right)
                if best_balance is None or balance < best_balance:
                    best, best_balance = i, balance
        if best < 0:
            raise CorruptPage("no legal split point -- an entry exceeds half a page")
        return best

    def _split_leaf(self, page: int, entries, next_page: int) -> tuple[bytes, int]:
        sizes = [4 + len(k) + len(v) for k, v in entries]
        mid = self._split_point(sizes, _LEAF_HEADER)
        left, right = entries[:mid], entries[mid:]
        right_page = self.pager.allocate()
        self.pager.write(right_page, Leaf(right, next_page).serialize())
        self.pager.write(page, Leaf(left, right_page).serialize())
        # In a B+ tree the separator also stays in the leaf; it only gets copied up.
        return right[0][0], right_page

    def _split_internal(self, page: int, node: Internal) -> tuple[bytes, int]:
        sizes = [2 + len(k) + 4 for k in node.keys]
        mid = self._split_point(sizes, _INTERNAL_HEADER)
        sep = node.keys[mid]                      # moves up, is not duplicated
        left = Internal(node.keys[:mid], node.children[:mid + 1])
        right = Internal(node.keys[mid + 1:], node.children[mid + 1:])
        right_page = self.pager.allocate()
        self.pager.write(right_page, right.serialize())
        self.pager.write(page, left.serialize())
        return sep, right_page

    def close(self) -> None:
        self.pager.close()

    def __enter__(self) -> "BTree":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
