from quantbot.risk import fixed_fractional, fractional_kelly, kelly_fraction


def test_fixed_fractional_risks_expected_amount():
    # 1% risk, stop 5% away -> notional = 20% of equity.
    frac = fixed_fractional(equity=10_000, entry_price=100, stop_price=95,
                            risk_per_trade=0.01, max_position=1.0)
    assert abs(frac - 0.20) < 1e-9


def test_fixed_fractional_capped_by_max_position():
    frac = fixed_fractional(equity=10_000, entry_price=100, stop_price=99.9,
                            risk_per_trade=0.05, max_position=1.0)
    assert frac == 1.0


def test_fixed_fractional_zero_stop_distance():
    assert fixed_fractional(10_000, 100, 100, 0.01) == 0.0


def test_kelly_positive_edge():
    # p=0.6, b=1 -> f* = 0.6 - 0.4 = 0.2
    assert abs(kelly_fraction(0.6, 1.0) - 0.2) < 1e-9


def test_kelly_negative_edge_is_zero():
    assert kelly_fraction(0.4, 1.0) == 0.0


def test_fractional_kelly_scales_and_clamps():
    full = kelly_fraction(0.7, 2.0)
    quarter = fractional_kelly(0.7, 2.0, kelly_frac=0.25)
    assert abs(quarter - full * 0.25) < 1e-9
    assert fractional_kelly(0.99, 100, kelly_frac=1.0, max_position=0.5) == 0.5
