#!/usr/bin/env python3
"""
Quantitative Trading Framework — Wave 2: Institutional Grade
═══════════════════════════════════════════════════════════════════
Instrument : NIFTY
Capital    : INR 1,500,000
Timeframes : 1m, 5m, 15m
Data       : 5 years historical (Parquet cached, realistic synthetic)
═══════════════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict
from itertools import product
import sqlite3
import os, time, warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

@dataclass
class Config:
    symbol: str = "NIFTY"
    initial_capital: float = 1500000.0
    risk_per_trade_pct: float = 0.01       # 1% per trade
    commission_pct: float = 0.05           # 0.05% per side
    slippage_pct: float = 0.05
    tick_size: float = 0.05
    lot_size: int = 75                     # NIFTY lot size
    
    timeframes: List[str] = field(default_factory=lambda: ["5m"])
    data_years: int = 5
    
    # ATR SL/TP defaults
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    
    # Position Management
    use_break_even: bool = True
    break_even_trigger_atr: float = 1.0    # Move SL to entry after 1 ATR profit
    use_trailing_stop: bool = True
    trail_atr_mult: float = 2.0
    max_holding_bars: int = 100            # Time stop
    
    # Time filter (IST market hours)
    market_open: str = "09:15"
    market_close: str = "15:30"
    avoid_open_minutes: int = 15
    avoid_close_minutes: int = 15
    
    # Walk-forward
    train_split_pct: float = 0.7

CFG = Config()

# ═══════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════

class DataManager:
    """
    Loads / caches OHLCV data. 
    Uses Parquet for fast I/O. Generates realistic synthetic data if API not connected.
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
        
        path = f"{self.CACHE_DIR}/{self.symbol}_{tf}.parquet"
        if os.path.exists(path):
            df = pd.read_parquet(path)
        else:
            print(f"  [Data] No cache found for {self.symbol} {tf}. Generating realistic synthetic...")
            df = self._generate_realistic(tf)
            df.to_parquet(path)
        self._cache[tf] = df
        return df

    def _generate_realistic(self, tf: str) -> pd.DataFrame:
        """Generate GBM-based synthetic data with correct calendar and vol."""
        bar_minutes = {"1m": 1, "5m": 5, "15m": 15}.get(tf, 5)
        
        # Generate business days for N years
        sessions = pd.bdate_range(start=datetime.now() - timedelta(days=self.years * 365), 
                                  end=datetime.now())
        
        # Generate datetime index for trading hours only
        idx_list = []
        for d in sessions:
            for t in pd.date_range(d + pd.Timedelta("9:15:00"), 
                                   d + pd.Timedelta("15:30:00"), 
                                   freq=f"{bar_minutes}min"):
                idx_list.append(t)
        idx = pd.DatetimeIndex(idx_list)
        
        # Realistic NIFTY params: ~15% annual vol, 10% annual drift
        bars_per_year = 252 * 75
        bar_vol = 0.15 / np.sqrt(bars_per_year)
        bar_drift = 0.10 / bars_per_year
        
        np.random.seed(42)
        returns = np.random.normal(bar_drift, bar_vol, len(idx))
        
        # Add intraday seasonality (higher vol in morning/evening)
        seasonality = np.ones(len(idx))
        times = idx.time
        seasonality[(times < datetime.strptime("10:00", "%H:%M").time())] = 1.5
        seasonality[(times > datetime.strptime("14:30", "%H:%M").time())] = 1.5
        returns = returns * seasonality
        
        prices = 18000 * np.exp(np.cumsum(returns))
        
        # Construct OHLC
        df = pd.DataFrame(index=idx, columns=["open", "high", "low", "close", "volume"])
        df["close"] = prices
        df["open"] = df["close"].shift(1).fillna(18000)
        
        # Intraday high/low
        intrabar_vol = bar_vol * 0.5
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.normal(0, intrabar_vol, len(idx))))
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.normal(0, intrabar_vol, len(idx))))
        df["volume"] = np.random.randint(10000, 500000, len(idx))
        
        return df.dropna()

# ═══════════════════════════════════════════════════════════════
# INDICATORS & STRATEGY REGISTRY
# ═══════════════════════════════════════════════════════════════

def calc_atr(df, period):
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period).mean()

