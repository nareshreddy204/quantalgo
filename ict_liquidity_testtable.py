#!/usr/bin/env python3
"""
ICT-inspired Liquidity Sweep & OB detector with an ICT-style report generator.
Extended: adds a compact second table listing instances and a compact OB/FVG indicator.
"""
from __future__ import annotations

import argparse
from typing import List
from dataclasses import dataclass
import pandas as pd
import numpy as np

# Optional pretty printing
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except Exception:
    console = None

@dataclass
class ICTInstance:
    index: int
    timestamp: pd.Timestamp
    symbol: str
    timeframe: str
    sweep_type: str  # "BSL" (Buy-Side) or "SSL" (Sell-Side)
    market_context: str
    sweep_level_low: float
    sweep_level_high: float
    entry_price: float
    exit_price: float | None
    atr_at_sweep: float
    vsf_at_sweep: float
    vwap_at_sweep: float
    fvg_present: bool
    ob_type: str | None
    ms_present: bool
    notes: str
    ob_fvg_indicator: str | None = None

# ----------------------------
# Indicators (lightweight)
# ----------------------------
def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"]
    l = df["low"]
    c = df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()

def compute_vsf(df: pd.DataFrame) -> pd.Series:
    vol_ma = df["volume"].rolling(window=20, min_periods=1).mean()
    return df["volume"] / (vol_ma + 1e-9)

