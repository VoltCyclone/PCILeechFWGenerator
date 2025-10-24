import pytest

from src.device_clone import overlay_utils


class BadLen:
    def __len__(self):
        raise RuntimeError("broken len")


def test_normalize_none_and_ints():
    assert overlay_utils.normalize_overlay_entry_count(None) == 0
    assert overlay_utils.normalize_overlay_entry_count(5) == 5
    # negative ints clamp to zero
    assert overlay_utils.normalize_overlay_entry_count(-3) == 0


def test_normalize_numeric_strings_and_bad_string():
    assert overlay_utils.normalize_overlay_entry_count("0x10") == 16
    assert overlay_utils.normalize_overlay_entry_count("42") == 42

    # non-numeric string falls back to __len__ behavior (current implementation)
    s = "notanumber"
    assert overlay_utils.normalize_overlay_entry_count(s) == len(s)


def test_normalize_sequences_and_sets():
    assert overlay_utils.normalize_overlay_entry_count([1, 2, 3]) == 3
    assert overlay_utils.normalize_overlay_entry_count((1, 2)) == 2
    assert overlay_utils.normalize_overlay_entry_count({1, 2, 3, 4}) == 4


def test_normalize_dict_with_special_keys_and_generic_dict():
    d = {"OVERLAY_ENTRIES": "0x4"}
    assert overlay_utils.normalize_overlay_entry_count(d) == 4

    d2 = {"overlay_entries": 7}
    assert overlay_utils.normalize_overlay_entry_count(d2) == 7

    # overlay map value treated like entries
    d3 = {"OVERLAY_MAP": [0, 1, 2]}
    assert overlay_utils.normalize_overlay_entry_count(d3) == 3

    # generic dict without special keys -> length of mapping
    d4 = {"a": 1, "b": 2}
    assert overlay_utils.normalize_overlay_entry_count(d4) == 2


def test_normalize_custom_len_and_broken_len():
    class C:
        def __len__(self):
            return 9

    assert overlay_utils.normalize_overlay_entry_count(C()) == 9
    # object with __len__ that raises should fall through to 0
    assert overlay_utils.normalize_overlay_entry_count(BadLen()) == 0
    # plain object without __len__ -> 0
    assert overlay_utils.normalize_overlay_entry_count(object()) == 0


@pytest.mark.parametrize(
    "entry_count, expected",
    [
        (0, 16),
        (1, 16),
        (5, 16),
        (9, 32),
    ],
)
def test_compute_sparse_hash_table_size_defaults(entry_count, expected):
    assert overlay_utils.compute_sparse_hash_table_size(entry_count) == expected


def test_compute_sparse_hash_table_size_min_and_max():
    # custom min_size
    assert overlay_utils.compute_sparse_hash_table_size(1, min_size=8) == 8

    # enforce max_size cap
    assert (
        overlay_utils.compute_sparse_hash_table_size(1000000, max_size=1024) == 1024
    )
