#!/usr/bin/env python3
"""
Live-but-paper-trading engine with multiple strategies (EMA crossover, Supertrend)
hookable to Fyers (in paper mode). This is a compact MVP intended for rapid iteration.

How to use:
  - Run in a local environment with Python 3.8+ and these dependencies:
      pandas, numpy
  - You can substitute a real Fyers client later; for now this runs in "paper" mode
    by simulating fills and price moves.
  - Modify CONFIG below to set universe, capital, symbols, and strategy toggles.
"""

from typing import List, Dict, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
import time
import random
import math

# ---------------------------
# Configuration (adjust to taste)
# ---------------------------

CONFIG = {
    "universe": ["NIFTY200", "BANKNIFTY"],  # symbolic universe (strings)
    "capital": 100000.0,                   # starting capital in USD-equivalent
    "per_trade_risk_pct": 0.01,            # 1% risk per trade
    "commission_per_trade": 0.0,           # paper commissions (flat per trade)
    "slippage_pips": 2,                    # simulated slippage in price units
    "allow_short": False,                   # whether short selling is allowed
    "mode": "paper",                         # 'paper' (simulate) or 'live' (connect to Fyers)
    "timeframe": "5m",                       # data cadence for the MVP (e.g., 5m bars)
    "start_time": "09:15",                   # market open (IST)
    "end_time": "15:30",                     # market close (IST)
    "enable_telegram_alerts": False,         # optional: wire Telegram alerts later
    "log_csv": "trade_log.csv",
    "equity_csv": "equity_curve.csv",
    "strategies": {
        "ema_cross": True,
        "supertrend": True
    }
}

# ---------------------------
# Minimal helper utilities
# ---------------------------

def now_ist():
    return datetime.now()

def clamp(n, a, b):
    return max(a, min(b, n))

# Simple IST-friendly timestamp (for demo)
def ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------
# Data layer (simplified)
# ---------------------------

