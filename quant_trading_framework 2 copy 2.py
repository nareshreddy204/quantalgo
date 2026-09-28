#!/usr/bin/env python3
"""
Quantitative Trading Framework — Wave 7: Modular SMC Research Engine
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
    if not os.path.exists("config.yaml"):
        return {
            "symbol": "NSE:NIFTY50-INDEX",
            "initial_capital": 2000000.0,
            "risk": {"max_capital_per_trade": 0.01},
            "backtest": {"slippage_points": 0.05, "commission_pct": 0.05},
            "fyers": {"app_id": "", "access_token": "", "use_real_data": False}
        }
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config.setdefault("fyers", {})["access_token"] = f.read().strip()
    return config

@dataclass
class Config:
    symbol: str = "NSE:NIFTY50-INDEX"
    initial_capital: float = 2000_000.0
    risk_per_trade_pct: float = 0.01
    commission_pct: float = 0.05
    slippage_pct: float = 0.05
    tick_size: float = 0.05
    lot_size: int = 75
    
    timeframes: List[str] = field(default_factory=lambda: ["5m"])
    data_years: int = 5
    
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
    
    fyers_token: str = ""
    fyers_app_id: str = ""
    use_real_data: bool = False
    
    sizing_method: str = "fixed"  # "fixed", "vol_adj", "kelly"

_raw_cfg = load_config()
_fyers_cfg = _raw_cfg.get("fyers", {})
_risk_cfg = _raw_cfg.get("risk", {})
_backtest_cfg = _raw_cfg.get("backtest", {})

CFG = Config(
    symbol=_raw_cfg.get("symbol", "NSE:NIFTY50-INDEX"),
    initial_capital=float(_raw_cfg.get("initial_capital", 2_000_000.0)),
    risk_per_trade_pct=float(_risk_cfg.get("max_capital_per_trade", 0.01)),
    slippage_pct=float(_backtest_cfg.get("slippage_points", 0.05)),
    commission_pct=float(_backtest_cfg.get("commission_pct", 0.05)),
    fyers_token=_fyers_cfg.get("access_token", ""),
    fyers_app_id=_fyers_cfg.get("app_id", ""),
    use_real_data=_fyers_cfg.get("use_real_data", False)
)

# ═══════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════

class DataManager:
    CACHE_DIR = "./data_cache"

    def __init__(self, symbol: str = "NSE:NIFTY50-INDEX", years: int = 5, cfg: Config = CFG):
        self.symbol = symbol
        self.years = years
        self.cfg = cfg
        self._cache: Dict[str, pd.DataFrame] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def load(self, tf: str) -> pd.DataFrame:
        if tf in self._cache:
            return self._cache[tf]
        
        clean_symbol = self.symbol.replace(":", "-").replace("/", "-")
        path = f"{self.CACHE_DIR}/{clean_symbol}_{tf}.parquet"
        if os.path.exists(path):
            print(f"  [Data] Loading cached Parquet for {self.symbol} {tf}...")
            df = pd.read_parquet(path)
        else:
            if self.cfg.use_real_data:
                if not self.cfg.fyers_token:
                    print("  [Data] Fyers token missing. Falling back to synthetic data.")
                    df = self._generate_realistic(tf)
                else:
                    try:
                        print(f"  [Data] Fetching real data from Fyers for {self.symbol} {tf}...")
                        df = self._fetch_fyers_data(tf)
                    except Exception as e:
                        print(f"  [Data] Fyers API failed: {e}. Falling back to synthetic data.")
                        df = self._generate_realistic(tf)
                df.to_parquet(path)
            else:
                print(f"  [Data] Generating realistic synthetic for {self.symbol} {tf}...")
                df = self._generate_realistic(tf)
                df.to_parquet(path)
                
        # CRITICAL FIX: Force timezone conversion to IST if data looks like UTC
        if df.index.tz is not None:
            df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
        elif df.index.hour.min() < 5:  # Heuristic: if hours are < 5, it's likely UTC
            print("  [Data] UTC timestamps detected. Converting to Asia/Kolkata (IST)...")
            df.index = df.index + timedelta(hours=5, minutes=30)
                
        self._cache[tf] = df
        return df

    def _fetch_fyers_data(self, tf: str) -> pd.DataFrame:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError:
            from fyers_api import fyersModel
        
        import inspect
        resolution = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240"}[tf]
        
        sig_params = inspect.signature(fyersModel.FyersModel).parameters
        init_kwargs = {"is_async": False, "log_path": os.getcwd(), "client_id": self.cfg.fyers_app_id}
        if "token" in sig_params:
            init_kwargs["token"] = self.cfg.fyers_token
            
        fyers = fyersModel.FyersModel(**init_kwargs)
        if "token" not in sig_params:
            if hasattr(fyers, "set_token"):
                fyers.set_token(self.cfg.fyers_token)
            elif hasattr(fyers, "set_access_token"):
                fyers.set_access_token(self.cfg.fyers_token)
            else:
                fyers.token = self.cfg.fyers_token
                fyers.access_token = self.cfg.fyers_token
                
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.years * 365)       
        all_candles = []
        current_start = start_date
        
        print(f"  [Fyers] Downloading {self.years} years of data...")
        
        while current_start < end_date:
            current_end = min(current_start + timedelta(days=59), end_date)
            range_from = current_start.strftime("%Y-%m-%d")
            range_to = current_end.strftime("%Y-%m-%d")
            
            data = {
                "symbol": self.symbol, "resolution": resolution, "date_format": "1",
                "range_from": range_from, "range_to": range_to, "cont_flag": "1"
            }
            try:
                response = fyers.history(data)
                if response is None:
                    print(f"    No response for {range_from} → {range_to}")
                elif "candles" in response and response["candles"]:
                    all_candles.extend(response["candles"])
                    print(f"    Fetched {range_from} → {range_to}: {len(response['candles'])} bars")
                else:
                    print(f"    No data for {range_from} → {range_to}. Response: {response}")
            except Exception as e:
                print(f"    Error fetching {range_from}: {e}")
            current_start = current_end + timedelta(days=1)
            time.sleep(1)
            
        if not all_candles:
            raise ValueError("No data fetched from Fyers. Check token/symbol or use synthetic data.")
            
        df = pd.DataFrame(all_candles, columns=["epoch", "open", "high", "low", "close", "volume"])
        # Fyers returns UTC epoch. Convert directly to IST.
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s").tz_localize("UTC").tz_convert("Asia/Kolkata").tz_localize(None)
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
# BASE CLASSES (Strategy, Trade, DB)
# ═══════════════════════════════════════════════════════════════

def find_fvg(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['Bullish_FVG'] = (df['low'] > df['high'].shift(2)) & (df['low'].shift(1) > df['high'].shift(2))
    df['Bearish_FVG'] = (df['high'] < df['low'].shift(2)) & (df['high'].shift(1) < df['low'].shift(2))
    return df

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

class PositionSizer:
    @staticmethod
    def fixed_fractional(equity, risk_pct, stop_distance, price, lot_size):
        risk_qty = int((equity * risk_pct) / stop_distance)
        cap_qty = int(equity * 0.95 / price)
        qty = min(risk_qty, cap_qty)
        return (qty // lot_size) * lot_size

    @staticmethod
    def volatility_adjusted(equity, risk_pct, atr, atr_mult, price, lot_size, target_vol=0.15):
        vol_scalar = (atr / price) / target_vol
        adj_risk = risk_pct / max(vol_scalar, 0.1)
        adj_risk = min(adj_risk, risk_pct * 2) 
        return PositionSizer.fixed_fractional(equity, adj_risk, atr*atr_mult, price, lot_size)

    @staticmethod
    def kelly_fraction(equity, trades, price, stop_distance, lot_size, fraction=0.25):
        if len(trades) < 20: 
            return PositionSizer.fixed_fractional(equity, 0.01, stop_distance, price, lot_size)
        pnls = np.array([t.pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        if len(wins) == 0 or len(losses) == 0:
            return PositionSizer.fixed_fractional(equity, 0.01, stop_distance, price, lot_size)
        W = len(wins) / len(pnls)
        R = wins.mean() / abs(losses.mean())
        kelly_f = W - (1 - W) / R if R > 0 else 0
        kelly_f = max(0, min(kelly_f * fraction, 0.05)) 
        risk_qty = int((equity * kelly_f) / stop_distance)
        return (risk_qty // lot_size) * lot_size


# ═══════════════════════════════════════════════════════════════
# MODULAR SIGNAL LAYER FRAMEWORK
# ═══════════════════════════════════════════════════════════════

@dataclass
class SignalContext:
    df: pd.DataFrame
    signals: pd.Series          
    trend: pd.Series = None     
    structure: pd.Series = None 
    liquidity: pd.Series = None 
    pd_ratio: pd.Series = None  
    regime: pd.Series = None    
    meta: Dict[str, Any] = field(default_factory=dict)

class SignalLayer:
    name: str = "base"
    def compute(self, ctx: SignalContext) -> SignalContext:
        raise NotImplementedError

class EMATrendFilter(SignalLayer):
    name = "ema_trend"
    def __init__(self, period: int = 200):
        self.period = period
    def compute(self, ctx):
        ema = ctx.df["close"].ewm(span=self.period, adjust=False).mean()
        ctx.trend = pd.Series(0, index=ctx.df.index, dtype=int)
        ctx.trend[ctx.df["close"] > ema] = 1
        ctx.trend[ctx.df["close"] < ema] = -1
        ctx.meta["ema"] = ema
        return ctx

class ADXTrendFilter(SignalLayer):
    name = "adx_filter"
    def __init__(self, period: int = 14, threshold: float = 25.0):
        self.period = period
        self.threshold = threshold
    def compute(self, ctx):
        if not PANDAS_TA_INSTALLED: return ctx
        adx_df = ta.adx(ctx.df["high"], ctx.df["low"], ctx.df["close"], length=self.period)
        adx_val = adx_df[f"ADX_{self.period}"]
        if ctx.trend is None:
            ctx.trend = pd.Series(0, index=ctx.df.index, dtype=int)
        ctx.trend[adx_val < self.threshold] = 0
        ctx.meta["adx"] = adx_val
        return ctx

class RegimeClassifier(SignalLayer):
    name = "regime"
    def __init__(self, adx_len=14, adx_threshold=25, atr_len=14, vol_lookback=100):
        self.adx_len = adx_len
        self.adx_threshold = adx_threshold
        self.atr_len = atr_len
        self.vol_lookback = vol_lookback
    def compute(self, ctx):
        df = ctx.df
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_len)
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=self.adx_len)
        adx_val = adx_df[f"ADX_{self.adx_len}"]
        ema200 = df["close"].ewm(span=200, adjust=False).mean()
        atr_pct = atr / df["close"]
        vol_p75 = atr_pct.rolling(self.vol_lookback).quantile(0.75)
        vol_p25 = atr_pct.rolling(self.vol_lookback).quantile(0.25)

        regime = pd.Series("range", index=df.index)
        regime[(adx_val > self.adx_threshold) & (df["close"] > ema200)] = "trend_up"
        regime[(adx_val > self.adx_threshold) & (df["close"] < ema200)] = "trend_dn"
        regime[(atr_pct > vol_p75) & (regime == "range")] = "high_vol"
        regime[(atr_pct < vol_p25) & (regime == "range")] = "low_vol"
        ctx.regime = regime
        ctx.meta["atr_pct"] = atr_pct
        return ctx

class SwingStructure(SignalLayer):
    name = "swing_structure"
    def __init__(self, swing_length: int = 5):
        self.swing_length = swing_length
    def compute(self, ctx):
        df = ctx.df
        L = self.swing_length
        roll_high = df["high"].rolling(2*L+1, center=True).max()
        roll_low = df["low"].rolling(2*L+1, center=True).min()
        is_swing_high = (df["high"] == roll_high)
        is_swing_low = (df["low"] == roll_low)

        trend_state = 0
        last_sh, last_sl = np.nan, np.nan

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        ish = is_swing_high.values
        isl = is_swing_low.values
        struct_arr = np.array([""] * len(df), dtype=object)

        for i in range(len(df)):
            if ish[i]: last_sh = highs[i]
            if isl[i]: last_sl = lows[i]

            if not np.isnan(last_sh) and closes[i] > last_sh:
                if trend_state == 1: struct_arr[i] = "BOS_UP"
                elif trend_state == -1: struct_arr[i] = "CHOCH_UP"
                trend_state = 1
                last_sl = np.nan 
            elif not np.isnan(last_sl) and closes[i] < last_sl:
                if trend_state == -1: struct_arr[i] = "BOS_DN"
                elif trend_state == 1: struct_arr[i] = "CHOCH_DN"
                trend_state = -1
                last_sh = np.nan

        ctx.structure = pd.Series(struct_arr, index=df.index)
        ctx.meta["structure_trend"] = trend_state
        return ctx

class LiquiditySweep(SignalLayer):
    name = "liquidity_sweep"
    def __init__(self, lookback: int = 20):
        self.lookback = lookback
    def compute(self, ctx):
        df = ctx.df
        prior_high = df["high"].rolling(self.lookback).max().shift(1)
        prior_low = df["low"].rolling(self.lookback).min().shift(1)
        bull_sweep = (df["low"] < prior_low) & (df["close"] > prior_low)
        bear_sweep = (df["high"] > prior_high) & (df["close"] < prior_high)
        liq = pd.Series(0, index=df.index, dtype=int)
        liq[bull_sweep] = 1
        liq[bear_sweep] = -1
        ctx.liquidity = liq
        return ctx

class PremiumDiscount(SignalLayer):
    name = "premium_discount"
    def __init__(self, lookback: int = 100):
        self.lookback = lookback
    def compute(self, ctx):
        df = ctx.df
        hh = df["high"].rolling(self.lookback).max()
        ll = df["low"].rolling(self.lookback).min()
        rng = (hh - ll).replace(0, np.nan)
        ctx.pd_ratio = ((df["close"] - ll) / rng).fillna(0.5)
        return ctx

class FVGEntry(SignalLayer):
    """Explicit FVG mitigation tracking - accurately mimics institutional order block logic."""
    name = "fvg_entry"
    def __init__(self, lookback=10, min_displacement_atr=1.5, require_trend=True, 
                 require_pd=True, require_sweep=False, pd_long_max=0.5, pd_short_min=0.5):
        self.lookback = lookback
        self.min_displacement_atr = min_displacement_atr
        self.require_trend = require_trend
        self.require_pd = require_pd
        self.require_sweep = require_sweep
        self.pd_long_max = pd_long_max
        self.pd_short_min = pd_short_min

    def compute(self, ctx):
        df = ctx.df
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Raw 3-candle gaps
        bull_fvg_raw = (df["low"] > df["high"].shift(2)) & (df["low"].shift(1) > df["high"].shift(2))
        bear_fvg_raw = (df["high"] < df["low"].shift(2)) & (df["high"].shift(1) < df["low"].shift(2))

        # Displacement filter
        mid_body = (df["close"].shift(1) - df["open"].shift(1)).abs()
        disp_ok = mid_body >= (atr.shift(1) * self.min_displacement_atr)

        bull_fvg = bull_fvg_raw & disp_ok
        bear_fvg = bear_fvg_raw & disp_ok

        signals = pd.Series(0, index=df.index, dtype=int)
        
        active_bull_zones = [] # Stores tuples: (zone_top, zone_bottom, creation_index)
        active_bear_zones = []
        
        highs = df["high"].values
        lows = df["low"].values
        
        for i in range(len(df)):
            # 1. Check mitigations for Bullish FVGs
            mitigated = []
            for z_top, z_bot, z_idx in active_bull_zones:
                if i - z_idx > self.lookback:
                    mitigated.append((z_top, z_bot, z_idx)) # Expired
                elif lows[i] <= z_top: # Price tapped the zone
                    signals.iloc[i] = 1
                    mitigated.append((z_top, z_bot, z_idx)) # Mitigated
            for m in mitigated: active_bull_zones.remove(m)
                
            # 2. Check mitigations for Bearish FVGs
            mitigated = []
            for z_top, z_bot, z_idx in active_bear_zones:
                if i - z_idx > self.lookback:
                    mitigated.append((z_top, z_bot, z_idx)) # Expired
                elif highs[i] >= z_bot: # Price tapped the zone
                    signals.iloc[i] = -1
                    mitigated.append((z_top, z_bot, z_idx)) # Mitigated
            for m in mitigated: active_bear_zones.remove(m)
            
            # 3. Register newly formed FVGs
            if bull_fvg.iloc[i]:
                z_top = df["low"].shift(1).iloc[i]
                z_bot = df["high"].shift(2).iloc[i]
                active_bull_zones.append((z_top, z_bot, i))
            if bear_fvg.iloc[i]:
                z_top = df["low"].shift(2).iloc[i]
                z_bot = df["high"].shift(1).iloc[i]
                active_bear_zones.append((z_top, z_bot, i))

        # ---- FILTERS ----
        if self.require_trend and ctx.trend is not None:
            signals[(signals == 1) & (ctx.trend != 1)] = 0
            signals[(signals == -1) & (ctx.trend != -1)] = 0

        if self.require_pd and ctx.pd_ratio is not None:
            signals[(signals == 1) & (ctx.pd_ratio > self.pd_long_max)] = 0
            signals[(signals == -1) & (ctx.pd_ratio < self.pd_short_min)] = 0

        if self.require_sweep and ctx.liquidity is not None:
            recent_bull_sweep = ctx.liquidity.rolling(self.lookback).apply(lambda x: (x == 1).any(), raw=False).fillna(0)
            recent_bear_sweep = ctx.liquidity.rolling(self.lookback).apply(lambda x: (x == -1).any(), raw=False).fillna(0)
            signals[(signals == 1) & (recent_bull_sweep == 0)] = 0
            signals[(signals == -1) & (recent_bear_sweep == 0)] = 0

        ctx.signals = signals
        return ctx

class SessionFilter(SignalLayer):
    name = "session_filter"
    SESSIONS = {
        "india_open":  ("09:30", "11:30"),
        "india_power": ("13:00", "15:00"),
        "india_full":  ("09:20", "15:10"),
    }
    def __init__(self, session: str = "india_full"):
        self.session = session
    def compute(self, ctx):
        if ctx.signals is None: return ctx
        start, end = self.SESSIONS.get(self.session, self.SESSIONS["india_full"])
        s = pd.Timestamp(start).time()
        e = pd.Timestamp(end).time()
        valid = (ctx.df.index.time >= s) & (ctx.df.index.time <= e)
        ctx.signals[~valid] = 0
        return ctx

class VolumeConfirm(SignalLayer):
    name = "volume_confirm"
    def __init__(self, mult: float = 1.2, lookback: int = 20):
        self.mult = mult
        self.lookback = lookback
    def compute(self, ctx):
        if ctx.signals is None: return ctx
        df = ctx.df
        avg_vol = df["volume"].rolling(self.lookback).mean()
        spike = df["volume"] > (avg_vol * self.mult)
        ctx.signals[(ctx.signals != 0) & ~spike.fillna(False)] = 0
        return ctx

class CompositeStrategy(Strategy):
    name = "composite"
    category = "modular"
    def __init__(self, layers: List[SignalLayer], debug: bool = False):
        self.layers = layers
        self.debug = debug
        self.params = {f"layer_{i}": l.name for i, l in enumerate(layers)}
        
    def generate(self, df):
        ctx = SignalContext(df=df, signals=pd.Series(0, index=df.index, dtype=int))
        for layer in self.layers:
            ctx = layer.compute(ctx)
            if self.debug:
                longs = shorts = 0
                if ctx.signals is not None:
                    longs = (ctx.signals == 1).sum()
                    shorts = (ctx.signals == -1).sum()
                
                ctx_info = []
                if ctx.trend is not None: ctx_info.append(f"Trend(1:{(ctx.trend==1).sum()}, -1:{(ctx.trend==-1).sum()})")
                if ctx.liquidity is not None: ctx_info.append(f"Liq(1:{(ctx.liquidity==1).sum()}, -1:{(ctx.liquidity==-1).sum()})")
                if ctx.pd_ratio is not None: ctx_info.append(f"PD(avg:{ctx.pd_ratio.mean():.2f})")
                if ctx.regime is not None: ctx_info.append(f"Regime(up:{(ctx.regime=='trend_up').sum()})")
                
                print(f"  [Layer] {layer.name:20s} | Sig(L:{longs}, S:{shorts}) | {', '.join(ctx_info)}")
                
        return StrategyResult(ctx.signals, ctx.meta)

def make_institutional_fvg(params: dict = None, debug: bool = False):
    params = params or {}
    return CompositeStrategy([
        EMATrendFilter(200),
        ADXTrendFilter(14, 20),
        RegimeClassifier(),
        SwingStructure(5),
        LiquiditySweep(20),
        PremiumDiscount(100),
        FVGEntry(
            lookback=params.get("lookback", 50),           
            min_displacement_atr=params.get("min_displacement_atr", 0.2), 
            require_trend=True, 
            require_pd=True,
            require_sweep=params.get("require_sweep", False),
            pd_long_max=0.8,
            pd_short_min=0.2
        ),
        SessionFilter("india_full"),
    ], debug=debug)


# ═══════════════════════════════════════════════════════════════
# RESEARCH MODULES (Walk-Forward, Monte Carlo, Heatmaps)
# ═══════════════════════════════════════════════════════════════

class WalkForwardOptimizer:
    def __init__(self, cfg: Config = CFG, db: ResearchDB = None,
                 train_years: float = 2.0, test_years: float = 1.0, step_years: float = 1.0):
        self.cfg = cfg
        self.bt = EnhancedBacktester(cfg)
        self.db = db
        self.train_years = train_years
        self.test_years = test_years
        self.step_years = step_years

    def _folds(self, df):
        start = df.index[0]
        end = df.index[-1]
        train_td = timedelta(days=self.train_years * 365)
        test_td = timedelta(days=self.test_years * 365)
        step_td = timedelta(days=self.step_years * 365)

        cur = start + train_td
        while cur + test_td <= end:
            tr = df[(df.index >= cur - train_td) & (df.index < cur)]
            te = df[(df.index >= cur) & (df.index < cur + test_td)]
            yield tr, te, f"{(cur-train_td).date()}→{cur.date()} | TEST {cur.date()}→{(cur+test_td).date()}"
            cur += step_td

    def search(self, df, strategy_factory, param_grid):
        keys = list(param_grid.keys())
        all_results = []
        for values in product(*param_grid.values()):
            params = dict(zip(keys, values))
            fold_results = []
            for tr, te, label in self._folds(df):
                s = strategy_factory(params)
                m_tr = self.bt.run(tr, s, is_oos=False)["metrics"]
                m_te = self.bt.run(te, s, is_oos=True)["metrics"]
                fold_results.append({"fold": label, "train": m_tr, "test": m_te})
                if self.db:
                    self.db.save_result(s.name, "5m", params, 0, m_tr)
                    self.db.save_result(s.name, "5m", params, 1, m_te)
            
            oos_pfs = [fr["test"].get("profit_factor", 0) for fr in fold_results]
            oos_dds = [fr["test"].get("max_dd_pct", 0) for fr in fold_results]
            
            agg = {
                "params": params,
                "n_folds": len(fold_results),
                "oos_pf_mean": np.mean(oos_pfs) if oos_pfs else 0,
                "oos_pf_min": np.min(oos_pfs) if oos_pfs else 0,
                "oos_pf_std": np.std(oos_pfs) if oos_pfs else 0,
                "oos_dd_max": np.min(oos_dds) if oos_dds else 0,
                "folds": fold_results,
            }
            all_results.append(agg)
            print(f"  {params} | OOS PF: mean={agg['oos_pf_mean']:.2f} min={agg['oos_pf_min']:.2f} maxDD={agg['oos_dd_max']:.1f}%")
            
        if not all_results:
            return []
            
        all_results.sort(key=lambda x: x["oos_pf_min"], reverse=True)
        return all_results

class MonteCarlo:
    def __init__(self, initial_capital: float, n_sims: int = 1000, risk_pct: float = 0.01):
        self.initial = initial_capital
        self.n_sims = n_sims
        self.risk_pct = risk_pct

    def run(self, trades: List[Trade]):
        if not trades: 
            return {
                "median_final_equity": self.initial,
                "p5_final_equity": self.initial,
                "p95_final_equity": self.initial,
                "median_max_dd_pct": 0,
                "p95_max_dd_pct": 0,
                "worst_max_dd_pct": 0,
                "prob_of_ruin_pct": 0,
                "prob_profitable_pct": 0,
            }
            
        pnls = np.array([t.pnl for t in trades])
        rng = np.random.default_rng(42)
        final_eq = np.zeros(self.n_sims)
        max_dds = np.zeros(self.n_sims)
        ruin_count = 0
        ruin_threshold = 0.5 

        for sim in range(self.n_sims):
            shuffled = rng.permutation(pnls)
            eq = self.initial
            peak = eq
            max_dd = 0
            for pnl in shuffled:
                eq += pnl
                if eq > peak: peak = eq
                dd = (eq - peak) / peak
                if dd < max_dd: max_dd = dd
                if eq <= self.initial * ruin_threshold:
                    ruin_count += 1
                    break
            final_eq[sim] = eq
            max_dds[sim] = max_dd

        return {
            "median_final_equity": np.median(final_eq),
            "p5_final_equity": np.percentile(final_eq, 5),
            "p95_final_equity": np.percentile(final_eq, 95),
            "median_max_dd_pct": np.median(max_dds) * 100,
            "p95_max_dd_pct": np.percentile(max_dds, 95) * 100,
            "worst_max_dd_pct": np.min(max_dds) * 100,
            "prob_of_ruin_pct": ruin_count / self.n_sims * 100,
            "prob_profitable_pct": (final_eq > self.initial).mean() * 100,
        }

class HeatmapBuilder:
    def __init__(self, results: List[Dict]):
        self.results = results

    def build(self, x_param: str, y_param: str, metric: str = "profit_factor", dataset: str = "test"):
        rows = []
        for r in self.results:
            p = r["params"]
            if x_param in p and y_param in p:
                rows.append({x_param: p[x_param], y_param: p[y_param], metric: r[dataset].get(metric, np.nan)})
        df = pd.DataFrame(rows)
        return df.pivot(index=y_param, columns=x_param, values=metric)

    def plot(self, x_param, y_param, metric="profit_factor", dataset="test", savepath=None):
        import matplotlib.pyplot as plt
        import seaborn as sns
        pivot = self.build(x_param, y_param, metric, dataset)
        plt.figure(figsize=(10, 6))
        sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdYlGn", center=1.0 if metric == "profit_factor" else 0, cbar_kws={"label": metric})
        plt.title(f"{metric.upper()} — {dataset.upper()} | {y_param} vs {x_param}")
        plt.tight_layout()
        if savepath: plt.savefig(savepath, dpi=120)
        plt.show()


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

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
                
                if cfg.sizing_method == "vol_adj":
                    qty = PositionSizer.volatility_adjusted(equity, cfg.risk_per_trade_pct, atr_val, cfg.sl_atr_mult, entry_price, cfg.lot_size)
                elif cfg.sizing_method == "kelly":
                    qty = PositionSizer.kelly_fraction(equity, trades, entry_price, stop_distance, cfg.lot_size)
                else:
                    qty = PositionSizer.fixed_fractional(equity, cfg.risk_per_trade_pct, stop_distance, entry_price, cfg.lot_size)
                
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
                "total_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                "avg_win": 0, "avg_loss": 0, "profit_factor": 0, "expectancy_r": 0,
                "avg_r": 0, "sharpe": 0, "sortino": 0, "calmar": 0, "sqn": 0,
                "omega": 0, "max_dd_pct": 0, "recovery_factor": 0, "ulcer_index": 0,
                "cagr": 0, "exposure_pct": 0, "avg_holding_bars": 0,
                "max_consec_wins": 0, "max_consec_losses": 0,
                "monthly_returns": {}, "yearly_returns": {}
            }
            
        pnls = np.array([t.pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        n = len(trades)

        risk_per_trade = self.cfg.initial_capital * self.cfg.risk_per_trade_pct
        r_multiples = pnls / risk_per_trade

        returns = equity.pct_change().dropna()
        peak = equity.cummax()
        dd = (equity - peak) / peak * 100
        max_dd = dd.min()
        days = (end_dt - start_dt).days
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (365/days) - 1 if days > 0 else 0

        ulcer = np.sqrt((dd**2).mean())

        gains = returns[returns > 0].sum()
        losses_abs = -returns[returns < 0].sum()
        omega = gains / losses_abs if losses_abs > 0 else np.inf

        recovery = pnls.sum() / abs(max_dd) if max_dd < 0 else 0
        expectancy_r = r_multiples.mean()

        streak_win = streak_loss = cur_w = cur_l = 0
        for p in pnls:
            if p > 0: cur_w += 1; cur_l = 0; streak_win = max(streak_win, cur_w)
            else:     cur_l += 1; cur_w = 0; streak_loss = max(streak_loss, cur_l)

        total_bars_held = sum(t.bars_held for t in trades)
        total_bars = len(equity)
        exposure = total_bars_held / total_bars * 100

        eq_monthly = equity.resample("ME").last().dropna()
        monthly_ret = eq_monthly.pct_change().dropna()
        yearly_ret = equity.resample("YE").last().pct_change().dropna()

        sharpe = (returns.mean() / returns.std() * np.sqrt(252*75)) if returns.std() > 0 else 0
        downside_std = returns[returns < 0].std()
        sortino = (returns.mean() / downside_std * np.sqrt(252*75)) if downside_std and downside_std > 0 else 0
        calmar = cagr / abs(max_dd/100) if max_dd < 0 else 0
        sqn = np.sqrt(n) * (pnls.mean() / pnls.std()) if pnls.std() > 0 else 0

        return {
            "total_trades": n,
            "win_rate": round(len(wins) / n * 100, 2),
            "total_pnl": round(pnls.sum(), 2),
            "avg_pnl": round(pnls.mean(), 2),
            "avg_win": round(wins.mean(), 2) if len(wins) else 0,
            "avg_loss": round(losses.mean(), 2) if len(losses) else 0,
            "profit_factor": round(wins.sum() / abs(losses.sum()), 2) if losses.sum() != 0 else 0,
            "expectancy_r": round(expectancy_r, 3),
            "avg_r": round(r_multiples.mean(), 3),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "calmar": round(calmar, 2),
            "sqn": round(sqn, 2),
            "omega": round(omega, 3),
            "max_dd_pct": round(max_dd, 2),
            "recovery_factor": round(recovery, 2),
            "ulcer_index": round(ulcer, 2),
            "cagr": round(cagr*100, 2),
            "exposure_pct": round(exposure, 2),
            "avg_holding_bars": round(total_bars_held/n, 1),
            "max_consec_wins": streak_win,
            "max_consec_losses": streak_loss,
            "monthly_returns": monthly_ret.round(4).to_dict(),
            "yearly_returns": yearly_ret.round(4).to_dict(),
        }

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
                        
                    self.results.append({"strategy": sname, "tf": tf, "params": params, "train": m_train, "test": m_test})
                    if self.db:
                        self.db.save_result(sname, tf, params, 0, m_train)
                        if m_test: self.db.save_result(sname, tf, params, 1, m_test)
        self.results.sort(key=lambda x: x["train"].get("profit_factor", 0), reverse=True)
        return self.results

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main_wave7():
    print("="*70)
    print("  QUANT FRAMEWORK — Wave 7: Research Engine + SMC Modular")
    print("="*70)

    db = ResearchDB()
    loader = DataManager(symbol=CFG.symbol, years=CFG.data_years, cfg=CFG)
    df = loader.load("5m")

    # --- DATA DIAGNOSTICS ---
    print("\n▸ Data Integrity Check:")
    print(f"  Shape: {df.shape}")
    print(f"  Index Range: {df.index.min()} to {df.index.max()}")
    print(f"  OHLC Nulls:\n{df[['open','high','low','close','volume']].isnull().sum()}")
    print(f"  Sample OHLC:\n{df.head(3)}")

    # --- SIGNAL DIAGNOSTICS ---
    print("\n▸ Signal Generation Diagnostics (Full Dataset):")
    strategy = make_institutional_fvg(debug=True)
    result = strategy.generate(df)
    
    print("\n▸ Final Signal Counts:")
    print(f"  Long signals : {(result.signals == 1).sum()}")
    print(f"  Short signals: {(result.signals == -1).sum()}")

    wfo = WalkForwardOptimizer(CFG, db, train_years=2, test_years=1, step_years=1)
    print("\n▸ Rolling Walk-Forward Optimization:")
    wf_results = wfo.search(df,
        strategy_factory=lambda p: make_institutional_fvg(p),
        param_grid={
            "min_displacement_atr": [0.2, 0.5, 1.0],
            "require_sweep": [False]
        })

    if not wf_results:
        print("⚠️ No trades generated across any walk-forward folds. Consider loosening SMC filters.")
        return

    best = wf_results[0]
    print(f"\n▸ Best params: {best['params']} | OOS PF min: {best['oos_pf_min']:.2f}")

    bt = EnhancedBacktester(CFG)
    full_res = bt.run(df, strategy)
    trades = full_res["trades"]

    print("\n▸ Monte Carlo Analysis (1,000 sims):")
    mc = MonteCarlo(CFG.initial_capital, n_sims=1000)
    mc_stats = mc.run(trades)
    for k, v in mc_stats.items():
        print(f"  {k:30s}: {v:.2f}")

    print("\n▸ Full Metrics:")
    for k, v in full_res["metrics"].items():
        if k not in ("monthly_returns", "yearly_returns"):
            print(f"  {k:25s}: {v}")

if __name__ == "__main__":
    main_wave7()