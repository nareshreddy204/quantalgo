#!/usr/bin/env python3
"""
Quantitative Trading Framework — Unified Refactor
═══════════════════════════════════════════════════════════════════
Consolidates Wave 1 / 2 / 6 / 7 into a single, coherent codebase.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import product
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
    PANDAS_TA_INSTALLED = True
except ImportError:
    PANDAS_TA_INSTALLED = False

try:
    import yaml
    YAML_INSTALLED = True
except ImportError:
    YAML_INSTALLED = False

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

DEFAULT_YAML: Dict[str, Any] = {
    "symbol": "NSE:NIFTY50-INDEX",
    "initial_capital": 2_000_000.0,
    "risk": {"max_capital_per_trade": 0.01},
    "backtest": {"slippage_points": 0.05, "commission_pct": 0.05},
    "fyers": {"app_id": "", "access_token": "", "use_real_data": False},
    "symbols": [],
}

def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    if not YAML_INSTALLED or not os.path.exists(path):
        return DEFAULT_YAML.copy()
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            cfg.setdefault("fyers", {})["access_token"] = f.read().strip()
    return cfg

@dataclass
class Config:
    symbol: str = "NSE:NIFTY50-INDEX"
    initial_capital: float = 2_000_000.0
    risk_per_trade_pct: float = 0.01
    commission_pct: float = 0.05
    slippage_pct: float = 0.05
    tick_size: float = 0.05
    lot_size: int = 75

    timeframes: List[str] = field(default_factory=lambda: ["5m"])
    data_years: int = 5

    atr_period: int = 14
    sl_atr_mult: float = 2.0  # Widened from 1.5 to avoid instant SL_GAP
    tp_atr_mult: float = 4.0  # Widened to maintain 1:2 Risk/Reward

    use_break_even: bool = True
    break_even_trigger_atr: float = 1.0
    use_trailing_stop: bool = True
    trail_atr_mult: float = 2.0
    max_holding_bars: int = 100

    market_open: str = "09:15"
    market_close: str = "15:30"
    avoid_open_minutes: int = 15
    avoid_close_minutes: int = 15

    train_split_pct: float = 0.7

    fyers_token: str = ""
    fyers_app_id: str = ""
    use_real_data: bool = False

    sizing_method: str = "fixed"

    @classmethod
    def from_yaml(cls, raw: Dict[str, Any]) -> "Config":
        fyers = raw.get("fyers", {}) or {}
        risk = raw.get("risk", {}) or {}
        bt = raw.get("backtest", {}) or {}
        return cls(
            symbol=raw.get("symbol", "NSE:NIFTY50-INDEX"),
            initial_capital=float(raw.get("initial_capital", 2_000_000.0)),
            risk_per_trade_pct=float(risk.get("max_capital_per_trade", 0.01)),
            commission_pct=float(bt.get("commission_pct", 0.05)),
            slippage_pct=float(bt.get("slippage_points", 0.05)),
            fyers_token=fyers.get("access_token", ""),
            fyers_app_id=fyers.get("app_id", ""),
            use_real_data=bool(fyers.get("use_real_data", False)),
        )

CFG = Config.from_yaml(load_config())

# ═══════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════

_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
_FYERS_RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240"}

class DataManager:
    CACHE_DIR = "./data_cache"

    def __init__(self, symbol: str = CFG.symbol, years: int = CFG.data_years, cfg: Config = CFG):
        self.symbol = symbol
        self.years = years
        self.cfg = cfg
        self._cache: Dict[str, pd.DataFrame] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def load(self, tf: str) -> pd.DataFrame:
        if tf in self._cache:
            return self._cache[tf]

        clean = self.symbol.replace(":", "-").replace("/", "-")
        path = f"{self.CACHE_DIR}/{clean}_{tf}.parquet"

        if os.path.exists(path):
            print(f"  [Data] Loading cached Parquet for {self.symbol} {tf}...")
            df = pd.read_parquet(path)
        else:
            if self.cfg.use_real_data and self.cfg.fyers_token:
                try:
                    print(f"  [Data] Fetching {self.symbol} {tf} from Fyers...")
                    df = self._fetch_fyers(tf)
                    df.to_parquet(path)
                except Exception as e:
                    print(f"  [Data] Fyers failed ({e}). Falling back to synthetic.")
                    df = self._generate_synthetic(tf)
                    df.to_parquet(path)
            else:
                print(f"  [Data] Generating synthetic for {self.symbol} {tf}...")
                df = self._generate_synthetic(tf)
                df.to_parquet(path)

        df = self._normalize_ist(df)
        self._cache[tf] = df
        return df

    @staticmethod
    def _normalize_ist(df: pd.DataFrame) -> pd.DataFrame:
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
        elif df.index.hour.min() < 5:
            print("  [Data] UTC detected → converting to IST.")
            df.index = df.index + timedelta(hours=5, minutes=30)
        return df

    def _fetch_fyers(self, tf: str) -> pd.DataFrame:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError:
            from fyers_api import fyersModel

        import inspect
        resolution = _FYERS_RESOLUTION[tf]
        sig = inspect.signature(fyersModel.FyersModel).parameters
        kwargs: Dict[str, Any] = {"is_async": False, "log_path": os.getcwd(), "client_id": self.cfg.fyers_app_id}
        if "token" in sig:
            kwargs["token"] = self.cfg.fyers_token

        fyers = fyersModel.FyersModel(**kwargs)
        if "token" not in sig:
            if hasattr(fyers, "set_token"):
                fyers.set_token(self.cfg.fyers_token)
            elif hasattr(fyers, "set_access_token"):
                fyers.set_access_token(self.cfg.fyers_token)
            else:
                fyers.token = self.cfg.fyers_token
                fyers.access_token = self.cfg.fyers_token

        end = datetime.now()
        start = end - timedelta(days=self.years * 365)
        candles: List[List[float]] = []
        cur = start
        print(f"  [Fyers] Downloading {self.years}y in 59-day chunks...")
        while cur < end:
            nxt = min(cur + timedelta(days=59), end)
            payload = {
                "symbol": self.symbol, "resolution": resolution, "date_format": "1",
                "range_from": cur.strftime("%Y-%m-%d"), "range_to": nxt.strftime("%Y-%m-%d"),
                "cont_flag": "1",
            }
            try:
                resp = fyers.history(payload)
                if resp and resp.get("candles"):
                    candles.extend(resp["candles"])
                    print(f"    {cur.date()} → {nxt.date()}: {len(resp['candles'])} bars")
            except Exception as e:
                print(f"    Error {cur.date()}: {e}")
            cur = nxt + timedelta(days=1)
            time.sleep(1)

        if not candles:
            raise ValueError("Fyers returned no candles.")
        df = pd.DataFrame(candles, columns=["epoch", "open", "high", "low", "close", "volume"])
        df["datetime"] = (pd.to_datetime(df["epoch"], unit="s")
                          .dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        df = df.set_index("datetime").drop(columns=["epoch"])
        df = df[~df.index.duplicated(keep="first")]
        return df

    def _generate_synthetic(self, tf: str) -> pd.DataFrame:
        bar_min = _TF_MINUTES.get(tf, 5)
        sessions = pd.bdate_range(end=datetime.now(), periods=self.years * 252)
        idx_list = [t for d in sessions for t in pd.date_range(d + pd.Timedelta("9:15:00"), d + pd.Timedelta("15:30:00"), freq=f"{bar_min}min")]
        idx = pd.DatetimeIndex(idx_list)
        bars_per_year = 252 * 75
        bar_vol = 0.15 / np.sqrt(bars_per_year)
        bar_drift = 0.10 / bars_per_year
        rng = np.random.default_rng(42)
        rets = rng.normal(bar_drift, bar_vol, len(idx))
        seasonality = np.ones(len(idx))
        times = idx.time
        seasonality[times < datetime.strptime("10:00", "%H:%M").time()] = 1.5
        seasonality[times > datetime.strptime("14:30", "%H:%M").time()] = 1.5
        rets = rets * seasonality
        prices = 18000 * np.exp(np.cumsum(rets))
        intrabar = bar_vol * 0.5
        df = pd.DataFrame(index=idx, columns=["open", "high", "low", "close", "volume"])
        df["close"] = prices
        df["open"] = df["close"].shift(1).fillna(18000)
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + np.abs(rng.normal(0, intrabar, len(idx))))
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - np.abs(rng.normal(0, intrabar, len(idx))))
        df["volume"] = rng.integers(10_000, 500_000, len(idx))
        return df.dropna()

# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c_prev = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

# ═══════════════════════════════════════════════════════════════
# CORE TYPES
# ═══════════════════════════════════════════════════════════════

@dataclass
class StrategyResult:
    signals: pd.Series
    meta: Dict[str, Any] = field(default_factory=dict)

class Strategy:
    name: str = "base"
    category: str = "unknown"
    params: Dict[str, Any] = field(default_factory=dict)
    def generate(self, df: pd.DataFrame) -> StrategyResult:
        raise NotImplementedError

STRATEGY_REGISTRY: Dict[str, type] = {}
def register(cls: type) -> type:
    STRATEGY_REGISTRY[cls.name] = cls
    return cls

@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    side: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    bars_held: int

# ═══════════════════════════════════════════════════════════════
# POSITION SIZING
# ═══════════════════════════════════════════════════════════════

class PositionSizer:
    @staticmethod
    def fixed_fractional(equity: float, risk_pct: float, stop_distance: float, price: float, lot_size: int) -> int:
        if stop_distance <= 0 or price <= 0: return 0
        risk_qty = int((equity * risk_pct) / stop_distance)
        cap_qty = int(equity * 0.95 / price)
        qty = max(0, min(risk_qty, cap_qty))
        return (qty // lot_size) * lot_size

# ═══════════════════════════════════════════════════════════════
# SMC STATE MACHINE STRATEGY
# ═══════════════════════════════════════════════════════════════

@register
class SMCStateMachineStrategy(Strategy):
    name = "smc_state_machine"
    category = "smc"
    params = {
        "ema_period": 200, "swing_length": 5, "sweep_lookback": 20,
        "fvg_max_lookback": 30, "session": "india_full",
        "mss_disp_atr": 1.0, "fvg_disp_atr": 1.0,
    }
    SESSIONS = {
        "india_open":  ("09:30", "11:30"),
        "india_power": ("13:00", "15:00"),
        "india_full":  ("09:20", "15:10"),
    }

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        p = self.params
        signals = pd.Series(0, index=df.index, dtype=int)
        ema = df["close"].ewm(span=p["ema_period"], adjust=False).mean()
        atr = calc_atr(df, 14)

        L = p["swing_length"]
        roll_high = df["high"].rolling(2 * L + 1, center=True).max()
        roll_low = df["low"].rolling(2 * L + 1, center=True).min()
        is_sh = (df["high"] == roll_high).values
        is_sl = (df["low"] == roll_low).values

        s_t = pd.Timestamp(self.SESSIONS[p["session"]][0]).time()
        e_t = pd.Timestamp(self.SESSIONS[p["session"]][1]).time()

        highs, lows = df["high"].values, df["low"].values
        opens, closes = df["open"].values, df["close"].values
        atrs = atr.fillna(0).values
        times = df.index.time

        trend_state = 0
        bull_active = False; bull_sweep_i = -1; bull_mss_i = -1
        bear_active = False; bear_sweep_i = -1; bear_mss_i = -1
        active_bull_fvgs: List[Tuple[float, float, int]] = []
        active_bear_fvgs: List[Tuple[float, float, int]] = []
        last_sh = last_sl = np.nan

        n_sweep = n_mss = n_fvg = 0

        for i in range(len(df)):
            if i < 2: continue

            if closes[i] > ema.iloc[i]: trend_state = 1
            elif closes[i] < ema.iloc[i]: trend_state = -1

            lb = p["sweep_lookback"]
            if i >= lb:
                ph = np.max(highs[i - lb:i])
                pl = np.min(lows[i - lb:i])
                if lows[i] < pl and closes[i] > pl and trend_state == 1 and not bull_active:
                    bull_active = True; bull_sweep_i = i; bull_mss_i = -1; n_sweep += 1
                elif highs[i] > ph and closes[i] < ph and trend_state == -1 and not bear_active:
                    bear_active = True; bear_sweep_i = i; bear_mss_i = -1; n_sweep += 1

            if bull_active and i - bull_sweep_i > 50: bull_active = False
            if bear_active and i - bear_sweep_i > 50: bear_active = False

            body = abs(closes[i] - opens[i])
            if bull_active and bull_mss_i == -1 and not np.isnan(last_sh) \
               and closes[i] > last_sh and body > atrs[i] * p["mss_disp_atr"]:
                bull_mss_i = i; n_mss += 1
            if bear_active and bear_mss_i == -1 and not np.isnan(last_sl) \
               and closes[i] < last_sl and body > atrs[i] * p["mss_disp_atr"]:
                bear_mss_i = i; n_mss += 1

            if bull_active and bull_mss_i != -1 and i > bull_mss_i:
                if lows[i] > highs[i - 2] and body > atrs[i] * p["fvg_disp_atr"]:
                    active_bull_fvgs.append((lows[i], highs[i - 2], i))
                    bull_active = False; n_fvg += 1
            if bear_active and bear_mss_i != -1 and i > bear_mss_i:
                if highs[i] < lows[i - 2] and body > atrs[i] * p["fvg_disp_atr"]:
                    active_bear_fvgs.append((lows[i - 2], highs[i], i))
                    bear_active = False; n_fvg += 1

            if s_t <= times[i] <= e_t:
                for arr, side in [(active_bull_fvgs, 1), (active_bear_fvgs, -1)]:
                    mitigated = []
                    for z_top, z_bot, z_idx in arr:
                        if i - z_idx > p["fvg_max_lookback"]:
                            mitigated.append((z_top, z_bot, z_idx))
                        elif i > z_idx and side == 1 and lows[i] <= z_top and closes[i] > opens[i]:
                            signals.iloc[i] = 1
                            mitigated.append((z_top, z_bot, z_idx))
                        elif i > z_idx and side == -1 and highs[i] >= z_bot and closes[i] < opens[i]:
                            signals.iloc[i] = -1
                            mitigated.append((z_top, z_bot, z_idx))
                    for m in mitigated: arr.remove(m)

            if is_sh[i]: last_sh = highs[i]
            if is_sl[i]: last_sl = lows[i]

        print(f"  [SMC-SM] sweeps={n_sweep}  mss={n_mss}  fvgs={n_fvg}  signals(+/-)={int((signals==1).sum())}/{int((signals==-1).sum())}")
        return StrategyResult(signals, {"ema": ema})

# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

class EnhancedBacktester:
    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg
        self.open_time = pd.Timestamp(cfg.market_open).time()
        self.close_time = pd.Timestamp(cfg.market_close).time()
        self.open_avoid_t = (pd.Timestamp(cfg.market_open) + timedelta(minutes=cfg.avoid_open_minutes)).time()
        self.close_avoid_t = (pd.Timestamp(cfg.market_close) - timedelta(minutes=cfg.avoid_close_minutes)).time()

    def _slip(self, price: float, side: int, is_entry: bool) -> float:
        slip = self.cfg.slippage_pct / 100
        return price * (1 + slip * side) if is_entry else price * (1 - slip * side)

    def _tick(self, price: float) -> float:
        return round(price / self.cfg.tick_size) * self.cfg.tick_size

    def _good_time(self, t) -> bool:
        if t < self.open_time or t >= self.close_time: return False
        if t < self.open_avoid_t: return False
        if t >= self.close_avoid_t: return False
        return True

    def run(self, df: pd.DataFrame, strategy: Strategy, is_oos: bool = False) -> Dict[str, Any]:
        cfg = self.cfg
        result = strategy.generate(df)

        opens, highs, lows, closes = df["open"].values, df["high"].values, df["low"].values, df["close"].values
        times = df.index.time
        raw_signals = result.signals.values
        atr_arr = calc_atr(df, cfg.atr_period).fillna(0).values

        trades: List[Trade] = []
        exit_reasons: Dict[str, int] = {}
        equity = cfg.initial_capital
        equity_curve = np.zeros(len(df))

        position = 0
        entry_price = sl_price = tp_price = 0.0
        entry_bar_idx = -1
        qty = 0
        max_favorable = 0.0

        for i in range(1, len(df)):
            t = times[i]
            price_open = self._tick(opens[i])
            price_high = self._tick(highs[i])
            price_low = self._tick(lows[i])
            price_close = self._tick(closes[i])
            atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
            signal = raw_signals[i - 1]
            
            if not self._good_time(t):
                signal = 0
                is_eod = True
            else:
                is_eod = False

            if position != 0:
                exit_now, exit_reason, exit_price = False, "", price_close

                if position == 1 and price_open <= sl_price:
                    exit_price, exit_now, exit_reason = price_open, True, "SL_GAP"
                elif position == -1 and price_open >= sl_price:
                    exit_price, exit_now, exit_reason = price_open, True, "SL_GAP"
                elif position == 1 and price_open >= tp_price:
                    exit_price, exit_now, exit_reason = price_open, True, "TP_GAP"
                elif position == -1 and price_open <= tp_price:
                    exit_price, exit_now, exit_reason = price_open, True, "TP_GAP"

                if not exit_now:
                    if position == 1:
                        if price_low <= sl_price: exit_price, exit_now, exit_reason = sl_price, True, "SL"
                        elif price_high >= tp_price: exit_price, exit_now, exit_reason = tp_price, True, "TP"
                    else:
                        if price_high >= sl_price: exit_price, exit_now, exit_reason = sl_price, True, "SL"
                        elif price_low <= tp_price: exit_price, exit_now, exit_reason = tp_price, True, "TP"

                if not exit_now and cfg.use_trailing_stop and atr_val > 0:
                    fav = (price_high - entry_price) if position == 1 else (entry_price - price_low)
                    if fav > max_favorable: max_favorable = fav
                    if cfg.use_break_even and max_favorable >= atr_val * cfg.break_even_trigger_atr:
                        new_sl = entry_price
                        if (position == 1 and new_sl > sl_price) or (position == -1 and new_sl < sl_price):
                            sl_price = self._tick(new_sl)
                    trail = (price_high - atr_val * cfg.trail_atr_mult) if position == 1 \
                            else (price_low + atr_val * cfg.trail_atr_mult)
                    if (position == 1 and trail > sl_price) or (position == -1 and trail < sl_price):
                        sl_price = self._tick(trail)

                bars_held = i - entry_bar_idx
                if not exit_now and bars_held >= cfg.max_holding_bars:
                    exit_price, exit_now, exit_reason = price_close, True, "TIME"

                if not exit_now and signal != 0 and signal != position:
                    exit_price, exit_now, exit_reason = price_open, True, "REV"

                if not exit_now and is_eod:
                    exit_price, exit_now, exit_reason = price_close, True, "EOD"

                if exit_now:
                    exit_slipped = self._tick(self._slip(exit_price, position, is_entry=False))
                    gross = (exit_slipped - entry_price) * qty * position
                    commission = (entry_price * qty + exit_slipped * qty) * (cfg.commission_pct / 100)
                    pnl = gross - commission
                    equity += pnl
                    exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                    trades.append(Trade(
                        entry_time=df.index[entry_bar_idx], exit_time=df.index[i],
                        side="LONG" if position == 1 else "SHORT",
                        entry_price=entry_price, exit_price=exit_slipped,
                        qty=qty, pnl=round(pnl, 2),
                        pnl_pct=round(pnl / (entry_price * qty) * 100, 2) if qty else 0.0,
                        exit_reason=exit_reason, bars_held=bars_held,
                    ))
                    position = 0
                    signal = 0

            if position == 0 and signal != 0 and atr_val > 0 and not is_eod:
                entry_price = self._tick(self._slip(price_open, signal, is_entry=True))
                stop_distance = atr_val * cfg.sl_atr_mult

                qty = PositionSizer.fixed_fractional(equity, cfg.risk_per_trade_pct, stop_distance, entry_price, cfg.lot_size)

                if qty > 0:
                    sl_price = self._tick(entry_price - stop_distance * signal)
                    tp_price = self._tick(entry_price + atr_val * cfg.tp_atr_mult * signal)
                    position = signal
                    entry_bar_idx = i
                    max_favorable = 0.0

            if position != 0:
                equity_curve[i] = equity + (price_close - entry_price) * qty * position
            else:
                equity_curve[i] = equity

        print(f"  [Backtest] trades={len(trades)}  exits={exit_reasons}")
        eq_series = pd.Series(equity_curve, index=df.index)
        metrics = self._compute_metrics(trades, eq_series, df.index[0], df.index[-1])
        return {"trades": trades, "equity_curve": eq_series, "metrics": metrics, "is_oos": is_oos}

    def _compute_metrics(self, trades: List[Trade], equity: pd.Series, start_dt, end_dt) -> Dict[str, Any]:
        empty = {
            "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0,
            "avg_r": 0.0, "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0, "sqn": 0.0,
            "omega": 0.0, "max_dd_pct": 0.0, "recovery_factor": 0.0, "ulcer_index": 0.0,
            "cagr": 0.0, "exposure_pct": 0.0, "avg_holding_bars": 0.0,
            "max_consec_wins": 0, "max_consec_losses": 0,
            "monthly_returns": {}, "yearly_returns": {},
        }
        if not trades: return empty

        pnls = np.array([t.pnl for t in trades], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        n = len(trades)
        risk_per_trade = self.cfg.initial_capital * self.cfg.risk_per_trade_pct
        r_multiples = pnls / risk_per_trade if risk_per_trade > 0 else np.zeros(n)

        returns = equity.pct_change().dropna()
        peak = equity.cummax()
        dd = (equity - peak) / peak * 100
        max_dd = float(dd.min())
        days = (end_dt - start_dt).days
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (365 / days) - 1) if days > 0 and equity.iloc[0] > 0 else 0.0
        ulcer = float(np.sqrt((dd ** 2).mean())) if len(dd) > 0 else 0.0

        gains = returns[returns > 0].sum()
        losses_abs = -returns[returns < 0].sum()
        omega = gains / losses_abs if losses_abs > 0 else 0.0
        recovery = pnls.sum() / abs(max_dd) if max_dd < 0 else 0.0

        sw = sl_ = cw = cl_ = 0
        for p in pnls:
            if p > 0: cw += 1; cl_ = 0; sw = max(sw, cw)
            else: cl_ += 1; cw = 0; sl_ = max(sl_, cl_)

        total_bars_held = sum(t.bars_held for t in trades)
        total_bars = len(equity)
        exposure = total_bars_held / total_bars * 100 if total_bars > 0 else 0.0

        monthly_ret = equity.resample("ME").last().dropna().pct_change().dropna()
        yearly_ret = equity.resample("YE").last().dropna().pct_change().dropna()

        # Fixed zero-division edge cases (inf/nan) when all trades are losses
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 75)) if returns.std() > 1e-8 else 0.0
        d_std = returns[returns < 0].std()
        sortino = (returns.mean() / d_std * np.sqrt(252 * 75)) if d_std and d_std > 1e-8 else 0.0
        calmar = cagr / abs(max_dd / 100) if max_dd < 0 else 0.0
        sqn = np.sqrt(n) * (pnls.mean() / pnls.std()) if pnls.std() > 1e-8 else 0.0
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 0.0

        return {
            "total_trades": n, "win_rate": round(len(wins) / n * 100, 2),
            "total_pnl": round(pnls.sum(), 2), "avg_pnl": round(pnls.mean(), 2),
            "avg_win": round(wins.mean(), 2) if len(wins) else 0.0,
            "avg_loss": round(losses.mean(), 2) if len(losses) else 0.0,
            "profit_factor": round(pf, 2), "expectancy_r": round(r_multiples.mean(), 3),
            "avg_r": round(r_multiples.mean(), 3), "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2), "calmar": round(calmar, 2),
            "sqn": round(sqn, 2), "omega": round(omega, 3),
            "max_dd_pct": round(max_dd, 2), "recovery_factor": round(recovery, 2),
            "ulcer_index": round(ulcer, 2), "cagr": round(cagr * 100, 2),
            "exposure_pct": round(exposure, 2), "avg_holding_bars": round(total_bars_held / n, 1),
            "max_consec_wins": sw, "max_consec_losses": sl_,
            "monthly_returns": monthly_ret.round(4).to_dict(),
            "yearly_returns": yearly_ret.round(4).to_dict(),
        }

# ═══════════════════════════════════════════════════════════════
# RESEARCH MODULES
# ═══════════════════════════════════════════════════════════════

class ResearchDB:
    SCHEMA = """CREATE TABLE IF NOT EXISTS experiments (
        id INTEGER PRIMARY KEY, timestamp TEXT, strategy TEXT, tf TEXT,
        params TEXT, is_oos INTEGER, pf REAL, sharpe REAL, sortino REAL,
        max_dd REAL, cagr REAL, calmar REAL, sqn REAL
    )"""
    def __init__(self, path: str = "research.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(self.SCHEMA)
        self.conn.commit()

    def save_result(self, strategy: str, tf: str, params: Dict, is_oos: bool, metrics: Dict):
        self.conn.execute(
            """INSERT INTO experiments
               (timestamp, strategy, tf, params, is_oos, pf, sharpe, sortino, max_dd, cagr, calmar, sqn)
               VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy, tf, json.dumps(params), int(is_oos),
             metrics.get("profit_factor", 0), metrics.get("sharpe", 0),
             metrics.get("sortino", 0), metrics.get("max_dd_pct", 0),
             metrics.get("cagr", 0), metrics.get("calmar", 0), metrics.get("sqn", 0)),
        )
        self.conn.commit()