def calc_adx(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=period).mean()
    
    plus_di = 100 * (plus_dm.ewm(span=period).mean() / atr)
    minus_di = 100 * (minus_dm.abs().ewm(span=period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return dx.ewm(span=period).mean()

@dataclass
class StrategyResult:
    signals: pd.Series
    meta: Dict[str, Any] = field(default_factory=dict)

class Strategy:
    name: str = "base"
    category: str = "unknown"
    params: Dict[str, Any] = {}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        raise NotImplementedError

STRATEGY_REGISTRY: Dict[str, type] = {}

def register(cls):
    STRATEGY_REGISTRY[cls.name] = cls
    return cls

@register
class EMACrossover(Strategy):
    name = "ema_crossover"
    category = "trend_following"
    params = {"fast": 9, "slow": 21}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        fast = df["close"].ewm(span=self.params["fast"]).mean()
        slow = df["close"].ewm(span=self.params["slow"]).mean()
        
        # Event-based crossover
        cross_up = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
        cross_dn = (fast.shift(1) >= slow.shift(1)) & (fast < slow)
        
        signals = pd.Series(0, index=df.index)
        signals[cross_up] = 1
        signals[cross_dn] = -1
        return StrategyResult(signals, {"fast": fast, "slow": slow})

@register
class Supertrend(Strategy):
    name = "supertrend"
    category = "trend_following"
    params = {"atr_period": 10, "multiplier": 3.0}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        atr = calc_atr(df, self.params["atr_period"])
        hl2 = (df["high"] + df["low"]) / 2
        upper_basic = hl2 + self.params["multiplier"] * atr
        lower_basic = hl2 - self.params["multiplier"] * atr

        final_upper = upper_basic.copy()
        final_lower = lower_basic.copy()
        st = pd.Series(index=df.index, dtype=float)
        trend = pd.Series(1, index=df.index, dtype=int)

        for i in range(1, len(df)):
            if (upper_basic.iloc[i] < final_upper.iloc[i-1]) or (df["close"].iloc[i-1] > final_upper.iloc[i-1]):
                final_upper.iloc[i] = upper_basic.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]

            if (lower_basic.iloc[i] > final_lower.iloc[i-1]) or (df["close"].iloc[i-1] < final_lower.iloc[i-1]):
                final_lower.iloc[i] = lower_basic.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

            if st.iloc[i-1] == final_upper.iloc[i-1] and df["close"].iloc[i] <= final_upper.iloc[i]:
                trend.iloc[i] = -1
                st.iloc[i] = final_upper.iloc[i]
            elif st.iloc[i-1] == final_upper.iloc[i-1] and df["close"].iloc[i] > final_upper.iloc[i]:
                trend.iloc[i] = 1
                st.iloc[i] = final_lower.iloc[i]
            elif st.iloc[i-1] == final_lower.iloc[i-1] and df["close"].iloc[i] >= final_lower.iloc[i]:
                trend.iloc[i] = 1
                st.iloc[i] = final_lower.iloc[i]
            elif st.iloc[i-1] == final_lower.iloc[i-1] and df["close"].iloc[i] < final_lower.iloc[i]:
                trend.iloc[i] = -1
                st.iloc[i] = final_upper.iloc[i]
            else:
                trend.iloc[i] = trend.iloc[i-1]
                st.iloc[i] = final_lower.iloc[i] if trend.iloc[i] == 1 else final_upper.iloc[i]

        signals = pd.Series(0, index=df.index)
        signals[(trend == 1) & (trend.shift(1) == -1)] = 1
        signals[(trend == -1) & (trend.shift(1) == 1)] = -1
        
        return StrategyResult(signals, {"supertrend": st, "trend": trend})

@register
class MACDStrategy(Strategy):
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
        signals[(hist.shift(1) <= 0) & (hist > 0)] = 1
        signals[(hist.shift(1) >= 0) & (hist < 0)] = -1
        return StrategyResult(signals, {"macd": macd_line, "signal": signal_line})


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

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

class EnhancedBacktester:
    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg

    def _apply_slippage(self, price, side, is_entry=True):
        slip = self.cfg.slippage_pct / 100
        if is_entry:
            return price * (1 + slip * side)  # Buy(+1)->pay more, Sell(-1)->recv less
        else:
            return price * (1 - slip * side)  # Exit Long(+1)->recv less, Exit Short(-1)->pay more

    def _round_tick(self, price):
        return round(price / self.cfg.tick_size) * self.cfg.tick_size

    def _is_good_time(self, dt: pd.Timestamp) -> bool:
        cfg = self.cfg
        t = dt.time()
        if t < pd.Timestamp(cfg.market_open).time() or t >= pd.Timestamp(cfg.market_close).time():
            return False
        if t < (pd.Timestamp(cfg.market_open) + timedelta(minutes=cfg.avoid_open_minutes)).time():
            return False
        if t >= (pd.Timestamp(cfg.market_close) - timedelta(minutes=cfg.avoid_close_minutes)).time():
            return False
        return True

    def run(self, df: pd.DataFrame, strategy: Strategy, is_oos: bool = False) -> Dict[str, Any]:
        cfg = self.cfg
        result = strategy.generate(df)
        raw_signals = result.signals
        atr = calc_atr(df, cfg.atr_period)

        trades: List[Trade] = []
        equity = cfg.initial_capital
        equity_curve = []
        
        position = 0
        entry_price = 0.0
        sl_price = 0.0
        tp_price = 0.0
        entry_bar_idx = -1
        qty = 0
        max_favorable = 0.0

        for i in range(1, len(df)):
            idx = df.index[i]
            dt = pd.Timestamp(idx)
            price_open = self._round_tick(df["open"].iloc[i])
            price_close = self._round_tick(df["close"].iloc[i])
            price_high = self._round_tick(df["high"].iloc[i])
            price_low = self._round_tick(df["low"].iloc[i])
            atr_val = atr.iloc[i] if pd.notna(atr.iloc[i]) else 0
            signal = raw_signals.iloc[i - 1]  # act on previous bar's signal

            if not self._is_good_time(dt):
                signal = 0

            # --- Manage Open Position ---
            if position != 0:
                exit_now = False
                exit_reason = ""
                exit_price = price_close

                # 1. Gap Risk Check
                if position == 1 and price_open <= sl_price:
                    exit_price = price_open
                    exit_now, exit_reason = True, "SL_GAP"
                elif position == -1 and price_open >= sl_price:
                    exit_price = price_open
                    exit_now, exit_reason = True, "SL_GAP"
                elif position == 1 and price_open >= tp_price:
                    exit_price = price_open
                    exit_now, exit_reason = True, "TP_GAP"
                elif position == -1 and price_open <= tp_price:
                    exit_price = price_open
                    exit_now, exit_reason = True, "TP_GAP"
                
                # 2. Intraday SL/TP Check
                if not exit_now:
                    if position == 1:
                        if price_low <= sl_price: exit_price, exit_now, exit_reason = sl_price, True, "SL"
                        elif price_high >= tp_price: exit_price, exit_now, exit_reason = tp_price, True, "TP"
                    else:
                        if price_high >= sl_price: exit_price, exit_now, exit_reason = sl_price, True, "SL"
                        elif price_low <= tp_price: exit_price, exit_now, exit_reason = tp_price, True, "TP"

                # 3. Trailing Stop / Break-Even
                if not exit_now and cfg.use_trailing_stop and atr_val > 0:
                    current_fav = (price_high - entry_price) if position == 1 else (entry_price - price_low)
                    if current_fav > max_favorable:
                        max_favorable = current_fav
                    
                    # Break-even
                    if cfg.use_break_even and max_favorable >= atr_val * cfg.break_even_trigger_atr:
                        new_sl = entry_price + (1 if position == 1 else -1)
                        if (position == 1 and new_sl > sl_price) or (position == -1 and new_sl < sl_price):
                            sl_price = self._round_tick(new_sl)
                    
                    # ATR Trail
                    trail_sl = (price_high - atr_val * cfg.trail_atr_mult) if position == 1 else (price_low + atr_val * cfg.trail_atr_mult)
                    if (position == 1 and trail_sl > sl_price) or (position == -1 and trail_sl < sl_price):
                        sl_price = self._round_tick(trail_sl)

                # 4. Time Stop
                bars_held = i - entry_bar_idx
                if not exit_now and bars_held >= cfg.max_holding_bars:
                    exit_price = price_close
                    exit_now, exit_reason = True, "TIME"

                # 5. Signal Reversal
                if not exit_now and signal != 0 and signal != position:
                    exit_price = price_open
                    exit_now, exit_reason = True, "REV"

                # 6. EOD Exit
                if not exit_now and dt.time() >= (pd.Timestamp(cfg.market_close) - timedelta(minutes=1)).time():
                    exit_price = price_close
                    exit_now, exit_reason = True, "EOD"

                # Execute Exit
                if exit_now:
                    exit_price_slipped = self._round_tick(self._apply_slippage(exit_price, position, is_entry=False))
                    gross_pnl = (exit_price_slipped - entry_price) * qty * position
                    commission = self._commission(entry_price * qty) + self._commission(exit_price_slipped * qty)
                    pnl = gross_pnl - commission
                    equity += pnl
                    
                    trades.append(Trade(
                        entry_time=df.index[entry_bar_idx], exit_time=idx,
                        side="LONG" if position == 1 else "SHORT",
                        entry_price=entry_price, exit_price=exit_price_slipped,
                        qty=qty, pnl=round(pnl, 2), pnl_pct=round(pnl/(entry_price*qty)*100, 2),
                        exit_reason=exit_reason, bars_held=bars_held
                    ))
                    position = 0
                    signal = 0

            # --- Enter New Position ---
            if position == 0 and signal != 0 and atr_val > 0:
                entry_price = self._round_tick(self._apply_slippage(price_open, signal, is_entry=True))
                stop_distance = atr_val * cfg.sl_atr_mult
                
                # Capital & Risk Position Sizing
                risk_per_trade = equity * cfg.risk_per_trade_pct
                risk_qty = int(risk_per_trade / stop_distance)
                capital_qty = int(equity * 0.95 / entry_price)  # 95% max cash deployment
                
                qty = min(risk_qty, capital_qty)
                qty = (qty // cfg.lot_size) * cfg.lot_size  # Enforce lot size
                
                if qty > 0:
                    sl_price = entry_price - (stop_distance * signal)
                    tp_price = entry_price + (atr_val * cfg.tp_atr_mult * signal)
                    sl_price = self._round_tick(sl_price)
                    tp_price = self._round_tick(tp_price)
                    
                    position = signal
                    entry_bar_idx = i
                    max_favorable = 0.0

            # Mark-to-Market Equity
            if position != 0:
                unrealized = (price_close - entry_price) * qty * position
                equity_curve.append(equity + unrealized)
            else:
                equity_curve.append(equity)

        eq_series = pd.Series(equity_curve, index=df.index[:len(equity_curve)])
        metrics = self._compute_metrics(trades, eq_series, df.index[0], df.index[-1])
        return {"trades": trades, "equity_curve": eq_series, "metrics": metrics, "is_oos": is_oos}

    def _commission(self, notional):
        return notional * self.cfg.commission_pct / 100

    def _compute_metrics(self, trades, equity, start_dt, end_dt):
        if not trades:
            # Added cagr, calmar, sqn to prevent KeyError
            return {
                "total_trades": 0, "win_rate": 0, "total_pnl": 0, 
                "profit_factor": 0, "sharpe": 0, "sortino": 0, 
                "max_dd_pct": 0, "cagr": 0, "calmar": 0, "sqn": 0
            }
            
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t.pnl for t in trades)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        pf = gross_profit / gross_loss
        
        returns = equity.pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 75)) if returns.std() > 0 else 0
        
        downside = returns[returns < 0]
        sortino = (returns.mean() / downside.std() * np.sqrt(252 * 75)) if len(downside) > 0 and downside.std() > 0 else 0
        
        peak = equity.cummax()
        dd = (equity - peak) / peak * 100
        max_dd = dd.min()
        
        # CAGR & Calmar
        days = (end_dt - start_dt).days
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (365/days) - 1 if days > 0 and equity.iloc[0] > 0 else 0
        calmar = cagr / abs(max_dd / 100) if max_dd < 0 else 0
        
        # SQN
        pnls = np.array([t.pnl for t in trades])
        sqn = np.sqrt(len(trades)) * (pnls.mean() / pnls.std()) if pnls.std() > 0 else 0
        
        return {
            "total_trades": len(trades), "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2), "profit_factor": round(pf, 2),
            "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
            "max_dd_pct": round(max_dd, 2), "cagr": round(cagr*100, 2),
            "calmar": round(calmar, 2), "sqn": round(sqn, 2)
        }

