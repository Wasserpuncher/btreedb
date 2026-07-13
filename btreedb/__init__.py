"""btreedb -- an on-disk B+ tree, in pure Python, with no dependencies."""

from .btree import PAGE_SIZE, BTree, CorruptPage

__all__ = ["BTree", "CorruptPage", "PAGE_SIZE"]
__version__ = "0.1.0"
