"""Measures the one thing the README claims: lookup cost does not grow with n.

Reports page reads per lookup, not just wall-clock time. Page reads are the
honest cost model -- they don't depend on how fast this particular laptop is,
and they are what a database on a real disk actually pays for.
"""

from __future__ import annotations

import os
import random
import tempfile
import time

from .btree import BTree


def _cold(tree: BTree) -> None:
    """Drop the page cache, so a lookup pays full price."""
    tree.pager.cache.clear()
    tree.pager.reads = 0


def run(sizes=(1_000, 10_000, 100_000, 1_000_000), probes: int = 200, seed: int = 0) -> None:
    rng = random.Random(seed)
    print(f"{'keys':>10}  {'height':>6}  {'page reads/lookup':>17}  "
          f"{'lookup':>10}  {'full scan':>12}  {'speedup':>8}")
    print("-" * 76)

    for n in sizes:
        path = tempfile.mktemp(suffix=".db")
        tree = BTree(path)
        keys = [f"key:{i:012d}".encode() for i in range(n)]
        rng.shuffle(keys)
        for k in keys:
            tree.put(k, b"v" * 32)
        tree.commit()

        sample = rng.sample(keys, min(probes, n))

        _cold(tree)
        t0 = time.perf_counter()
        for k in sample:
            assert tree.get(k) is not None
            tree.pager.cache.clear()          # every probe pays the full descent
        lookup = (time.perf_counter() - t0) / len(sample)
        reads = tree.pager.reads / len(sample)

        # The alternative: no index at all. Walk the leaves until the key shows
        # up. Same data, same file -- only the access path differs.
        _cold(tree)
        target = sample[len(sample) // 2]
        t0 = time.perf_counter()
        for k, _ in tree.items():
            if k == target:
                break
        scan = time.perf_counter() - t0

        print(f"{n:>10,}  {tree.height():>6}  {reads:>17.1f}  "
              f"{lookup * 1e6:>7.0f} µs  {scan * 1e3:>9.1f} ms  "
              f"{scan / lookup:>7.0f}×")

        tree.close()
        os.unlink(path)


if __name__ == "__main__":
    run()