class WalkForwardOptimizer:
    def __init__(self, cfg: Config = CFG, db: Optional[ResearchDB] = None,
                 train_years: float = 2.0, test_years: float = 1.0, step_years: float = 1.0):
        self.cfg = cfg
        self.bt = EnhancedBacktester(cfg)
        self.db = db
        self.train_years, self.test_years, self.step_years = train_years, test_years, step_years

    def _folds(self, df: pd.DataFrame):
        start, end = df.index[0], df.index[-1]
        train_td = timedelta(days=self.train_years * 365)
        test_td = timedelta(days=self.test_years * 365)
        step_td = timedelta(days=self.step_years * 365)
        cur = start + train_td
        while cur + test_td <= end:
            tr = df[(df.index >= cur - train_td) & (df.index < cur)]
            te = df[(df.index >= cur) & (df.index < cur + test_td)]
            yield tr, te, f"{(cur - train_td).date()}→{cur.date()} | TEST {cur.date()}→{(cur + test_td).date()}"
            cur += step_td

    def search(self, df: pd.DataFrame, strategy_factory: Callable[[Dict], Strategy], param_grid: Dict[str, list]) -> List[Dict]:
        keys = list(param_grid.keys())
        all_results: List[Dict] = []
        for values in product(*param_grid.values()):
            params = dict(zip(keys, values))
            folds = []
            for tr, te, label in self._folds(df):
                s = strategy_factory(params)
                m_tr = self.bt.run(tr, s, is_oos=False)["metrics"]
                m_te = self.bt.run(te, s, is_oos=True)["metrics"]
                folds.append({"fold": label, "train": m_tr, "test": m_te})
                if self.db:
                    self.db.save_result(s.name, "5m", params, 0, m_tr)
                    self.db.save_result(s.name, "5m", params, 1, m_te)
            oos_pfs = [f["test"].get("profit_factor", 0) for f in folds]
            oos_dds = [f["test"].get("max_dd_pct", 0) for f in folds]
            agg = {
                "params": params, "n_folds": len(folds),
                "oos_pf_mean": float(np.mean(oos_pfs)) if oos_pfs else 0.0,
                "oos_pf_min": float(np.min(oos_pfs)) if oos_pfs else 0.0,
                "oos_pf_std": float(np.std(oos_pfs)) if oos_pfs else 0.0,
                "oos_dd_max": float(np.min(oos_dds)) if oos_dds else 0.0,
                "folds": folds,
            }
            all_results.append(agg)
            print(f"  {params} | OOS PF mean={agg['oos_pf_mean']:.2f} min={agg['oos_pf_min']:.2f} maxDD={agg['oos_dd_max']:.1f}%")
        all_results.sort(key=lambda x: x["oos_pf_min"], reverse=True)
        return all_results

