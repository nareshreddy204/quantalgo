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
                
        if df.index.tz is not None:
            df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
        elif df.index.hour.min() < 5:
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
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s").tz_localize("UTC").tz_convert("Asia/Kolkata").tz_localize(None)
        df.set_index("datetime", inplace=True)
        df.drop(columns=["epoch"], inplace=True)
        df = df[~df.index.duplicated(keep='first')]
        return df

    def _generate_realistic(self, tf: str) -> pd.DataFrame:
        bar_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": "240"}[tf]
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

# ═══════════════════════════════════════════════════════════════
# SMC EVENT-DRIVEN STATE MACHINE STRATEGY
# ═══════════════════════════════════════════════════════════════

@register
class SMCStateMachineStrategy(Strategy):
    """
    Implements sequential ICT logic:
    Trend -> Liquidity Sweep -> MSS/BOS (with Displacement) -> FVG (with Displacement) -> Mitigation + Confirmation -> Entry
    """
    name = "smc_state_machine"
    category = "smc"
    params = {
        "ema_period": 200, "swing_length": 5, "sweep_lookback": 20,
        "fvg_max_lookback": 30, "session": "india_full",
        "mss_disp_atr": 1.0, "fvg_disp_atr": 1.0
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
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        
        L = p["swing_length"]
        roll_high = df["high"].rolling(2*L+1, center=True).max()
        roll_low = df["low"].rolling(2*L+1, center=True).min()
        is_swing_high = (df["high"] == roll_high)
        is_swing_low = (df["low"] == roll_low)

        start_t, end_t = self.SESSIONS.get(p["session"], self.SESSIONS["india_full"])
        start_time = pd.Timestamp(start_t).time()
        end_time = pd.Timestamp(end_t).time()

        trend_state = 0
        
        bull_setup_active = False
        bull_sweep_idx = -1
        bull_mss_idx = -1
        
        bear_setup_active = False
        bear_sweep_idx = -1
        bear_mss_idx = -1
        
        active_bull_fvgs = [] 
        active_bear_fvgs = []

        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values
        atrs = atr.fillna(0).values
        ish = is_swing_high.values
        isl = is_swing_low.values
        times = df.index.time
        
        last_sh = np.nan
        last_sl = np.nan

        sweep_cnt = 0
        mss_cnt = 0
        fvg_cnt = 0

        for i in range(len(df)):
            if i < 2: continue

            # 1. Update Trend
            if closes[i] > ema.iloc[i]: trend_state = 1
            elif closes[i] < ema.iloc[i]: trend_state = -1

            # 2. LIQUIDITY SWEEP DETECTION
            lookback = p["sweep_lookback"]
            if i >= lookback:
                prior_high = np.max(highs[i-lookback:i])
                prior_low = np.min(lows[i-lookback:i])
                
                if lows[i] < prior_low and closes[i] > prior_low and trend_state == 1:
                    if not bull_setup_active:
                        bull_setup_active = True
                        bull_sweep_idx = i
                        bull_mss_idx = -1 
                        sweep_cnt += 1
                    
                elif highs[i] > prior_high and closes[i] < prior_high and trend_state == -1:
                    if not bear_setup_active:
                        bear_setup_active = True
                        bear_sweep_idx = i
                        bear_mss_idx = -1
                        sweep_cnt += 1

            # Setup Timeout
            if bull_setup_active and i - bull_sweep_idx > 50:
                bull_setup_active = False
            if bear_setup_active and i - bear_sweep_idx > 50:
                bear_setup_active = False

            # 3. MARKET STRUCTURE SHIFT (MSS) DETECTION
            # Requires Displacement: Body must be > mss_disp_atr * ATR
            if bull_setup_active and bull_mss_idx == -1:
                body = abs(closes[i] - opens[i])
                if not np.isnan(last_sh) and closes[i] > last_sh and body > (atrs[i] * p["mss_disp_atr"]):
                    bull_mss_idx = i
                    mss_cnt += 1
                    
            if bear_setup_active and bear_mss_idx == -1:
                body = abs(closes[i] - opens[i])
                if not np.isnan(last_sl) and closes[i] < last_sl and body > (atrs[i] * p["mss_disp_atr"]):
                    bear_mss_idx = i
                    mss_cnt += 1

            # 4. FAIR VALUE GAP (FVG) DETECTION
            # Requires Displacement: Body of candle 3 must be > fvg_disp_atr * ATR
            if bull_setup_active and bull_mss_idx != -1 and i > bull_mss_idx:
                body = abs(closes[i] - opens[i])
                if lows[i] > highs[i-2] and body > (atrs[i] * p["fvg_disp_atr"]): 
                    z_top = lows[i]
                    z_bot = highs[i-2]
                    active_bull_fvgs.append((z_top, z_bot, i))
                    bull_setup_active = False 
                    fvg_cnt += 1
                    
            if bear_setup_active and bear_mss_idx != -1 and i > bear_mss_idx:
                body = abs(closes[i] - opens[i])
                if highs[i] < lows[i-2] and body > (atrs[i] * p["fvg_disp_atr"]):
                    z_top = lows[i-2]
                    z_bot = highs[i]
                    active_bear_fvgs.append((z_top, z_bot, i))
                    bear_setup_active = False
                    fvg_cnt += 1

            # 5. FVG MITIGATION & CONFIRMATION ENTRY
            if times[i] >= start_time and times[i] <= end_time:
                mitigated = []
                for z_top, z_bot, z_idx in active_bull_fvgs:
                    if i - z_idx > p["fvg_max_lookback"]:
                        mitigated.append((z_top, z_bot, z_idx))
                    # Mitigation + Confirmation Candle (Close > Open)
                    elif lows[i] <= z_top and closes[i] > opens[i]: 
                        signals.iloc[i] = 1
                        mitigated.append((z_top, z_bot, z_idx))
                for m in mitigated: active_bull_fvgs.remove(m)
                
                mitigated = []
                for z_top, z_bot, z_idx in active_bear_fvgs:
                    if i - z_idx > p["fvg_max_lookback"]:
                        mitigated.append((z_top, z_bot, z_idx))
                    # Mitigation + Confirmation Candle (Close < Open)
                    elif highs[i] >= z_bot and closes[i] < opens[i]:
                        signals.iloc[i] = -1
                        mitigated.append((z_top, z_bot, z_idx))
                for m in mitigated: active_bear_fvgs.remove(m)

            # Update Swings AT THE END to avoid same-bar BOS look-ahead
            if ish[i]: last_sh = highs[i]
            if isl[i]: last_sl = lows[i]

        print(f"  [SMC State Machine] Sweeps: {sweep_cnt} | MSS: {mss_cnt} | FVGs: {fvg_cnt}")
        return StrategyResult(signals, {"ema": ema})

def make_smc_state_machine(params: dict = None):
    params = params or {}
    strat = SMCStateMachineStrategy()
    strat.params.update(params)
    return strat


# ═══════════════════════════════════════════════════════════════
# RESEARCH MODULES
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
            print("  Monte Carlo skipped: no trades generated.")
            return {}
            
        pnls = np.array([t.pnl for t in trades])
        rng = np.random.default_rng(42)
        final_eq = np.zeros(self.n_sims)
        max_dds = np.zeros(self.n_sims)
        ruin_count = 0
        ruin_threshold = 0.5 

        for sim in range(self.n_sims):
            # Add execution uncertainty: randomly skip 5% of trades
            mask = rng.random(len(pnls)) > 0.05
            pnls_sim = pnls[mask]
            
            # Add slippage/noise: 0.2% std dev per trade
            noise = rng.normal(0, 0.002, len(pnls_sim))
            pnls_sim = pnls_sim * (1 + noise)
            
            shuffled = rng.permutation(pnls_sim)
            eq = self.initial
            peak = eq
            max_dd = 0
            for pnl in shuffled:
                eq += pnl
                if eq > peak: peak = eq
                dd = (eq - peak) / peak if peak > 0 else 0
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
        exit_reasons = {}
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
                    
                    # FIX: Set SL precisely to entry price, not entry+1
                    if cfg.use_break_even and max_favorable >= atr_val * cfg.break_even_trigger_atr:
                        new_sl = entry_price
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
                    
                    exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                    
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

        print(f"  [Backtest Exit Reasons] {exit_reasons}")
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
        r_multiples = pnls / risk_per_trade if risk_per_trade > 0 else np.zeros(n)

        returns = equity.pct_change().dropna()
        peak = equity.cummax()
        dd = (equity - peak) / peak * 100
        max_dd = dd.min()
        
        days = (end_dt - start_dt).days
        if days > 0 and equity.iloc[0] > 0:
            cagr = (equity.iloc[-1] / equity.iloc[0]) ** (365/days) - 1
        else:
            cagr = 0.0

        ulcer = np.sqrt((dd**2).mean()) if len(dd) > 0 else 0.0

        gains = returns[returns > 0].sum()
        losses_abs = -returns[returns < 0].sum()
        omega = gains / losses_abs if losses_abs > 0 else 0.0

        recovery = pnls.sum() / abs(max_dd) if max_dd < 0 else 0.0
        expectancy_r = r_multiples.mean()

        streak_win = streak_loss = cur_w = cur_l = 0
        for p in pnls:
            if p > 0: cur_w += 1; cur_l = 0; streak_win = max(streak_win, cur_w)
            else:     cur_l += 1; cur_w = 0; streak_loss = max(streak_loss, cur_l)

        total_bars_held = sum(t.bars_held for t in trades)
        total_bars = len(equity)
        exposure = total_bars_held / total_bars * 100 if total_bars > 0 else 0.0

        eq_monthly = equity.resample("ME").last().dropna()
        monthly_ret = eq_monthly.pct_change().dropna()
        yearly_ret = equity.resample("YE").last().pct_change().dropna()

        sharpe = (returns.mean() / returns.std() * np.sqrt(252*75)) if returns.std() > 0 else 0.0
        downside_std = returns[returns < 0].std()
        sortino = (returns.mean() / downside_std * np.sqrt(252*75)) if downside_std and downside_std > 0 else 0.0
        calmar = cagr / abs(max_dd/100) if max_dd < 0 else 0.0
        sqn = np.sqrt(n) * (pnls.mean() / pnls.std()) if pnls.std() > 0 else 0.0

        return {
            "total_trades": n,
            "win_rate": round(len(wins) / n * 100, 2),
            "total_pnl": round(pnls.sum(), 2),
            "avg_pnl": round(pnls.mean(), 2),
            "avg_win": round(wins.mean(), 2) if len(wins) else 0,
            "avg_loss": round(losses.mean(), 2) if len(losses) else 0,
            "profit_factor": round(wins.sum() / abs(losses.sum()), 2) if losses.sum() != 0 else 0.0,
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

    print("\n▸ Signal Generation Diagnostics (Full Dataset):")
    strategy = make_smc_state_machine()
    result = strategy.generate(df)
    
    print("\n▸ Final Signal Counts:")
    print(f"  Long signals : {(result.signals == 1).sum()}")
    print(f"  Short signals: {(result.signals == -1).sum()}")

    wfo = WalkForwardOptimizer(CFG, db, train_years=2, test_years=1, step_years=1)
    print("\n▸ Rolling Walk-Forward Optimization:")
    wf_results = wfo.search(df,
        strategy_factory=lambda p: make_smc_state_machine(p),
        param_grid={
            "sweep_lookback": [20, 30],
            "fvg_max_lookback": [30, 50]
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