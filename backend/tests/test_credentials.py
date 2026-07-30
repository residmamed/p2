"""The credential pools: how many accounts a vendor has, and in what order they
are handed out.

Worth testing despite being small, because every failure mode here is silent.
A pool that drops the second account just runs slower; one that pairs an Oxylabs
username with the wrong password sends a valid-looking request that 401s; one
that rotates within an Apify run polls for a run that does not exist on that
account and reads back an empty dataset, which the pipeline reports as "no
suppliers found" rather than as a fault.
"""
import pytest

from app.credentials import KeyPool, _numbered


def test_numbered_reads_base_then_suffixes_in_order():
    env = {"K": "one", "K_2": "two", "K_3": "three"}
    assert _numbered(env, "K") == ["one", "two", "three"]


def test_numbered_without_suffixes_is_a_single_key():
    """The configuration every existing deployment has. It must keep behaving
    exactly as it did before pools existed."""
    assert _numbered({"K": "only"}, "K") == ["only"]


def test_numbered_skips_gaps_and_blanks():
    """A gap is a deleted account, not the end of the list. Stopping at the
    first missing number would silently ignore every account past it."""
    env = {"K": "one", "K_3": "three", "K_4": "   ", "K_5": "five"}
    assert _numbered(env, "K") == ["one", "three", "five"]


def test_numbered_strips_surrounding_whitespace():
    assert _numbered({"K": "  spaced  "}, "K") == ["spaced"]


def test_numbered_ignores_unrelated_names():
    """`K_X` is a typo, not an account. It must not be picked up as one."""
    env = {"K": "one", "K_X": "typo", "KK_2": "other", "K_2": "two"}
    assert _numbered(env, "K") == ["one", "two"]


def test_unset_pool_is_falsey_and_yields_none():
    pool = KeyPool("NOTHING", [])
    assert not pool
    assert len(pool) == 0
    assert pool.next() is None


def test_single_key_pool_always_returns_it():
    pool = KeyPool("ONE", ["a"])
    assert bool(pool)
    assert [pool.next() for _ in range(3)] == ["a", "a", "a"]


def test_rotation_is_round_robin_and_wraps():
    pool = KeyPool("THREE", ["a", "b", "c"])
    assert [pool.next() for _ in range(7)] == ["a", "b", "c", "a", "b", "c", "a"]


def test_pool_holds_tuples_for_paired_credentials():
    """Oxylabs identifies an account by username AND password, so the pool has
    to rotate the pair as one unit — rotating the two lists independently would
    eventually pair account 1's username with account 2's password."""
    pool = KeyPool("PAIRS", [("u1", "p1"), ("u2", "p2")])
    assert pool.next() == ("u1", "p1")
    assert pool.next() == ("u2", "p2")
    assert pool.next() == ("u1", "p1")


def test_all_returns_a_copy_not_the_backing_list():
    """Callers that fan out across every account get a list they can consume
    without emptying the pool for everyone else."""
    pool = KeyPool("TWO", ["a", "b"])
    got = pool.all
    got.clear()
    assert len(pool) == 2


@pytest.mark.parametrize(
    "usernames, passwords, expected",
    [
        (["u1", "u2"], ["p1", "p2"], [("u1", "p1"), ("u2", "p2")]),
        # A username with no matching password is dropped rather than sent as
        # half a credential — an empty password is a guaranteed 401 that looks
        # like a rejected account.
        (["u1", "u2"], ["p1"], [("u1", "p1")]),
        ([], ["p1"], []),
    ],
)
def test_oxylabs_pairing_drops_unmatched_halves(usernames, passwords, expected):
    assert list(zip(usernames, passwords)) == expected
