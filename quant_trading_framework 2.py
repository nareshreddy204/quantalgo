#!/usr/bin/env python3
"""
Quantitative Trading Framework — Wave 6: YAML Config Integration
═══════════════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Any
from itertools import product
import sqlite3
import os, json, warnings, yaml
from datetime import datetime, timedelta
import pandas_ta as ta
import time

try:
    
    PANDAS_TA_INSTALLED = True
except ImportError:
    PANDAS_TA_INSTALLED = False

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# CONFIG LOADER
# ═══════════════════════════════════════════════════════════════

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config.setdefault("fyers", {})["access_token"] = f.read().strip()
    return config

@dataclass
class Config:
    # Core symbols/capital
    symbol: str = "NIFTY"
    initial_capital: float = 2000_000.0
    risk_per_trade_pct: float = 0.01
    commission_pct: float = 0.05
    slippage_pct: float = 0.05
    tick_size: float = 0.05
    lot_size: int = 75
    
    timeframes: List[str] = field(default_factory=lambda: ["5m"])
    data_years: int = 5
    
    # Strategy / Risk Management
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
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
    
    # Fyers API Credentials
    fyers_token: str = ""
    fyers_app_id: str = ""
    use_real_data: bool = False

# Load YAML config and map it to our Config dataclass
_raw_cfg = load_config()
_fyers_cfg = _raw_cfg.get("fyers", {})
_risk_cfg = _raw_cfg.get("risk", {})
_backtest_cfg = _raw_cfg.get("backtest", {})

CFG = Config(
    symbol=_raw_cfg.get("symbol", "NIFTY"),
    initial_capital=float(_raw_cfg.get("initial_capital", 2_000_000.0)),
    risk_per_trade_pct=float(_risk_cfg.get("max_capital_per_trade", 0.01)),
    slippage_pct=float(_backtest_cfg.get("slippage_points", 0.05)),
    commission_pct=float(_backtest_cfg.get("commission_pct", 0.05)),
    fyers_token=_fyers_cfg.get("access_token", ""),
    fyers_app_id=_fyers_cfg.get("app_id", ""),
    use_real_data=_fyers_cfg.get("use_real_data", False)
)
# ═══════════════════════════════════════════════════════════════
# DATA LAYER (with FYERS API Integration)
# ═══════════════════════════════════════════════════════════════

class DataManager:
    CACHE_DIR = "./data_cache"

    def __init__(self, symbol: str = "NIFTY", years: int = 5, cfg: Config = CFG):
        self.symbol = symbol
        self.years = years
        self.cfg = cfg
        self._cache: Dict[str, pd.DataFrame] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def load(self, tf: str) -> pd.DataFrame:
        if tf in self._cache:
            return self._cache[tf]
        
        path = f"{self.CACHE_DIR}/{self.symbol}_{tf}.parquet"
        if os.path.exists(path):
            print(f"  [Data] Loading cached Parquet for {self.symbol} {tf}...")
            df = pd.read_parquet(path)
        else:
            if self.cfg.use_real_data:
                if not self.cfg.fyers_token:
                    raise ValueError("Fyers token is missing! Paste your token in token.txt.")
                print(f"  [Data] Fetching real data from Fyers for {self.symbol} {tf}...")
                df = self._fetch_fyers_data(tf)
                df.to_parquet(path)
                print(f"  [Data] Saved to Parquet cache: {path}")
            else:
                print(f"  [Data] No cache found. Generating realistic synthetic for {self.symbol} {tf}...")
                df = self._generate_realistic(tf)
                df.to_parquet(path)
                
        self._cache[tf] = df
        return df

    def _fetch_fyers_data(self, tf: str) -> pd.DataFrame:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError:
            from fyers_api import fyersModel
        
        import inspect
        
        resolution = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240"}[tf]
        
        # Dynamically detect what arguments the FyersModel accepts
        sig_params = inspect.signature(fyersModel.FyersModel).parameters
        init_kwargs = {
            "is_async": False, 
            "log_path": os.getcwd(),
            "client_id": self.cfg.fyers_app_id
        }
        
        # If the constructor accepts 'token', pass it there
        if "token" in sig_params:
            init_kwargs["token"] = self.cfg.fyers_token
            
        fyers = fyersModel.FyersModel(**init_kwargs)
        
        # If it didn't accept 'token' in the constructor, set it via method or attribute
        if "token" not in sig_params:
            if hasattr(fyers, "set_token"):
                fyers.set_token(self.cfg.fyers_token)
            elif hasattr(fyers, "set_access_token"):
                fyers.set_access_token(self.cfg.fyers_token)
            else:
                # Fallback to direct attribute assignment
                fyers.token = self.cfg.fyers_token
                fyers.access_token = self.cfg.fyers_token
                
        # ... (The rest of the method: end_date, while loop, etc. remains exactly the same)
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.years * 365)       
        
        
        all_candles = []
        current_start = start_date
        
        print(f"  [Fyers] Downloading {self.years} years of data in chunks (this may take a minute)...")
        
        while current_start < end_date:
            current_end = min(current_start + timedelta(days=59), end_date)
            range_from = current_start.strftime("%Y-%m-%d")
            range_to = current_end.strftime("%Y-%m-%d")
            
            data = {
                "symbol": self.symbol,  # Now resolves to NSE:NIFTY50-INDEX
                "resolution": resolution,
                "date_format": "1",
                "range_from": range_from,
                "range_to": range_to,
                "cont_flag": "1"
            }
            
            try:
                response = fyers.history(data)
                if "candles" in response and response["candles"]:
                    all_candles.extend(response["candles"])
                    print(f"    Fetched {range_from} → {range_to}: {len(response['candles'])} bars")
                else:
                    print(f"    No data for {range_from} → {range_to}. Response: {response.get('message', '')}")
            except Exception as e:
                print(f"    Error fetching {range_from}: {e}")
                
            current_start = current_end + timedelta(days=1)
            
            # Add a 1-second sleep to avoid hitting Fyers API rate limits (100 req/min)
            time.sleep(1)
            
        if not all_candles:
            raise ValueError("No data fetched from Fyers. Check token/symbol.")
            
        df = pd.DataFrame(all_candles, columns=["epoch", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
        df.set_index("datetime", inplace=True)
        df.drop(columns=["epoch"], inplace=True)
        df = df[~df.index.duplicated(keep='first')]
        return df

    def _generate_realistic(self, tf: str) -> pd.DataFrame:
        bar_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(tf, 5)
        sessions = pd.bdate_range(start=datetime.now() - timedelta(days=self.years * 365), end=datetime.now())
        
        idx_list = []
        for d in sessions:
            for t in pd.date_range(d + pd.Timedelta("9:15:00"), d + pd.Timedelta("15:30:00"), freq=f"{bar_minutes}min"):
                idx_list.append(t)
        idx = pd.DatetimeIndex(idx_list)
        
        bars_per_year = 252 * 75
        bar_vol = 0.15 / np.sqrt(bars_per_year)
        bar_drift = 0.10 / bars_per_year
        
        np.random.seed(42)
        returns = np.random.normal(bar_drift, bar_vol, len(idx))
        
        seasonality = np.ones(len(idx))
        times = idx.time
        seasonality[(times < datetime.strptime("10:00", "%H:%M").time())] = 1.5
        seasonality[(times > datetime.strptime("14:30", "%H:%M").time())] = 1.5
        returns = returns * seasonality
        
        prices = 18000 * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame(index=idx, columns=["open", "high", "low", "close", "volume"])
        df["close"] = prices
        df["open"] = df["close"].shift(1).fillna(18000)
        
        intrabar_vol = bar_vol * 0.5
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.normal(0, intrabar_vol, len(idx))))
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.normal(0, intrabar_vol, len(idx))))
        df["volume"] = np.random.randint(10000, 500000, len(idx))
        
        return df.dropna()


# ═══════════════════════════════════════════════════════════════
# CUSTOM SMC INDICATORS
# ═══════════════════════════════════════════════════════════════

def find_fvg(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['Bullish_FVG'] = (df['low'] > df['high'].shift(2)) & (df['low'].shift(1) > df['high'].shift(2))
    df['Bearish_FVG'] = (df['high'] < df['low'].shift(2)) & (df['high'].shift(1) < df['low'].shift(2))
    return df

# ═══════════════════════════════════════════════════════════════
# STRATEGY REGISTRY
# ═══════════════════════════════════════════════════════════════

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
        if PANDAS_TA_INSTALLED:
            fast = ta.ema(df["close"], length=self.params["fast"])
            slow = ta.ema(df["close"], length=self.params["slow"])
        else:
            fast = df["close"].ewm(span=self.params["fast"]).mean()
            slow = df["close"].ewm(span=self.params["slow"]).mean()
        
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
        if PANDAS_TA_INSTALLED:
            st_df = ta.supertrend(df["high"], df["low"], df["close"], 
                                  length=self.params["atr_period"], multiplier=self.params["multiplier"])
            dir_col = [c for c in st_df.columns if c.startswith("SUPERTd_")][0]
            trend = st_df[dir_col]
        else:
            trend = pd.Series(1, index=df.index, dtype=int)
            
        signals = pd.Series(0, index=df.index)
        signals[(trend == 1) & (trend.shift(1) == -1)] = 1
        signals[(trend == -1) & (trend.shift(1) == 1)] = -1
        return StrategyResult(signals, {"trend": trend})

@register
class SupertrendMACD(Strategy):
    """Hybrid: Supertrend direction + MACD histogram confirmation."""
    name = "supertrend_macd"
    category = "hybrid"
    params = {"atr_period": 10, "multiplier": 3.0, "fast": 12, "slow": 26, "signal": 9}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        st_df = ta.supertrend(df["high"], df["low"], df["close"], 
                              length=self.params["atr_period"], multiplier=self.params["multiplier"])
        dir_col = [c for c in st_df.columns if c.startswith("SUPERTd_")][0]
        trend = st_df[dir_col]
        
        macd_df = ta.macd(df["close"], fast=self.params["fast"], slow=self.params["slow"], signal=self.params["signal"])
        hist_col = f"MACDh_{self.params['fast']}_{self.params['slow']}_{self.params['signal']}"
        hist = macd_df[hist_col]
        
        signals = pd.Series(0, index=df.index)
        # Buy: Supertrend is UP (1) and MACD hist crosses above 0
        buy_cond = (trend == 1) & (hist.shift(1) <= 0) & (hist > 0)
        # Sell: Supertrend is DOWN (-1) and MACD hist crosses below 0
        sell_cond = (trend == -1) & (hist.shift(1) >= 0) & (hist < 0)
        
        signals[buy_cond] = 1
        signals[sell_cond] = -1
        return StrategyResult(signals, {"trend": trend, "hist": hist})
@register
class FVGStrategy(Strategy):
    name = "fvg_smc"
    category = "smc"
    params = {"lookback": 10}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        df = find_fvg(df)
        signals = pd.Series(0, index=df.index)
        
        bull_fvg_zone_high = df['high'].shift(2).where(df['Bullish_FVG'])
        active_bull_zone = bull_fvg_zone_high.rolling(window=self.params["lookback"], min_periods=1).max()
        bull_tap = (df['low'] <= active_bull_zone) & (df['close'] > active_bull_zone) & active_bull_zone.notna()
        signals[bull_tap] = 1
        
        bear_fvg_zone_low = df['low'].shift(2).where(df['Bearish_FVG'])
        active_bear_zone = bear_fvg_zone_low.rolling(window=self.params["lookback"], min_periods=1).min()
        bear_tap = (df['high'] >= active_bear_zone) & (df['close'] < active_bear_zone) & active_bear_zone.notna()
        signals[bear_tap] = -1
                    
        return StrategyResult(signals, {"fvg_bull": active_bull_zone, "fvg_bear": active_bear_zone})
    
@register
class LiquiditySwings(Strategy):
    """SMC Break of Structure: Breakout of recent swing high/low."""
    name = "liquidity_swings"
    category = "smc"
    params = {"length": 14}

    def generate(self, df: pd.DataFrame) -> StrategyResult:
        swing_high = df['high'].rolling(window=self.params["length"]).max().shift(1)
        swing_low = df['low'].rolling(window=self.params["length"]).min().shift(1)
        
        # Detect the EXACT bar of the breakout (not every bar after)
        breakout_up = df['close'] > swing_high
        breakout_dn = df['close'] < swing_low
        
        signals = pd.Series(0, index=df.index)
        # Only signal if it's crossing the line on this specific candle
        signals[breakout_up & ~breakout_up.shift(1, fill_value=False)] = 1
        signals[breakout_dn & ~breakout_dn.shift(1, fill_value=False)] = -1
        
        return StrategyResult(signals, {"swing_high": swing_high, "swing_low": swing_low})

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
        self.open_time = pd.Timestamp(cfg.market_open).time()
        self.close_time = pd.Timestamp(cfg.market_close).time()
        self.open_avoid_time = (pd.Timestamp(cfg.market_open) + timedelta(minutes=cfg.avoid_open_minutes)).time()
        self.close_avoid_time = (pd.Timestamp(cfg.market_close) - timedelta(minutes=cfg.avoid_close_minutes)).time()

    def _apply_slippage(self, price, side, is_entry=True):
        slip = self.cfg.slippage_pct / 100
        if is_entry: return price * (1 + slip * side)
        else: return price * (1 - slip * side)

    def _round_tick(self, price):
        return round(price / self.cfg.tick_size) * self.cfg.tick_size

    def _is_good_time(self, t) -> bool:
        if t < self.open_time or t >= self.close_time: return False
        if t < self.open_avoid_time: return False
        if t >= self.close_avoid_time: return False
        return True

    def run(self, df: pd.DataFrame, strategy: Strategy, is_oos: bool = False) -> Dict[str, Any]:
        cfg = self.cfg
        result = strategy.generate(df)
        
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df.index.time
        raw_signals = result.signals.values
        
        if PANDAS_TA_INSTALLED:
            atr_arr = ta.atr(df["high"], df["low"], df["close"], length=cfg.atr_period).values
        else:
            h, l, c = df["high"], df["low"], df["close"].shift(1)
            tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
            atr_arr = tr.ewm(span=cfg.atr_period).mean().values

        trades: List[Trade] = []
        equity = cfg.initial_capital
        equity_curve = np.zeros(len(df))
        
        position = 0
        entry_price = 0.0
        sl_price = 0.0
        tp_price = 0.0
        entry_bar_idx = -1
        qty = 0
        max_favorable = 0.0

        for i in range(1, len(df)):
            t = times[i]
            price_open = round(opens[i] / cfg.tick_size) * cfg.tick_size
            price_close = round(closes[i] / cfg.tick_size) * cfg.tick_size
            price_high = round(highs[i] / cfg.tick_size) * cfg.tick_size
            price_low = round(lows[i] / cfg.tick_size) * cfg.tick_size
            atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
            signal = raw_signals[i - 1]

            if not self._is_good_time(t):
                signal = 0

            if position != 0:
                exit_now = False
                exit_reason = ""
                exit_price = price_close

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
                    current_fav = (price_high - entry_price) if position == 1 else (entry_price - price_low)
                    if current_fav > max_favorable: max_favorable = current_fav
                    
                    if cfg.use_break_even and max_favorable >= atr_val * cfg.break_even_trigger_atr:
                        new_sl = entry_price + (1 if position == 1 else -1)
                        if (position == 1 and new_sl > sl_price) or (position == -1 and new_sl < sl_price):
                            sl_price = round(new_sl / cfg.tick_size) * cfg.tick_size
                    
                    trail_sl = (price_high - atr_val * cfg.trail_atr_mult) if position == 1 else (price_low + atr_val * cfg.trail_atr_mult)
                    if (position == 1 and trail_sl > sl_price) or (position == -1 and trail_sl < sl_price):
                        sl_price = round(trail_sl / cfg.tick_size) * cfg.tick_size

                bars_held = i - entry_bar_idx
                if not exit_now and bars_held >= cfg.max_holding_bars:
                    exit_price, exit_now, exit_reason = price_close, True, "TIME"

                if not exit_now and signal != 0 and signal != position:
                    exit_price, exit_now, exit_reason = price_open, True, "REV"

                if not exit_now and t >= (pd.Timestamp(cfg.market_close) - timedelta(minutes=1)).time():
                    exit_price, exit_now, exit_reason = price_close, True, "EOD"

                if exit_now:
                    exit_price_slipped = round(self._apply_slippage(exit_price, position, is_entry=False) / cfg.tick_size) * cfg.tick_size
                    gross_pnl = (exit_price_slipped - entry_price) * qty * position
                    commission = (entry_price * qty + exit_price_slipped * qty) * (cfg.commission_pct / 100)
                    pnl = gross_pnl - commission
                    equity += pnl
                    
                    trades.append(Trade(
                        entry_time=df.index[entry_bar_idx], exit_time=df.index[i],
                        side="LONG" if position == 1 else "SHORT",
                        entry_price=entry_price, exit_price=exit_price_slipped,
                        qty=qty, pnl=round(pnl, 2), pnl_pct=round(pnl/(entry_price*qty)*100, 2),
                        exit_reason=exit_reason, bars_held=bars_held
                    ))
                    position = 0
                    signal = 0

            if position == 0 and signal != 0 and atr_val > 0:
                entry_price = round(self._apply_slippage(price_open, signal, is_entry=True) / cfg.tick_size) * cfg.tick_size
                stop_distance = atr_val * cfg.sl_atr_mult
                
                risk_per_trade = equity * cfg.risk_per_trade_pct
                risk_qty = int(risk_per_trade / stop_distance)
                capital_qty = int(equity * 0.95 / entry_price)
                
                qty = min(risk_qty, capital_qty)
                qty = (qty // cfg.lot_size) * cfg.lot_size
                
                if qty > 0:
                    sl_price = entry_price - (stop_distance * signal)
                    tp_price = entry_price + (atr_val * cfg.tp_atr_mult * signal)
                    sl_price = round(sl_price / cfg.tick_size) * cfg.tick_size
                    tp_price = round(tp_price / cfg.tick_size) * cfg.tick_size
                    
                    position = signal
                    entry_bar_idx = i
                    max_favorable = 0.0

            if position != 0:
                unrealized = (price_close - entry_price) * qty * position
                equity_curve[i] = equity + unrealized
            else:
                equity_curve[i] = equity

        eq_series = pd.Series(equity_curve, index=df.index)
        metrics = self._compute_metrics(trades, eq_series, df.index[0], df.index[-1])
        return {"trades": trades, "equity_curve": eq_series, "metrics": metrics, "is_oos": is_oos}

    def _compute_metrics(self, trades, equity, start_dt, end_dt):
        if not trades:
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
        downside_std = downside.std()
        if np.isnan(downside_std) or downside_std == 0:
            sortino = 0
        else:
            sortino = (returns.mean() / downside_std * np.sqrt(252 * 75))
        
        peak = equity.cummax()
        dd = (equity - peak) / peak * 100
        max_dd = dd.min()
        
        days = (end_dt - start_dt).days
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (365/days) - 1 if days > 0 and equity.iloc[0] > 0 else 0
        calmar = cagr / abs(max_dd / 100) if max_dd < 0 else 0
        
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
                    
                    res_train = self.bt.run(train_df, s, is_oos=False)
                    m_train = res_train["metrics"]
                    
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

def main():
    print("=" * 60)
    print("  QUANT FRAMEWORK — Wave 7: Portfolio YAML Integration")
    print("=" * 60)

    db = ResearchDB()
    
    # Map Fyers resolution strings to our standard TF strings
    res_map = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h"}
    
    # Read symbols from config.yaml
    symbols_cfg = _raw_cfg.get("symbols", [])
    if not symbols_cfg:
        symbols_cfg = [{"symbol": CFG.symbol, "strategy": "ema_crossover", "resolution": "5"}]

    for sym_cfg in symbols_cfg:
        symbol = sym_cfg.get("symbol")
        strategy_name = sym_cfg.get("strategy")
        
        # Fallback to 'fvg_smc' if user just typed 'fvg' in YAML
        if strategy_name == "fvg":
            strategy_name = "fvg_smc"
            
        resolution = str(sym_cfg.get("resolution", "5"))
        tf = res_map.get(resolution, "5m")
        
        print(f"\n{'─' * 60}")
        print(f"📊 Processing {symbol} | Strategy: {strategy_name} | TF: {tf}")
        print(f"{'─' * 60}")
        
        try:
            loader = DataManager(symbol=symbol, years=CFG.data_years, cfg=CFG)
            df = loader.load(tf)
            print(f"  Data: {len(df):,} bars ({df.index[0].date()} → {df.index[-1].date()})")
            
            if strategy_name in STRATEGY_REGISTRY:
                strat_cls = STRATEGY_REGISTRY[strategy_name]
                strat = strat_cls()
                if "params" in sym_cfg:
                    strat.params.update(sym_cfg["params"])
                    
                # Override lot size if specified in YAML, else use default (75)
                if "lot_size" in sym_cfg:
                    CFG.lot_size = int(sym_cfg["lot_size"])
                else:
                    CFG.lot_size = 75
                    
                bt = EnhancedBacktester(CFG)
                
                # Walk-Forward Split
                split_idx = int(len(df) * CFG.train_split_pct)
                train_df = df.iloc[:split_idx]
                test_df = df.iloc[split_idx:]
                
                res_train = bt.run(train_df, strat, is_oos=False)
                m_train = res_train["metrics"]
                
                res_test = bt.run(test_df, strat, is_oos=True)
                m_test = res_test["metrics"]
                
                print(f"  🟢 IS  -> PF: {m_train['profit_factor']:.2f} | Win%: {m_train['win_rate']}% | DD: {m_train['max_dd_pct']:.2f}%")
                print(f"  🔵 OOS -> PF: {m_test['profit_factor']:.2f} | Win%: {m_test['win_rate']}% | DD: {m_test['max_dd_pct']:.2f}%")
                
                if db:
                    db.save_result(strategy_name, tf, strat.params, 0, m_train)
                    db.save_result(strategy_name, tf, strat.params, 1, m_test)
            else:
                print(f"  ⚠️ Strategy '{strategy_name}' not found in registry. Skipping.")
                
        except Exception as e:
            print(f"  ❌ Error processing {symbol}: {e}")

    print("\n✅ Portfolio backtest complete. Results saved to SQLite database.")

if __name__ == "__main__":
    main()