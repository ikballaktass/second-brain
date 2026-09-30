"""Plain tests for NoteWriter's title sanitizing — no test framework.

Run from the repo root:  PYTHONPATH=src python tests/test_note_writer.py
"""
import sys

from second_brain.tools.note_writer import MAX_TITLE_LEN, _sanitize_title


def check(raw, expected):
    got = _sanitize_title(raw)
    assert got == expected, f"_sanitize_title({raw!r}) -> {got!r}, expected {expected!r}"


def test_forbidden_chars_replaced():
    check('a<b>c:d"e/f\\g|h?i*j', "a-b-c-d-e-f-g-h-i-j")  # Windows/Android
    check("Toplantı #2 [[plan]] ^x", "Toplantı -2 --plan-- -x")  # Obsidian syntax


def test_whitespace_collapsed_and_trimmed():
    check("  alışveriş \n\t listesi  ", "alışveriş listesi")


def test_leading_and_trailing_dots_trimmed():
    check("..gizli not..", "gizli not")


def test_long_title_truncated():
    got = _sanitize_title("a" * 200)
    assert len(got) == MAX_TITLE_LEN, f"length {len(got)}, expected {MAX_TITLE_LEN}"


def test_truncation_does_not_leave_trailing_space():
    check("x" * (MAX_TITLE_LEN - 1) + " yyyyyyyy", "x" * (MAX_TITLE_LEN - 1))


def test_empty_after_cleaning_becomes_untitled():
    for raw in ["", "   ", " . . ", "\n"]:
        check(raw, "untitled")


TESTS = [
    test_forbidden_chars_replaced,
    test_whitespace_collapsed_and_trimmed,
    test_leading_and_trailing_dots_trimmed,
    test_long_title_truncated,
    test_truncation_does_not_leave_trailing_space,
    test_empty_after_cleaning_becomes_untitled,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as err:
            failed += 1
            print(f"FAIL  {test.__name__}: {err}")
        except Exception as err:
            failed += 1
            print(f"ERROR {test.__name__}: {type(err).__name__}: {err}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main()
    )