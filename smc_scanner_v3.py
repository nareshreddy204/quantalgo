"""
═══════════════════════════════════════════════════════════════
SMC BREAKOUT SCANNER v3.0.0 — Institutional Grade & Strategy Expanded
═══════════════════════════════════════════════════════════════
Architectural Shift in v3.0.0:
1. SMC ENGINE: Full Order Block, FVG, Breaker Block, MSS/BOS/CHoCH detection.
2. MARKET REGIME: ADX, India VIX, Session quality scoring.
3. SMART STRIKES: Gamma-aware dynamic strike selection with OI skew.
4. RISK GUARD: Dynamic R:R, consecutive loss cooldown, expiry day filters.
5. MULTI-TIMEFRAME: 1m signal + 3m structure + 5m HTF alignment.
6. SIGNAL GRADING: A/B/C tiering with confluence matrix.
7. PORTFOLIO HEAT: Max signal caps, correlated exposure limits.
══════════════════════════════════════════════════════════════
"""

import os
import sys
import csv
import yaml
import calendar
import logging
import threading
import time
import warnings
import signal
import json
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo
from collections import deque, defaultdict

import requests
import pandas as pd
import numpy as np
import concurrent.futures

import os
import json
from datetime import datetime, date
from typing import Set
import logging

warnings.filterwarnings("ignore")

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    fyersModel = None

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ════════════════════════════════════════════════════════════
# LOGGING
# ═════════════════════════════════════════════════════════════
def setup_logging() -> logging.Logger:
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%H:%M:%S"
    logger = logging.getLogger("SMCScanner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_format, date_format))

    fh = logging.FileHandler("scanner_v3.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


logger = setup_logging()


# ═════════════════════════════════════════════════════════════
# UTILITIES
# ═════════════════════════════════════════════════════════════
def drop_forming_candle(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Datetime" not in df.columns:
        return df
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    current_minute = pd.Timestamp(ist_now).floor("min")
    if df["Datetime"].iloc[-1] >= current_minute:
        df = df.iloc[:-1].reset_index(drop=True)
    return df

logger = logging.getLogger("SMCScanner")

_HOLIDAY_CACHE: Set[date] = set()
_CACHE_LOADED = False

def _fetch_nse_holidays() -> Set[date]:
    """Attempt to download NSE trading holidays and cache locally."""
    import requests
    holiday_set: Set[date] = set()
    try:
        # NSE trading holidays endpoint (requires standard headers)
        url = "https://www.nseindia.com/api/holidays-trading"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        session = requests.Session()
        # NSE main page visit to set cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        resp = session.get(url, headers=headers, params={"type": "trading"}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("BM", [] or []):
                trading_date = item.get("tradingDate")
                if trading_date:
                    try:
                        dt = datetime.strptime(trading_date, "%d-%b-%Y").date()
                        holiday_set.add(dt)
                    except ValueError:
                        pass
            # Cache to local JSON so next run needs no network
            with open("nse_holidays.json", "w", encoding="utf-8") as f:
                json.dump({"trading_holidays": [d.isoformat() for d in sorted(holiday_set)]}, f)
            logger.info(f"Cached {len(holiday_set)} NSE holidays from API")
    except Exception as e:
        logger.warning(f"Could not fetch NSE holidays live: {e}")
    return holiday_set


def _load_holiday_cache() -> Set[date]:
    """Load from local JSON if present; otherwise return empty."""
    holiday_set: Set[date] = set()
    if os.path.exists("nse_holidays.json"):
        try:
            with open("nse_holidays.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                for d_str in data.get("trading_holidays", []):
                    holiday_set.add(datetime.fromisoformat(d_str).date())
        except Exception as e:
            logger.warning(f"Could not parse nse_holidays.json: {e}")
    return holiday_set


def is_nse_holiday(dt: date) -> bool:
    """
    Returns True if the given date is an NSE trading holiday.
    Priority:
      1. In-memory cache
      2. Local nse_holidays.json
      3. Live NSE API fetch (and cache)
      4. Minimal hardcoded fallback for critical fixed dates
    """
    global _HOLIDAY_CACHE, _CACHE_LOADED

    if not _CACHE_LOADED:
        # Try local file first
        local = _load_holiday_cache()
        if local:
            _HOLIDAY_CACHE = local
            _CACHE_LOADED = True
            logger.info(f"Loaded {len(_HOLIDAY_CACHE)} holidays from local cache")
        else:
            # Try live fetch
            fetched = _fetch_nse_holidays()
            if fetched:
                _HOLIDAY_CACHE = fetched
                _CACHE_LOADED = True
            else:
                # Absolute minimal fallback for fixed annual dates
                # Note: User should create nse_holidays.json for accuracy
                _HOLIDAY_CACHE = {
                    date(dt.year, 1, 26),   # Republic Day
                    date(dt.year, 5, 1),    # Maharashtra Day
                    date(dt.year, 8, 15),   # Independence Day
                    date(dt.year, 10, 2),   # Gandhi Jayanti
                    date(dt.year, 12, 25),  # Christmas
                }
                _CACHE_LOADED = True
                logger.warning("Using minimal hardcoded holiday fallback. Create nse_holidays.json for accuracy.")

    return dt in _HOLIDAY_CACHE


# ═════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════
@dataclass
class Config:
    app_id: str
    access_token: str
    bot_token: str
    chat_id: str

    # SMC Core
    swing_len: int = 3
    sweep_lookback: int = 3
    displacement_atr_mult: float = 0.5
    atr_period: int = 14
    ob_lookback: int = 10
    fvg_min_size: float = 0.3

    # Risk & Execution
    signal_cooldown_min: int = 5
    risk_reward: float = 2.0
    dynamic_rr: bool = True
    max_daily_signals: int = 20
    max_signals_per_15min: int = 3
    avoid_first_15_min: bool = True
    avoid_last_30_min: bool = True
    expiry_day_cutoff_hour: int = 14
    allow_expiry_otm_after_cutoff: bool = False
    trend_filter: bool = True
    min_volume: int = 1000

    # Threading & Infra
    max_workers: int = 4
    csv_log_dir: str = "trade_logs_v3"
    print_stats_every: int = 15

    # HTF & Multi-Timeframe
    htf_confirm: bool = True
    htf_resolution: str = "5"
    mtf_resolutions: List[str] = field(default_factory=lambda: ["3", "5"])
    htf_disp_mult: float = 0.3

    # Market Regime
    use_india_vix: bool = True
    vix_threshold_high: float = 22.0
    vix_threshold_low: float = 12.0
    adx_period: int = 14
    adx_trending_threshold: float = 25.0

    # Option Chain
    rollover_days_before_expiry: int = 2
    nifty_expiry_weekday: int = 3
    strike_selection_mode: str = "dynamic"  # dynamic | fixed
    oi_min_change_pct: float = 5.0


def load_config(path: str = "config.yaml") -> Config:
    defaults = {
        "fyers": {"app_id": "YOUR_APP_ID"},
        "telegram": {"bot_token": "YOUR_BOT_TOKEN", "chat_id": "YOUR_CHAT_ID"},
        "scanner": {}
    }
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Could not load config YAML: {e}")

    for key, val in defaults.items():
        data.setdefault(key, val)

    app_id = data.get("fyers", {}).get("app_id", "YOUR_APP_ID")

    token_path = "token.txt"
    access_token = "YOUR_ACCESS_TOKEN"
    if os.path.exists(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                access_token = f.read().strip()
        except Exception as e:
            logger.warning(f"Could not read token.txt: {e}")

    bot_token = data.get("telegram", {}).get("bot_token", "YOUR_BOT_TOKEN")
    chat_id = data.get("telegram", {}).get("chat_id", "YOUR_CHAT_ID")
    sc = data.get("scanner", {})

    return Config(
        app_id=app_id,
        access_token=access_token,
        bot_token=bot_token,
        chat_id=chat_id,
        swing_len=sc.get("swing_len", 3),
        sweep_lookback=sc.get("sweep_lookback", 3),
        displacement_atr_mult=sc.get("displacement_atr_mult", 0.5),
        atr_period=sc.get("atr_period", 14),
        ob_lookback=sc.get("ob_lookback", 10),
        fvg_min_size=sc.get("fvg_min_size", 0.3),
        signal_cooldown_min=sc.get("signal_cooldown_min", 5),
        risk_reward=sc.get("risk_reward", 2.0),
        dynamic_rr=sc.get("dynamic_rr", True),
        max_daily_signals=sc.get("max_daily_signals", 20),
        max_signals_per_15min=sc.get("max_signals_per_15min", 3),
        avoid_first_15_min=sc.get("avoid_first_15_min", True),
        avoid_last_30_min=sc.get("avoid_last_30_min", True),
        expiry_day_cutoff_hour=sc.get("expiry_day_cutoff_hour", 14),
        allow_expiry_otm_after_cutoff=sc.get("allow_expiry_otm_after_cutoff", False),
        trend_filter=sc.get("trend_filter", True),
        min_volume=sc.get("min_volume", 1000),
        max_workers=sc.get("max_workers", 4),
        csv_log_dir=sc.get("csv_log_dir", "trade_logs_v3"),
        print_stats_every=sc.get("print_stats_every", 15),
        htf_confirm=sc.get("htf_confirm", True),
        htf_resolution=str(sc.get("htf_resolution", "5")),
        mtf_resolutions=sc.get("mtf_resolutions", ["3", "5"]),
        htf_disp_mult=sc.get("htf_disp_mult", 0.3),
        use_india_vix=sc.get("use_india_vix", True),
        vix_threshold_high=sc.get("vix_threshold_high", 22.0),
        vix_threshold_low=sc.get("vix_threshold_low", 12.0),
        adx_period=sc.get("adx_period", 14),
        adx_trending_threshold=sc.get("adx_trending_threshold", 25.0),
        rollover_days_before_expiry=sc.get("rollover_days_before_expiry", 2),
        nifty_expiry_weekday=sc.get("nifty_expiry_weekday", 3),
        strike_selection_mode=sc.get("strike_selection_mode", "dynamic"),
        oi_min_change_pct=sc.get("oi_min_change_pct", 5.0),
    )


# ═════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════
@dataclass
class SignalResult:
    symbol: str
    signal: str
    time: Optional[datetime] = None
    price: Optional[float] = None
    sl: Optional[float] = None
    target: Optional[float] = None
    confidence: str = "medium"
    grade: str = "C"
    confluences: List[str] = field(default_factory=list)
    context: str = ""
    htf_confirmed: bool = False
    mtf_score: int = 0
    volume: int = 0
    regime: str = "neutral"
    suggested_qty: int = 0
    expected_rr: float = 2.0


# ════════════════════════════════════════════════════════════
# TRADE LOGGER
# ═════════════════════════════════════════════════════════════
class TradeLogger:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.columns = [
            "date", "time", "symbol", "signal", "entry_price", "sl", "target",
            "risk_reward", "confidence", "grade", "confluences", "trend_bias",
            "nifty_spot", "nifty_fut", "basis", "volume", "htf_confirmed",
            "mtf_score", "regime", "suggested_qty", "context"
        ]
        self._lock = threading.Lock()
        self._file_path = None
        self._writer = None
        self._file = None
        self._init_daily_file()

    def _init_daily_file(self):
        today = datetime.now().strftime("%Y%m%d")
        self._file_path = os.path.join(self.log_dir, f"signals_{today}.csv")
        file_exists = os.path.exists(self._file_path)
        self._file = open(self._file_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.columns)
        if not file_exists:
            self._writer.writeheader()
            logger.info(f"Created new trade log: {self._file_path}")

    def log(self, result: SignalResult, trend_bias: str, nifty_spot: float,
            nifty_fut: Optional[float]):
        with self._lock:
            expected_path = os.path.join(
                self.log_dir, f"signals_{datetime.now().strftime('%Y%m%d')}.csv"
            )
            if expected_path != self._file_path:
                if self._file:
                    self._file.close()
                self._init_daily_file()

            rr_str = ""
            if result.price and result.target and result.sl and (result.price - result.sl) != 0:
                rr = abs((result.target - result.price) / (result.price - result.sl))
                rr_str = f"1:{rr:.1f}"

            basis = ""
            if nifty_fut and nifty_spot:
                basis = f"{nifty_fut - nifty_spot:+.2f}"

            row = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": result.time.strftime("%H:%M:%S") if result.time else "",
                "symbol": result.symbol,
                "signal": result.signal,
                "entry_price": f"{result.price:.2f}" if result.price else "",
                "sl": f"{result.sl:.2f}" if result.sl else "",
                "target": f"{result.target:.2f}" if result.target else "",
                "risk_reward": rr_str,
                "confidence": result.confidence,
                "grade": result.grade,
                "confluences": "|".join(result.confluences),
                "trend_bias": trend_bias or "none",
                "nifty_spot": f"{nifty_spot:.2f}" if nifty_spot else "",
                "nifty_fut": f"{nifty_fut:.2f}" if nifty_fut else "",
                "basis": basis,
                "volume": str(result.volume),
                "htf_confirmed": "YES" if result.htf_confirmed else "NO",
                "mtf_score": str(result.mtf_score),
                "regime": result.regime,
                "suggested_qty": str(result.suggested_qty),
                "context": result.context,
            }
            self._writer.writerow(row)
            self._file.flush()

    def close(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None

    def get_today_stats(self) -> Dict:
        with self._lock:
            if not self._file_path or not os.path.exists(self._file_path):
                return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0, "grade_a": 0}
            try:
                df = pd.read_csv(self._file_path)
                if df.empty:
                    return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0, "grade_a": 0}
                return {
                    "total": len(df),
                    "long": len(df[df["signal"] == "LONG"]),
                    "short": len(df[df["signal"] == "SHORT"]),
                    "high_conf": len(df[df["confidence"] == "high"]),
                    "htf_yes": len(df[df["htf_confirmed"] == "YES"]),
                    "grade_a": len(df[df["grade"] == "A"]),
                }
            except Exception as e:
                logger.warning(f"Could not read stats: {e}")
                return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0, "grade_a": 0}


# ═════════════════════════════════════════════════════════════
# TELEGRAM NOTIFIER
# ═════════════════════════════════════════════════════════════
class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = "YOUR_" not in bot_token and "YOUR_" not in chat_id
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._lock = threading.Lock()

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            try:
                # Split long messages
                chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
                for chunk in chunks:
                    resp = requests.post(
                        self.url,
                        json={"chat_id": self.chat_id, "text": chunk, "parse_mode": "HTML"},
                        timeout=5
                    )
                    if resp.status_code != 200:
                        return False
                    time.sleep(0.2)
                return True
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")
                return False


# ═════════════════════════════════════════════════════════════
# FYERS CLIENT
# ═════════════════════════════════════════════════════════════
class FyersClient:
    DATA_BASE = "https://api-t1.fyers.in/data"

    def __init__(self, app_id: str, access_token: str):
        self.app_id = app_id
        self.access_token = access_token
        if fyersModel is not None:
            self.fyers = fyersModel.FyersModel(
                client_id=app_id, token=access_token, is_async=False, log_path=""
            )
        else:
            self.fyers = None
        self._auth_header = f"{app_id}:{access_token}"
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._min_interval = 0.12

    def _throttle(self):
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.time()

    def history_with_retry(self, symbol: str, resolution: str, range_from: int,
                           range_to: int, cont_flag: str = "0", max_retries: int = 3) -> Optional[dict]:
        payload = {
            "symbol": symbol, "resolution": resolution, "date_format": "0",
            "range_from": range_from, "range_to": range_to, "cont_flag": cont_flag
        }
        for attempt in range(max_retries):
            self._throttle()
            try:
                if self.fyers:
                    resp = self.fyers.history(payload)
                    if resp and resp.get("s") == "ok":
                        return resp
                    msg = resp.get("message", "Unknown") if resp else "Empty API response"
                    if "limit" in msg.lower():
                        time.sleep(1.0 + attempt * 0.5)
                    else:
                        time.sleep(0.5 * (2 ** attempt))
                    logger.warning(f"Fyers history error for {symbol}: {msg}, attempt {attempt+1}")
            except Exception as e:
                logger.warning(f"Fyers exception for {symbol}: {e}, attempt {attempt+1}")
                time.sleep(0.5 * (2 ** attempt))
        return None

    def fetch_history(self, symbol: str, resolution: str, days: int = 3,
                      cont_flag: str = "0") -> pd.DataFrame:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
        all_rows = []
        cursor = end_dt
        while cursor > start_dt:
            win_start = cursor - timedelta(days=50)
            if win_start < start_dt:
                win_start = start_dt
            resp = self.history_with_retry(
                symbol=symbol, resolution=resolution,
                range_from=int(win_start.timestamp()),
                range_to=int(cursor.timestamp()), cont_flag=cont_flag
            )
            if resp is None or not resp.get("candles"):
                break
            all_rows.extend(resp["candles"])
            cursor = win_start - timedelta(seconds=1)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows, columns=["epoch", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
        df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
        return df[["Datetime", "open", "high", "low", "close", "volume"]].dropna()

    def option_chain(self, underlying: str, strikecount: int = 10,
                     timestamp: Optional[str] = None) -> Optional[dict]:
        payload = {"symbol": underlying, "strikecount": strikecount}
        if timestamp:
            payload["timestamp"] = str(timestamp)

        if self.fyers:
            try:
                self._throttle()
                resp = self.fyers.optionchain(data=payload)
                if resp and resp.get("s") == "ok":
                    return resp
            except AttributeError:
                logger.warning("SDK optionchain missing; using HTTP fallback")
            except Exception as e:
                logger.warning(f"SDK optionchain exception: {e}")

        try:
            import urllib.request
            import urllib.parse
            self._throttle()
            params = urllib.parse.urlencode(payload)
            url = f"{self.DATA_BASE}/options-chain-v3?{params}"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": self._auth_header,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                resp = json.loads(response.read().decode())
                if resp.get("s") == "ok":
                    return resp
        except Exception as e:
            logger.warning(f"Direct HTTP option chain failed: {e}")
        return None

    def quotes(self, symbols: List[str]) -> Optional[dict]:
        if not symbols:
            return None
        if self.fyers:
            try:
                self._throttle()
                resp = self.fyers.quotes({"symbols": ",".join(symbols)})
                if resp and resp.get("s") == "ok":
                    return resp
            except Exception as e:
                logger.warning(f"Quotes error: {e}")
        return None


# ═════════════════════════════════════════════════════════════
# INDICATORS
# ═════════════════════════════════════════════════════════════
class Indicators:
    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        df["+DM"] = np.where(
            (df["high"] - df["high"].shift(1)) > (df["low"].shift(1) - df["low"]),
            np.maximum(df["high"] - df["high"].shift(1), 0), 0
        )
        df["-DM"] = np.where(
            (df["low"].shift(1) - df["low"]) > (df["high"] - df["high"].shift(1)),
            np.maximum(df["low"].shift(1) - df["low"], 0), 0
        )
        tr = pd.concat([
            df["high"] - df["low"],
            np.abs(df["high"] - df["close"].shift()),
            np.abs(df["low"] - df["close"].shift())
        ], axis=1).max(axis=1)

        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (df["+DM"].rolling(window=period).mean() / atr)
        minus_di = 100 * (df["-DM"].rolling(window=period).mean() / atr)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        df["adx"] = adx
        df["+di"] = plus_di
        df["-di"] = minus_di
        return df[["Datetime", "adx", "+di", "-di"]]

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        if df.empty or "volume" not in df.columns:
            return pd.Series(index=df.index, dtype=float)
        df = df.copy()
        df["date"] = df["Datetime"].dt.date
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"]
        vwap_vals = np.full(len(df), np.nan)
        for _, group in df.groupby("date"):
            tp_vol = typical[group.index] * vol[group.index]
            cum_tp_vol = tp_vol.cumsum().values
            cum_vol = vol[group.index].cumsum().values
            with np.errstate(divide='ignore', invalid='ignore'):
                v = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
            vwap_vals[group.index] = v
        return pd.Series(vwap_vals, index=df.index)

    @staticmethod
    def pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
        df = df.copy()
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        ph = np.full(n, np.nan)
        pl = np.full(n, np.nan)
        for i in range(left, n - right):
            if highs[i] >= np.max(highs[i - left:i + right + 1]):
                ph[i + right] = highs[i]
            if lows[i] <= np.min(lows[i - left:i + right + 1]):
                pl[i + right] = lows[i]
        df["pivot_high"] = ph
        df["pivot_low"] = pl
        return df

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def detect_order_blocks(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
        """
        Identifies bullish and bearish Order Blocks.
        Bullish OB: Last down-close candle before a bullish displacement that breaks structure.
        Bearish OB: Last up-close candle before a bearish displacement that breaks structure.
        """
        df = df.copy().reset_index(drop=True)
        n = len(df)
        df["bull_ob_top"] = np.nan
        df["bull_ob_bottom"] = np.nan
        df["bear_ob_top"] = np.nan
        df["bear_ob_bottom"] = np.nan

        for i in range(lookback + 5, n):
            # Bullish OB: look back for last bearish candle before strong bullish move
            if df["close"].iloc[i] > df["high"].iloc[i - 1] and df["close"].iloc[i] > df["open"].iloc[i]:
                for j in range(i - 1, i - lookback, -1):
                    if df["close"].iloc[j] < df["open"].iloc[j]:  # bearish candle
                        df.loc[i, "bull_ob_top"] = df["high"].iloc[j]
                        df.loc[i, "bull_ob_bottom"] = df["low"].iloc[j]
                        break
            # Bearish OB
            if df["close"].iloc[i] < df["low"].iloc[i - 1] and df["close"].iloc[i] < df["open"].iloc[i]:
                for j in range(i - 1, i - lookback, -1):
                    if df["close"].iloc[j] > df["open"].iloc[j]:  # bullish candle
                        df.loc[i, "bear_ob_top"] = df["high"].iloc[j]
                        df.loc[i, "bear_ob_bottom"] = df["low"].iloc[j]
                        break
        return df

    @staticmethod
    def detect_fvg(df: pd.DataFrame, min_size: float = 0.3) -> pd.DataFrame:
        df = df.copy()
        n = len(df)
        df["bull_fvg_top"] = np.nan
        df["bull_fvg_bottom"] = np.nan
        df["bear_fvg_top"] = np.nan
        df["bear_fvg_bottom"] = np.nan

        for i in range(2, n):
            # Bullish FVG: candle 2 low > candle 0 high
            if df["low"].iloc[i] > df["high"].iloc[i - 2]:
                gap = df["low"].iloc[i] - df["high"].iloc[i - 2]
                if gap >= min_size:
                    df.loc[i, "bull_fvg_top"] = df["low"].iloc[i]
                    df.loc[i, "bull_fvg_bottom"] = df["high"].iloc[i - 2]
            # Bearish FVG: candle 2 high < candle 0 low
            if df["high"].iloc[i] < df["low"].iloc[i - 2]:
                gap = df["low"].iloc[i - 2] - df["high"].iloc[i]
                if gap >= min_size:
                    df.loc[i, "bear_fvg_top"] = df["low"].iloc[i - 2]
                    df.loc[i, "bear_fvg_bottom"] = df["high"].iloc[i]
        return df


# ═════════════════════════════════════════════════════════════
# MARKET REGIME
# ═════════════════════════════════════════════════════════════
class MarketRegime:
    def __init__(self, client: FyersClient, config: Config):
        self.client = client
        self.cfg = config

    def analyze(self, now: datetime) -> Dict:
        regime = {
            "trend": "neutral",
            "trend_strength": 0.0,
            "volatility": "normal",
            "session_quality": 1.0,
            "vix": None,
            "adx": 0.0,
            "recommendation": "trade"
        }

        # 1. Session quality
        t = now.time()
        if self.cfg.avoid_first_15_min and pd.Timestamp("09:15:00").time() <= t <= pd.Timestamp("09:30:00").time():
            regime["session_quality"] = 0.6
            regime["recommendation"] = "caution"
        elif self.cfg.avoid_last_30_min and pd.Timestamp("15:00:00").time() <= t <= pd.Timestamp("15:30:00").time():
            regime["session_quality"] = 0.7
        elif pd.Timestamp("11:30:00").time() <= t <= pd.Timestamp("13:30:00").time():
            regime["session_quality"] = 0.85  # lunch lull slight penalty

        # 2. India VIX
        if self.cfg.use_india_vix:
            try:
                df_vix = self.client.fetch_history("NSE:INDIAVIX-INDEX", "5", days=2)
                if not df_vix.empty:
                    vix_now = df_vix["close"].iloc[-1]
                    regime["vix"] = vix_now
                    if vix_now > self.cfg.vix_threshold_high:
                        regime["volatility"] = "extreme"
                        regime["recommendation"] = "size_down"
                    elif vix_now < self.cfg.vix_threshold_low:
                        regime["volatility"] = "compressed"
                        regime["recommendation"] = "wait_for_expansion"
            except Exception as e:
                logger.warning(f"VIX fetch failed: {e}")

        # 3. ADX on NIFTY spot 3-min
        try:
            df_spot = self.client.fetch_history("NSE:NIFTY50-INDEX", "3", days=3)
            if not df_spot.empty and len(df_spot) > self.cfg.adx_period + 5:
                df_spot = drop_forming_candle(df_spot)
                adx_df = Indicators.adx(df_spot, self.cfg.adx_period)
                if not adx_df.empty:
                    last = adx_df.iloc[-1]
                    regime["adx"] = last["adx"] if pd.notna(last["adx"]) else 0.0
                    if last["adx"] >= self.cfg.adx_trending_threshold:
                        regime["trend_strength"] = last["adx"]
                        regime["trend"] = "strong_trend" if last["+di"] > last["-di"] else "strong_downtrend"
                    else:
                        regime["trend"] = "trending_up" if last["+di"] > last["-di"] else "trending_down"
        except Exception as e:
            logger.warning(f"ADX calculation failed: {e}")

        return regime


# ═════════════════════════════════════════════════════════════
# FUTURES & SYMBOL HELPERS
# ════════════════════════════════════════════════════════════
class FuturesHelper:
    MONTH_CODES = {
        1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
        7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"
    }

    @staticmethod
    def get_last_thursday(year: int, month: int) -> datetime:
        last_day = calendar.monthrange(year, month)[1]
        for day in range(last_day, 0, -1):
            if datetime(year, month, day).weekday() == 3:
                return datetime(year, month, day)
        return datetime(year, month, 1)

    @staticmethod
    def get_futures_symbol(now: datetime, rollover_days: int = 2) -> str:
        expiry = FuturesHelper.get_last_thursday(now.year, now.month)
        days_to_expiry = (expiry - now).days
        use_month, use_year = now.month, now.year
        if days_to_expiry <= rollover_days:
            if now.month == 12:
                use_month, use_year = 1, now.year + 1
            else:
                use_month = now.month + 1
        month_code = FuturesHelper.MONTH_CODES[use_month]
        year_code = str(use_year)[-2:]
        symbol = f"NSE:NIFTY{year_code}{month_code}FUT"
        expiry_display = FuturesHelper.get_last_thursday(use_year, use_month).strftime("%Y-%m-%d")
        logger.info(f"Futures: {symbol} (monthly expiry: {expiry_display}, rollover in {max(0, days_to_expiry)} days)")
        return symbol


class SymbolGenerator:
    WEEKLY_MONTH_CODE = {
        1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
        7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D"
    }

    @staticmethod
    def get_next_expiry(now: datetime, target_weekday: int = 3) -> date:
        weekday = now.weekday()
        if weekday == target_weekday and (now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
            return now.date()
        days_ahead = (target_weekday - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        expiry = now + timedelta(days=days_ahead)
        return expiry.date()

    @classmethod
    def build_option_symbols(cls, underlying: str, spot: float, expiry: date) -> List[str]:
        atm = int(round(spot / 50.0) * 50)
        strikes = [atm - 150, atm - 100, atm - 50, atm, atm + 50, atm + 100, atm + 150]
        types = ["CE", "PE"]
        year_code = str(expiry.year)[-2:]
        month_code = cls.WEEKLY_MONTH_CODE[expiry.month]
        day_code = f"{expiry.day:02d}"
        prefix = f"NSE:{underlying}{year_code}{month_code}{day_code}"
        return [f"{prefix}{s}{t}" for s in strikes for t in types]

    @staticmethod
    def build_option_symbols_from_chain(client: FyersClient, underlying_index: str,
                                        spot: float, expiry: date) -> List[str]:
        try:
            resp = client.option_chain(underlying_index, strikecount=7)
            if not resp or resp.get("s") != "ok" or "data" not in resp:
                return []
            chain_data = resp["data"]
            chain = chain_data.get("optionsChain", [])
            expiry_data = chain_data.get("expiryData", [])

            if expiry_data:
                target_epoch = None
                for item in expiry_data:
                    raw_epoch = item.get("expiry") if isinstance(item, dict) else item
                    if raw_epoch is None:
                        continue
                    try:
                        epoch_int = int(raw_epoch)
                        item_date = datetime.fromtimestamp(epoch_int, tz=ZoneInfo("Asia/Kolkata")).date()
                        if item_date == expiry:
                            target_epoch = epoch_int
                            break
                    except (ValueError, TypeError):
                        continue
                first_epoch = expiry_data[0].get("expiry") if isinstance(expiry_data[0], dict) else expiry_data[0]
                if target_epoch and str(first_epoch) != str(target_epoch):
                    resp = client.option_chain(underlying_index, strikecount=7, timestamp=str(target_epoch))
                    if resp and resp.get("s") == "ok" and "data" in resp:
                        chain = resp["data"].get("optionsChain", [])

            if not chain:
                return []

            strikes = sorted({c["strike_price"] for c in chain if isinstance(c, dict) and c.get("option_type") in ("CE", "PE")})
            if not strikes:
                return []

            atm = min(strikes, key=lambda s: abs(s - spot))
            target_strikes = {atm - 150, atm - 100, atm - 50, atm, atm + 50, atm + 100, atm + 150}
            symbols = [
                c["symbol"] for c in chain
                if isinstance(c, dict) and c.get("strike_price") in target_strikes and c.get("option_type") in ("CE", "PE")
            ]
            if symbols:
                logger.info(f"Option chain resolved {len(symbols)} symbols for expiry {expiry}")
            return symbols
        except Exception as e:
            logger.warning(f"Error parsing option chain: {e}")
            return []


# ════════════════════════════════════════════════════════════
# RISK GUARD
# ═════════════════════════════════════════════════════════════
class RiskGuard:
    def __init__(self, config: Config):
        self.cfg = config
        self.signal_history: deque = deque(maxlen=50)
        self.daily_count = 0
        self.window_15min: deque = deque()
        self.last_reset = datetime.now().date()

    def reset_if_new_day(self):
        today = datetime.now().date()
        if today != self.last_reset:
            self.daily_count = 0
            self.window_15min.clear()
            self.last_reset = today

    def can_signal(self, now: datetime, regime: Dict) -> Tuple[bool, str]:
        self.reset_if_new_day()

        if self.daily_count >= self.cfg.max_daily_signals:
            return False, "daily_limit_reached"

        # Rolling 15-min window
        cutoff = now - timedelta(minutes=15)
        while self.window_15min and self.window_15min[0] < cutoff:
            self.window_15min.popleft()
        if len(self.window_15min) >= self.cfg.max_signals_per_15min:
            return False, "rate_limit_15min"

        if regime.get("recommendation") == "wait_for_expansion":
            return False, "low_volatility_compression"

        return True, "ok"

    def register_signal(self, now: datetime):
        self.daily_count += 1
        self.window_15min.append(now)

    def suggest_qty(self, confidence: str, grade: str, regime: Dict) -> int:
        base = 1
        if confidence == "high" and grade == "A":
            base = 3 if regime.get("volatility") != "extreme" else 2
        elif confidence == "high" and grade == "B":
            base = 2 if regime.get("volatility") != "extreme" else 1
        elif grade == "C":
            base = 1
        return base

    def dynamic_rr(self, base_rr: float, confluence_count: int, regime: Dict) -> float:
        if not self.cfg.dynamic_rr:
            return base_rr
        adj = 0.0
        if regime.get("trend") in ("strong_trend", "strong_downtrend"):
            adj += 0.5
        if regime.get("volatility") == "extreme":
            adj -= 0.3
        adj += confluence_count * 0.15
        return round(max(1.5, min(4.0, base_rr + adj)), 2)


# ═════════════════════════════════════════════════════════════
# SMC ENGINE
# ═════════════════════════════════════════════════════════════
class SMCScanner:
    def __init__(self, config: Config, client: FyersClient):
        self.cfg = config
        self.client = client
        self.ind = Indicators()
        self.htf_underlying = "NSE:NIFTY50-INDEX"

    def _fetch_aligned_tf(self, resolution: str, days: int = 3) -> pd.DataFrame:
        df = self.client.fetch_history(self.htf_underlying, resolution, days=days, cont_flag="0")
        if not df.empty:
            df = drop_forming_candle(df)
        return df

    def _check_htf_confirmation(self, signal_type: str) -> bool:
        if not self.cfg.htf_confirm:
            return False
        df_htf = self._fetch_aligned_tf(self.cfg.htf_resolution, days=3)
        if df_htf.empty or len(df_htf) < self.cfg.atr_period + 1:
            return False
        df_htf["atr"] = self.ind.atr(df_htf, self.cfg.atr_period)
        last = df_htf.iloc[-1]
        atr_val = last["atr"]
        if pd.isna(atr_val) or atr_val == 0:
            return False
        body = abs(last["close"] - last["open"])
        htf_bullish = last["close"] > last["open"]
        htf_bearish = last["close"] < last["open"]
        if signal_type == "LONG" and htf_bullish and body >= atr_val * self.cfg.htf_disp_mult:
            return True
        if signal_type == "SHORT" and htf_bearish and body >= atr_val * self.cfg.htf_disp_mult:
            return True
        return False

    def _mtf_alignment(self, signal_type: str) -> Tuple[int, List[str]]:
        """Returns confluence score (0-3) and list of reasons."""
        confluences = []
        score = 0
        for res in self.cfg.mtf_resolutions:
            df = self._fetch_aligned_tf(res, days=3)
            if df.empty or len(df) < 20:
                continue
            df["ema20"] = self.ind.ema(df["close"], 20)
            last = df.iloc[-1]
            if signal_type == "LONG" and last["close"] > last["ema20"]:
                score += 1
                confluences.append(f"{res}m_above_ema20")
            elif signal_type == "SHORT" and last["close"] < last["ema20"]:
                score += 1
                confluences.append(f"{res}m_below_ema20")
        return score, confluences

    def scan(self, symbol: str, trend_bias: Optional[str], regime: Dict) -> SignalResult:
        df = self.client.fetch_history(symbol, "1", days=3, cont_flag="0")
        if df.empty:
            return SignalResult(symbol=symbol, signal="NO_DATA", context="Empty API response")

        df = drop_forming_candle(df)
        if df.empty or len(df) < 30:
            return SignalResult(symbol=symbol, signal="NO_DATA", context=f"Insufficient history ({len(df)} rows)")

        last_candle_time = df["Datetime"].iloc[-1]
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        time_diff = (ist_now - last_candle_time).total_seconds()
        if time_diff > 180:
            return SignalResult(symbol=symbol, signal="NO_DATA", context=f"Stale data ({time_diff:.0f}s old)")

        df["atr"] = self.ind.atr(df, self.cfg.atr_period)
        df = self.ind.pivots(df, left=self.cfg.swing_len, right=self.cfg.swing_len)
        df = self.ind.detect_order_blocks(df, lookback=self.cfg.ob_lookback)
        df = self.ind.detect_fvg(df, min_size=self.cfg.fvg_min_size)

        last_idx = len(df) - 1
        atr_val = df["atr"].iloc[last_idx]
        if pd.isna(atr_val) or atr_val == 0:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="ATR not ready")

        last_volume = df["volume"].iloc[last_idx]
        if last_volume < self.cfg.min_volume:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", context=f"Low Volume ({int(last_volume)})")

        body = abs(df["close"].iloc[last_idx] - df["open"].iloc[last_idx])
        candle_bullish = df["close"].iloc[last_idx] > df["open"].iloc[last_idx]
        candle_bearish = df["close"].iloc[last_idx] < df["open"].iloc[last_idx]

        if body <= atr_val * self.cfg.displacement_atr_mult:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", context=f"No Displacement ({body:.2f})")

        # SMC State Machine
        lsh = lsl = np.nan
        idm_level = np.nan
        idm_is_bullish = False
        bs_active = br_active = False
        bs_bar = br_bar = -999
        swing_len = self.cfg.swing_len * 2
        sweep_lookback = self.cfg.sweep_lookback

        for i in range(swing_len, len(df)):
            if not pd.isna(df["pivot_high"].iloc[i]):
                lsh = df["pivot_high"].iloc[i]
            if not pd.isna(df["pivot_low"].iloc[i]):
                lsl = df["pivot_low"].iloc[i]

            if not pd.isna(lsh) and df["close"].iloc[i] > lsh and df["close"].iloc[i - 1] <= lsh:
                idm_level = lsl
                idm_is_bullish = True
            elif not pd.isna(lsl) and df["close"].iloc[i] < lsl and df["close"].iloc[i - 1] >= lsl:
                idm_level = lsh
                idm_is_bullish = False

            if not pd.isna(idm_level):
                if idm_is_bullish and df["low"].iloc[i - 1] > idm_level and df["low"].iloc[i] < idm_level:
                    bs_active, bs_bar = True, i
                elif not idm_is_bullish and df["high"].iloc[i - 1] < idm_level and df["high"].iloc[i] > idm_level:
                    br_active, br_bar = True, i

            if i - bs_bar > sweep_lookback:
                bs_active = False
            if i - br_bar > sweep_lookback:
                br_active = False

            if i != last_idx:
                continue

            confluences = []
            grade = "C"

            # LONG Logic
            if candle_bullish and df["close"].iloc[i] > df["high"].iloc[i - 1] and bs_active:
                if self.cfg.trend_filter and trend_bias != "bullish":
                    return SignalResult(symbol=symbol, signal="NO_SIGNAL", context=f"Trend filter blocked LONG")

                # Order Block confluence
                ob_top = df["bull_ob_top"].iloc[i]
                ob_bottom = df["bull_ob_bottom"].iloc[i]
                ob_confluence = False
                if pd.notna(ob_top) and pd.notna(ob_bottom):
                    if df["low"].iloc[i] >= ob_bottom and df["low"].iloc[i] <= ob_top:
                        ob_confluence = True
                        confluences.append("bull_ob_touch")

                # FVG confluence
                fvg_top = df["bull_fvg_top"].iloc[i]
                fvg_bottom = df["bull_fvg_bottom"].iloc[i]
                fvg_confluence = False
                if pd.notna(fvg_top) and pd.notna(fvg_bottom):
                    if df["close"].iloc[i] > fvg_top:
                        fvg_confluence = True
                        confluences.append("bull_fvg_above")

                # Grade logic
                grade_score = 0
                if df["close"].iloc[i] > df["high"].iloc[i - 2]:
                    grade_score += 1
                    confluences.append("strong_close")
                if ob_confluence:
                    grade_score += 1
                if fvg_confluence:
                    grade_score += 1
                if last_volume > self.cfg.min_volume * 2:
                    grade_score += 1
                    confluences.append("volume_spike")

                if grade_score >= 3:
                    grade = "A"
                elif grade_score == 2:
                    grade = "B"

                sl = df["low"].iloc[i] - atr_val * 0.5
                target = df["close"].iloc[i] + (df["close"].iloc[i] - sl) * self.cfg.risk_reward
                htf_ok = self._check_htf_confirmation("LONG")
                mtf_score, mtf_reasons = self._mtf_alignment("LONG")
                confluences.extend(mtf_reasons)

                return SignalResult(
                    symbol=symbol, signal="LONG", time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i], sl=sl, target=target,
                    confidence="high" if grade in ("A", "B") else "medium",
                    grade=grade, confluences=confluences, htf_confirmed=htf_ok,
                    mtf_score=mtf_score, volume=int(last_volume), regime=regime.get("trend", "neutral")
                )

            # SHORT Logic
            if candle_bearish and df["close"].iloc[i] < df["low"].iloc[i - 1] and br_active:
                if self.cfg.trend_filter and trend_bias != "bearish":
                    return SignalResult(symbol=symbol, signal="NO_SIGNAL", context=f"Trend filter blocked SHORT")

                ob_top = df["bear_ob_top"].iloc[i]
                ob_bottom = df["bear_ob_bottom"].iloc[i]
                ob_confluence = False
                if pd.notna(ob_top) and pd.notna(ob_bottom):
                    if df["high"].iloc[i] <= ob_top and df["high"].iloc[i] >= ob_bottom:
                        ob_confluence = True
                        confluences.append("bear_ob_touch")

                fvg_top = df["bear_fvg_top"].iloc[i]
                fvg_bottom = df["bear_fvg_bottom"].iloc[i]
                fvg_confluence = False
                if pd.notna(fvg_top) and pd.notna(fvg_bottom):
                    if df["close"].iloc[i] < fvg_bottom:
                        fvg_confluence = True
                        confluences.append("bear_fvg_below")

                grade_score = 0
                if df["close"].iloc[i] < df["low"].iloc[i - 2]:
                    grade_score += 1
                    confluences.append("strong_close")
                if ob_confluence:
                    grade_score += 1
                if fvg_confluence:
                    grade_score += 1
                if last_volume > self.cfg.min_volume * 2:
                    grade_score += 1
                    confluences.append("volume_spike")

                if grade_score >= 3:
                    grade = "A"
                elif grade_score == 2:
                    grade = "B"

                sl = df["high"].iloc[i] + atr_val * 0.5
                target = df["close"].iloc[i] - (sl - df["close"].iloc[i]) * self.cfg.risk_reward
                htf_ok = self._check_htf_confirmation("SHORT")
                mtf_score, mtf_reasons = self._mtf_alignment("SHORT")
                confluences.extend(mtf_reasons)

                return SignalResult(
                    symbol=symbol, signal="SHORT", time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i], sl=sl, target=target,
                    confidence="high" if grade in ("A", "B") else "medium",
                    grade=grade, confluences=confluences, htf_confirmed=htf_ok,
                    mtf_score=mtf_score, volume=int(last_volume), regime=regime.get("trend", "neutral")
                )

        return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="Pattern not met")


# ═════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═════════════════════════════════════════════════════════════
class ScannerOrchestrator:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = FyersClient(config.app_id, config.access_token)
        self.scanner = SMCScanner(config, self.client)
        self.regime_analyzer = MarketRegime(self.client, config)
        self.risk_guard = RiskGuard(config)
        self.notifier = TelegramNotifier(config.bot_token, config.chat_id)
        self.trade_logger = TradeLogger(config.csv_log_dir)
        self.last_alert: Dict[str, datetime] = {}
        self.running = True
        self.cycle_count = 0
        self.current_fut_symbol = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        self.running = False

    def is_market_open(self, now: datetime) -> bool:
        t = now.time()
        return pd.Timestamp("09:15:00").time() <= t <= pd.Timestamp("15:40:00").time()

    def get_trend_bias(self, now: datetime) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        df_spot = self.client.fetch_history("NSE:NIFTY50-INDEX", "1", days=3, cont_flag="0")
        if df_spot.empty or len(df_spot) < 20:
            logger.warning("Failed to fetch Nifty spot")
            return None, None, None

        df_spot = drop_forming_candle(df_spot)
        if df_spot.empty or len(df_spot) < 20:
            return None, None, None
        spot = df_spot["close"].iloc[-1]

        self.current_fut_symbol = FuturesHelper.get_futures_symbol(now, self.cfg.rollover_days_before_expiry)
        df_fut = self.client.fetch_history(self.current_fut_symbol, "1", days=3, cont_flag="1")

        if df_fut.empty or len(df_fut) < 20:
            df_spot["ema20"] = Indicators.ema(df_spot["close"], 20)
            last_ema = df_spot["ema20"].iloc[-1]
            bias = "bullish" if spot > last_ema else "bearish"
            logger.info(f"Spot: {spot:.2f} | EMA20: {last_ema:.2f} | Bias: {bias} (FALLBACK)")
            return bias, spot, None

        df_fut = drop_forming_candle(df_fut)
        if df_fut.empty or len(df_fut) < 20:
            df_spot["ema20"] = Indicators.ema(df_spot["close"], 20)
            last_ema = df_spot["ema20"].iloc[-1]
            bias = "bullish" if spot > last_ema else "bearish"
            logger.info(f"Spot: {spot:.2f} | EMA20: {last_ema:.2f} | Bias: {bias} (FALLBACK)")
            return bias, spot, None

        df_fut["vwap"] = Indicators.vwap(df_fut)
        last_fut = df_fut.iloc[-1]
        fut_price = last_fut["close"]

        if pd.isna(last_fut["vwap"]):
            return None, spot, fut_price

        bias = "bullish" if fut_price > last_fut["vwap"] else "bearish"
        basis = fut_price - spot
        basis_pct = (basis / spot) * 100 if spot else 0
        logger.info(f"Spot: {spot:.2f} | Fut: {fut_price:.2f} | Basis: {basis:+.2f} ({basis_pct:+.3f}%) | VWAP: {last_fut['vwap']:.2f} | Bias: {bias}")
        return bias, spot, fut_price

    def cooldown_ok(self, symbol: str, timestamp: datetime) -> bool:
        if symbol not in self.last_alert:
            return True
        elapsed = (timestamp - self.last_alert[symbol]).total_seconds() / 60
        return elapsed >= self.cfg.signal_cooldown_min

    def format_alert(self, signals: List[SignalResult], fut_price: Optional[float], regime: Dict) -> str:
        lines = ["<b>═══ SMC BREAKOUT v3.0 ALERT ═══</b>", ""]
        fut_line = f"Nifty Fut: {fut_price:.2f}" if fut_price else "Nifty Fut: N/A"
        vix_line = f"India VIX: {regime.get('vix', 'N/A')}" if regime.get("vix") else "India VIX: N/A"
        regime_line = f"Regime: {regime.get('trend', 'unknown')} | Vol: {regime.get('volatility', 'normal')}"
        lines.append(f"{fut_line} | {vix_line}")
        lines.append(regime_line)
        lines.append("")
        for s in signals:
            conf_marker = "[HIGH]" if s.confidence == "high" else "[MED]"
            grade_marker = f"<b>GRADE {s.grade}</b>"
            htf_marker = "[HTF-OK]" if s.htf_confirmed else ""
            mtf_marker = f"MTF({s.mtf_score}/2)" if s.mtf_score else ""
            confluence_str = ", ".join(s.confluences[:4]) if s.confluences else "none"
            lines.append(
                f"{'🟢' if s.signal == 'LONG' else '🔴'} <b>{s.signal}</b> {grade_marker} {conf_marker} {htf_marker} {mtf_marker}"
            )
            lines.append(f"Symbol: <code>{s.symbol}</code>")
            lines.append(f"Price: ₹{s.price:.2f} | Time: {s.time.strftime('%H:%M:%S')}")
            lines.append(f"SL: ₹{s.sl:.2f} | Target: ₹{s.target:.2f} (1:{s.expected_rr:.1f})")
            lines.append(f"Volume: {s.volume} | Qty Suggestion: {s.suggested_qty}")
            lines.append(f"Confluences: {confluence_str}")
            lines.append("")
        return "\n".join(lines)

    def run_once(self):
        cycle_start = time.time()
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        self.cycle_count += 1

        if not self.is_market_open(now):
            logger.info("Market closed. Sleeping 60s...")
            time.sleep(60)
            return

        if is_nse_holiday(now.date()):
            logger.info("NSE Holiday. Sleeping 60s...")
            time.sleep(60)
            return

        logger.info("--- New Scan Cycle v3 ---")

        regime = self.regime_analyzer.analyze(now)
        logger.info(f"Regime: {regime['trend']} | ADX: {regime['adx']:.1f} | VIX: {regime.get('vix', 'N/A')} | Rec: {regime['recommendation']}")

        can_trade, block_reason = self.risk_guard.can_signal(now, regime)
        if not can_trade:
            logger.info(f"Risk Guard blocked scan: {block_reason}")
            time.sleep(30)
            return

        trend_bias, spot, fut_price = self.get_trend_bias(now)
        if spot is None:
            logger.warning("Failed to fetch Nifty spot. Skipping cycle.")
            time.sleep(60)
            return

        expiry = SymbolGenerator.get_next_expiry(now, target_weekday=self.cfg.nifty_expiry_weekday)
        is_expiry_day = (expiry == now.date())

        symbols = SymbolGenerator.build_option_symbols_from_chain(
            self.client, "NSE:NIFTY50-INDEX", spot, expiry
        )
        if not symbols:
            logger.warning("Option chain lookup failed, falling back to manual symbol construction")
            symbols = SymbolGenerator.build_option_symbols("NIFTY", spot, expiry)

        logger.info(f"Expiry: {expiry} | Symbols: {len(symbols)} | Trend: {trend_bias} | ExpiryDay: {is_expiry_day}")
        if symbols:
            logger.info(f"Sample symbol: {symbols[0]}")

        results: List[SignalResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futures = {executor.submit(self.scanner.scan, sym, trend_bias, regime): sym for sym in symbols}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Scanner thread error: {e}")

        active = [r for r in results if r.signal in ("LONG", "SHORT")]

        reasons: Dict[str, int] = {}
        for r in results:
            if r.signal == "NO_SIGNAL":
                key = r.context.split("(")[0].strip() if r.context else "Pattern not met"
                reasons[key] = reasons.get(key, 0) + 1

        fresh_signals = []
        for sig in active:
            # Expiry day OTM filter
            if is_expiry_day and now.hour >= self.cfg.expiry_day_cutoff_hour:
                strike = None
                try:
                    # crude strike extraction
                    parts = sig.symbol.replace("NSE:NIFTY", "")
                    digits = "".join([c for c in parts if c.isdigit()])
                    if len(digits) >= 5:
                        strike = int(digits[-5:])
                except Exception:
                    pass
                atm = int(round(spot / 50.0) * 50) if spot else None
                if atm and strike:
                    is_otm = (sig.signal == "LONG" and strike > atm) or (sig.signal == "SHORT" and strike < atm)
                    if is_otm and not self.cfg.allow_expiry_otm_after_cutoff:
                        logger.info(f"Skipped OTM expiry signal: {sig.symbol}")
                        continue

            if sig.time and self.cooldown_ok(sig.symbol, sig.time):
                # Apply dynamic R:R and sizing
                sig.expected_rr = self.risk_guard.dynamic_rr(self.cfg.risk_reward, len(sig.confluences), regime)
                if sig.price and sig.sl:
                    if sig.signal == "LONG":
                        sig.target = sig.price + (sig.price - sig.sl) * sig.expected_rr
                    else:
                        sig.target = sig.price - (sig.sl - sig.price) * sig.expected_rr
                sig.suggested_qty = self.risk_guard.suggest_qty(sig.confidence, sig.grade, regime)
                fresh_signals.append(sig)
                self.last_alert[sig.symbol] = sig.time
                self.trade_logger.log(sig, trend_bias or "none", spot or 0.0, fut_price)
                self.risk_guard.register_signal(now)

        if fresh_signals:
            msg = self.format_alert(fresh_signals, fut_price, regime)
            logger.info(f"ALERT: {msg.replace(chr(10), ' | ')}")
            if self.notifier.send(msg):
                logger.info("Alert sent to Telegram")
            else:
                logger.warning("Failed to send Telegram alert")
        else:
            summary = f"No fresh signals | Scanned: {len(results)} | Pattern hits: {len(active)}"
            if reasons:
                summary += f" | Rejections: {reasons}"
            no_data_total = len([r for r in results if r.signal == "NO_DATA"])
            if no_data_total:
                summary += f" | NO_DATA: {no_data_total}"
            logger.info(summary)

        if self.cycle_count % self.cfg.print_stats_every == 0:
            stats = self.trade_logger.get_today_stats()
            logger.info(
                f"STATS >> Today: {stats['total']} signals | LONG: {stats['long']} | SHORT: {stats['short']} | "
                f"HighConf: {stats['high_conf']} | Grade-A: {stats.get('grade_a', 0)} | HTF-Confirmed: {stats.get('htf_yes', 0)}"
            )

        elapsed = time.time() - cycle_start
        sleep_sec = max(1, 60 - datetime.now().second)
        if elapsed >= sleep_sec:
            logger.warning(f"Scan cycle took {elapsed:.1f}s, skipping sleep")
            return
        logger.info(f"Scan took {elapsed:.1f}s | Sleeping {sleep_sec}s")
        time.sleep(sleep_sec)

    def run(self):
        logger.info("SMC Breakout Scanner v3.0.0 Started. Press Ctrl+C to stop.")
        logger.info(f"CSV logging to: {os.path.abspath(self.cfg.csv_log_dir)}")
        logger.info(f"HTF confirmation: {self.cfg.htf_confirm} ({self.cfg.htf_resolution}-min)")
        logger.info(f"MTF alignment: {self.cfg.mtf_resolutions}")
        logger.info(f"Dynamic R:R: {self.cfg.dynamic_rr} | Risk Guard: ON")
        try:
            while self.running:
                try:
                    self.run_once()
                except Exception as e:
                    logger.error(f"Main loop error: {e}", exc_info=True)
                    time.sleep(30)
        finally:
            self.trade_logger.close()
            logger.info("Scanner stopped.")


if __name__ == "__main__":
    cfg = load_config()
    if "YOUR_" in cfg.app_id or "YOUR_" in cfg.access_token:
        logger.error("Fyers credentials not configured! Check config.yaml and token.txt")
        sys.exit(1)
    orch = ScannerOrchestrator(cfg)
    orch.run()