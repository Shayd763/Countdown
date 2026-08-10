from cv_engine.ir35 import classify, matches_filter
from cv_engine.models import IR35_INSIDE, IR35_OUTSIDE, IR35_UNKNOWN


def test_inside_variants():
    assert classify("This role is Inside IR35") == IR35_INSIDE
    assert classify("determined inside via PAYE only") == IR35_INSIDE
    assert classify("Umbrella company basis") == IR35_INSIDE
    assert classify("IR35: inside") == IR35_INSIDE
    assert classify("6 month contract, inside ir-35") == IR35_INSIDE


def test_outside_variants():
    assert classify("Genuine outside IR35 engagement") == IR35_OUTSIDE
    assert classify("This is outside of IR35") == IR35_OUTSIDE
    assert classify("IR35 - Outside") == IR35_OUTSIDE


def test_outside_takes_precedence_over_ir35_mention():
    # An advert that says "outside IR35" must not be misread as inside.
    assert classify("Not inside; this is strictly outside IR35") == IR35_OUTSIDE


def test_unknown():
    assert classify("Great contract role, competitive rate") == IR35_UNKNOWN
    assert classify("") == IR35_UNKNOWN


def test_matches_filter():
    assert matches_filter(IR35_INSIDE, "inside")
    assert not matches_filter(IR35_OUTSIDE, "inside")
    assert matches_filter(IR35_OUTSIDE, "any")
    assert matches_filter(IR35_UNKNOWN, "all")
