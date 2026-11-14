import math

import pytest

from terminal import calc


def test_black_scholes_matches_the_textbook_case():
    call = calc.greeks(100, 100, 1, 0.05, 0.2, True)
    put = calc.greeks(100, 100, 1, 0.05, 0.2, False)
    assert call["price"] == pytest.approx(10.4506, abs=1e-3) and call["delta"] == pytest.approx(0.6368, abs=1e-3)
    assert put["price"] == pytest.approx(5.5735, abs=1e-3)
    # put-call parity
    assert call["price"] - put["price"] == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-9)
    assert calc.greeks(100, 100, 0, 0.05, 0.2, True) is None


def test_beta_of_a_series_that_moves_twice_the_market():
    market = [0.01, -0.02, 0.005, 0.03, -0.01] * 6
    out = calc.beta([2 * r + 0.001 for r in market], market)
    assert out["beta"] == pytest.approx(2.0) and out["r2"] == pytest.approx(1.0)
    assert out["adjusted"] == pytest.approx(2 * 2 / 3 + 1 / 3)
    assert calc.beta([0.01] * 5, [0.01] * 5) is None  # too short


def test_aligned_keeps_only_shared_days():
    assert calc.aligned({1: 10, 2: 11, 3: 12}, {2: 5, 3: 6, 4: 7}) == ([11, 12], [5, 6])


def test_max_pain_is_where_holders_get_least():
    calls = [{"strike": 90, "oi": 10}, {"strike": 100, "oi": 500}, {"strike": 110, "oi": 10}]
    puts = [{"strike": 90, "oi": 10}, {"strike": 100, "oi": 500}, {"strike": 110, "oi": 10}]
    assert calc.max_pain(calls, puts) == 100
    assert calc.max_pain([], []) is None


def test_drawdown_and_var():
    assert calc.max_drawdown([100, 120, 60, 90]) == pytest.approx(-0.5)
    rets = [-0.10] * 5 + [0.01] * 95
    assert calc.var95(rets) == pytest.approx(0.01) and calc.var95(rets[:10]) is None
    assert calc.var95([-0.10] * 6 + [0.01] * 94) == pytest.approx(-0.10)


def test_dcf_of_a_flat_perpetuity():
    # no growth at all: value is cash / rate
    assert calc.dcf(100, 0.0, 0.10, 0.0, 0, 1) == pytest.approx(1000)
    assert calc.dcf(100, 0.0, 0.10, 0.0, 200, 10) == pytest.approx(80)
    assert calc.dcf(100, 0.0, 0.02, 0.03, 0, 1) is None  # growth above the discount rate has no value


def test_wacc_weights_equity_and_debt():
    w = calc.wacc(800, 200, 1.0, 0.04, 0.06, 0.25)
    assert w["cost_equity"] == pytest.approx(0.09)
    assert w["wacc"] == pytest.approx(0.8 * 0.09 + 0.2 * 0.06 * 0.75)


def test_merton_safe_firm_is_far_from_default_and_a_leveraged_one_is_not():
    safe, risky = calc.merton(1000, 0.25, 50, 0.04), calc.merton(100, 0.9, 1000, 0.04)
    assert safe["default_prob"] < 1e-6 and safe["distance"] > 5
    assert risky["default_prob"] > 0.05
    assert calc.merton(1000, 0.25, 0, 0.04) is None


def test_altman_and_piotroski():
    assert calc.altman_z(200, 300, 120, 1000, 500, 500, 1000) == pytest.approx(1.2 * .2 + 1.4 * .3 + 3.3 * .12 + 0.6 * 2 + 0.5)
    assert calc.altman_z(None, 300, 120, 1000, 500, 500, 1000) is None
    now = {"net_income": 90, "assets": 1000, "cfo": 110, "long_debt": 150, "current_assets": 400, "current_liabilities": 200,
           "shares": 10, "gross_profit": 300, "revenue": 500}
    before = {"net_income": 60, "assets": 900, "cfo": 80, "long_debt": 200, "current_assets": 300, "current_liabilities": 200,
              "shares": 10, "gross_profit": 220, "revenue": 400}
    assert [ok for _, ok in calc.piotroski(now, before)] == [True] * 9
    assert calc.piotroski({}, {})[0][1] is None


def test_fed_path_reads_rates_off_futures_prices():
    path = calc.fed_path([("2026-11", 96.0), ("2026-12", 96.25), ("2027-01", 95.5)])
    assert path[0]["rate"] == pytest.approx(4.0) and path[0]["moves"] == 0
    assert path[1]["change_bp"] == pytest.approx(-25) and path[1]["moves"] == pytest.approx(-1)
    assert path[2]["moves"] == pytest.approx(2)


def test_seasonality_averages_each_calendar_month():
    monthly = [(2024, 12, 100), (2025, 1, 110), (2025, 2, 99), (2025, 12, 100), (2026, 1, 105)]
    stats = {s["month"]: s for s in calc.seasonality(monthly)}
    assert stats[1]["mean"] == pytest.approx(0.075) and stats[1]["up"] == 2
    assert stats[2]["mean"] == pytest.approx(-0.1) and stats[3]["mean"] is None


def test_hist_vol_and_rsi():
    assert calc.hist_vol([100] * 40, 30) == 0
    assert calc.hist_vol([100, 101], 30) is None
    assert calc.rsi(list(range(1, 40))) == 100
