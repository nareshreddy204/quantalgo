#!/usr/bin/env python3
"""
Unified Liquidity System: combines a Liquidity Grab trading strategy
with an ICT-style Liquidity Sweep & Order Block detector.

Modes:
  --mode signal   : run LiquidityGrabStrategy on CSV and print BUY/SELL signals
  --mode report   : run ICT Sweep Detector on CSV and print full ICT report
  --mode both     : run both and produce a combined output

Dependencies: pandas, numpy
Install: pip install pandas numpy
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import pandas as pd
import numpy as np

# ==========================================================
# SHARED INDICATOR LIBRARY
# ==========================================================
def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h = df["high"]
    l = df["low"]
    c = df["close"].shift(1)
    tr = pd.concat([
        h - l,
        (h - c).abs(),
        (l - c).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()

def compute_vsf(df: pd.DataFrame, window: int = 20) -> pd.Series:
    vol_ma = df["volume"].rolling(window=window, min_periods=1).mean()
    return df["volume"] / (vol_ma + 1e-9)

def compute_vwap_proxy(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ctv = (tp * df["volume"]).cumsum()
    vol = df["volume"].cumsum() + 1e-9
    return ctv / vol

def add_base_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    df = df.copy()
    if "volume" not in df.columns:
        df["volume"] = 1
    df["atr"] = compute_atr(df, atr_period)
    df["vsf"] = compute_vsf(df)
    df["vwap"] = compute_vwap_proxy(df)
    df["dev_from_vwap"] = (df["close"] - df["vwap"]).abs()
    return df

# ==========================================================
# DATA STRUCTURES
# ==========================================================
@dataclass
class LiquiditySignal:
    timestamp: pd.Timestamp
    symbol: str
    side: str  # "BUY" or "SELL"
    reason: str
    price: float
    vsf: float
    atr: float
    vwap_dev: float
    confidence: str  # "HIGH" / "MEDIUM" / "LOW"

@dataclass
class ICTInstance:
    index: int
    timestamp: pd.Timestamp
    symbol: str
    timeframe: str
    sweep_type: str  # "BSI" or "SSI"
    market_context: str
    sweep_level_low: float
    sweep_level_high: float
    entry_price: float
    exit_price: Optional[float]
    atr_at_sweep: float
    vsf_at_sweep: float
    vwap_at_sweep: float
    fvg_present: bool
    ob_type: Optional[str]
    ms_present: bool
    notes: str
    ob_fvg_indicator: Optional[str] = None

# ==========================================================
# LIQUIDITY GRAB STRATEGY (live signal generator)
# ==========================================================
class LiquidityGrabStrategy:
    """
    Strategy that identifies rapid price movements driven by
    large-volume influx/efflux, often triggered by news or chart patterns.

    Signals are generated when:
      - Volume Spike Factor (VSF) exceeds threshold
      - Price deviates from VWAP by more than ATR-multiple
      - Direction aligns with price momentum (up = BUY, down = SELL)
    """

    def __init__(
        self,
        vsf_threshold: float = 2.0,
        atr_period: int = 14,
        vwap_multiplier: float = 1.0,
        symbol: str = "NIFTY200",
    ):
        self.vsf_threshold = vsf_threshold
        self.atr_period = atr_period
        self.vwap_multiplier = vwap_multiplier
        self.symbol = symbol

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_base_indicators(df, self.atr_period)
        if "volume" not in df.columns:
            df["volume"] = 1
        df["vsf"] = compute_vsf(df)
        df["atr"] = compute_atr(df, self.atr_period)
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        df["vwap"] = (tp * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)
        df["dev_from_vwap"] = (df["close"] - df["vwap"]).abs()
        df["vol_delta"] = df["volume"].diff().fillna(0).abs()
        df["liq_seed"] = (
            (df["vsf"] > self.vsf_threshold) &
            (df["dev_from_vwap"] > df["atr"] * self.vwap_multiplier)
        ).astype(int)
        return df

    def generate_signals(self, df: pd.DataFrame) -> List[LiquiditySignal]:
        signals: List[LiquiditySignal] = []
        if df.empty:
            return signals

        df = self.prepare_data(df)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last

        if int(last.get("liq_seed", 0)) != 1:
            return signals

        if last["close"] > prev["close"]:
            side = "BUY"
            reason = "Liquidity grab bullish"
            confidence = "HIGH" if last["vsf"] > self.vsf_threshold * 1.5 else "MEDIUM"
        elif last["close"] < prev["close"]:
            side = "SELL"
            reason = "Liquidity grab bearish"
            confidence = "HIGH" if last["vsf"] > self.vsf_threshold * 1.5 else "MEDIUM"
        else:
            return signals

        sig = LiquiditySignal(
            timestamp=last.name if isinstance(last.name, pd.Timestamp) else pd.Timestamp(last.name),
            symbol=self.symbol,
            side=side,
            reason=reason,
            price=float(last["close"]),
            vsf=float(last["vsf"]),
            atr=float(last["atr"]),
            vwap_dev=float(last["dev_from_vwap"]),
            confidence=confidence,
        )
        signals.append(sig)
        return signals

# ==========================================================
# ICT LIQUIDITY SWEEP DETECTOR (analytical engine)
# ==========================================================
class LiquiditySweepDetector:
    """
    ICT-inspired detector for liquidity sweeps (BSI/SSI), order blocks,
    FVGs, and market structure shifts.

    Detection logic:
      - Find swing high/low over a lookback window
      - Detect when price wicks beyond the level but closes back inside
      - Confirm with VSF and ATR thresholds
      - Post-sweep look ahead for possible OB/FVG formation
    """

    def __init__(
        self,
        vsf_threshold: float = 1.5,
        atr_period: int = 14,
        min_swing_bars: int = 3,
        timeframe: str = "AUTO",
        symbol: str = "DATA",
    ):
        self.vsf_threshold = vsf_threshold
        self.atr_period = atr_period
        self.min_swing_bars = min_swing_bars
        self.timeframe = timeframe
        self.symbol = symbol

    def detect(self, df: pd.DataFrame) -> List[ICTInstance]:
        if df is None or df.empty:
            return []

        df = add_base_indicators(df, self.atr_period)
        instances: List[ICTInstance] = []
        n = len(df)

        for i in range(self.min_swing_bars, n - 1):
            nxt = df.iloc[i + 1]
            window = df.iloc[max(0, i - self.min_swing_bars):i + 1]
            swing_high = float(window["high"].max())
            swing_low = float(window["low"].min())

            if df.iloc[i]["volume"] < 1:
                continue

            pool_above = swing_high
            pool_below = swing_low

            move_down = nxt["low"] < pool_below
            move_up = nxt["high"] > pool_above

            is_sweep = False
            sweep_type = None
            direction = None
            if move_down and nxt["close"] < df.iloc[i]["close"]:
                is_sweep = True
                sweep_type = "SSI"
                direction = "down"
            elif move_up and nxt["close"] > df.iloc[i]["close"]:
                is_sweep = True
                sweep_type = "BSI"
                direction = "up"

            if not is_sweep:
                continue

            vsf_at_pivot = float(df["vsf"].iloc[i + 1])
            atr_at_pivot = float(df["atr"].iloc[i + 1])
            if vsf_at_pivot < self.vsf_threshold:
                continue
            if atr_at_pivot <= 0:
                continue

            # Post-sweep OB/FVG heuristic
            ob_fvg_indicator = None
            if sweep_type == "BSI" and i + 2 < n:
                after2 = df.iloc[i + 2]
                if after2["close"] > nxt["close"] * 1.0005:
                    ob_fvg_indicator = "Possible Bullish OB"
                elif after2["close"] < nxt["close"] * 0.999:
                    ob_fvg_indicator = "Possible Bearish OB"
            if sweep_type == "SSI" and i + 2 < n:
                after2 = df.iloc[i + 2]
                if after2["close"] < nxt["close"] * 0.9995:
                    ob_fvg_indicator = "Possible Bearish OB"
                elif after2["close"] > nxt["close"] * 1.0005:
                    ob_fvg_indicator = "Possible Bullish OB"

            inst = ICTInstance(
                index=i + 1,
                timestamp=df.index[i + 1],
                symbol=self.symbol,
                timeframe=self.timeframe,
                sweep_type=sweep_type,
                market_context="UNKNOWN",
                sweep_level_low=pool_low,
                sweep_level_high=pool_above,
                entry_price=float(nxt["close"]),
                exit_price=None,
                atr_at_sweep=atr_at_pivot,
                vsf_at_sweep=vsf_at_pivot,
                vwap_at_sweep=float(df["vwap"].iloc[i + 1]),
                fvg_present=False,
                ob_type=None,
                ms_present=False,
                notes=f"Detected {sweep_type} sweep. Wicked beyond swing low/high and closed back inside.",
                ob_fvg_indicator=ob_fvg_indicator,
            )
            instances.append(inst)

        return instances

# ==========================================================
# ICT REPORT GENERATOR (two-table output)
# ==========================================================
def ict_report_template(
    instances: List[ICTInstance],
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> str:
    lines: List[str] = []
    header = f"ICT Liquidity Sweep & OB Report | {symbol} | TF={timeframe} | bars={len(df)}"
    lines.append(header)
    lines.append("=" * len(header))

    if not instances:
        lines.append("No credible liquidity-sweep / OB candidates found in this window.")
        return "\n".join(lines)

    # Table 1: Per-instance narrative
    for idx, inst in enumerate(instances):
        lines.append(f"\nInstance {idx + 1} (index={inst.index}):")
        lines.append(f"  Type of Liquidity Sweep: {inst.sweep_type}")
        lines.append(f"  Market Context: {inst.market_context} | Timeframe: {inst.timeframe}")
        lines.append(
            f"  Price Action Leading to Sweep: sweep_level_high={inst.sweep_level_high:.2f}, "
            f"sweep_level_low={inst.sweep_level_low:.2f}; entry_price={inst.entry_price:.2f}"
        )
        lines.append(f"  Trigger/Context: timestamp={inst.timestamp}")
        lines.append(f"  ICT Concept Application:")
        lines.append(
            f"    Liquidity Sweep: {inst.sweep_type} liquidity trapped at/above "
            f"{inst.sweep_level_high:.2f} or below {inst.sweep_level_low:.2f}"
        )
        lines.append(
            f"    Order Block (approx): bearish/bullish OB cues not conclusively defined in this run"
        )
        lines.append(
            f"    FVG/Imbalance: {'present' if inst.fvg_present else 'absent'} (heuristic)"
        )
        lines.append(f"    MSS: {'present' if inst.ms_present else 'absent'} (heuristic)")
        lines.append(
            f"  Potential ICT Trading Implications: watch for OB formation, FVG fills, and BOS retests"
        )
        lines.append(f"  Notes: {inst.notes}")

    # Table 2: Compact instance snapshot
    lines.append("\nICT Instance Snapshot:")
    col_names = ["#", "Sweep", "Timestamp", "TF", "SweepHigh", "SweepLow", "Entry", "OB/FVG"]
    col_widths = [4, 5, 22, 5, 12, 12, 12, 28]
    header_line = "  ".join(n.ljust(w) for n, w in zip(col_names, col_widths))
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

# ==========================================================
# SIGNAL REPORT GENERATOR (strategy mode)
# ==========================================================
def signal_report(signals: List[LiquiditySignal], symbol: str) -> str:
    lines: List[str] = []
    header = f"Liquidity Grab Signals | {symbol}"
    lines.append(header)
    lines.append("=" * len(header))

    if not signals:
        lines.append("No liquidity grab signals in this window.")
        return "\n".join(lines)

    lines.append(f"\nTotal signals: {len(signals)}")
    for i, sig in enumerate(signals):
        lines.append(
            f"  [{i + 1}] {sig.timestamp} | {sig.side} | Price: {sig.price:.2f} | "
            f"VSF: {sig.vsf:.2f} | ATR: {sig.atr:.2f} | VWAP Dev: {sig.vwap_dev:.2f} | "
            f"Confidence: {sig.confidence} | Reason: {sig.reason}"
        )

    return "\n".join(lines)

# ==========================================================
# CSV LOADER HELPER
# ==========================================================
def _normalize_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c.lower() for c in df.columns]
    for alias in ("timestamp", "datetime", "date"):
        if alias in cols:
            df = df.rename(columns={df.columns[cols.index(alias)]: "timestamp"})
            return df
    df = df.rename(columns={df.columns[0]: "timestamp"})
    return df

def load_csv(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df.rename(columns={c: c.strip() for c in df.columns})
    df.columns = [c.lower() for c in df.columns]
    df = _normalize_timestamp(df)
    needed = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [n for n in needed if n not in df.columns]
    if missing:
        raise ValueError(f"CSV must contain columns {needed}. Missing: {missing}. Found: {list(df.columns)}")
    df_sub = df[needed].copy()
    df_sub.set_index("timestamp", inplace=True)
    df_sub.index = pd.to_datetime(df_sub.index)
    df_sub = df_sub.sort_index()
    return df_sub

# ==========================================================
# CLI / MAIN
# ==========================================================
def main():
    parser = argparse.ArgumentParser(
        description="Unified Liquidity System: Liquidity Grab Strategy + ICT Sweep Detector"
    )
    parser.add_argument("--csv", required=True, help="Path to CSV file (timestamp, open, high, low, close, volume)")
    parser.add_argument("--symbol", default="NIFTY200", help="Symbol label")
    parser.add_argument("--tf", default="5", help="Timeframe in minutes")
    parser.add_argument("--mode", default="both", choices=["signal", "report", "both"], help="Mode: signal, report, or both")
    parser.add_argument("--vsf", type=float, default=1.5, help="VSF threshold for both strategy and detector")
    parser.add_argument("--atr", type=int, default=14, help="ATR period")
    parser.add_argument("--vwap-mult", type=float, default=1.0, help="VWAP deviation multiplier (strategy only)")
    parser.add_argument("--out", default="liquidity_report.txt", help="Output report path")
    args = parser.parse_args()

    try:
        df = load_csv(args.csv)
    except Exception as e:
        print(f"Failed to load CSV: {e}")
        return

    output_parts: List[str] = []

    # Mode: signal only
    if args.mode in ("signal", "both"):
        strat = LiquidityGrabStrategy(
            vsf_threshold=args.vsf,
            atr_period=args.atr,
            vwap_multiplier=args.vwap_mult,
            symbol=args.symbol,
        )
        signals = strat.generate_signals(df)
        sig_report = signal_report(signals, args.symbol)
        output_parts.append(sig_report)

    # Mode: report only
    if args.mode in ("report", "both"):
        detector = LiquiditySweepDetector(
            vsf_threshold=args.vsf,
            atr_period=args.atr,
            min_swing_bars=3,
            timeframe=f"{args.tf}m",
            symbol=args.symbol,
        )
        instances = detector.detect(df)
        rep = ict_report_template(instances, df, args.symbol, f"{args.tf}m")
        output_parts.append(rep)

    full_report = "\n\n".join(output_parts)
    print(full_report)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(full_report)
    print(f"\nReport written to {args.out}")

if __name__ == "__main__":
    main()

# python liquidity_system.py --csv "C:\Users\bnare\Desktop\new way\data\options\NSE_NIFTY50-INDEX_1m.csv" --symbol NIFTY50 --tf 1 --mode signal --vsf 2.0 --atr 14