class DataFetcher:
    """
    Lightweight data layer.
    For MVP: generate synthetic historical data per symbol, or in a real setup,
    pull from Fyers API (historical & real-time) and cache locally.
    """

    def __init__(self, mode="paper"):
        self.mode = mode

    def _synthetic_bars(self, symbol: str, n: int = 500, start_price: float = 100.0) -> pd.DataFrame:
        # generate a simple random walk price series
        rng = np.random.default_rng(seed=1234 if symbol == "NIFTY200" else 4321)
        prices = [start_price]
        for _ in range(n-1):
            step = rng.normal(loc=0.0, scale=1.0)  # small moves
            prices.append(max(0.1, prices[-1] + step))
        df = pd.DataFrame({
            "timestamp": [datetime.now() - timedelta(minutes=5*(n-i)) for i in range(n)],
            "open": prices[:-1] if n>1 else [start_price],
            "high": np.array(prices[1:]+[prices[-1]*1.01]),
            "low": np.array(prices[:-1]+[prices[-1]*0.99]),
            "close": prices,
        })
        df.reset_index(drop=True, inplace=True)
        return df

    def get_historical(self, symbol: str, start: datetime, end: datetime, interval: str = "5m") -> pd.DataFrame:
        # For MVP: return synthetic data
        n = max(100, int((end - start).total_seconds() // 60 / 5))
        df = self._synthetic_bars(symbol, n=n, start_price=100.0 + hash(symbol) % 50)
        return df

    def get_latest_quote(self, symbol: str) -> float:
        # For MVP: return a plausible latest price
        base = 100.0 + (hash(symbol) % 40)
        return base + random.uniform(-0.5, 0.5)

# ---------------------------
# Strategy layer (base and two MVP strategies)
# ---------------------------

class Strategy:
    name = "Base"

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def generate_signals(self, df: pd.DataFrame) -> List[Dict]:
        """
        Returns a list of signals as dicts:
        { "time": datetime, "symbol": str, "side": "BUY"|"SELL", "reason": str, "size": float }
        """
        return []

# EMA Crossover Strategy
class EMACrossoverStrategy(Strategy):
    name = "EMA_Cross"

    def __init__(self, short=9, long=21, size_per_order=0.02):
        self.short = short
        self.long = long
        self.size_per_order = size_per_order

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_short"] = df["close"].ewm(span=self.short, adjust=False).mean()
        df["ema_long"] = df["close"].ewm(span=self.long, adjust=False).mean()
        df["signal_raw"] = 0
        df.loc[df["ema_short"] > df["ema_long"], "signal_raw"] = 1
        df.loc[df["ema_short"] < df["ema_long"], "signal_raw"] = -1
        # sticky: persist last non-zero signal
        df["signal"] = df["signal_raw"].replace(to_replace=0, method="ffill").fillna(0).astype(int)
        return df

    def generate_signals(self, df: pd.DataFrame) -> List[Dict]:
        signals = []
        if df.empty:
            return signals
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        if last["signal"] == 1 and prev["signal"] != 1:
            signals.append({"time": last.name, "symbol": "NIFTY200", "side": "BUY", "reason": "EMA cross up", "size": CONFIG["capital"] * self.size_per_order})
        elif last["signal"] == -1 and prev["signal"] != -1:
            signals.append({"time": last.name, "symbol": "NIFTY200", "side": "SELL", "reason": "EMA cross down", "size": CONFIG["capital"] * self.size_per_order})
        return signals

# Lightweight Supertrend (very simplified)
class SupertrendStrategy(Strategy):
    name = "Supertrend"

    def __init__(self, period=7, multiplier=3.0, size_per_order=0.02):
        self.period = period
        self.multiplier = multiplier
        self.size_per_order = size_per_order

    def _atr(self, highs, lows, closes, period):
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(window=period, min_periods=1).mean()

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["atr"] = self._atr(df["high"], df["low"], df["close"], self.period)
        hl2 = (df["high"] + df["low"]) / 2
        df["upper"] = hl2 + self.multiplier * df["atr"]
        df["lower"] = hl2 - self.multiplier * df["atr"]
        df["in_uptrend"] = True  # naive start
        # pseudo of supertrend: if close > lower, in_uptrend true
        df["in_uptrend"] = (df["close"] > df["lower"]).astype(int)
        df["signal"] = 0
        # generate signals on cross of price above/below bands
        if len(df) >= 2:
            if df.iloc[-1]["close"] > df.iloc[-1]["upper"] and df.iloc[-2]["close"] <= df.iloc[-2]["upper"]:
                df.at[df.index[-1], "signal"] = 1
            elif df.iloc[-1]["close"] < df.iloc[-1]["lower"] and df.iloc[-2]["close"] >= df.iloc[-2]["lower"]:
                df.at[df.index[-1], "signal"] = -1
        return df

    def generate_signals(self, df: pd.DataFrame) -> List[Dict]:
        signals = []
        if df.empty:
            return signals
        last = df.iloc[-1]
        if last["signal"] == 1:
            signals.append({"time": last.name, "symbol": "NIFTY200", "side": "BUY", "reason": "Supertrend up", "size": CONFIG["capital"] * self.size_per_order})
        elif last["signal"] == -1:
            signals.append({"time": last.name, "symbol": "NIFTY200", "side": "SELL", "reason": "Supertrend down", "size": CONFIG["capital"] * self.size_per_order})
        return signals

# ---------------------------
# Risk & sizing (basic)
# ---------------------------

@dataclass
class Position:
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float
    pnl: float = 0.0
    open: bool = True
    id: int = 0

class RiskManager:
    def __init__(self, capital: float, per_trade_risk_pct: float, allow_short: bool = False):
        self.capital = capital
        self.per_trade_risk_pct = per_trade_risk_pct
        self.allow_short = allow_short

    def compute_position_size(self, entry_price: float, stop_price: float) -> float:
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            risk_per_share = 0.01
        max_risk_budget = self.capital * self.per_trade_risk_pct
        size = max_risk_budget / risk_per_share
        return max(0.0, size)

# ---------------------------
# Paper trading engine (execution layer)
# ---------------------------

class PaperEngine:
    def __init__(self, capital: float, commission_per_trade: float, slippage_pips: int, mode: str = "paper"):
        self.capital = capital
        self.commission_per_trade = commission_per_trade
        self.slippage_pips = slippage_pips
        self.mode = mode
        self.positions: List[Position] = []
        self.cash = capital
        self.equity = capital
        self.trade_id_counter = 1
        self.log: List[Dict] = []

    def _apply_slippage(self, price: float, side: str) -> float:
        # simple fixed slippage model
        adj = self.slippage_pips * 0.01 * price  # proportional
        return price + adj if side == "BUY" else price - adj

    def place_order(self, symbol: str, side: str, quantity: float, entry_price: float, stop_loss: float, take_profit: float):
        # In paper mode: immediate "fill" at adjusted price
        price = self._apply_slippage(entry_price, side)
        if quantity <= 0:
            return None
        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            size=quantity,
            id=self.trade_id_counter,
        )
        self.trade_id_counter += 1
        self.positions.append(pos)
        # deduct tentative commission
        self.cash -= self.commission_per_trade
        self.log.append({
            "ts": ts(),
            "action": "OPEN",
            "id": pos.id,
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "size": quantity,
            "profit": 0.0,
            "reason": "entry"
        })
        return pos

    def _update_position_pnl(self, pos: Position, market_price: float) -> float:
        # simple PnL for long: (price - entry) * size; for short: (entry - price) * size
        if pos.side == "BUY":
            return (market_price - pos.entry_price) * pos.size
        else:
            return (pos.entry_price - market_price) * pos.size

    def update(self, current_prices: Dict[str, float]):
        # iterate over open positions and apply stop/TP or close if exit conditions met
        for pos in self.positions[:]:
            if not pos.open:
                continue
            price = current_prices.get(pos.symbol, pos.entry_price)
            pnl = self._update_position_pnl(pos, price)
            pos.pnl = pnl
            exit_signal = False
            exit_reason = ""
            if pos.take_profit is not None:
                if (pos.side == "BUY" and price >= pos.take_profit) or (pos.side == "SELL" and price <= pos.take_profit):
                    exit_signal = True
                    exit_reason = "TP"
            if pos.stop_loss is not None:
                if (pos.side == "BUY" and price <= pos.stop_loss) or (pos.side == "SELL" and price >= pos.stop_loss):
                    exit_signal = True
                    exit_reason = "SL"
            if exit_signal:
                # close position
                self.positions.remove(pos)
                exit_price = price
                exit_price = self._apply_slippage(exit_price, "SELL" if pos.side == "BUY" else "BUY")
                gross_pnl = self._update_position_pnl(pos, exit_price)
                self.cash += pos.size * exit_price  # realize value (simplified)
                self.cash -= self.commission_per_trade
                self.equity = self.cash + sum(p.size * 0 for p in self.positions)  # rough
                self.log.append({
                    "ts": ts(),
                    "action": "CLOSE",
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "exit_price": exit_price,
                    "size": pos.size,
                    "profit": gross_pnl,
                    "reason": exit_reason
                })

        # update equity baseline
        self.equity = self.cash + sum((p.size * current_prices.get(p.symbol, p.entry_price)) for p in self.positions)

    def report(self) -> pd.DataFrame:
        df = pd.DataFrame(self.log)
        return df

# ---------------------------
# Runner / orchestrator
# ---------------------------

def main():
    # instantiate data layer
    df_fetcher = DataFetcher(mode=CONFIG["mode"])

    # instantiate strategies
    strategies: List[Strategy] = []
    if CONFIG["strategies"].get("ema_cross"):
        strategies.append(EMACrossoverStrategy(short=9, long=21, size_per_order=0.02))
    if CONFIG["strategies"].get("supertrend"):
        strategies.append(SupertrendStrategy(period=7, multiplier=3.0, size_per_order=0.02))

    # risk manager (per-trade sizing decision is inside the strategy in this MVP)
    risk = RiskManager(CONFIG["capital"], CONFIG["per_trade_risk_pct"], CONFIG["allow_short"])

    # paper engine
    engine = PaperEngine(
        capital=CONFIG["capital"],
        commission_per_trade=CONFIG["commission_per_trade"],
        slippage_pips=CONFIG["slippage_pips"],
        mode=CONFIG["mode"]
    )

    # initial synthetic data load for a small window
    universe = CONFIG["universe"]
    # For MVP, generate 200 bars per symbol
    bars_per_symbol = 200
    data_store: Dict[str, pd.DataFrame] = {}

    for symbol in universe:
        end = now_ist()
        start = end - timedelta(days=1)
        df = df_fetcher.get_historical(symbol, start, end, interval=CONFIG["timeframe"])
        data_store[symbol] = df

    # simple loop: emulate a live cadence (e.g., every 60 seconds). We'll simulate 300 steps.
    cadence_seconds = 30  # short cadence for MVP
    steps = 60  # run for ~30 minutes of simulated time (adjust as desired)

    last_equity = CONFIG["capital"]
    equity_history = []

    for step in range(steps):
        # simulate tick/bar progression: generate/append a new bar per symbol
        current_prices = {}
        for symbol, df in data_store.items():
            # append a new synthetic bar (simulate a live tick)
            last_price = df["close"].iloc[-1]
            new_price = max(0.01, last_price * (1.0 + np.random.normal(0, 0.0008)))
            new_row = {
                "timestamp": now_ist(),
                "open": last_price,
                "high": max(last_price, new_price),
                "low": min(last_price, new_price),
                "close": new_price
            }
            df = df.append(new_row, ignore_index=True)
            data_store[symbol] = df
            current_prices[symbol] = new_price

        # generate signals from each strategy on latest bar
        for strategy in strategies:
            for symbol, df in data_store.items():
                # filter to symbol-specific df, compute signals
                df_proc = strategy.prepare_data(df)
                signals = strategy.generate_signals(df_proc)
                for s in signals:
                    # risk sizing (basic): compute quantity using entry price and a stop
                    entry_price = s["size"]  # in this MVP, we treat size as notional; we'll convert to quantity
                    side = s["side"]
                    if side not in ("BUY", "SELL"):
                        continue

                    # Simple quantity derivation: use fixed fraction of capital per trade
                    qty = (CONFIG["capital"] * 0.01) / max(1e-6, current_prices.get(symbol, 1.0))
                    # define a basic stop and target (ATR-like roughness)
                    stop_loss = current_prices[symbol] * (0.995 if side == "BUY" else 1.005)
                    take_profit = current_prices[symbol] * (1.005 if side == "BUY" else 0.995)

                    # place order in paper engine
                    engine.place_order(
                        symbol=symbol,
                        side=side,
                        quantity=qty,
                        entry_price=current_prices[symbol],
                        stop_loss=stop_loss,
                        take_profit=take_profit
                    )

        # update engine with new prices and possibly close trades
        engine.update(current_prices)

        # record equity
        equity = engine.equity
        equity_history.append({"step": step, "equity": equity, "timestamp": now_ist()})

        # try to sleep (commented out in real runs to avoid long runtimes here)
        # time.sleep(cadence_seconds)

    # final reports
    trade_log = engine.report()
    trade_log.to_csv(CONFIG["log_csv"], index=False)
    equity_df = pd.DataFrame(equity_history)
    equity_df.to_csv(CONFIG["equity_csv"], index=False)

    print("Paper trading run complete.")
    print(f"Final equity: {engine.equity:.2f}")
    print(f"Trades recorded: {len(engine.positions) if engine.positions else 0} (open positions may be present in live mode)")

if __name__ == "__main__":
    main()
