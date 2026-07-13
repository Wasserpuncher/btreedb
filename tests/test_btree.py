import os
import random
import tempfile

import pytest

from btreedb.btree import MAX_ENTRY, PAGE_SIZE, BTree, CorruptPage, Internal, Leaf


@pytest.fixture
def db(tmp_path):
    with BTree(str(tmp_path / "t.db")) as tree:
        yield tree


def test_get_missing_key_is_none(db):
    assert db.get(b"nope") is None


def test_put_then_get(db):
    db.put(b"k", b"v")
    assert db.get(b"k") == b"v"


def test_overwrite_replaces_value(db):
    db.put(b"k", b"one")
    db.put(b"k", b"two")
    assert db.get(b"k") == b"two"
    assert sum(1 for _ in db.items()) == 1


def test_overwrite_with_larger_value_can_split_the_page(db):
    # Two bugs lived here. An overwrite grows the entry, so it has to go
    # through the same size check as an insert -- and the split that follows
    # has to cut by bytes, not by entry count, or the fat value lands in a
    # half that still overflows.
    for i in range(60):
        db.put(f"k{i:03d}".encode(), b"x" * 60)
    db.put(b"k007", b"y" * 1800)
    assert db.get(b"k007") == b"y" * 1800
    assert db.get(b"k008") == b"x" * 60
    assert sum(1 for _ in db.items()) == 60


def test_split_stays_correct_when_values_have_wildly_different_sizes(db):
    # The case that a count-based split gets wrong: a page whose entries are
    # nowhere near uniform.
    sizes = [5, 1900, 5, 1900, 5, 1900, 5, 5, 1900, 5]
    for i, n in enumerate(sizes):
        db.put(f"k{i:02d}".encode(), b"v" * n)
    for i, n in enumerate(sizes):
        assert db.get(f"k{i:02d}".encode()) == b"v" * n


def test_empty_value_is_not_a_missing_key(db):
    db.put(b"k", b"")
    assert db.get(b"k") == b""


def test_entry_larger_than_a_page_is_rejected(db):
    with pytest.raises(ValueError, match="exceeds"):
        db.put(b"k", b"x" * MAX_ENTRY)


def test_items_are_sorted_bytewise(db):
    for k in [b"b", b"a", b"c", b"aa"]:
        db.put(k, b"")
    assert [k for k, _ in db.items()] == [b"a", b"aa", b"b", b"c"]


def test_tree_grows_taller_and_lookup_cost_stays_logarithmic(db):
    for i in range(5000):
        db.put(f"key:{i:08d}".encode(), b"v" * 40)
    # 5000 keys across many pages, but a lookup still touches a handful.
    assert db.height() >= 2
    assert db.height() <= 4
    assert db.get(b"key:00002500") == b"v" * 40


def test_survives_reopen(tmp_path):
    path = str(tmp_path / "p.db")
    with BTree(path) as tree:
        for i in range(2000):
            tree.put(f"k{i:05d}".encode(), f"v{i}".encode())
    with BTree(path) as tree:
        assert tree.get(b"k01999") == b"v1999"
        assert sum(1 for _ in tree.items()) == 2000


def test_rejects_a_file_that_is_not_a_database(tmp_path):
    path = tmp_path / "junk.db"
    path.write_bytes(b"definitely not a btreedb file" + b"\0" * 5000)
    with pytest.raises(CorruptPage, match="not a btreedb file"):
        BTree(str(path))


def test_internal_node_needs_one_more_child_than_keys():
    with pytest.raises(CorruptPage):
        Internal([b"a", b"b"], [1, 2])


def test_leaf_roundtrips_through_bytes():
    leaf = Leaf([(b"a", b"1"), (b"b", b"2")], next_page=9)
    back = Leaf.parse(leaf.serialize())
    assert back.entries == [(b"a", b"1"), (b"b", b"2")]
    assert back.next == 9


def test_internal_roundtrips_through_bytes():
    node = Internal([b"m"], [3, 4])
    back = Internal.parse(node.serialize())
    assert back.keys == [b"m"] and back.children == [3, 4]


def test_route_picks_the_child_that_can_hold_the_key():
    node = Internal([b"d", b"h"], [1, 2, 3])
    assert node.route(b"a") == 1      # < d
    assert node.route(b"d") == 2      # >= d, < h
    assert node.route(b"z") == 3      # >= h


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_matches_a_dict_on_random_operations(tmp_path, seed):
    """The real test: behave exactly like dict, on data we did not choose.

    Same idea as verifying a regex engine against `re` -- the reference is
    known-correct, so any disagreement is our bug.
    """
    rng = random.Random(seed)
    ref: dict[bytes, bytes] = {}
    with BTree(str(tmp_path / f"r{seed}.db")) as tree:
        for _ in range(4000):
            key = rng.randbytes(rng.randint(1, 50))
            value = rng.randbytes(rng.randint(0, 120))
            tree.put(key, value)
            ref[key] = value
            if rng.random() < 0.10:            # probe a key we never inserted
                miss = rng.randbytes(60)
                assert tree.get(miss) == ref.get(miss)

        for key, value in ref.items():
            assert tree.get(key) == value
        assert list(tree.items()) == sorted(ref.items())
