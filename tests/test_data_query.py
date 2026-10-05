"""Paging, sorting and text filters shared by both repositories."""

import pytest

from pred_platform.contract.errors import InvalidQuery
from pred_platform.data.query import (
    DEFAULT_SIZE,
    MAX_SIZE,
    PageRequest,
    check_choice,
    check_sort,
    contains_text,
    paginate,
    sort_items,
)


def test_the_defaults_are_page_one_of_fifty() -> None:
    assert PageRequest() == PageRequest(1, DEFAULT_SIZE) == PageRequest(number=1, size=50)


@pytest.mark.parametrize(
    ("number", "size"), [(0, 50), (-1, 50), (1, 0), (1, MAX_SIZE + 1), (1.5, 50), (1, "10")]
)
def test_a_bad_page_request_is_rejected(number: object, size: object) -> None:
    with pytest.raises(InvalidQuery):
        PageRequest(number, size)  # type: ignore[arg-type]  # deliberately wrong types


def test_the_maximum_size_is_accepted() -> None:
    assert PageRequest(1, MAX_SIZE).size == MAX_SIZE


def test_paginate_returns_the_slice_and_counts_all_items() -> None:
    items, page = paginate(list(range(1, 11)), PageRequest(2, 4))
    assert items == [5, 6, 7, 8]
    assert (page.number, page.size, page.total) == (2, 4, 10)


def test_the_last_page_may_be_partial() -> None:
    items, page = paginate(list(range(1, 11)), PageRequest(3, 4))
    assert items == [9, 10]
    assert page.total == 10


def test_a_page_past_the_end_is_empty_but_keeps_the_total() -> None:
    items, page = paginate(list(range(1, 11)), PageRequest(9, 4))
    assert items == []
    assert page.total == 10


def test_an_empty_list_has_a_valid_empty_page() -> None:
    items, page = paginate([], PageRequest())
    assert items == []
    assert page.total == 0


def test_check_sort_accepts_an_allowed_key_and_names_the_options_otherwise() -> None:
    assert check_sort("adi", {"adi", "cv2"}) == "adi"
    with pytest.raises(InvalidQuery, match=r"\['adi', 'cv2'\]"):
        check_sort("price", {"adi", "cv2"})


def test_check_choice_accepts_empty_and_allowed_values_and_rejects_others() -> None:
    assert check_choice("estado", None, {"a", "b"}) is None
    assert check_choice("estado", "", {"a", "b"}) is None
    assert check_choice("estado", "a", {"a", "b"}) == "a"
    with pytest.raises(InvalidQuery, match="unknown estado 'z'"):
        check_choice("estado", "z", {"a", "b"})


def test_sort_items_orders_in_both_directions() -> None:
    assert sort_items([3, 1, 2], key=lambda x: x) == [1, 2, 3]
    assert sort_items([3, 1, 2], key=lambda x: x, descending=True) == [3, 2, 1]


def test_sort_items_puts_missing_keys_last_in_either_direction() -> None:
    rows: list[tuple[str, float | None]] = [("a", 2.0), ("b", None), ("c", 1.0)]
    assert [r[0] for r in sort_items(rows, key=lambda r: r[1])] == ["c", "a", "b"]
    assert [r[0] for r in sort_items(rows, key=lambda r: r[1], descending=True)] == ["a", "c", "b"]


def test_sort_items_does_not_mutate_its_input() -> None:
    rows = [3, 1, 2]
    sort_items(rows, key=lambda x: x)
    assert rows == [3, 1, 2]


@pytest.mark.parametrize("needle", [None, "", "   "])
def test_an_empty_filter_matches_everything(needle: str | None) -> None:
    assert contains_text(needle, "FIL-0001")
    assert contains_text(needle, None)


def test_the_text_filter_ignores_case_and_surrounding_spaces() -> None:
    assert contains_text("  fil-00 ", "FIL-0001")
    assert not contains_text("zzz", "FIL-0001")


def test_the_text_filter_matches_any_of_the_given_fields() -> None:
    assert contains_text("sarima", "FIL-0001", None, "classical:sarima")
    assert not contains_text("sarima", "FIL-0001", None)