# ═══════════════════════════════════════════════════════════════
# OPTIMIZER & DB
# ═══════════════════════════════════════════════════════════════

class ResearchDB:
    def __init__(self, path="research.db"):
        self.conn = sqlite3.connect(path)
        self._init_db()
        
    def _init_db(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY, timestamp TEXT, strategy TEXT, tf TEXT,
            params TEXT, is_oos INTEGER, pf REAL, sharpe REAL, sortino REAL,
            max_dd REAL, cagr REAL, calmar REAL, sqn REAL
        )''')
        
    def save_result(self, strategy, tf, params, is_oos, metrics):
        self.conn.execute('''INSERT INTO experiments 
            (timestamp, strategy, tf, params, is_oos, pf, sharpe, sortino, max_dd, cagr, calmar, sqn)
            VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (strategy, tf, json.dumps(params), int(is_oos), 
             metrics.get('profit_factor', 0), metrics.get('sharpe', 0), metrics.get('sortino', 0),
             metrics.get('max_dd_pct', 0), metrics.get('cagr', 0), metrics.get('calmar', 0), metrics.get('sqn', 0)))
        self.conn.commit()

class GridSearchOptimizer:
    def __init__(self, data_loader: DataManager, cfg: Config = CFG, db: ResearchDB = None):
        self.loader = data_loader
        self.cfg = cfg
        self.bt = EnhancedBacktester(cfg)
        self.db = db
        self.results = []

    def search(self, strategies, timeframes, param_grids, walk_forward=True):
        self.results = []
        for tf in timeframes:
            df = self.loader.load(tf)
            split_idx = int(len(df) * self.cfg.train_split_pct) if walk_forward else len(df)
            train_df = df.iloc[:split_idx]
            test_df = df.iloc[split_idx:] if walk_forward else None

            for sname in strategies:
                cls = STRATEGY_REGISTRY[sname]
                grid = param_grids.get(sname, {})
                keys = list(grid.keys())
                
                for values in product(*grid.values()):
                    params = dict(zip(keys, values))
                    s = cls()
                    s.params = params
                    
                    # Train (In-Sample)
                    res_train = self.bt.run(train_df, s, is_oos=False)
                    m_train = res_train["metrics"]
                    
                    # Test (Out-of-Sample)
                    m_test = {}
                    if walk_forward and test_df is not None and len(test_df) > 0:
                        res_test = self.bt.run(test_df, s, is_oos=True)
                        m_test = res_test["metrics"]
                        
                    self.results.append({
                        "strategy": sname, "tf": tf, "params": params,
                        "train": m_train, "test": m_test
                    })
                    
                    if self.db:
                        self.db.save_result(sname, tf, params, 0, m_train)
                        if m_test: self.db.save_result(sname, tf, params, 1, m_test)
                        
                    print(f"  [{sname} @ {tf} {params}] IS PF: {m_train.get('profit_factor', 0):.2f} | OOS PF: {m_test.get('profit_factor', 0):.2f}")

        self.results.sort(key=lambda x: x["train"].get("profit_factor", 0), reverse=True)
        return self.results

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
import json

