import pandas as pd

from quantbot.data import load_coinmetrics_csv


def test_load_coinmetrics_builds_ohlcv(tmp_path):
    # Coin Metrics community CSVs have a `time` col, a `PriceUSD` col, and many
    # other metric columns we ignore.
    csv = tmp_path / "btc.csv"
    csv.write_text(
        "time,AdrActCnt,PriceUSD,CapMrktCurUSD\n"
        "2021-01-01,100,29000.0,5e11\n"
        "2021-01-02,110,29500.0,5.1e11\n"
        "2021-01-03,120,30000.0,5.2e11\n"
    )
    df = load_coinmetrics_csv(str(csv))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    # single daily reference price -> OHLC all equal
    assert (df["open"] == df["close"]).all()
    assert (df["high"] == df["close"]).all()
    assert df.iloc[1]["close"] == 29500.0
    assert str(df.index.tz) == "UTC"


def test_load_coinmetrics_drops_missing_prices(tmp_path):
    csv = tmp_path / "btc.csv"
    csv.write_text(
        "time,PriceUSD\n"
        "2010-07-17,\n"       # no price yet -> dropped
        "2021-01-01,29000.0\n"
    )
    df = load_coinmetrics_csv(str(csv))
    assert len(df) == 1
    assert df.iloc[0]["close"] == 29000.0
