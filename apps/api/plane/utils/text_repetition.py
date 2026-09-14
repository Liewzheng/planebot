# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Guard against values that repeat themselves.

A stale client copy union-merged into a collaborative document duplicates its
whole content, and the page title is no exception: the stored name ends up as
"TitleTitle" and keeps growing on every reconnect. `collapse_repeated_text`
folds such a value back to a single copy; it is applied to page names on write
so no client can persist a self-repeating title.
"""

DEFAULT_MIN_UNIT_LENGTH = 8


def collapse_repeated_text(value: str, min_unit_length: int = DEFAULT_MIN_UNIT_LENGTH) -> str:
    """Collapse an exactly repeated value to a single copy.

    "TitleTitle" -> "Title", "ABCABCABC" -> "ABC". Values that are not an exact
    repetition of a unit of at least `min_unit_length` characters are returned
    unchanged, so ordinary short repetitions (e.g. "ABAB") are left alone.
    """
    if not value:
        return value

    candidate_value = value.strip()
    if len(candidate_value) < min_unit_length * 2:
        return candidate_value

    for unit_length in range(min_unit_length, len(candidate_value) // 2 + 1):
        if len(candidate_value) % unit_length != 0:
            continue
        unit = candidate_value[:unit_length]
        if unit * (len(candidate_value) // unit_length) == candidate_value:
            return unit

    return candidate_value
