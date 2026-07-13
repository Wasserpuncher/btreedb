"""CLI: put, get, scan, stat, bench."""

from __future__ import annotations

import argparse
import sys

from .btree import BTree


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="btreedb", description="An on-disk B+ tree.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("put", help="store a key")
    sp.add_argument("db")
    sp.add_argument("key")
    sp.add_argument("value")

    sg = sub.add_parser("get", help="look a key up")
    sg.add_argument("db")
    sg.add_argument("key")

    ss = sub.add_parser("scan", help="print every pair, in key order")
    ss.add_argument("db")
    ss.add_argument("--limit", type=int, default=0)

    st = sub.add_parser("stat", help="tree height and page count")
    st.add_argument("db")

    sb = sub.add_parser("bench", help="show that lookup cost stays flat")
    sb.add_argument("--max", type=int, default=1_000_000)

    args = p.parse_args(argv)

    if args.cmd == "bench":
        from .bench import run
        run(sizes=[n for n in (1_000, 10_000, 100_000, 1_000_000) if n <= args.max])
        return 0

    with BTree(args.db) as tree:
        if args.cmd == "put":
            tree.put(args.key.encode(), args.value.encode())
            tree.commit()
        elif args.cmd == "get":
            value = tree.get(args.key.encode())
            if value is None:
                print(f"{args.key}: not found", file=sys.stderr)
                return 1
            print(value.decode(errors="replace"))
        elif args.cmd == "scan":
            for i, (k, v) in enumerate(tree.items()):
                if args.limit and i >= args.limit:
                    break
                print(f"{k.decode(errors='replace')}\t{v.decode(errors='replace')}")
        elif args.cmd == "stat":
            n = sum(1 for _ in tree.items())
            print(f"keys:   {n:,}")
            print(f"height: {tree.height()}  (page reads per lookup)")
            print(f"pages:  {tree.pager.npages:,}  ({tree.pager.npages * 4096 / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