def compute_vwap_proxy(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    return (tp * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)

def add_indicators(df: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = compute_atr(df, atr_period)
    df["vsf"] = compute_vsf(df)
    df["vwap"] = compute_vwap_proxy(df)
    return df

# ----------------------------
# Detector core
# ----------------------------
def detect_liquidity_sweeps(
    df: pd.DataFrame,
    vsf_threshold: float = 1.5,
    atr_period: int = 14,
    min_swing_bars: int = 3,
) -> List[ICTInstance]:
    if df is None or df.empty:
        return []

    df = add_indicators(df, atr_period)
    instances: List[ICTInstance] = []
    n = len(df)

    for i in range(min_swing_bars, n - 1):
        nxt = df.iloc[i + 1]
        window = df.iloc[max(0, i - min_swing_bars):i + 1]
        swing_high = window["high"].max()
        swing_low = window["low"].min()

        if df.iloc[i]["volume"] < 1:
            continue

        pool_above = swing_high
        pool_below = swing_low

        move_down = nxt["low"] < pool_below
        move_up = nxt["high"] > pool_above

        is_sweep = False
        sweep_type = None
        if move_down and nxt["close"] < df.iloc[i]["close"]:
            is_sweep = True
            sweep_type = "SSL"
        elif move_up and nxt["close"] > df.iloc[i]["close"]:
            is_sweep = True
            sweep_type = "BSL"

        if not is_sweep:
            continue

        vsf_at_pivot = df["vsf"].iloc[i + 1]
        atr_at_pivot = df["atr"].iloc[i + 1]
        if vsf_at_pivot < vsf_threshold:
            continue
        if atr_at_pivot <= 0:
            continue

        pool_high = float(pool_above)
        pool_low = float(pool_below)

        # Heuristic OB/FVG indicator (basic, post-sweep)
        ob_fvg_indicator = None
        if sweep_type == "BSL" and i + 2 < n:
            after2 = df.iloc[i + 2]
            if after2["close"] > nxt["close"] * 1.0005:
                ob_fvg_indicator = "Possible Bullish OB"
            elif after2["close"] < nxt["close"] * 0.999:
                ob_fvg_indicator = "Possible Bearish OB"
        if sweep_type == "SSL" and i + 2 < n:
            after2 = df.iloc[i + 2]
            if after2["close"] < nxt["close"] * 0.9995:
                ob_fvg_indicator = "Possible Bearish OB"
            elif after2["close"] > nxt["close"] * 1.0005:
                ob_fvg_indicator = "Possible Bullish OB"

        inst = ICTInstance(
            index=i + 1,
            timestamp=df.index[i + 1],
            symbol="DATA",
            timeframe="AUTO",
            sweep_type=sweep_type,
            market_context="UNKNOWN",
            sweep_level_low=float(swing_low),
            sweep_level_high=float(swing_high),
            entry_price=float(nxt["close"]),
            exit_price=None,
            atr_at_sweep=float(atr_at_pivot),
            vsf_at_sweep=float(vsf_at_pivot),
            vwap_at_sweep=float(df["vwap"].iloc[i + 1]),
            fvg_present=False,
            ob_type=None,
            ms_present=False,
            notes=f"Detected {sweep_type} sweep. Wicked beyond swing low/high and closed back inside.",
            ob_fvg_indicator=ob_fvg_indicator,
        )
        instances.append(inst)
    return instances

# ----------------------------
# ICT-style report generator (two tables)
# ----------------------------
def ict_report_template(instances: List[ICTInstance], df: pd.DataFrame, symbol: str, timeframe: str) -> str:
    lines: List[str] = []
    header = f"ICT Liquidity Sweep & OB Report for {symbol} | timeframe={timeframe} | bars={len(df)}"
    lines.append(header)
    lines.append("=" * len(header))

    if not instances:
        lines.append("No credible liquidity-sweep / OB candidates found in the provided window.")
        return "\n".join(lines)

    # -------- Table 1: Per-instance narrative
    for idx, inst in enumerate(instances):
        lines.append(f"\nInstance {idx + 1}:")
        lines.append(f"  Type of Liquidity Sweep: {inst.sweep_type}")
        lines.append(f"  Market Context: {inst.market_context} | Timeframe: {inst.timeframe}")
        lines.append(f"  Price Action Leading to Sweep: sweep_level_high={inst.sweep_level_high:.2f}, sweep_level_low={inst.sweep_level_low:.2f}; entry_price={inst.entry_price:.2f}")
        lines.append(f"  Trigger/Context: timestamp={inst.timestamp}")
        lines.append(f"  ICT Concept Application:")
        lines.append(f"    Liquidity Sweep: {inst.sweep_type} liquidity trapped at/above {inst.sweep_level_high:.2f} or below {inst.sweep_level_low:.2f}")
        lines.append(f"    Order Block (approx): bearish/bullish OB cues not conclusively defined in this lightweight run")
        lines.append(f"    FVG/Imbalance: {'present' if inst.fvg_present else 'absent'} (heuristic)")
        lines.append(f"    MSS: {'present' if inst.ms_present else 'absent'} (heuristic)")
        lines.append(f"  Potential ICT Trading Implications: watch for OB formation, FVG fills, and BOS retests around sweep level")
        lines.append(f"  Notes: {inst.notes}")

    # -------- Table 2: Compact instance snapshot
    lines.append("\nICT Instance Snapshot:")

    # Build a plain-text table (works with or without Rich)
    col_names = ["#", "Sweep", "Timestamp", "TF", "Sweep High", "Sweep Low", "Entry", "OB/FVG"]
    col_widths = [4, 6, 22, 4, 12, 12, 12, 30]
    header_line = "  ".join(name.ljust(w) for name, w in zip(col_names, col_widths))
    sep_line = "  ".join("-" * w for w in col_widths)

    table_lines = [header_line, sep_line]

    for idx, inst in enumerate(instances):
        row = [
            str(idx + 1).ljust(col_widths[0]),
            inst.sweep_type.ljust(col_widths[1]),
            str(inst.timestamp).ljust(col_widths[2]),
            inst.timeframe.ljust(col_widths[3]),
            f"{inst.sweep_level_high:.2f}".ljust(col_widths[4]),
            f"{inst.sweep_level_low:.2f}".ljust(col_widths[5]),
            f"{inst.entry_price:.2f}".ljust(col_widths[6]),
            (inst.ob_fvg_indicator if inst.ob_fvg_indicator else "N/A").ljust(col_widths[7]),
        ]
        table_lines.append("  ".join(row))

    lines.extend(table_lines)
    return "\n".join(lines)

# ----------------------------
# Helper: load CSV with auto-header handling
# ----------------------------
def _normalize_timestamp_header(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c.lower() for c in df.columns]
    if "timestamp" in cols:
        df = df.rename(columns={df.columns[cols.index("timestamp")]: "timestamp"})
    elif "datetime" in cols:
        df = df.rename(columns={df.columns[cols.index("datetime")]: "timestamp"})
    elif "date" in cols:
        df = df.rename(columns={df.columns[cols.index("date")]: "timestamp"})
    else:
        df = df.rename(columns={df.columns[0]: "timestamp"})
    return df

def load_csv_df(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df.rename(columns={c: c.strip() for c in df.columns})
    df.columns = [c.lower() for c in df.columns]
    df = _normalize_timestamp_header(df)
    needed = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [n for n in needed if n not in df.columns]
    if missing:
        raise ValueError(f"CSV must contain columns {needed}. Missing: {missing}. Found columns: {list(df.columns)}")
    df_sub = df[needed].copy()
    df_sub.set_index("timestamp", inplace=True)
    df_sub.index = pd.to_datetime(df_sub.index)
    df_sub = df_sub.sort_index()
    return df_sub

# ----------------------------
# CLI / entry point
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="ICT Liquidity Sweep & OB detector (CSV test harness)")
    parser.add_argument("--csv", required=True, help="Path to CSV file (headers: timestamp/datetime/date, open, high, low, close, volume)")
    parser.add_argument("--symbol", default="DATA", help="Symbol label")
    parser.add_argument("--tf", default="5", help="Timeframe in minutes")
    parser.add_argument("--vsf", type=float, default=1.5, help="Volume Spike Factor threshold (VSF)")
    parser.add_argument("--atr", type=int, default=14, help="ATR period")
    parser.add_argument("--out", default="ict_report.txt", help="Output report path")
    args = parser.parse_args()

    try:
        df = load_csv_df(args.csv)
    except Exception as e:
        if console:
            console.print(f"[red]Failed to load CSV: {e}[/red]")
        else:
            print(f"Failed to load CSV: {e}")
        return

    instances = detect_liquidity_sweeps(df, vsf_threshold=args.vsf, atr_period=args.atr)
    report = ict_report_template(instances, df, symbol=args.symbol, timeframe=f"{args.tf}m")

    if console:
        console.print(report)
    else:
        print(report)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report1)
    if console:
        console.print(f"\nICT report written to {args.out}")
    else:
        print(f"\nICT report written to {args.out}")

if __name__ == "__main__":
    main()
