# btreedb

**An on-disk B+ tree in pure Python. A thousand times more data costs one extra page read.**

This is the data structure underneath SQLite, PostgreSQL and every other
database that stays fast when the table gets big. btreedb implements it from
scratch: a file of fixed-size pages, internal nodes that route, leaves that
hold the data, and splits that propagate upward when a page fills. No
dependencies — `os`, `struct` and nothing else.

## The one number that matters

The point of a B-tree is not that it is fast. It is that it *stops getting
slower*. Same file, same data, same machine — only the number of keys changes:

| keys | page reads per lookup | lookup | full scan of the same file | speedup |
| ---: | ---: | ---: | ---: | ---: |
| 1 000 | 2 | 51 µs | 0.7 ms | 13× |
| 10 000 | 2 | 185 µs | 6.6 ms | 36× |
| 100 000 | 3 | 157 µs | 100.3 ms | 637× |
| **1 000 000** | **3** | **287 µs** | **885.1 ms** | **3 083×** |

A thousand times more data. **One extra page read.** That is the whole idea of
a B-tree in one line, and it is why a database can hold a billion rows and
still answer in milliseconds.

Page reads are the honest number here. Wall-clock time depends on how fast this
laptop is; page reads are what the structure actually costs, and they'd be the
same on any machine. Reproduce it yourself:

```console
$ python -m btreedb bench
      keys  height  page reads/lookup      lookup     full scan   speedup
----------------------------------------------------------------------------
     1,000       2                2.0      ~51 µs       ~0.7 ms      ~13×
    10,000       2                2.0     ~185 µs       ~6.6 ms      ~36×
   100,000       3                3.0     ~157 µs     ~100.3 ms     ~637×
 1,000,000       3                3.0     ~287 µs     ~885.1 ms    ~3083×
```

The `~` is not modesty, it is the point. The timings move with the machine — on a
warmer laptop the first row measures 61 µs, not 51. **The page-read column does
not move at all**, and it is the only column making a claim: two reads at a
thousand keys, three at a million. That is what a B-tree promises, and it is the
number that has to be right.

## Use it

```console
$ python -m btreedb put library.db "hitchhiker" "42"
$ python -m btreedb get library.db "hitchhiker"
42

$ python -m btreedb stat library.db
keys:   1
height: 1  (page reads per lookup)
pages:  2  (0.0 MB)
```

```python
from btreedb import BTree

with BTree("library.db") as db:
    db.put(b"douglas", b"adams")
    db.commit()                      # fsync: durability is an explicit act
    print(db.get(b"douglas"))        # b'adams'

    for key, value in db.items():    # in key order, walking the leaf chain
        print(key, value)
```

## How it works

The file is a flat array of 4 KiB pages. Page 0 is the meta page and points at
the root.

```
internal   type(1) nkeys(2) child0(4)  [ klen(2) key child(4) ] * nkeys
leaf       type(1) nkeys(2) next(4)    [ klen(2) vlen(2) key value ] * nkeys
```

An internal node with *n* keys has *n+1* children: `child[i]` holds every key
strictly below `keys[i]`. A lookup starts at the root and routes down until it
reaches a leaf — one page read per level, and there are only three levels at a
million keys, because a 4 KiB page holds a *lot* of keys.

Values live only in leaves (that's the "+" in B+ tree), and the leaves are
chained, so `items()` is a linear walk rather than a tree traversal.

When a page overflows, it splits in two and the separator moves up. If that
fills the parent, the parent splits too. If it reaches the root, the tree gains
a level — which is the only way it ever gets taller, and why every leaf is
always at the same depth.

**The split has to cut by bytes, not by entry count.** Half the entries can be
far more than half the bytes when values differ in size, and then one half
still overflows. That bug is pinned by a test.

## Verified against `dict`

An index is only worth anything if it is *correct*, so the test suite doesn't
trust the tree — it compares it to a reference that is known to be right. Same
approach as checking a regex engine against `re`: hammer both with random data
and any disagreement is a bug.

```console
$ python -m pytest -q
20 passed
```

Five seeds, thousands of random keys and values each, plus overwrites,
absent-key probes, and a reopen from disk. Every key must come back with the
value `dict` says it has, and `items()` must equal `sorted(dict.items())`.

## Limits

Being honest about what this is not:

- **Entries are capped at 2040 bytes** (key + value). Splitting has to leave
  both halves fitting in a page, so no single entry may exceed half of one.
  Real databases spill large values onto overflow pages; btreedb doesn't.
- **No deletes.** Insert, overwrite and read only. Deletion in a B+ tree means
  merging and rebalancing underfull nodes — a good chunk of work in its own right.
- **No concurrency, no transactions.** `commit()` fsyncs; a crash mid-write can
  still leave a torn page. A write-ahead log is the honest fix, and it isn't here.
- **Single process.** No locking.

## Install

```console
$ git clone https://github.com/Wasserpuncher/btreedb
$ cd btreedb
$ python -m btreedb bench
```

Python 3.10+. No dependencies.

## License

MIT
