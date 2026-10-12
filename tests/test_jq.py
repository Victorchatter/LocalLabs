import pytest

from locallab.pipeline import JqError, _eval_jq

DATA = {
    "s": "a==b",
    "t": "x|y",
    "a": {"b": [1, 2, 3]},
    "n": None,
    "f": False,
    "num": -3,
    "total_cost": 1.5,
    "runs": [{"results": []}],
}


@pytest.mark.parametrize(
    "expr, expected",
    [
        ('.s == "a==b"', True),
        ('.t == "x|y"', True),
        ('.s != "a==b"', False),
        (".missing", None),
        (".missing.b", None),
        (".n.b[0]", None),
        (".a.b[0]", 1),
        (".a.b[-1]", 3),
        (".a.b[9]", None),
        (".a.b | length", 3),
        (".s | length", 4),
        (".a | length", 1),
        (".n | length", 0),
        (".num | length", 3),
        (".f // 5", 5),
        (".n // .a.b[1]", 2),
        (".num // 5", -3),
        (".f | not", True),
        (".num | not", False),
        ("true and (.n | not)", True),
        (".f and true", False),
        (".f or .num", True),
        (".n or .f", False),
        ("(.a.b | length) > 2 and .s != \"q\"", True),
        (".total_cost <= 2.00", True),
        (".runs[0].results | length == 0", True),
        (". | length", len(DATA)),
        ("null", None),
        ("false == 0", False),
        ("true == 1", False),
        ("false != 0", True),
        ("1 == 1.0", True),
        (".f == 0", False),
        (".n == false", False),
        (".a == .a", True),
    ],
)
def test_eval(expr, expected):
    assert _eval_jq(expr, DATA) == expected


def test_nested_equality_keeps_booleans_apart():
    assert _eval_jq(".x == .y", {"x": [False, {"k": True}], "y": [0, {"k": 1}]}) is False
    assert _eval_jq(".x == .y", {"x": [1, {"k": True}], "y": [1.0, {"k": True}]}) is True


def test_identity_returns_input():
    assert _eval_jq(".", DATA) is DATA


@pytest.mark.parametrize(
    "expr",
    [
        ".s.x",  # key on a string
        ".s[0]",  # index on a string
        ".a < 1",  # incompatible comparison
        ".f | length",  # boolean has no length
        ".a ==",  # incomplete
        "1 == 1 == 1",  # non-associative
        "foo",  # unknown function
        ".[1.5]",  # non-integer index
        '"unterminated',
        "",
    ],
)
def test_errors(expr):
    with pytest.raises(JqError):
        _eval_jq(expr, DATA)


def test_packaged_recipe_expressions():
    assert _eval_jq(".total_cost <= 2.00", {"total_cost": 2.0}) is True
    assert _eval_jq(".total_cost <= 2.00", {"total_cost": 2.5}) is False
    assert _eval_jq(".runs[0].results | length == 0", {"runs": [{"results": []}]}) is True
    assert _eval_jq(".runs[0].results | length == 0", {"runs": [{"results": [{}]}]}) is False
