#!/usr/bin/env python3
"""
Quantitative Trading Framework — Wave 1: Trend Following + Momentum
═══════════════════════════════════════════════════════════════════
Instrument : NIFTY
Capital    : INR 100,000
Timeframes : 1m, 5m, 15m
Data       : 5 years historical (synthetic for now, production hook ready)
═══════════════════════════════════════════════════════════════════

Architecture:
  DataLoader  →  Strategy Registry  →  EnhancedBacktester  →  GridSearchOptimizer
                                          ↓
                                    Metrics & Trade Log
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Callable, Any
from collections import defaultdict
from itertools import product
import json, time, warnings, os, hashlib
from datetime import datetime, timedelta
from enum import Enum

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

@dataclass
class Config:
    """Central configuration — single source of truth."""
    symbol: str = "NIFTY"
    initial_capital: float = 100_000.0
    risk_per_trade_pct: float = 0.02       # 2% per trade
    max_positions: int = 1
    commission_pct: float = 0.05           # 0.05% per side
    slippage_pct: float = 0.05
    timeframes: List[str] = field(default_factory=lambda: ["1m", "5m", "15m"])
    data_years: int = 5
    # ATR SL/TP defaults
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    # Time filter (IST market hours)
    market_open: str = "09:15"
    market_close: str = "15:30"
    avoid_open_minutes: int = 15
    avoid_close_minutes: int = 15
    avoid_lunch: bool = True
    lunch_start: str = "12:00"
    lunch_end: str = "13:00"
    # Trend filter
    trend_lookback: int = 50


CFG = Config()


# ═══════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════

class DataLoader:
    """
    Loads / caches / generates OHLCV data per timeframe.
    Synthetic data for now — swap generate_synthetic() with an API call.
    """

    CACHE_DIR = "./data_cache"

    def __init__(self, symbol: str = "NIFTY", years: int = 5):
        self.symbol = symbol
        self.years = years
        self._cache: Dict[str, pd.DataFrame] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def load(self, tf: str) -> pd.DataFrame:
        if tf in self._cache:
            return self._cache[tf]
        path = f"{self.CACHE_DIR}/{self.symbol}_{tf}.csv"
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            df = self._generate(tf)
            df.to_csv(path)
        self._cache[tf] = df
        return df

    def _generate(self, tf: str) -> pd.DataFrame:
        """Synthetic OHLCV with realistic drift + volatility."""
        minutes_map = {"1m": 1, "5m": 5, "15m": 15}
        bar_minutes = minutes_map.get(tf, 5)
        n_bars = (self.years * 252 * 390) // bar_minutes

        np.random.seed(hash(self.symbol + tf) % (2**31))
        close = 18000.0
        prices = []
        for _ in range(n_bars):
            ret = np.random.normal(0.0002, 0.012)
            close *= (1 + ret)
            o = close * (1 + np.random.normal(0, 0.003))
            h = max(o, close) * (1 + abs(np.random.normal(0, 0.005)))
            l = min(o, close) * (1 - abs(np.random.normal(0, 0.005)))
            v = np.random.randint(1000, 50000)
            prices.append((o, h, l, close, v))

        idx = pd.date_range(
            start=datetime.now() - timedelta(days=self.years * 365),
            periods=n_bars, freq=f"{bar_minutes}min"
        )
        df = pd.DataFrame(prices, index=idx, columns=["open", "high", "low", "close", "volume"])
        df = df[(df.index.time >= pd.Timestamp("09:15").time()) &
                (df.index.time <= pd.Timestamp("15:30").time())]
        return df


# ═══════════════════════════════════════════════════════════════
# STRATEGY REGISTRY
# ═══════════════════════════════════════════════════════════════

@dataclass
class StrategyResult:
    signals: pd.Series          # 1=buy, -1=sell, 0=neutral
    meta: Dict[str, Any]        # extra info (indicator values etc.)


class Strategy:
    """Base class for all strategies."""
    name: str = "base"
    category: str = "unknown"
    params: Dict[str, Any] = {}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        raise NotImplementedError


STRATEGY_REGISTRY: Dict[str, type] = {}

def register(cls):
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


# ═══════════════════════════════════════════════════════════════
# WAVE 1: TREND FOLLOWING STRATEGIES
# ═══════════════════════════════════════════════════════════════

@register
class EMACrossover(Strategy):
    """EMA crossover: fast EMA crosses above slow EMA → buy; below → sell."""
    name = "ema_crossover"
    category = "trend_following"
    params = {"fast": 9, "slow": 21}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        fast_ema = df["close"].ewm(span=self.params["fast"]).mean()
        slow_ema = df["close"].ewm(span=self.params["slow"]).mean()
        signals = pd.Series(0, index=df.index)
        signals[fast_ema > slow_ema] = 1
        signals[fast_ema < slow_ema] = -1
        return StrategyResult(signals, {"fast_ema": fast_ema, "slow_ema": slow_ema})


@register
class SMACrossover(Strategy):
    """SMA crossover: fast SMA crosses above slow SMA → buy; below → sell."""
    name = "sma_crossover"
    category = "trend_following"
    params = {"fast": 10, "slow": 30}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        fast_sma = df["close"].rolling(self.params["fast"]).mean()
        slow_sma = df["close"].rolling(self.params["slow"]).mean()
        signals = pd.Series(0, index=df.index)
        signals[fast_sma > slow_sma] = 1
        signals[fast_sma < slow_sma] = -1
        return StrategyResult(signals, {"fast_sma": fast_sma, "slow_sma": slow_sma})


@register
class Supertrend(Strategy):
    """Supertrend using ATR bands: price above band → buy; below → sell."""
    name = "supertrend"
    category = "trend_following"
    params = {"atr_period": 10, "multiplier": 3.0}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        atr = self._calc_atr(df, self.params["atr_period"])
        hl2 = (df["high"] + df["low"]) / 2
        upper = hl2 + self.params["multiplier"] * atr
        lower = hl2 - self.params["multiplier"] * atr

        trend = pd.Series(1, index=df.index, dtype=int)
        for i in range(1, len(df)):
            if df["close"].iloc[i] > upper.iloc[i - 1]:
                trend.iloc[i] = 1
            elif df["close"].iloc[i] < lower.iloc[i - 1]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = trend.iloc[i - 1]
        signals = pd.Series(0, index=df.index)
        signals[trend == 1] = 1
        signals[trend == -1] = -1
        return StrategyResult(signals, {"upper": upper, "lower": lower, "atr": atr})

    @staticmethod
    def _calc_atr(df, period):
        h, l, c = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        return tr.ewm(span=period).mean()


@register
class DonchianBreakout(Strategy):
    """Donchian channel breakout: price breaks above N-period high → buy."""
    name = "donchian_breakout"
    category = "trend_following"
    params = {"lookback": 20}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        upper = df["high"].rolling(self.params["lookback"]).max()
        lower = df["low"].rolling(self.params["lookback"]).min()
        signals = pd.Series(0, index=df.index)
        signals[df["close"] > upper.shift(1)] = 1
        signals[df["close"] < lower.shift(1)] = -1
        return StrategyResult(signals, {"upper": upper, "lower": lower})


# ═══════════════════════════════════════════════════════════════
# WAVE 1: MOMENTUM STRATEGIES
# ═══════════════════════════════════════════════════════════════

@register
class RSIMomentum(Strategy):
    """RSI momentum: oversold (<30) → buy; overbought (>70) → sell."""
    name = "rsi_momentum"
    category = "momentum"
    params = {"period": 14, "oversold": 30, "overbought": 70}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=self.params["period"]).mean()
        avg_loss = loss.ewm(span=self.params["period"]).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        signals = pd.Series(0, index=df.index)
        signals[rsi < self.params["oversold"]] = 1
        signals[rsi > self.params["overbought"]] = -1
        return StrategyResult(signals, {"rsi": rsi})


@register
class StochasticMomentum(Strategy):
    """Stochastic oscillator: %K crosses above %D in oversold → buy."""
    name = "stochastic"
    category = "momentum"
    params = {"k_period": 14, "d_period": 3, "oversold": 20, "overbought": 80}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        low_n = df["low"].rolling(self.params["k_period"]).min()
        high_n = df["high"].rolling(self.params["k_period"]).max()
        k = 100 * (df["close"] - low_n) / (high_n - low_n).replace(0, 1e-9)
        d = k.rolling(self.params["d_period"]).mean()
        signals = pd.Series(0, index=df.index)
        signals[(k < self.params["oversold"]) & (k > d)] = 1
        signals[(k > self.params["overbought"]) & (k < d)] = -1
        return StrategyResult(signals, {"k": k, "d": d})


@register
class MACDStrategy(Strategy):
    """MACD crossover: MACD line crosses above signal → buy."""
    name = "macd"
    category = "momentum"
    params = {"fast": 12, "slow": 26, "signal": 9}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        ema_fast = df["close"].ewm(span=self.params["fast"]).mean()
        ema_slow = df["close"].ewm(span=self.params["slow"]).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.params["signal"]).mean()
        hist = macd_line - signal_line
        signals = pd.Series(0, index=df.index)
        signals[hist > 0] = 1
        signals[hist < 0] = -1
        return StrategyResult(signals, {"macd": macd_line, "signal": signal_line, "histogram": hist})


@register
class CCIMomentum(Strategy):
    """CCI: crosses above +100 → overbought → sell; below -100 → oversold → buy."""
    name = "cci"
    category = "momentum"
    params = {"period": 20, "upper": 100, "lower": -100}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        tp = (df["high"] + df["low"] + df["close"]) / 3
        sma_tp = tp.rolling(self.params["period"]).mean()
        mad = tp.rolling(self.params["period"]).apply(lambda x: (x - x.mean()).abs().mean())
        cci = (tp - sma_tp) / (0.015 * mad)
        signals = pd.Series(0, index=df.index)
        signals[cci < self.params["lower"]] = 1
        signals[cci > self.params["upper"]] = -1
        return StrategyResult(signals, {"cci": cci})


@register
class AwesomeOscillator(Strategy):
    """Awesome Oscillator: positive and rising → buy; negative and falling → sell."""
    name = "awesome_oscillator"
    category = "momentum"
    params = {"fast": 5, "slow": 34}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        mid = (df["high"] + df["low"]) / 2
        ao = mid.rolling(self.params["fast"]).mean() - mid.rolling(self.params["slow"]).mean()
        signals = pd.Series(0, index=df.index)
        signals[(ao > 0) & (ao > ao.shift(1))] = 1
        signals[(ao < 0) & (ao < ao.shift(1))] = -1
        return StrategyResult(signals, {"ao": ao})


@register
class ROCMomentum(Strategy):
    """Rate of Change: ROC crosses above zero → buy."""
    name = "roc"
    category = "momentum"
    params = {"period": 12}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        roc = (df["close"] - df["close"].shift(self.params["period"])) / df["close"].shift(self.params["period"]) * 100
        signals = pd.Series(0, index=df.index)
        signals[roc > 0] = 1
        signals[roc < 0] = -1
        return StrategyResult(signals, {"roc": roc})


# ═══════════════════════════════════════════════════════════════
# ENHANCED BACKTESTER
# ═══════════════════════════════════════════════════════════════

@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    side: str            # "LONG" | "SHORT"
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    exit_reason: str     # "TP" | "SL" | "SIGNAL" | "EOD"
    bars_held: int


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity_curve: pd.Series
    metrics: Dict[str, float]


class EnhancedBacktester:
    """
    Realistic backtester based on the attached backtester_enhanced.txt:
    - Enters on next bar open after signal
    - ATR-based dynamic SL/TP
    - Time filter (avoid open/lunch/close)
    - Trend filter
    - Slippage + commission
    - Risk-based position sizing
    - Exit at end-of-day
    """

    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg

    def run(self, df: pd.DataFrame, strategy: Strategy,
            use_atr_sltp: bool = True,
            use_time_filter: bool = True,
            use_trend_filter: bool = False,
            ) -> BacktestResult:
        cfg = self.cfg

        # Generate signals
        result = strategy.generate(df)
        raw_signals = result.signals

        # Compute ATR for SL/TP
        atr = self._calc_atr(df, cfg.atr_period)

        # Position sizing
        risk_per_trade = cfg.initial_capital * cfg.risk_per_trade_pct

        trades: List[Trade] = []
        equity = cfg.initial_capital
        equity_curve = [equity]
        curve_index = [df.index[0]]
        position = 0         # 0=none, 1=long, -1=short
        entry_price = 0.0
        sl_price = 0.0
        tp_price = 0.0
        entry_bar_idx = -1
        qty = 0

        for i in range(1, len(df)):
            idx = df.index[i]
            dt = pd.Timestamp(idx)
            price_open = df["open"].iloc[i]
            price_close = df["close"].iloc[i]
            price_high = df["high"].iloc[i]
            price_low = df["low"].iloc[i]
            atr_val = atr.iloc[i] if pd.notna(atr.iloc[i]) else 0
            signal = raw_signals.iloc[i - 1]  # act on previous bar's signal

            # ---- Time filter ----
            if use_time_filter and not self._is_good_time(dt):
                signal = 0

            # ---- Trend filter ----
            if use_trend_filter and signal != 0 and not self._is_trending(df, i):
                signal = 0

            # ---- Manage open position ----
            if position != 0:
                exit_now = False
                exit_reason = ""

                # Check SL/TP on bar's high/low
                if use_atr_sltp and atr_val > 0:
                    if position == 1:  # Long
                        if price_low <= sl_price:
                            exit_price = sl_price
                            exit_now = True
                            exit_reason = "SL"
                        elif price_high >= tp_price:
                            exit_price = tp_price
                            exit_now = True
                            exit_reason = "TP"
                    else:  # Short
                        if price_high >= sl_price:
                            exit_price = sl_price
                            exit_now = True
                            exit_reason = "SL"
                        elif price_low <= tp_price:
                            exit_price = tp_price
                            exit_now = True
                            exit_reason = "TP"

                # Signal reversal
                if not exit_now and signal != 0 and signal != position:
                    exit_price = price_open
                    exit_now = True
                    exit_reason = "SIGNAL"

                # EOD forced exit
                is_last = (i == len(df) - 1)
                near_close = dt.time() >= pd.Timestamp(cfg.market_close).time()
                if not exit_now and (is_last or near_close):
                    exit_price = price_close
                    exit_now = True
                    exit_reason = "EOD"

                if exit_now:
                    exit_price_slipped = self._apply_slippage(exit_price, position)
                    pnl = (exit_price_slipped - entry_price) * qty * position
                    pnl_pct = pnl / (entry_price * qty) * 100
                    pnl -= self._commission(entry_price * qty + exit_price_slipped * qty)
                    equity += pnl
                    bars_held = i - entry_bar_idx

                    trades.append(Trade(
                        entry_time=df.index[entry_bar_idx],
                        exit_time=idx,
                        side="LONG" if position == 1 else "SHORT",
                        entry_price=entry_price,
                        exit_price=exit_price_slipped,
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 2),
                        exit_reason=exit_reason,
                        bars_held=bars_held,
                    ))
                    position = 0
                    signal = 0  # don't re-enter same bar

            # ---- Enter new position ----
            if position == 0 and signal != 0:
                entry_price = self._apply_slippage(price_open, signal)
                qty = max(1, int(risk_per_trade / (atr_val * cfg.sl_atr_mult))) if atr_val > 0 else 1
                if use_atr_sltp and atr_val > 0:
                    if signal == 1:  # Long
                        sl_price = entry_price - (atr_val * cfg.sl_atr_mult)
                        tp_price = entry_price + (atr_val * cfg.tp_atr_mult)
                    else:  # Short
                        sl_price = entry_price + (atr_val * cfg.sl_atr_mult)
                        tp_price = entry_price - (atr_val * cfg.tp_atr_mult)
                position = signal
                entry_bar_idx = i

            # Record equity
            if position != 0:
                unrealized = (price_close - entry_price) * qty * position
                curve_val = equity + unrealized
            else:
                curve_val = equity
            equity_curve.append(curve_val)
            curve_index.append(idx)

        # ---- Compute metrics ----
        eq_series = pd.Series(equity_curve, index=curve_index)
        metrics = self._compute_metrics(trades, eq_series)
        return BacktestResult(trades=trades, equity_curve=eq_series, metrics=metrics)

    # ── Helpers ──────────────────────────────────────────────

    def _calc_atr(self, df, period):
        h, l, c = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        return tr.ewm(span=period).mean()

    def _is_good_time(self, dt: pd.Timestamp) -> bool:
        cfg = self.cfg
        t = dt.time()
        open_t = pd.Timestamp(cfg.market_open).time()
        close_t = pd.Timestamp(cfg.market_close).time()
        open_avoid = (pd.Timestamp("00:00") + timedelta(minutes=cfg.avoid_open_minutes)).time()
        close_avoid_start = (pd.Timestamp(cfg.market_close) - timedelta(minutes=cfg.avoid_close_minutes)).time()
        if t < open_t or t > close_t:
            return False
        if open_avoid and t < (pd.Timestamp(cfg.market_open) + timedelta(minutes=cfg.avoid_open_minutes)).time():
            return False
        if t >= close_avoid_start:
            return False
        if cfg.avoid_lunch:
            lunch_s = pd.Timestamp(cfg.lunch_start).time()
            lunch_e = pd.Timestamp(cfg.lunch_end).time()
            if lunch_s <= t < lunch_e:
                return False
        return True

    def _is_trending(self, df, idx, lookback=None):
        if lookback is None:
            lookback = self.cfg.trend_lookback
        if idx < lookback:
            return True
        window = df.iloc[idx - lookback:idx]
        sma = window["close"].rolling(20).mean()
        if sma.iloc[-1] - sma.iloc[0] > 0:
            return True
        price_range = window["high"].max() - window["low"].min()
        avg_range = (window["high"] - window["low"]).mean()
        return price_range > avg_range * 1.5

    def _apply_slippage(self, price, direction):
        slip = self.cfg.slippage_pct / 100
        return price * (1 + slip) if direction == 1 else price * (1 - slip)

    def _commission(self, notional):
        return notional * self.cfg.commission_pct / 100

    def _compute_metrics(self, trades, equity):
        if not trades:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "profit_factor": 0,
                    "sharpe": 0, "max_drawdown_pct": 0, "avg_win": 0, "avg_loss": 0,
                    "expectancy": 0}
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t.pnl for t in trades)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        profit_factor = gross_profit / gross_loss

        returns = equity.pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 75)) if returns.std() > 0 else 0  # ~75 5-min bars/day

        peak = equity.cummax()
        drawdown = (equity - peak) / peak * 100
        max_dd = drawdown.min()

        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        return {
            "total_trades": len(trades),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
        }


# ═══════════════════════════════════════════════════════════════
# GRID SEARCH OPTIMIZER
# ═══════════════════════════════════════════════════════════════

@dataclass
class GridResult:
    strategy_name: str
    category: str
    timeframe: str
    params: Dict[str, Any]
    metrics: Dict[str, float]
    rank_score: float


class GridSearchOptimizer:
    """
    Runs a grid search over strategy × timeframe × parameter combinations.
    Ranks results by the chosen scoring metric.
    """

    def __init__(self, data_loader: DataLoader, cfg: Config = CFG):
        self.loader = data_loader
        self.cfg = cfg
        self.backtester = EnhancedBacktester(cfg)
        self.results: List[GridResult] = []

    def search(self,
               strategies: List[str],
               timeframes: List[str],
               param_grids: Dict[str, Dict[str, list]],
               score_by: str = "profit_factor",
               verbose: bool = True,
               ) -> List[GridResult]:
        self.results = []
        total = sum(
            len(list(product(*grid.values())))
            for s in strategies
            for grid in [param_grids.get(s, {})]
        ) * len(timeframes) if param_grids else len(strategies) * len(timeframes)

        count = 0
        for tf in timeframes:
            df = self.loader.load(tf)
            for sname in strategies:
                cls = STRATEGY_REGISTRY.get(sname)
                if cls is None:
                    if verbose:
                        print(f"  ⚠ Unknown strategy: {sname}")
                    continue

                grid = param_grids.get(sname, {})
                if not grid:
                    # Single run with default params
                    strategy = cls()
                    bt = self.backtester.run(df, strategy)
                    self.results.append(GridResult(
                        strategy_name=sname, category=cls.category, timeframe=tf,
                        params=strategy.params, metrics=bt.metrics,
                        rank_score=bt.metrics.get(score_by, 0),
                    ))
                    count += 1
                    if verbose:
                        print(f"  [{count}/{total}] {sname} @ {tf} → PF={bt.metrics.get('profit_factor',0):.2f}")
                else:
                    keys = list(grid.keys())
                    for values in product(*grid.values()):
                        params = dict(zip(keys, values))
                        strategy = cls()
                        strategy.params = params
                        bt = self.backtester.run(df, strategy)
                        self.results.append(GridResult(
                            strategy_name=sname, category=cls.category, timeframe=tf,
                            params=params, metrics=bt.metrics,
                            rank_score=bt.metrics.get(score_by, 0),
                        ))
                        count += 1
                        if verbose:
                            print(f"  [{count}/{total}] {sname} @ {tf} {params} → PF={bt.metrics.get('profit_factor',0):.2f}")

        self.results.sort(key=lambda r: r.rank_score, reverse=True)
        return self.results

    def top(self, n: int = 20) -> List[GridResult]:
        return self.results[:n]

    def by_category(self, n: int = 5) -> Dict[str, List[GridResult]]:
        grouped = defaultdict(list)
        for r in self.results:
            grouped[r.category].append(r)
        return {cat: sorted(items, key=lambda r: r.rank_score, reverse=True)[:n]
                for cat, items in grouped.items()}

    def summary(self, n: int = 20) -> str:
        lines = [f"{'Rank':<5} {'Strategy':<22} {'TF':<5} {'PF':<8} {'Win%':<8} {'PnL':<12} {'Sharpe':<8} {'MaxDD%':<8} {'Params'}"]
        lines.append("-" * 130)
        for i, r in enumerate(self.top(n), 1):
            m = r.metrics
            params_str = ", ".join(f"{k}={v}" for k, v in r.params.items())
            lines.append(
                f"{i:<5} {r.strategy_name:<22} {r.timeframe:<5} "
                f"{m.get('profit_factor',0):<8.2f} {m.get('win_rate',0):<8.1f} "
                f"₹{m.get('total_pnl',0):<11.0f} {m.get('sharpe',0):<8.2f} "
                f"{m.get('max_drawdown_pct',0):<8.1f} {params_str}"
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════

def print_full_report(grid: GridSearchOptimizer):
    """Print a comprehensive results report."""
    print("\n" + "═" * 100)
    print("QUANTITATIVE TRADING FRAMEWORK — WAVE 1 RESULTS")
    print(f"Instrument: {CFG.symbol}  |  Capital: ₹{CFG.initial_capital:,.0f}  |  Risk/Trade: {CFG.risk_per_trade_pct*100:.0f}%")
    print("═" * 100)

    print("\n🏆 TOP 20 CONFIGURATIONS")
    print(grid.summary(20))

    print("\n📊 BEST BY CATEGORY")
    by_cat = grid.by_category(n=3)
    for cat, items in by_cat.items():
        if items:
            best = items[0]
            m = best.metrics
            print(f"  {cat.upper():<25} → {best.strategy_name:<20} @ {best.timeframe:<4}  "
                  f"PF={m.get('profit_factor',0):.2f}  Win%={m.get('win_rate',0):.0f}%  "
                  f"PnL=₹{m.get('total_pnl',0):,.0f}  Sharpe={m.get('sharpe',0):.2f}")

    # Overall stats
    all_pf = [r.metrics.get("profit_factor", 0) for r in grid.results if r.metrics.get("profit_factor", 0) > 0]
    print(f"\n📈 AGGREGATE: {len(grid.results)} configs tested  |  "
          f"Mean PF: {np.mean(all_pf):.2f}  |  "
          f"Profitable: {sum(1 for pf in all_pf if pf > 1)} ({sum(1 for pf in all_pf if pf > 1)/len(all_pf)*100:.0f}%)  |  "
          f"Best PF: {max(all_pf):.2f}")
    print("═" * 100 + "\n")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  QUANT FRAMEWORK — Wave 1: Trend + Momentum")
    print(f"  {CFG.symbol}  |  ₹{CFG.initial_capital:,.0f}  |  {CFG.data_years}y data")
    print("=" * 60)

    # 1. Load data
    print("\n📦 Loading data...")
    loader = DataLoader(symbol=CFG.symbol, years=CFG.data_years)
    for tf in CFG.timeframes:
        df = loader.load(tf)
        print(f"  {tf}: {len(df):,} bars  ({df.index[0].date()} → {df.index[-1].date()})")

    # 2. Quick smoke test — single strategy
    print("\n🧪 Smoke test: EMA Crossover on 5m...")
    df_5m = loader.load("5m")
    strat = EMACrossover()
    bt = EnhancedBacktester(CFG)
    result = bt.run(df_5m, strat)
    m = result.metrics
    print(f"  Trades: {m['total_trades']}  |  Win%: {m['win_rate']}%  |  PF: {m['profit_factor']}  |  "
          f"PnL: ₹{m['total_pnl']:,.0f}  |  MaxDD: {m['max_drawdown_pct']}%")

    # 3. Grid search — Wave 1 strategies
    print("\n🔍 GRID SEARCH — Wave 1 (Trend Following + Momentum)")
    print("-" * 60)

    wave1_strategies = [
        "ema_crossover", "sma_crossover", "supertrend", "donchian_breakout",
        "rsi_momentum", "stochastic", "macd", "cci", "awesome_oscillator", "roc",
    ]

    param_grids = {
        "ema_crossover": {"fast": [5, 9, 13], "slow": [21, 34, 50]},
        "sma_crossover": {"fast": [10, 20], "slow": [30, 50]},
        "supertrend": {"atr_period": [7, 10, 14], "multiplier": [2.0, 3.0, 4.0]},
        "donchian_breakout": {"lookback": [10, 20, 40]},
        "rsi_momentum": {"period": [10, 14, 21], "oversold": [25, 30], "overbought": [70, 75]},
        "stochastic": {"k_period": [10, 14], "d_period": [3, 5], "oversold": [20], "overbought": [80]},
        "macd": {"fast": [8, 12], "slow": [21, 26], "signal": [7, 9]},
        "cci": {"period": [14, 20], "upper": [100], "lower": [-100]},
        "awesome_oscillator": {"fast": [5], "slow": [34]},
        "roc": {"period": [8, 12, 20]},
    }

    optimizer = GridSearchOptimizer(loader, CFG)
    optimizer.search(
        strategies=wave1_strategies,
        timeframes=CFG.timeframes,
        param_grids=param_grids,
        score_by="profit_factor",
    )

    # 4. Report
    print_full_report(optimizer)

    # 5. Trade inspection for best config
    best = optimizer.top(1)[0]
    print(f"\n🔎 TRADE INSPECTION — {best.strategy_name} @ {best.timeframe}  {best.params}")
    print("-" * 80)
    df_best = loader.load(best.timeframe)
    s_cls = STRATEGY_REGISTRY[best.strategy_name]
    s = s_cls()
    s.params = best.params
    bt_result = EnhancedBacktester(CFG).run(df_best, s)
    for t in bt_result.trades[-10:]:
        print(f"  {t.entry_time} → {t.exit_time}  {t.side:5}  "
              f"Entry: ₹{t.entry_price:.1f}  Exit: ₹{t.exit_price:.1f}  "
              f"PnL: ₹{t.pnl:7.0f}  {t.exit_reason:6}  Bars: {t.bars_held}")

    print("\n✅ Wave 1 complete. Ready for Wave 2 (Volatility + Volume) or data integration.")


if __name__ == "__main__":
    main()
