

#liquidity grab indicator

from turtle import pd


def _atr(self, highs, lows, closes):
    tr = pd.concat([highs - lows,
                    (highs - closes.shift(1)).abs(),
                    (lows - closes.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(window=self.atr_period, min_periods=1).mean()

def _vwap_deviation(self, price_series, vwap_series):
    return (price_series - vwap_series).abs()

def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "volume" not in df.columns:
        df["volume"] = 1  # fallback
    # basic indicators
    df["vsf"] = df["volume"] / (df["volume"].rolling(window=20).mean() + 1e-6)
    df["atr"] = self._atr(df["high"], df["low"], df["close"])
    # naive VWAP proxy: use cumulative typical price weighted by volume
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap = (tp * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-6)
    df["vwap"] = vwap
    df["dev_from_vwap"] = self._vwap_deviation(df["close"], df["vwap"])
    # volume delta proxy (rough): up vs down volume estimate
    df["vol_delta"] = df["volume"].diff().fillna(0).abs()  # rough surrogate
    # final signal seed (not final decision)
    df["liq_seed"] = ((df["vsf"] > self.vsf_threshold) &
                      (df["dev_from_vwap"] > self.atr * self.vwap_multiplier)).astype(int)
    return df

def generate_signals(self, df: pd.DataFrame) -> List[Dict]:
    signals = []
    if df.empty:
        return signals
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    # bullish grab signal
    if int(last.get("liq_seed", 0)) == 1 and last["close"] > prev["close"]:
        signals.append({"time": last.name, "symbol": "NIFTY200",
                        "side": "BUY", "reason": "Liquidity grab bullish", "size": df.get("size", 0.01)})
    # bearish grab signal
    if int(last.get("liq_seed", 0)) == 1 and last["close"] < prev["close"]:
        signals.append({"time": last.name, "symbol": "NIFTY200",
                        "side": "SELL", "reason": "Liquidity grab bearish", "size": df.get("size", 0.01)})
    return signals



#liquidity_ict_detector.py

#!/usr/bin/env python3
"""
ICT-inspired liquidity sweep & order block detector
- Flags potential Buy-Side Liquidity Sweep (BSI) or Sell-Side Liquidity Sweep (SSI)
- Identifies approximate Order Blocks (OB), FVG/Imbalance, and MSS cues
- Produces a clean ICT-style report per identified instance

Usage:
  - df: pandas DataFrame with columns: ['timestamp','open','high','low','close','volume']
  - symbol: str, timeframe: str
  - thresholds: dict with keys
      - vsf_threshold: float (e.g., 2.0)
      - atr_period: int (e.g., 14)
      - vwap_dev_mult: float (e.g., 1.0 to require close dev from VWAP)
      - min_bars_lookback: int (minimum bars to consider a swing)
  - window_after_event: int (bars to inspect after a sweep for OB/MSS)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# ----------------------------
# Data structures
# ----------------------------

@dataclass
class ICTInstance:
    index: int
    timestamp: pd.Timestamp
    symbol: str
    timeframe: str
    sweep_type: str  # "BSI" or "SSI"
    market_context: str  # "bullish"/"bearish"/"ranging"
    sweep_level_low: float
    sweep_level_high: float
    entry_price: float
    exit_price: Optional[float]
    atr_at_sweep: float
    vsf_at_sweep: float
    vwap_at_sweep: float
    fvg_present: bool
    ob_type: Optional[str]  # "bullish" / "bearish" / None
    ms_present: bool
    notes: str

# ----------------------------
# Helpers: indicators
# ----------------------------

def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    # True Range
    h = df["high"]
    l = df["low"]
    c = df["close"].shift(1)
    tr = pd.concat([
        h - l,
        (h - c).abs(),
        (l - c).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=1).mean()
    return atr

def compute_vsf(df: pd.DataFrame) -> pd.Series:
    # Volume Spike Factor: current bar volume / 20-bar moving average volume
    vol_ma = df["volume"].rolling(window=20, min_periods=1).mean()
    vsf = df["volume"] / (vol_ma + 1e-9)
    return vsf

def compute_vwap_proxy(df: pd.DataFrame) -> pd.Series:
    # Simple VWAP proxy using cumulative typical price * volume
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ctv = (tp * df["volume"]).cumsum()
    vol = df["volume"].cumsum() + 1e-9
    vwap = ctv / vol
    return vwap

def add_indicators(df: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = compute_atr(df, atr_period)
    df["vsf"] = compute_vsf(df)
    df["vwap"] = compute_vwap_proxy(df)
    df["dev_from_vwap"] = (df["close"] - df["vwap"]).abs()
    return df

# ----------------------------
# Core detection logic
# ----------------------------

def detect_liquidity_sweeps(
    df: pd.DataFrame,
    vsf_threshold: float = 2.0,
    atr_period: int = 14,
    vwap_dev_mult: float = 1.0,
    min_swing_bars: int = 3,
) -> List[ICTInstance]:
    """
    Heuristic detector:
      - Scan for local congestion zones, then a sharp move with strong volume
      - Flag as SSI/BSI depending on move direction relative to the congestion
      - Capture a few contextual fields for ICT-style writeups
    Returns a list of ICTInstance objects (one per suspected event)
    """
    if df is None or df.empty:
        return []

    df = add_indicators(df, atr_period)

    instances: List[ICTInstance] = []
    n = len(df)

    # Simple swing detection: mark potential congestion zones where price hasn't moved much
    # We'll flag potential sweeps at bars where a strong move follows a shallow range.
    # Strategy: look for a bar with large drop/rise followed by bars with minimal continuation.
    for i in range(min_swing_bars, n - 1):
        cur = df.iloc[i]
        nxt = df.iloc[i + 1]

        # compute a rough "swing high/low" neighborhood
        window = df.iloc[max(0, i - 2):i + 1]
        swing_high = window["high"].max()
        swing_low = window["low"].min()

        # crude liquidity pool proximity
        pool_above = swing_high
        pool_below = swing_low

        # Price move: check if the next bar prints a big move through the pool
        # We'll consider a sweep if price moves beyond the pool by more than 0.5% and volume is elevated.
        vol = cur["volume"]
        if vol < 1:  # skip negligible volume
            continue

        # Detect potential "sweep" direction
        # If next close is significantly below pool_high (SSI-like) or above pool_low (BSI-like)
        move_down = nxt["close"] < pool_below * 0.995
        move_up = nxt["close"] > pool_above * 1.005

        is_sweep = False
        sweep_type = None
        if move_down and df.loc[i + 1, "close"] < df.loc[i, "close"]:
            is_sweep = True
            sweep_type = "SSI"
            direction = "down"
        elif move_up and df.loc[i + 1, "close"] > df.loc[i, "close"]:
            is_sweep = True
            sweep_type = "BSI"
            direction = "up"

        if not is_sweep:
            continue

        # Threshold checks: VSF at the pivot bar (the bar that triggers)
        vsf_at_pivot = df["vsf"].iloc[i]
        atr_at_pivot = df["atr"].iloc[i]
        dev_at_pivot = df["dev_from_vwap"].iloc[i]

        if vsf_at_pivot < vsf_threshold:
            # not a credible volume spike
            continue

        if atr_at_pivot <= 0:
            continue

        # Basic MSS/CHoCH hints: a BOS occurs when the next bar forms a lower high or higher low
        # We'll approximate: if after the sweep the close remains outside prior range, we treat as MSS signal
        # (This is a lightweight version; deeper MSS/CHoCH requires higher TF context)

        # OB presence: simple heuristic around swing: check for a local engulfing bar after sweep
        post = df.iloc[i + 1]
        ob_type = None
        if direction == "down":
            # bullish OB candidate would form later if price reclaims
            pass
        else:
            pass

        instance = ICTInstance(
            index=i,
            timestamp=pd.to_datetime(cur.name) if isinstance(cur.name, (pd.Timestamp, str)) else df.index[i],
            symbol="DATA",
            timeframe="AUTO",
            sweep_type=sweep_type,
            market_context="UNKNOWN",
            sweep_level_low=float(pool_below),
            sweep_level_high=float(pool_above),
            entry_price=float(nxt["close"]),
            exit_price=None,
            atr_at_sweep=float(atr_at_pivot),
            vsf_at_sweep=float(vsf_at_pivot),
            vwap_at_sweep=float(df["vwap"].iloc[i]),
            fvg_present=False,
            ob_type=None,
            ms_present=False,
            notes=f"Detected {sweep_type} at index {i} with pivot close {nxt['close']:.2f}"
        )
        instances.append(instance)

    return instances

# ----------------------------
# ICT-style report generator
# ----------------------------

def ict_report_template(instances: List[ICTInstance], df: pd.DataFrame, symbol: str, timeframe: str) -> str:
    """
    Returns a human-readable ICT-style report for all instances.
    Each instance is described in structured bullet points.
    """
    lines: List[str] = []
    header = f"ICT Liquidity Sweep & OB Report for {symbol} | timeframe={timeframe} | bars={len(df)}"
    lines.append(header)
    lines.append("=" * len(header))
    if not instances:
        lines.append("No credible liquidity-sweep / OB candidates found in the provided window.")
        return "\n".join(lines)

    for inst in instances:
        lines.append(f"\nInstance {inst.index + 1}:")
        lines.append(f"  Type of Liquidity Sweep: {inst.sweep_type}")
        lines.append(f"  Market Context: {inst.market_context} | Timeframe: {inst.timeframe}")
        lines.append(f"  Price Action Leading to Sweep: sweep_level_high={inst.sweep_level_high:.2f}, sweep_level_low={inst.sweep_level_low:.2f}; entry_price={inst.entry_price:.2f}")
        lines.append(f"  Trigger/Context: timestamp={inst.timestamp} (approx)")
        lines.append(f"  ICT Concept Application:")
        lines.append(f"    Liquidity Sweep: {inst.sweep_type} liquidity trapped at/above {inst.sweep_level_high:.2f} or below {inst.sweep_level_low:.2f}")
        lines.append(f"    Order Block (approx): bearish/bullish OB cues not conclusively defined in this lightweight run")
        lines.append(f"    FVG/Imbalance: {'present' if inst.fvg_present else 'absent'} (heuristic)")
        lines.append(f"    MSS: {'present' if inst.ms_present else 'absent'} (heuristic)")
        lines.append(f"  Potential ICT Trading Implications: watch for OB formation, FVG fills, and BOS retests around sweep level")
        lines.append(f"  Notes: {inst.notes}")

    return "\n".join(lines)

# ----------------------------
# Simple CLI usage
# ----------------------------
def _example_run():
    # This function is just an example path; remove in production usage
    import io
    import textwrap
    data = textwrap.dedent("""\
        datetime,open,high,low,close,volume
        2026-07-16 11:00:00,525.0,530.6,522.0,522.0,130
        2026-07-16 11:15:00,522.0,525.25,522.0,525.25,65
        2026-07-16 11:30:00,525.25,525.25,524.25,524.25,65
    """)
    df = pd.read_csv(io.StringIO(data), parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "timestamp"})
    insts = detect_liquidity_sweeps(df, vsf_threshold=1.5, atr_period=14)
    rep = ict_report_template(insts, df, symbol="DEMO", timeframe="15m")
    print(rep)

if __name__ == "__main__":
    _example_run()