class MonteCarlo:
    def __init__(self, initial_capital: float, n_sims: int = 1000, seed: int = 42):
        self.initial = initial_capital
        self.n_sims = n_sims
        self.rng = np.random.default_rng(seed)

    def run(self, trades: List[Trade]) -> Dict[str, float]:
        if not trades: return {}
        pnls = np.array([t.pnl for t in trades], dtype=float)
        final_eq = np.zeros(self.n_sims)
        max_dds = np.zeros(self.n_sims)
        ruin_count = 0
        ruin_thr = self.initial * 0.5

        for sim in range(self.n_sims):
            mask = self.rng.random(len(pnls)) > 0.05
            sim_pnls = pnls[mask] * (1 + self.rng.normal(0, 0.002, mask.sum()))
            shuffled = self.rng.permutation(sim_pnls)
            eq, peak, max_dd = self.initial, self.initial, 0.0
            for p in shuffled:
                eq += p
                if eq > peak: peak = eq
                dd = (eq - peak) / peak if peak > 0 else 0.0
                if dd < max_dd: max_dd = dd
                if eq <= ruin_thr: ruin_count += 1; break
            final_eq[sim] = eq
            max_dds[sim] = max_dd

        return {
            "median_final_equity": float(np.median(final_eq)),
            "p5_final_equity": float(np.percentile(final_eq, 5)),
            "p95_final_equity": float(np.percentile(final_eq, 95)),
            "median_max_dd_pct": float(np.median(max_dds) * 100),
            "p95_max_dd_pct": float(np.percentile(max_dds, 95) * 100),
            "worst_max_dd_pct": float(np.min(max_dds) * 100),
            "prob_of_ruin_pct": ruin_count / self.n_sims * 100,
            "prob_profitable_pct": float((final_eq > self.initial).mean() * 100),
        }

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def make_smc_strategy(params: Dict[str, Any]) -> SMCStateMachineStrategy:
    s = SMCStateMachineStrategy()
    s.params = {**s.params, **params}
    return s

