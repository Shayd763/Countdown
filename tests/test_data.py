import pandas as pd

from quantbot.data import load_kraken_csv


def test_load_kraken_csv_headerless(tmp_path):
    # Kraken OHLCVT format: unix_seconds,open,high,low,close,volume,trades
    csv = tmp_path / "XBTGBP_1440.csv"
    csv.write_text(
        "1609459200,29000.0,29500.0,28800.0,29300.0,120.5,4210\n"
        "1609545600,29300.0,30000.0,29100.0,29900.0,98.2,3900\n"
    )
    df = load_kraken_csv(str(csv))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df.iloc[0]["close"] == 29300.0
    # 1609459200 = 2021-01-01T00:00:00Z
    assert df.index[0] == pd.Timestamp("2021-01-01T00:00:00Z")


def test_load_kraken_csv_tolerates_header(tmp_path):
    csv = tmp_path / "XBTGBP_1440.csv"
    csv.write_text(
        "time,open,high,low,close,volume,trades\n"
        "1609459200,29000.0,29500.0,28800.0,29300.0,120.5,4210\n"
    )
    df = load_kraken_csv(str(csv))
    assert len(df) == 1
    assert df.iloc[0]["open"] == 29000.0


def test_load_kraken_csv_sorted(tmp_path):
    csv = tmp_path / "XBTGBP_1440.csv"
    csv.write_text(
        "1609545600,29300.0,30000.0,29100.0,29900.0,98.2,3900\n"
        "1609459200,29000.0,29500.0,28800.0,29300.0,120.5,4210\n"
    )
    df = load_kraken_csv(str(csv))
    assert df.index.is_monotonic_increasing