def main():
    print("=" * 60)
    print("  QUANT FRAMEWORK — Wave 2: Institutional Grade")
    print("=" * 60)

    loader = DataManager(symbol=CFG.symbol, years=CFG.data_years)
    db = ResearchDB()
    
    # Load data
    df_5m = loader.load("5m")
    print(f"  5m: {len(df_5m):,} bars ({df_5m.index[0].date()} → {df_5m.index[-1].date()})")
    
    # Smoke Test
    print("\n🧪 Smoke test: EMA Crossover on 5m (Realistic Sizing)...")
    strat = EMACrossover()
    bt = EnhancedBacktester(CFG)
    res = bt.run(df_5m, strat)
    m = res["metrics"]
    print(f"  Trades: {m['total_trades']} | Win%: {m['win_rate']}% | PF: {m['profit_factor']}")
    print(f"  PnL: ₹{m['total_pnl']:,.0f} | MaxDD: {m['max_dd_pct']:.2f}% | Sharpe: {m['sharpe']}")
    print(f"  Sortino: {m['sortino']} | Calmar: {m['calmar']} | SQN: {m['sqn']}")

    # Grid Search
    print("\n🔍 GRID SEARCH — Walk-Forward (Train 70% / Test 30%)")
    grids = {
        "ema_crossover": {"fast": [9, 20], "slow": [21, 50]},
        "supertrend": {"atr_period": [10, 14], "multiplier": [2.0, 3.0]},
        "macd": {"fast": [12], "slow": [26], "signal": [9]}
    }
    
    opt = GridSearchOptimizer(loader, CFG, db)
    opt.search(["ema_crossover", "supertrend", "macd"], ["5m"], grids, walk_forward=True)
    
    print("\n🏆 TOP CONFIGURATIONS (Ranked by In-Sample PF)")
    print("-" * 80)
    for r in opt.results[:5]:
        t_train, t_test = r["train"], r["test"]
        print(f"  {r['strategy']} {r['params']}")
        print(f"    IS  -> PF: {t_train['profit_factor']:.2f} | DD: {t_train['max_dd_pct']:.2f}% | CAGR: {t_train['cagr']}%")
        print(f"    OOS -> PF: {t_test.get('profit_factor', 0):.2f} | DD: {t_test.get('max_dd_pct', 0):.2f}% | CAGR: {t_test.get('cagr', 0)}%")
        
    print("\n✅ Results saved to SQLite database (research.db).")

if __name__ == "__main__":
    main()