def _print_metrics(m: Dict[str, Any]) -> None:
    for k, v in m.items():
        if k in ("monthly_returns", "yearly_returns"): continue
        print(f"  {k:25s}: {v}")

def main() -> None:
    print("=" * 70)
    print("  QUANT FRAMEWORK — Unified Refactor (Wave 7 features)")
    print("=" * 70)

    db = ResearchDB()
    loader = DataManager(symbol=CFG.symbol, years=CFG.data_years, cfg=CFG)
    df = loader.load("5m")
    print(f"  {CFG.symbol} 5m: {len(df):,} bars  ({df.index[0].date()} → {df.index[-1].date()})")

    print("\n▸ Signal Diagnostics (full dataset):")
    strategy = make_smc_strategy({})
    result = strategy.generate(df)
    print(f"  Longs: {int((result.signals == 1).sum())}  Shorts: {int((result.signals == -1).sum())}")

    print("\n▸ Rolling Walk-Forward (train=2y, test=1y, step=1y):")
    wfo = WalkForwardOptimizer(CFG, db, train_years=2, test_years=1, step_years=1)
    wf = wfo.search(
        df,
        strategy_factory=make_smc_strategy,
        param_grid={"sweep_lookback": [20, 30], "fvg_max_lookback": [30, 50]},
    )
    if not wf:
        print("⚠️ No walk-forward folds produced. Loosen SMC filters.")
        return

    best = wf[0]
    print(f"\n▸ Best params: {best['params']}  OOS PF min: {best['oos_pf_min']:.2f}")

    print("\n▸ Full-sample backtest:")
    bt = EnhancedBacktester(CFG)
    full = bt.run(df, strategy)
    print("\n▸ Metrics:")
    _print_metrics(full["metrics"])

    print("\n▸ Monte Carlo (1,000 sims):")
    mc = MonteCarlo(CFG.initial_capital, n_sims=1000)
    for k, v in mc.run(full["trades"]).items():
        print(f"  {k:30s}: {v:.2f}")

    print("\n✅ Done. Results persisted to research.db.")


if __name__ == "__main__":
    main()