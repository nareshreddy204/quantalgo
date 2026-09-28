"""
═══════════════════════════════════════════════════════════════════
SMC BREAKOUT SCANNER v3.2 — WebSocket + SMC-Library Edition (Fixed)
═══════════════════════════════════════════════════════════════════
Integrated upgrades over v2.9.1:

1. smartmoneyconcepts (pip install smartmoneyconcepts)
   • smc.swing_highs_lows / smc.bos_choch / smc.liquidity /
     smc.fvg / smc.ob replace the hand-rolled pivot loop.
   • Signals now require a real structure break (BOS/CHoCH) +
     displacement, with optional FVG / order-block confluence.

2. SMC-Screener style filters
   • Premium/Discount zone filter (longs only in discount,
     shorts only in premium, vs latest swing range equilibrium).
   • Confluence scoring drives confidence: liquidity sweep,
     FVG, order block, displacement size, HTF confirmation.

3. Fyers v3 Data Socket (fyers_apiv3.FyersWebsocket.data_ws)
   • Live SymbolUpdate ticks build 1-min candles in memory.
   • Scans trigger on candle CLOSE — no REST polling of option
     symbols every minute (kills your rate-limit problem).
   • REST mode retained (--rest) as fallback + history seeding.

New config.yaml keys (under scanner:):
  use_websocket: true        smc_swing_length: 10
  close_break: true          premium_discount_filter: true
  require_bos_confirm: false fvg_confluence: true
  ob_confluence: false       liq_confluence: true

Install:  pip install smartmoneyconcepts fyers-apiv3 pandas numpy pyyaml requests
Run:      python smc_scanner_v3.py               # websocket mode
          python smc_scanner_v3.py --rest        # legacy polling mode
          python smc_scanner_v3.py --test-symbol NSE:NIFTY50-INDEX
═══════════════════════════════════════════════════════════════════
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
import argparse
import json
import re
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np
import concurrent.futures

warnings.filterwarnings("ignore")

try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except ImportError:
    fyersModel = None
    data_ws = None

try:
    from smartmoneyconcepts import smc
except ImportError:
    smc = None

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def setup_logging(debug: bool = False) -> logging.Logger:
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%H:%M:%S"
    logger = logging.getLogger("SMCScanner")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(logging.Formatter(log_format, date_format))
    fh = logging.FileHandler("scanner.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


logger = setup_logging(debug=False)


def ist_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def drop_forming_candle(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Datetime" not in df.columns:
        return df
    now = ist_now().replace(tzinfo=None)
    current_minute = pd.Timestamp(now).floor("min")
    if df["Datetime"].iloc[-1] >= current_minute:
        df = df.iloc[:-1].reset_index(drop=True)
    return df


VERSION = "3.2-fixed"
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"


# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
@dataclass
class Config:
    app_id: str
    access_token: str
    bot_token: str
    chat_id: str
    # v2.9 legacy
    swing_len: int = 3
    sweep_lookback: int = 3
    displacement_atr_mult: float = 0.5
    atr_period: int = 14
    signal_cooldown_min: int = 5
    risk_reward: float = 2.0
    trend_filter: bool = True
    max_workers: int = 4
    min_volume: int = 1000
    csv_log_dir: str = "trade_logs"
    htf_confirm: bool = True
    htf_resolution: str = "5"
    htf_disp_mult: float = 0.3
    print_stats_every: int = 15
    rollover_days_before_expiry: int = 2
    nifty_expiry_weekday: int = 1  # Tuesday (NSE NIFTY expiry day)
    debug_mode: bool = False
    validate_symbols: bool = True
    # v3.0 additions
    use_websocket: bool = True
    smc_swing_length: int = 10
    close_break: bool = True
    premium_discount_filter: bool = True
    require_bos_confirm: bool = False
    fvg_confluence: bool = True
    ob_confluence: bool = False
    liq_confluence: bool = True
    max_buffer_rows: int = 6000
    volume_filter_ws_bypass: bool = True
    # v3.1 additions
    htf_swing_length: int = 5
    htf_lookback: int = 15
    zone_reaction_entry: bool = True
    breakout_entry: bool = True
    option_refresh_minutes: int = 15
    market_start: str = "09:15"
    market_end: str = "15:30"

def load_config(path: str = "config.yaml") -> Config:
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Could not load config YAML: {e}")

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
        signal_cooldown_min=sc.get("signal_cooldown_min", 5),
        risk_reward=sc.get("risk_reward", 2.0),
        trend_filter=sc.get("trend_filter", True),
        max_workers=sc.get("max_workers", 4),
        min_volume=sc.get("min_volume", 1000),
        csv_log_dir=sc.get("csv_log_dir", "trade_logs"),
        htf_confirm=sc.get("htf_confirm", True),
        htf_resolution=str(sc.get("htf_resolution", "5")),
        htf_disp_mult=sc.get("htf_disp_mult", 0.3),
        print_stats_every=sc.get("print_stats_every", 15),
        rollover_days_before_expiry=sc.get("rollover_days_before_expiry", 2),
        nifty_expiry_weekday=sc.get("nifty_expiry_weekday", 1),
        debug_mode=sc.get("debug_mode", False),
        validate_symbols=sc.get("validate_symbols", True),
        use_websocket=sc.get("use_websocket", True),
        smc_swing_length=sc.get("smc_swing_length", 10),
        close_break=sc.get("close_break", True),
        premium_discount_filter=sc.get("premium_discount_filter", True),
        require_bos_confirm=sc.get("require_bos_confirm", False),
        fvg_confluence=sc.get("fvg_confluence", True),
        ob_confluence=sc.get("ob_confluence", False),
        liq_confluence=sc.get("liq_confluence", True),
        max_buffer_rows=sc.get("max_buffer_rows", 6000),
        volume_filter_ws_bypass=sc.get("volume_filter_ws_bypass", True),
        htf_swing_length=sc.get("htf_swing_length", 5),
        htf_lookback=sc.get("htf_lookback", 15),
        zone_reaction_entry=sc.get("zone_reaction_entry", True),
        breakout_entry=sc.get("breakout_entry", True),
        option_refresh_minutes=sc.get("option_refresh_minutes", 15),
        market_start=str(sc.get("market_start", "09:15")),
        market_end=str(sc.get("market_end", "15:30")),
    )

# ══════════════════════════════════════════════════════════════════
# SIGNAL / LOGGING / NOTIFY
# ══════════════════════════════════════════════════════════════════
@dataclass
class SignalResult:
    symbol: str
    signal: str
    time: Optional[datetime] = None
    price: Optional[float] = None
    sl: Optional[float] = None
    target: Optional[float] = None
    confidence: str = "medium"
    context: str = ""
    htf_confirmed: bool = False
    volume: int = 0
    no_data_reason: str = ""
    structure: str = ""       # v3: e.g. "BOS↑", "CHoCH↓", "none"
    zone: str = ""            # v3: "discount" | "premium" | "n/a"
    confluence: str = ""      # v3: comma list e.g. "sweep,FVG,HTF"


class TradeLogger:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.columns = [
            "date", "time", "symbol", "signal", "entry_price", "sl", "target",
            "risk_reward", "confidence", "trend_bias", "nifty_spot", "nifty_fut",
            "basis", "volume", "htf_confirmed", "structure", "zone", "confluence", "context"
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
            nifty_fut: Optional[float], htf_confirmed: bool = False):
        with self._lock:
            expected_path = os.path.join(self.log_dir, f"signals_{datetime.now().strftime('%Y%m%d')}.csv")
            if expected_path != self._file_path:
                if self._file:
                    self._file.close()
                self._init_daily_file()

            rr_str = ""
            if result.price and result.target and result.sl and (result.price - result.sl) != 0:
                rr = abs((result.target - result.price) / (result.price - result.sl))
                rr_str = f"1:{rr:.1f}"
            basis = f"{nifty_fut - nifty_spot:+.2f}" if (nifty_fut and nifty_spot) else ""

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
                "trend_bias": trend_bias or "none",
                "nifty_spot": f"{nifty_spot:.2f}" if nifty_spot else "",
                "nifty_fut": f"{nifty_fut:.2f}" if nifty_fut else "",
                "basis": basis,
                "volume": str(result.volume),
                "htf_confirmed": "YES" if htf_confirmed else "NO",
                "structure": result.structure,
                "zone": result.zone,
                "confluence": result.confluence,
                "context": result.context
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
                return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0}
            try:
                df = pd.read_csv(self._file_path)
                if df.empty:
                    return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0}
                return {
                    "total": len(df),
                    "long": len(df[df["signal"] == "LONG"]),
                    "short": len(df[df["signal"] == "SHORT"]),
                    "high_conf": len(df[df["confidence"] == "high"]),
                    "htf_yes": len(df[df["htf_confirmed"] == "YES"])
                }
            except Exception as e:
                logger.warning(f"Could not read stats: {e}")
                return {"total": 0, "long": 0, "short": 0, "high_conf": 0, "htf_yes": 0}


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = "YOUR_" not in bot_token and "YOUR_" not in chat_id
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                self.url,
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False


# ══════════════════════════════════════════════════════════════════
# FYERS CLIENT (REST — kept for seeding, quotes, option chain)
# ══════════════════════════════════════════════════════════════════
class FyersClient:
    DATA_BASE = "https://api-t1.fyers.in/data"

    def __init__(self, app_id: str, access_token: str, debug: bool = False):
        self.app_id = app_id
        self.access_token = access_token
        self.debug = debug
        self.fyers = None
        if fyersModel is not None:
            try:
                self.fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=False, log_path="")
            except Exception as e:
                logger.warning(f"FyersModel init failed: {e}")
        self._auth_header = f"{app_id}:{access_token}"
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._min_interval = 0.34  # v3: stay well under Fyers rate limits

    def _throttle(self):
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.time()

    def _log_debug(self, msg: str, data=None):
        if self.debug:
            try:
                snippet = json.dumps(data, indent=2)[:800] if data is not None else ""
                logger.debug(f"{msg}\n{snippet}")
            except Exception:
                logger.debug(msg)

    def quotes(self, symbols: List[str]) -> Optional[dict]:
        if not symbols:
            return None
        payload = {"symbols": ",".join(symbols)}
        try:
            self._throttle()
            if self.fyers and hasattr(self.fyers, "quotes"):
                resp = self.fyers.quotes(payload)
                self._log_debug(f"Quotes response for {symbols[:3]}...", resp)
                if resp and resp.get("s") == "ok":
                    return resp
            self._throttle()
            import urllib.parse
            url = f"{self.DATA_BASE}/quotes?{urllib.parse.urlencode(payload)}"
            headers = {"Authorization": self._auth_header,
                       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
            r = requests.get(url, headers=headers, timeout=10)
            resp = r.json()
            self._log_debug(f"Quotes HTTP response for {symbols[:3]}...", resp)
            if resp.get("s") == "ok":
                return resp
        except Exception as e:
            logger.warning(f"Quotes fetch failed: {e}")
        return None

    def history_with_retry(self, symbol: str, resolution: str, range_from: int,
                           range_to: int, cont_flag: str = "0", max_retries: int = 3) -> Optional[dict]:
        payload = {"symbol": symbol, "resolution": resolution, "date_format": "0",
                   "range_from": range_from, "range_to": range_to, "cont_flag": cont_flag}
        self._log_debug(f"History request: {payload}")
        for attempt in range(max_retries):
            self._throttle()
            try:
                if self.fyers:
                    resp = self.fyers.history(payload)
                    self._log_debug(f"History response ({symbol}, attempt {attempt+1}):", resp)
                    if resp and resp.get("s") == "ok":
                        return resp
                    msg = resp.get("message", "Unknown") if resp else "Empty API response"
                    if "limit reached" in msg.lower():
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
        end_dt = ist_now()
        start_dt = end_dt - timedelta(days=days)
        all_rows = []
        windows = [(end_dt, start_dt), (end_dt, end_dt - timedelta(days=1))]
        for win_end, win_start in windows:
            _cursor = win_end
            _rows = []
            while _cursor > win_start:
                _win_start = _cursor - timedelta(days=50)
                if _win_start < win_start:
                    _win_start = win_start
                resp = self.history_with_retry(
                    symbol=symbol, resolution=resolution,
                    range_from=int(_win_start.timestamp()),
                    range_to=int(_cursor.timestamp()), cont_flag=cont_flag)
                if resp is None or resp.get("s") != "ok":
                    return pd.DataFrame()
                candles = resp.get("candles", [])
                if not candles:
                    _cursor = _win_start - timedelta(seconds=1)
                    continue
                _rows.extend(candles)
                _cursor = _win_start - timedelta(seconds=1)
            if _rows:
                all_rows = _rows
                break

        if not all_rows:
            return pd.DataFrame()
        df = pd.DataFrame(all_rows, columns=["epoch", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
        df["Datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df[["Datetime", "open", "high", "low", "close", "volume"]].dropna()

    def option_chain(self, underlying: str, strikecount: int = 5,
                     timestamp: Optional[str] = None) -> Optional[dict]:
        payload = {"symbol": underlying, "strikecount": strikecount}
        if timestamp:
            payload["timestamp"] = str(timestamp)
        if self.fyers:
            try:
                self._throttle()
                resp = self.fyers.optionchain(data=payload)
                self._log_debug("SDK optionchain response:", resp)
                if resp and resp.get("s") == "ok":
                    return resp
                logger.warning(f"SDK optionchain warning: {resp.get('message') if resp else 'empty response'}")
            except AttributeError:
                logger.warning("fyersModel has no 'optionchain' method; attempting HTTP fallback")
            except Exception as e:
                logger.warning(f"SDK optionchain exception: {e}")
        try:
            self._throttle()
            import urllib.parse
            url = f"{self.DATA_BASE}/options-chain-v3?{urllib.parse.urlencode(payload)}"
            headers = {"Authorization": self._auth_header,
                       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                       "Content-Type": "application/json"}
            r = requests.get(url, headers=headers, timeout=10)
            resp = r.json()
            self._log_debug("HTTP optionchain response:", resp)
            if resp.get("s") == "ok":
                return resp
            logger.warning(f"Direct HTTP option chain error: {resp.get('message')}")
        except Exception as e:
            logger.warning(f"Direct HTTP option chain failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# INDICATORS / FUTURES / SYMBOL GENERATION (unchanged core)
# ══════════════════════════════════════════════════════════════════
class Indicators:
    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

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
            with np.errstate(divide="ignore", invalid="ignore"):
                v = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
            vwap_vals[group.index] = v
        return pd.Series(vwap_vals, index=df.index)

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()


class FuturesHelper:
    MONTH_CODES = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
                   7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}

    @staticmethod
    def get_last_tuesday(year: int, month: int) -> datetime:
        """Return calendar last Tuesday; exchange holidays are handled by the
        live/chain resolution path where possible."""
        last_day = calendar.monthrange(year, month)[1]
        for day in range(last_day, 0, -1):
            if datetime(year, month, day).weekday() == 1:
                return datetime(year, month, day, tzinfo=ZoneInfo("Asia/Kolkata"))
        return datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Kolkata"))

    @staticmethod
    def get_futures_symbol(now: datetime, rollover_days: int = 2) -> str:
        """Build the near NIFTY monthly future symbol using the current NSE
        expiry convention (last Tuesday of the expiry month)."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        expiry = FuturesHelper.get_last_tuesday(now.year, now.month)
        days_to_expiry = (expiry - now).total_seconds() / 86400.0
        use_month, use_year = now.month, now.year
        if days_to_expiry <= rollover_days:
            if now.month == 12:
                use_month, use_year = 1, now.year + 1
            else:
                use_month = now.month + 1
        month_code = FuturesHelper.MONTH_CODES[use_month]
        year_code = str(use_year)[-2:]
        return f"NSE:NIFTY{year_code}{month_code}FUT"


class SymbolGenerator:
    WEEKLY_MONTH_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
                         7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D"}

    @staticmethod
    def get_next_expiry(now: datetime, target_weekday: int = 1) -> date:
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        weekday = now.weekday()
        if weekday == target_weekday and (now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
            return now.date()
        days_ahead = (target_weekday - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).date()

    @classmethod
    def build_option_symbols(cls, underlying: str, spot: float, expiry: date) -> List[str]:
        atm = int(round(spot / 50.0) * 50)
        strikes = [atm - 100, atm - 50, atm, atm + 50, atm + 100]
        year_code = str(expiry.year)[-2:]
        month_code = cls.WEEKLY_MONTH_CODE[expiry.month]
        day_code = f"{expiry.day:02d}"
        prefix = f"NSE:{underlying}{year_code}{month_code}{day_code}"
        return [f"{prefix}{s}{t}" for s in strikes for t in ["CE", "PE"]]

    @staticmethod
    def build_option_symbols_from_chain(client: FyersClient, underlying_index: str,
                                        spot: float, expiry: date) -> List[str]:
        try:
            resp = client.option_chain(underlying_index, strikecount=5)
            if not resp or resp.get("s") != "ok" or "data" not in resp:
                return []
            chain_data = resp["data"]
            chain = chain_data.get("optionsChain", [])
            expiry_data = chain_data.get("expiryData", [])

            if expiry_data:
                available = []
                for item in expiry_data:
                    raw_epoch = item.get("expiry") if isinstance(item, dict) else item
                    if raw_epoch is None:
                        continue
                    try:
                        epoch_int = int(raw_epoch)
                        item_date = datetime.fromtimestamp(
                            epoch_int, tz=ZoneInfo("Asia/Kolkata")
                        ).date()
                        available.append((item_date, epoch_int))
                    except (ValueError, TypeError, OSError):
                        continue

                if available:
                    # Prefer the requested expiry; if it is a trading holiday
                    # or unavailable, use the nearest listed expiry. This keeps
                    # the option chain authoritative instead of inventing a
                    # holiday contract symbol.
                    available.sort(key=lambda x: x[0])
                    exact = [x for x in available if x[0] == expiry]
                    if exact:
                        actual_date, target_epoch = exact[0]
                    else:
                        actual_date, target_epoch = min(
                            available,
                            key=lambda x: abs((x[0] - expiry).days)
                        )
                    first_epoch = available[0][1]
                    if target_epoch != first_epoch:
                        resp = client.option_chain(
                            underlying_index,
                            strikecount=5,
                            timestamp=str(target_epoch),
                        )
                        if resp and resp.get("s") == "ok" and "data" in resp:
                            chain = resp["data"].get("optionsChain", [])
                    if actual_date != expiry:
                        logger.info(
                            f"Requested expiry {expiry} not listed; using chain expiry {actual_date}"
                        )

            if not chain:
                return []
            strikes = sorted({c["strike_price"] for c in chain
                              if isinstance(c, dict) and c.get("option_type") in ("CE", "PE")})
            if not strikes:
                return []
            atm = min(strikes, key=lambda s: abs(s - spot))
            target_strikes = {atm - 100, atm - 50, atm, atm + 50, atm + 100}
            symbols = [c["symbol"] for c in chain
                       if isinstance(c, dict) and c.get("strike_price") in target_strikes
                       and c.get("option_type") in ("CE", "PE")]
            if symbols:
                logger.info(f"Option chain resolved {len(symbols)} symbols for expiry {expiry}")
            return symbols
        except Exception as e:
            logger.warning(f"Error parsing option chain: {e}")
            return []

    @staticmethod
    def filter_symbols_for_signal(symbols: List[str], signal: str, spot: float) -> List[str]:
        """Select nearby CE/PE contracts using exact symbol suffixes. This
        avoids accidentally matching expiry/date digits as part of a strike."""
        atm = int(round(spot / 50.0) * 50)

        if signal == "LONG":
            want = [atm, atm + 50, atm + 100]
            option_type = "CE"
        else:
            want = [atm, atm - 50, atm - 100]
            option_type = "PE"

        ranked = []
        for sym in symbols:
            for rank, strike in enumerate(want):
                if sym.endswith(f"{strike}{option_type}"):
                    ranked.append((rank, sym))
                    break

        ranked.sort(key=lambda x: x[0])
        return [sym for _, sym in ranked]


# ══════════════════════════════════════════════════════════════════
# V3.0 — LIVE CANDLE BUFFER (builds 1-min candles from WS ticks)
# ══════════════════════════════════════════════════════════════════
class CandleBuffer:
    """Accumulates WebSocket ticks into 1-min closed candles (like the
    quantitative-trading-bot approach). Scan logic always reads CLOSED
    candles only — the forming candle lives in private tick state."""

    def __init__(self, symbol: str, max_rows: int = 6000):
        self.symbol = symbol
        self.max_rows = max_rows
        self._lock = threading.Lock()
        self.df = pd.DataFrame(columns=["Datetime", "open", "high", "low", "close", "volume"])
        self._minute: Optional[datetime] = None
        self._o = self._h = self._l = self._c = np.nan
        self._v = 0
        self._last_cum_vol: Optional[float] = None
        self.last_close_time: Optional[datetime] = None
        self.last_tick_time: Optional[datetime] = None
        self.has_volume = False

    def seed(self, df: Optional[pd.DataFrame]):
        if df is None or df.empty:
            return
        with self._lock:
            self.df = df.tail(self.max_rows).reset_index(drop=True)
            if "volume" in self.df.columns and float(self.df["volume"].fillna(0).max()) > 0:
                self.has_volume = True
            if not self.df.empty:
                self.last_close_time = self.df["Datetime"].iloc[-1]

    def snapshot(self) -> pd.DataFrame:
        with self._lock:
            return self.df.copy()

    def on_tick(self, ltp, ts: Optional[datetime] = None, cum_volume: Optional[float] = None):
        if ltp is None:
            return
        try:
            ltp = float(ltp)
        except (TypeError, ValueError):
            return
        if ts is None:
            ts = ist_now().replace(tzinfo=None)
        minute = ts.replace(second=0, microsecond=0)

        vol_delta = 0
        if cum_volume is not None:
            try:
                cum_volume = float(cum_volume)
                if self._last_cum_vol is not None and cum_volume > self._last_cum_vol:
                    vol_delta = cum_volume - self._last_cum_vol
                    self.has_volume = True
                self._last_cum_vol = cum_volume
            except (TypeError, ValueError):
                pass

        closed = None
        with self._lock:
            self.last_tick_time = ts
            if self._minute is None:
                self._minute, self._o, self._h, self._l, self._c = minute, ltp, ltp, ltp, ltp
                self._v = vol_delta
            elif minute < self._minute:
                # Late/out-of-order tick: never rewrite a candle that has
                # already advanced to a newer minute.
                return
            elif minute > self._minute:
                closed = (self._minute, self._o, self._h, self._l, self._c, self._v)
                self._minute, self._o, self._h, self._l, self._c = minute, ltp, ltp, ltp, ltp
                self._v = vol_delta
            else:
                self._h, self._l, self._c = max(self._h, ltp), min(self._l, ltp), ltp
                self._v += vol_delta

        if closed:
            row = pd.DataFrame([{"Datetime": closed[0], "open": closed[1], "high": closed[2],
                                 "low": closed[3], "close": closed[4], "volume": closed[5]}])
            with self._lock:
                self.df = pd.concat([self.df, row], ignore_index=True)
                if len(self.df) > self.max_rows:
                    self.df = self.df.iloc[-self.max_rows:].reset_index(drop=True)
                self.last_close_time = closed[0]


def parse_ws_time(raw) -> Optional[datetime]:
    """Fyers SymbolUpdate last_traded_time — epoch or 'YYYY-MM-DD HH:MM:SS' (IST)."""
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            value = float(raw)
            if value > 1_000_000_000_000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        s = str(raw).strip()
        if s.isdigit():
            value = int(s)
            if value > 1_000_000_000_000:
                value //= 1000
            return datetime.fromtimestamp(value, tz=ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# V3.1 — SMC SCANNER (smc library + HTF structure + zone reactions)
# ══════════════════════════════════════════════════════════════════
class SMCScanner:
    def __init__(self, config: Config, client: FyersClient, orchestrator=None):
        self.cfg = config
        self.client = client
        self.orch = orchestrator
        self.ind = Indicators()
        self.htf_underlying = SPOT_SYMBOL

    # ── defensive "unmitigated" filter ───────────────────────────────
    @staticmethod
    def _unmitigated(frame: pd.DataFrame, dir_col: str, direction: int) -> pd.DataFrame:
        """smartmoneyconcepts uses 0/NaN for not-yet-mitigated zones and a
        positive candle index when a zone has been mitigated."""
        if frame.empty or dir_col not in frame.columns:
            return pd.DataFrame(columns=frame.columns)

        sel = frame[dir_col] == direction
        if "MitigatedIndex" in frame.columns:
            mit = pd.to_numeric(frame["MitigatedIndex"], errors="coerce")
            sel &= mit.isna() | mit.eq(0)
        return frame[sel].copy()

    @staticmethod
    def _structure_events(bc: pd.DataFrame, current_index: int, lookback: int) -> pd.DataFrame:
        """Return BOS/CHoCH events whose actual break candle is within the
        requested lookback. The smc library places the event on the swing
        candidate and reports the actual break candle in BrokenIndex."""
        if bc is None or bc.empty or "BrokenIndex" not in bc.columns:
            return pd.DataFrame(columns=list(bc.columns) if bc is not None else [])

        events = bc.loc[bc["BOS"].notna() | bc["CHOCH"].notna()].copy()
        if events.empty:
            return events
        events["BrokenIndex"] = pd.to_numeric(events["BrokenIndex"], errors="coerce")
        events = events[events["BrokenIndex"].notna()]
        events = events[events["BrokenIndex"] <= current_index]
        events = events[events["BrokenIndex"] >= max(0, current_index - lookback)]
        return events.sort_values("BrokenIndex")

    # ── HTF confirmation: real BOS/CHoCH structure (smc), ATR fallback ──
    def _check_htf_confirmation(self, signal_type: str) -> bool:
        if not self.cfg.htf_confirm:
            return False

        df_htf = pd.DataFrame()
        # 1) live buffer (websocket mode) resampled to HTF resolution
        if self.orch is not None:
            buf = self.orch.buffers.get(self.htf_underlying)
            if buf is not None:
                df1 = buf.snapshot()
                if len(df1) >= self.cfg.atr_period + 2:
                    df1 = df1.set_index("Datetime")
                    agg = df1.resample(f"{self.cfg.htf_resolution}min").agg(
                        {"open": "first", "high": "max", "low": "min",
                         "close": "last", "volume": "sum"}).dropna()
                    df_htf = agg.reset_index()

        # 2) REST fallback
        if df_htf.empty:
            df_htf = self.client.fetch_history(self.htf_underlying, self.cfg.htf_resolution,
                                               days=3, cont_flag="0")
        if df_htf.empty:
            return False
        df_htf = drop_forming_candle(df_htf)

        # 3) primary: smc structure break on HTF (last N HTF candles)
        if smc is not None and len(df_htf) >= max(50, self.cfg.htf_swing_length * 4):
            try:
                ohlc = df_htf[["open", "high", "low", "close"]].copy()
                ohlc["volume"] = df_htf["volume"].fillna(0) if "volume" in df_htf.columns else 0.0
                ohlc = ohlc.astype(float).reset_index(drop=True)
                swings = smc.swing_highs_lows(ohlc, swing_length=self.cfg.htf_swing_length)
                structure = smc.bos_choch(ohlc, swings, close_break=self.cfg.close_break)
                current_index = len(ohlc) - 1
                recent = self._structure_events(structure, current_index, self.cfg.htf_lookback)
                if signal_type == "LONG":
                    return bool(((recent["BOS"] == 1) | (recent["CHOCH"] == 1)).any()) if not recent.empty else False
                if signal_type == "SHORT":
                    return bool(((recent["BOS"] == -1) | (recent["CHOCH"] == -1)).any()) if not recent.empty else False
            except Exception as e:
                logger.debug(f"HTF smc structure check failed ({e}); using ATR fallback")

        # 4) fallback: v3.0 ATR displacement candle
        if len(df_htf) < self.cfg.atr_period + 1:
            return False
        df_htf["atr"] = self.ind.atr(df_htf, self.cfg.atr_period)
        last = df_htf.iloc[-1]
        atr_val = last["atr"]
        if pd.isna(atr_val) or atr_val == 0:
            return False
        body = abs(last["close"] - last["open"])
        if signal_type == "LONG" and last["close"] > last["open"] and body >= atr_val * self.cfg.htf_disp_mult:
            return True
        if signal_type == "SHORT" and last["close"] < last["open"] and body >= atr_val * self.cfg.htf_disp_mult:
            return True
        return False

    # ── Core scan on a DataFrame of CLOSED candles ──────────────────
    def scan_dataframe(self, df: Optional[pd.DataFrame], symbol: str,
                       trend_bias: Optional[str]) -> SignalResult:
        if smc is None:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="SMC_MISSING",
                                context="smartmoneyconcepts not installed (pip install smartmoneyconcepts)")

        need = self.cfg.atr_period + self.cfg.smc_swing_length * 2 + 2
        have = 0 if df is None else len(df)
        if df is None or have < need:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="SHORT_HISTORY",
                                context=f"have {have} closed bars, need {need}")
        df = df.reset_index(drop=True)
        now = ist_now().replace(tzinfo=None)
        age = (now - df["Datetime"].iloc[-1]).total_seconds()
        if age > 180:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="STALE",
                                context=f"last candle {df['Datetime'].iloc[-1]:%H:%M} ({age:.0f}s old)")

        ohlc = df[["open", "high", "low", "close"]].copy()
        ohlc["volume"] = df["volume"].fillna(0) if "volume" in df.columns else 0.0
        ohlc = ohlc.astype(float)

        atr_val = self.ind.atr(df, self.cfg.atr_period).iloc[-1]
        if pd.isna(atr_val) or atr_val == 0:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="ATR not ready")

        try:
            swings = smc.swing_highs_lows(ohlc, swing_length=self.cfg.smc_swing_length)
            bc = smc.bos_choch(ohlc, swings, close_break=self.cfg.close_break)
            liq = smc.liquidity(ohlc, swings, range_percent=0.01)
            fvg = smc.fvg(ohlc, join_consecutive=False)
            obs = smc.ob(ohlc, swings, close_mitigation=False)
        except Exception as e:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="SMC_ERROR",
                                context=f"SMC library failed: {e}")

        i = len(df) - 1
        lb = self.cfg.sweep_lookback
        close = float(df["close"].iloc[i])
        open_ = float(df["open"].iloc[i])
        body = abs(close - open_)
        bullish_candle = close > open_
        bearish_candle = close < open_
        disp_ok = body >= atr_val * self.cfg.displacement_atr_mult
        disp_strong = body >= atr_val * 1.0

        # ── structure events ──────────────────────────────────────────
        # smc.bos_choch stores the event on a structural row but reports
        # the actual breakout candle in BrokenIndex. Use BrokenIndex so a
        # signal is genuinely tied to a recent structure break.
        recent_events = self._structure_events(bc, i, lb)
        wide_events = self._structure_events(bc, i, self.cfg.htf_lookback)

        bull_break = bool(
            ((recent_events["BOS"] == 1) | (recent_events["CHOCH"] == 1)).any()
        ) if not recent_events.empty else False
        bear_break = bool(
            ((recent_events["BOS"] == -1) | (recent_events["CHOCH"] == -1)).any()
        ) if not recent_events.empty else False
        wide_bull_break = bool(
            ((wide_events["BOS"] == 1) | (wide_events["CHOCH"] == 1)).any()
        ) if not wide_events.empty else False
        wide_bear_break = bool(
            ((wide_events["BOS"] == -1) | (wide_events["CHOCH"] == -1)).any()
        ) if not wide_events.empty else False

        struct_label = "none"
        if not recent_events.empty:
            latest_event = recent_events.iloc[-1]
            if latest_event.get("CHOCH") == 1:
                struct_label = "CHoCH↑"
            elif latest_event.get("CHOCH") == -1:
                struct_label = "CHoCH↓"
            elif latest_event.get("BOS") == 1:
                struct_label = "BOS↑"
            elif latest_event.get("BOS") == -1:
                struct_label = "BOS↓"

        # ── liquidity sweeps ──────────────────────────────────────────
        # smc.liquidity: +1 = buy-side liquidity around highs, -1 =
        # sell-side liquidity around lows; Swept is the actual sweep candle
        # index and 0 means no sweep.
        recent_liq = liq.iloc[:i + 1].copy()
        if not recent_liq.empty and "Swept" in recent_liq.columns:
            swept_idx = pd.to_numeric(recent_liq["Swept"], errors="coerce")
            recent_sweeps = recent_liq[
                swept_idx.notna() &
                swept_idx.gt(0) &
                swept_idx.le(i) &
                swept_idx.ge(max(0, i - lb))
            ]
        else:
            recent_sweeps = pd.DataFrame(columns=recent_liq.columns)

        sellside_swept = bool(
            (recent_sweeps["Liquidity"] == -1).any()
        ) if not recent_sweeps.empty else False
        buyside_swept = bool(
            (recent_sweeps["Liquidity"] == 1).any()
        ) if not recent_sweeps.empty else False

        # ── premium / discount vs latest confirmed swing range ────────
        zone = "n/a"
        sh_vals = swings.loc[swings["HighLow"] == 1, "Level"].dropna()
        sl_vals = swings.loc[swings["HighLow"] == -1, "Level"].dropna()
        if len(sh_vals) and len(sl_vals):
            eq = (float(sh_vals.iloc[-1]) + float(sl_vals.iloc[-1])) / 2.0
            zone = "discount" if close < eq else "premium"

        # ── unmitigated POIs + zone reaction test (price inside zone) ─
        unmit_bull_fvg = self._unmitigated(fvg, "FVG", 1)
        unmit_bear_fvg = self._unmitigated(fvg, "FVG", -1)

        bull_fvg_active = bool(
            (
                (unmit_bull_fvg["Bottom"] <= close) &
                (close <= unmit_bull_fvg["Top"])
            ).any()
        ) if not unmit_bull_fvg.empty else False

        bear_fvg_active = bool(
            (
                (unmit_bear_fvg["Bottom"] <= close) &
                (close <= unmit_bear_fvg["Top"])
            ).any()
        ) if not unmit_bear_fvg.empty else False

        bull_ob_rows = self._unmitigated(obs, "OB", 1)
        bear_ob_rows = self._unmitigated(obs, "OB", -1)
        bull_ob_active = bool(
            ((bull_ob_rows["Bottom"] <= close) & (close <= bull_ob_rows["Top"])).any()
        ) if not bull_ob_rows.empty else False
        bear_ob_active = bool(
            ((bear_ob_rows["Bottom"] <= close) & (close <= bear_ob_rows["Top"])).any()
        ) if not bear_ob_rows.empty else False

        # ── volume gate (WS candles may report vol=0 — see bypass flag) ─
        last_volume = int(df["volume"].iloc[i]) if "volume" in df.columns else 0
        if last_volume > 0 and last_volume < self.cfg.min_volume:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                context=f"Low Volume Filter (vol={last_volume} < {self.cfg.min_volume})",
                                structure=struct_label, zone=zone)
        if last_volume == 0 and not self.cfg.volume_filter_ws_bypass:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=0,
                                context="No volume feed on candle; blocked by volume_filter_ws_bypass:false",
                                structure=struct_label, zone=zone)

        # ── entry logic: breakout style + zone-reaction style ─────────
        broke_prev_high = close > float(df["high"].iloc[i - 1])
        broke_prev_low = close < float(df["low"].iloc[i - 1])

        long_breakout = bullish_candle and disp_ok and broke_prev_high and bull_break
        short_breakout = bearish_candle and disp_ok and broke_prev_low and bear_break
        long_reaction = wide_bull_break and (bull_fvg_active or bull_ob_active)
        short_reaction = wide_bear_break and (bear_fvg_active or bear_ob_active)

        long_setup = (self.cfg.breakout_entry and long_breakout) or \
                     (self.cfg.zone_reaction_entry and long_reaction)
        short_setup = (self.cfg.breakout_entry and short_breakout) or \
                      (self.cfg.zone_reaction_entry and short_reaction)

        if not long_setup and not short_setup:
            if wide_bull_break or wide_bear_break:
                reason = ("Structure break present but price not testing FVG/OB and "
                          "no qualifying breakout close")
            elif not disp_ok:
                reason = (f"No Displacement (body={body:.2f} <= "
                          f"{self.cfg.displacement_atr_mult}xATR={atr_val * self.cfg.displacement_atr_mult:.2f})")
            elif bullish_candle and not broke_prev_high:
                reason = "No candle-close breakout over prior high"
            elif bearish_candle and not broke_prev_low:
                reason = "No candle-close breakdown under prior low"
            else:
                reason = "Direction mismatch: candle vs structure"
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                context=reason, structure=struct_label, zone=zone)

        direction = "LONG" if long_setup else "SHORT"
        conf_tags = []
        reaction_entry = long_reaction if direction == "LONG" else short_reaction
        if reaction_entry:
            conf_tags.append("zoneRx")

        # ── filter gates ──────────────────────────────────────────────
        if self.cfg.trend_filter:
            need_bias = "bullish" if direction == "LONG" else "bearish"
            if trend_bias != need_bias:
                return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                    context=f"Trend filter blocked {direction} (bias={trend_bias})",
                                    structure=struct_label, zone=zone)

        if self.cfg.require_bos_confirm:
            want = ("BOS↑", "CHoCH↑") if direction == "LONG" else ("BOS↓", "CHoCH↓")
            if struct_label not in want:
                return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                    context=f"require_bos_confirm: latest structure is {struct_label}",
                                    structure=struct_label, zone=zone)

        if self.cfg.premium_discount_filter:
            if direction == "LONG" and zone == "premium":
                return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                    context="Premium/Discount filter blocked LONG (price in premium)",
                                    structure=struct_label, zone=zone)
            if direction == "SHORT" and zone == "discount":
                return SignalResult(symbol=symbol, signal="NO_SIGNAL", volume=last_volume,
                                    context="Premium/Discount filter blocked SHORT (price in discount)",
                                    structure=struct_label, zone=zone)

        # ── confluence scoring ────────────────────────────────────────
        score = 0
        if disp_strong:
            score += 1
            conf_tags.append("disp2x")
        if self.cfg.liq_confluence:
            if direction == "LONG" and sellside_swept:
                score += 1
                conf_tags.append("sweep")
            if direction == "SHORT" and buyside_swept:
                score += 1
                conf_tags.append("sweep")

        fvg_ref = None
        if self.cfg.fvg_confluence:
            if direction == "LONG" and bull_fvg_active:
                score += 1
                conf_tags.append("FVG")
                cand = unmit_bull_fvg[
                    (unmit_bull_fvg["Bottom"] <= close) &
                    (close <= unmit_bull_fvg["Top"])
                ]
                if not cand.empty:
                    fvg_ref = float(cand["Bottom"].max())
            elif direction == "SHORT" and bear_fvg_active:
                score += 1
                conf_tags.append("FVG")
                cand = unmit_bear_fvg[
                    (unmit_bear_fvg["Bottom"] <= close) &
                    (close <= unmit_bear_fvg["Top"])
                ]
                if not cand.empty:
                    fvg_ref = float(cand["Top"].min())

        ob_ref = None
        if self.cfg.ob_confluence:
            if direction == "LONG" and bull_ob_active:
                score += 1
                conf_tags.append("OB")
                cand = bull_ob_rows[bull_ob_rows["Bottom"] <= close]
                if not cand.empty:
                    ob_ref = float(cand["Bottom"].max())
            elif direction == "SHORT" and bear_ob_active:
                score += 1
                conf_tags.append("OB")
                cand = bear_ob_rows[bear_ob_rows["Top"] >= close]
                if not cand.empty:
                    ob_ref = float(cand["Top"].min())

        # ── SL / target: candle extreme, tightened to zone edge + buffer ──
        zone_buf = atr_val * 0.25
        if direction == "LONG":
            sl = float(df["low"].iloc[i]) - atr_val * 0.5
            if fvg_ref is not None and (fvg_ref - zone_buf) > sl:
                sl = fvg_ref - zone_buf
            elif ob_ref is not None and (ob_ref - zone_buf) > sl:
                sl = ob_ref - zone_buf
            target = close + (close - sl) * self.cfg.risk_reward
        else:
            sl = float(df["high"].iloc[i]) + atr_val * 0.5
            if fvg_ref is not None and (fvg_ref + zone_buf) < sl:
                sl = fvg_ref + zone_buf
            elif ob_ref is not None and (ob_ref + zone_buf) < sl:
                sl = ob_ref + zone_buf
            target = close - (sl - close) * self.cfg.risk_reward

        htf_ok = self._check_htf_confirmation(direction)
        if htf_ok:
            score += 1
            conf_tags.append("HTF")
        confidence = "high" if score >= 3 else "medium"

        ctx = (f"{struct_label} | {zone} | disp {body / atr_val:.1f}xATR | "
               f"{'+'.join(conf_tags) if conf_tags else 'base'}")
        return SignalResult(
            symbol=symbol, signal=direction,
            time=df["Datetime"].iloc[i], price=close, sl=sl, target=target,
            confidence=confidence, context=ctx, htf_confirmed=htf_ok,
            volume=last_volume, structure=struct_label, zone=zone,
            confluence=",".join(conf_tags)
        )

    # REST wrapper (used by --rest mode and --test-symbol)
    def scan(self, symbol: str, trend_bias: Optional[str] = None) -> SignalResult:
        df = self.client.fetch_history(symbol, "1", days=3, cont_flag="0")
        if df.empty:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="EMPTY_API",
                                context="History API returned empty. Check symbol format / permissions.")
        df = drop_forming_candle(df)
        if df.empty or len(df) < 30:
            return SignalResult(symbol=symbol, signal="NO_DATA", no_data_reason="SHORT_HISTORY",
                                context=f"Insufficient history after dropping forming candle (have {len(df)})")
        return self.scan_dataframe(df, symbol, trend_bias)


# ══════════════════════════════════════════════════════════════════
# V3.0 — ORCHESTRATOR (WS streaming + scan-on-close engine)
# ══════════════════════════════════════════════════════════════════
class ScannerOrchestrator:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = FyersClient(config.app_id, config.access_token, debug=config.debug_mode)
        self.scanner = SMCScanner(config, self.client, orchestrator=self)
        self.notifier = TelegramNotifier(config.bot_token, config.chat_id)
        self.trade_logger = TradeLogger(config.csv_log_dir)
        self.last_alert: Dict[str, datetime] = {}
        self.running = True
        self.cycle_count = 0
        self.current_fut_symbol = None
        self._fallback_mode = False

        self.buffers: Dict[str, CandleBuffer] = {}
        self.ws = None
        self._ws_lock = threading.Lock()
        self._subscribed: set = set()
        self._last_scanned_candle: Optional[datetime] = None

        # Option universe cache: prevents option-chain REST calls every
        # minute in WebSocket mode. Refresh only on schedule or expiry change.
        self.active_option_symbols: List[str] = []
        self.active_option_expiry: Optional[date] = None
        self._last_option_refresh: Optional[datetime] = None

        # Prevent repeated history requests for symbols that have no REST
        # history; WS ticks can still populate those buffers live.
        self._seed_attempted: set = set()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        self.running = False

    # ── market hours ────────────────────────────────────────────────
    def is_market_open(self, now: datetime) -> bool:
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        if now.weekday() >= 5:
            return False
        try:
            start_t = pd.Timestamp(self.cfg.market_start).time()
            end_t = pd.Timestamp(self.cfg.market_end).time()
        except Exception:
            start_t = pd.Timestamp("09:15").time()
            end_t = pd.Timestamp("15:30").time()
        return start_t <= now.time() <= end_t

    # ── buffers ─────────────────────────────────────────────────────
    def get_buffer(self, symbol: str) -> CandleBuffer:
        buf = self.buffers.get(symbol)
        if buf is None:
            buf = CandleBuffer(symbol, max_rows=self.cfg.max_buffer_rows)
            self.buffers[symbol] = buf
        return buf

    def ensure_buffer(self, symbol: str, seed_days: int = 2, resolution: str = "1",
                      cont_flag: str = "0", force: bool = False) -> CandleBuffer:
        buf = self.get_buffer(symbol)
        if buf.df.empty and (force or symbol not in self._seed_attempted):
            self._seed_attempted.add(symbol)
            df = self.client.fetch_history(symbol, resolution, days=seed_days, cont_flag=cont_flag)
            df = drop_forming_candle(df)
            buf.seed(df)
            if not df.empty:
                logger.info(f"Seeded {symbol}: {len(df)} bars | last close {df['close'].iloc[-1]}")
            else:
                logger.warning(f"No REST seed for {symbol}; waiting for live WebSocket ticks.")
        return buf

    def get_option_universe(self, now: datetime, spot: float, force: bool = False) -> Tuple[List[str], date]:
        """Return the cached option universe. The option chain is refreshed
        only every option_refresh_minutes or when the desired expiry changes."""
        desired_expiry = SymbolGenerator.get_next_expiry(
            now, target_weekday=self.cfg.nifty_expiry_weekday
        )

        cache_valid = (
            not force
            and bool(self.active_option_symbols)
            and self.active_option_expiry == desired_expiry
            and self._last_option_refresh is not None
            and (now - self._last_option_refresh).total_seconds()
                < self.cfg.option_refresh_minutes * 60
        )
        if cache_valid:
            return self.active_option_symbols, desired_expiry

        logger.info(
            f"Refreshing NIFTY option universe | desired expiry={desired_expiry} | "
            f"cache_age={(0 if self._last_option_refresh is None else (now - self._last_option_refresh).total_seconds()/60):.1f}m"
        )

        symbols = SymbolGenerator.build_option_symbols_from_chain(
            self.client, SPOT_SYMBOL, spot, desired_expiry
        )
        if not symbols:
            logger.warning("Option chain lookup failed, falling back to manual symbol construction")
            symbols = SymbolGenerator.build_option_symbols("NIFTY", spot, desired_expiry)

        self.active_option_symbols = symbols
        self.active_option_expiry = desired_expiry
        self._last_option_refresh = now
        return symbols, desired_expiry

    # ── trend bias from live buffers ────────────────────────────────
    def get_trend_bias(self, now: datetime) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        spot_buf = self.buffers.get(SPOT_SYMBOL)
        spot = None
        if spot_buf is not None and not spot_buf.df.empty:
            spot = float(spot_buf.df["close"].iloc[-1])

        if spot is None:
            df_spot = self.client.fetch_history(SPOT_SYMBOL, "1", days=3, cont_flag="0")
            if df_spot.empty or len(df_spot) < 20:
                logger.warning("Failed to fetch Nifty spot")
                return None, None, None
            df_spot = drop_forming_candle(df_spot)
            self.get_buffer(SPOT_SYMBOL).seed(df_spot)
            spot = float(df_spot["close"].iloc[-1]) if not df_spot.empty else None

        self.current_fut_symbol = FuturesHelper.get_futures_symbol(now, self.cfg.rollover_days_before_expiry)
        fut_buf = self.buffers.get(self.current_fut_symbol)

        if fut_buf is not None and len(fut_buf.df) >= 20:
            df_fut = fut_buf.df
            df_fut = df_fut.copy()
            df_fut["vwap"] = Indicators.vwap(df_fut)
            last_fut = df_fut.iloc[-1]
            fut_price = float(last_fut["close"])
            if not pd.isna(last_fut["vwap"]):
                bias = "bullish" if fut_price > last_fut["vwap"] else "bearish"
                basis = fut_price - spot
                basis_pct = (basis / spot) * 100 if spot else 0
                logger.info(
                    f"Spot: {spot:.2f} | Fut: {fut_price:.2f} ({self.current_fut_symbol}) | "
                    f"Basis: {basis:+.2f} ({basis_pct:+.3f}%) | VWAP: {last_fut['vwap']:.2f} | Bias: {bias} [LIVE-WS]")
                return bias, spot, fut_price

        # REST fallback path
        df_fut = self.client.fetch_history(self.current_fut_symbol, "1", days=3, cont_flag="1")
        if df_fut.empty or len(df_fut) < 20:
            df_spot = self.buffers[SPOT_SYMBOL].df
            df_spot = df_spot.copy()
            df_spot["ema20"] = Indicators.ema(df_spot["close"], 20)
            last_ema = df_spot["ema20"].iloc[-1]
            bias = "bullish" if spot > last_ema else "bearish"
            logger.info(f"Spot: {spot:.2f} | EMA20: {last_ema:.2f} | Bias: {bias} (FALLBACK)")
            return bias, spot, None

        df_fut = drop_forming_candle(df_fut)
        self.get_buffer(self.current_fut_symbol).seed(df_fut)
        df_fut["vwap"] = Indicators.vwap(df_fut)
        last_fut = df_fut.iloc[-1]
        fut_price = float(last_fut["close"])
        if pd.isna(last_fut["vwap"]):
            return None, spot, fut_price
        bias = "bullish" if fut_price > last_fut["vwap"] else "bearish"
        basis = fut_price - spot
        basis_pct = (basis / spot) * 100 if spot else 0
        logger.info(
            f"Spot: {spot:.2f} | Fut: {fut_price:.2f} ({self.current_fut_symbol}) | "
            f"Basis: {basis:+.2f} ({basis_pct:+.3f}%) | VWAP: {last_fut['vwap']:.2f} | Bias: {bias}")
        return bias, spot, fut_price

    # ── alerts / cooldown / table ───────────────────────────────────
    def cooldown_ok(self, symbol: str, timestamp: datetime) -> bool:
        if symbol not in self.last_alert:
            return True
        elapsed = (timestamp - self.last_alert[symbol]).total_seconds() / 60
        return elapsed >= self.cfg.signal_cooldown_min

    def format_alert(self, signals: List[SignalResult], fut_price: Optional[float]) -> str:
        lines = ["SMC BREAKOUT ALERT v3.2", ""]
        lines.append(f"Nifty Fut: {fut_price:.2f}" if fut_price else "Nifty Fut: N/A")
        for s in signals:
            conf_marker = "[HIGH]" if s.confidence == "high" else "[MED]"
            htf_marker = "[HTF-OK]" if s.htf_confirmed else ""
            lines.append(
                f"{s.signal} {conf_marker} {htf_marker} on {s.symbol}\n"
                f"Price: Rs {s.price:.2f} | Time: {s.time.strftime('%H:%M:%S')}\n"
                f"Structure: {s.structure} | Zone: {s.zone} | Confluence: {s.confluence or 'base'}\n"
                f"Suggested SL: Rs {s.sl:.2f} | Target: Rs {s.target:.2f} (1:{self.cfg.risk_reward})\n"
                f"Volume: {s.volume}"
            )
        return "\n\n".join(lines)

    def print_scan_table(self, results: List[SignalResult], spot: float, bias: str, expiry: date):
        now = ist_now().strftime("%H:%M:%S")
        logger.info(f"\n{'─'*92}\nSMC Scan #{self.cycle_count} | {now} IST | Spot: {spot:.2f} | "
                    f"Bias: {bias or 'N/A'} | Exp: {expiry}\n{'─'*92}")
        lines = []
        lines.append(f"{'Symbol':<32} {'Signal':<10} {'Conf':<8} {'Struct':<8} {'Zone':<9} {'Context / Confluence'}")
        lines.append(f"{'-'*32} {'-'*10} {'-'*8} {'-'*8} {'-'*9} {'-'*30}")
        counts = {"LONG": 0, "SHORT": 0, "NO_DATA": 0, "NO_SIGNAL": 0}
        nd_reasons: Dict[str, int] = {}
        for r in sorted(results, key=lambda x: x.symbol):
            counts[r.signal] = counts.get(r.signal, 0) + 1
            if r.signal == "NO_DATA":
                key = r.no_data_reason or "UNKNOWN"
                nd_reasons[key] = nd_reasons.get(key, 0) + 1
            conf = r.confidence if r.signal in ("LONG", "SHORT") else "-"
            struct = r.structure if r.structure else "-"
            zone = r.zone if r.zone else "-"
            ctx = r.context or ""
            if len(ctx) > 48:
                ctx = ctx[:45] + "..."
            lines.append(f"{r.symbol:<32} {r.signal:<10} {conf:<8} {struct:<8} {zone:<9} {ctx}")
        lines.append(f"{'─'*92}")
        summary = (f"Results: {counts.get('LONG', 0)} LONG | {counts.get('SHORT', 0)} SHORT | "
                   f"{counts.get('NO_DATA', 0)} NO_DATA | {counts.get('NO_SIGNAL', 0)} NO_SIGNAL")
        if nd_reasons:
            summary += f"   [NO_DATA: {nd_reasons}]"
        lines.append(summary)
        lines.append(f"{'─'*92}\n")
        logger.info("\n".join(lines))

    # ── WebSocket layer ─────────────────────────────────────────────
    def _on_ws_message(self, message):
        if not isinstance(message, dict):
            return
        sym = message.get("symbol")
        if not sym:
            return
        buf = self.buffers.get(sym)
        if buf is None:
            return
        ltp = message.get("ltp")
        if ltp is None:
            return
        ts = parse_ws_time(message.get("last_traded_time"))
        vol = None
        for key in (
            "vol_traded_today",
            "vol_traded",
            "volume",
            "total_traded_volume",
        ):
            if message.get(key) is not None:
                vol = message[key]
                break
        buf.on_tick(ltp, ts, vol)

    def _on_ws_open(self):
        logger.info("WebSocket connected. Subscribing to tracked symbols...")
        self._subscribe_all()
        if self.ws is not None:
            self.ws.keep_running()

    def _on_ws_error(self, message):
        logger.warning(f"WebSocket error: {message}")

    def _on_ws_close(self, message):
        logger.warning(f"WebSocket closed: {message} (reconnect handled by SDK)")

    def _subscribe_all(self):
        with self._ws_lock:
            pending = [s for s in self.buffers.keys() if s not in self._subscribed]
            if not pending:
                return
            if self.ws is not None:
                try:
                    self.ws.subscribe(symbols=pending, data_type="SymbolUpdate")
                    self._subscribed.update(pending)
                    logger.info(f"WS subscribed: {len(pending)} symbols (total {len(self._subscribed)})")
                except Exception as e:
                    logger.error(f"WS subscribe failed: {e}")

    def start_websocket(self):
        if data_ws is None:
            logger.error("fyers_apiv3 websocket module unavailable; run with --rest")
            return False
        access_token = f"{self.cfg.app_id}:{self.cfg.access_token}"
        self.ws = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=self._on_ws_open,
            on_close=self._on_ws_close,
            on_error=self._on_ws_error,
            on_message=self._on_ws_message,
        )
        try:
            self.ws.connect()
            return True
        except Exception as e:
            logger.error(f"WebSocket connect failed: {e}")
            return False

    def stop_websocket(self):
        """Best-effort shutdown; SDK method name differs across minor releases."""
        if self.ws is None:
            return
        for method_name in ("close_connection", "close"):
            method = getattr(self.ws, method_name, None)
            if callable(method):
                try:
                    method()
                    logger.info("WebSocket connection closed.")
                    break
                except Exception as e:
                    logger.debug(f"WebSocket {method_name} close failed: {e}")

    # ── preflight ───────────────────────────────────────────────────
    def preflight_check(self, symbols: List[str]) -> bool:
        logger.info("=== PRE-FLIGHT CHECK ===")
        test_spot = self.client.fetch_history(SPOT_SYMBOL, "1", days=1, cont_flag="0")
        if test_spot.empty:
            logger.error("CRITICAL: Cannot fetch NIFTY spot history. Verify access token and Fyers subscription.")
            return False
        logger.info(f"Spot check OK | Last close: {test_spot['close'].iloc[-1]} | Rows: {len(test_spot)}")

        if not symbols:
            logger.error("CRITICAL: No symbols to scan.")
            return False

        test_sym = symbols[0]
        logger.info(f"Option check symbol: {test_sym}")
        q_resp = self.client.quotes([test_sym])
        if q_resp and q_resp.get("s") == "ok" and q_resp.get("d"):
            logger.info(f"Quotes OK for {test_sym} | LTP: {q_resp['d'][0].get('v', {}).get('lp', 'N/A')}")
        else:
            logger.warning(f"Quotes failed for {test_sym}.")

        test_df = self.client.fetch_history(test_sym, "1", days=1, cont_flag="0")
        if test_df.empty:
            logger.error(f"CRITICAL: History API returned EMPTY for {test_sym}. "
                         "(Fyers weekly-option history limitation — v3.2 WS mode will stream these live instead.)")
            if self.ws is not None:
                logger.info("Proceeding in WS mode: live ticks will build candles even without history.")
                return True
            return False
        logger.info(f"Option history OK for {test_sym} | Rows: {len(test_df)}")
        logger.info("=== PRE-FLIGHT PASSED ===")
        return True

    # ── scan cycle (shared by WS and REST modes) ────────────────────
    def run_scan_cycle(self):
        cycle_start = time.time()
        now = ist_now()
        self.cycle_count += 1

        trend_bias, spot, fut_price = self.get_trend_bias(now)
        if spot is None:
            logger.warning("Failed to fetch Nifty spot. Skipping cycle.")
            return

        symbols, expiry = self.get_option_universe(now, spot)
        if not symbols:
            logger.error("No symbols to scan.")
            return

        # ensure live buffers + ws subscriptions for every symbol
        for sym in symbols:
            self.ensure_buffer(sym, seed_days=2, cont_flag="0")
        if self.cfg.use_websocket and self.ws is not None:
            self._subscribe_all()

        results: List[SignalResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futs = {}
            for sym in symbols:
                buf = self.buffers.get(sym)
                df = buf.snapshot() if buf is not None else pd.DataFrame()
                futs[executor.submit(self.scanner.scan_dataframe, df, sym, trend_bias)] = sym
            for future in concurrent.futures.as_completed(futs):
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.error(f"Scanner thread error on {futs[future]}: {e}")

        active = [r for r in results if r.signal in ("LONG", "SHORT")]

        no_data_all = all(r.signal == "NO_DATA" for r in results) if results else True
        if no_data_all and not self._fallback_mode:
            logger.warning("ALL option symbols NO_DATA. Activating FALLBACK MODE: scanning NIFTY spot chart.")
            self._fallback_mode = True

        if self._fallback_mode:
            spot_buf = self.buffers.get(SPOT_SYMBOL)
            spot_df = spot_buf.snapshot() if spot_buf is not None else pd.DataFrame()
            spot_result = self.scanner.scan_dataframe(spot_df, SPOT_SYMBOL, trend_bias)
            if spot_result.signal in ("LONG", "SHORT"):
                relevant = SymbolGenerator.filter_symbols_for_signal(symbols, spot_result.signal, spot or 0.0)
                for opt_sym in relevant:
                    synthetic = SignalResult(
                        symbol=opt_sym, signal=spot_result.signal, time=spot_result.time,
                        price=spot_result.price, sl=spot_result.sl, target=spot_result.target,
                        confidence=spot_result.confidence, htf_confirmed=spot_result.htf_confirmed,
                        volume=spot_result.volume, structure=spot_result.structure, zone=spot_result.zone,
                        confluence=spot_result.confluence,
                        context="FALLBACK: derived from NIFTY spot SMC breakout (option data unavailable)"
                    )
                    results.append(synthetic)
                    active.append(synthetic)
                logger.info(f"Fallback mode: {len(relevant)} synthetic option signals from spot breakout.")
            else:
                logger.info(f"Fallback spot scan: {spot_result.signal} ({spot_result.context})")

        fresh_signals = []
        for sig in active:
            if sig.time and self.cooldown_ok(sig.symbol, sig.time):
                fresh_signals.append(sig)
                self.last_alert[sig.symbol] = sig.time
                self.trade_logger.log(sig, trend_bias or "none", spot or 0.0, fut_price, sig.htf_confirmed)

        if fresh_signals:
            msg = self.format_alert(fresh_signals, fut_price)
            logger.info(f"ALERT: {msg.replace(chr(10), ' | ')}")
            if self.notifier.send(msg):
                logger.info("Alert sent to Telegram")
            else:
                logger.warning("Failed to send Telegram alert")

        self.print_scan_table(results, spot or 0.0, trend_bias or "N/A", expiry)

        if self.cycle_count % self.cfg.print_stats_every == 0:
            stats = self.trade_logger.get_today_stats()
            logger.info(
                f"STATS >> Today: {stats['total']} signals | LONG: {stats['long']} | SHORT: {stats['short']} | "
                f"HighConf: {stats['high_conf']} | HTF-Confirmed: {stats.get('htf_yes', 0)}")

        elapsed = time.time() - cycle_start
        logger.info(f"Scan cycle took {elapsed:.2f}s")

    # ── WS mode main loop: scan on spot candle close ────────────────
    def run_ws_mode(self):
        logger.info("Seeding history into live buffers (spot + futures)...")
        self.ensure_buffer(SPOT_SYMBOL, seed_days=3, cont_flag="0")
        now = ist_now()
        fut_sym = FuturesHelper.get_futures_symbol(now, self.cfg.rollover_days_before_expiry)
        spot = float(self.buffers[SPOT_SYMBOL].df["close"].iloc[-1]) if not self.buffers[SPOT_SYMBOL].df.empty else 0
        symbols, expiry = self.get_option_universe(now, spot, force=True)
        self.ensure_buffer(fut_sym, seed_days=3, cont_flag="1")
        self.current_fut_symbol = fut_sym
        for sym in symbols:
            self.ensure_buffer(sym, seed_days=2, cont_flag="0")

        if self.cfg.validate_symbols and symbols:
            self.preflight_check(symbols)

        logger.info("Starting Fyers Data Socket (SymbolUpdate)...")
        if not self.start_websocket():
            logger.error("WebSocket failed to start. Re-run with --rest for polling mode.")
            return

        spot_buf = self.buffers[SPOT_SYMBOL]
        self._last_scanned_candle = spot_buf.last_close_time
        logger.info(f"Waiting for first new 1-min candle close (last seeded: {self._last_scanned_candle})...")

        try:
            while self.running:
                now = ist_now()
                if not self.is_market_open(now):
                    time.sleep(30)
                    continue

                new_close = spot_buf.last_close_time
                if new_close is not None and new_close != self._last_scanned_candle:
                    self._last_scanned_candle = new_close
                    try:
                        self.run_scan_cycle()
                    except Exception as e:
                        logger.error(f"Scan cycle error: {e}", exc_info=True)

                # rollover: futures month changed → seed + subscribe new symbol
                expected_fut = FuturesHelper.get_futures_symbol(now, self.cfg.rollover_days_before_expiry)
                if self.current_fut_symbol and expected_fut != self.current_fut_symbol:
                    logger.info(f"Futures rollover detected: {self.current_fut_symbol} -> {expected_fut}")
                    self.ensure_buffer(expected_fut, seed_days=3, cont_flag="1", force=True)
                    self.current_fut_symbol = expected_fut
                    if self.ws is not None:
                        self._subscribe_all()

                time.sleep(2)
        finally:
            self.stop_websocket()
            logger.info("Scanner stopped.")

    # ── REST mode main loop (v2.9 behavior, v3.2 signal logic) ──────
    def run_rest_mode(self):
        try:
            while self.running:
                now = ist_now()
                if not self.is_market_open(now):
                    logger.info("Market closed. Sleeping 60s...")
                    time.sleep(60)
                    continue
                cycle_start = time.time()
                self.cycle_count += 1

                trend_bias, spot, fut_price = self.get_trend_bias(now)
                if spot is None:
                    time.sleep(60)
                    continue

                symbols, expiry = self.get_option_universe(now, spot)
                if not symbols:
                    time.sleep(60)
                    continue

                for sym in symbols:
                    self.ensure_buffer(sym, seed_days=2, cont_flag="0")

                if self.cycle_count == 1 and self.cfg.validate_symbols:
                    self.preflight_check(symbols)

                results: List[SignalResult] = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
                    futs = {executor.submit(self.scanner.scan, sym, trend_bias): sym for sym in symbols}
                    for future in concurrent.futures.as_completed(futs):
                        try:
                            results.append(future.result())
                        except Exception as e:
                            logger.error(f"Scanner thread error: {e}")

                active = [r for r in results if r.signal in ("LONG", "SHORT")]
                no_data_all = all(r.signal == "NO_DATA" for r in results) if results else True
                if no_data_all and not self._fallback_mode:
                    logger.warning("ALL option symbols NO_DATA. Activating FALLBACK MODE.")
                    self._fallback_mode = True
                if self._fallback_mode:
                    spot_result = self.scanner.scan(SPOT_SYMBOL, trend_bias)
                    if spot_result.signal in ("LONG", "SHORT"):
                        relevant = SymbolGenerator.filter_symbols_for_signal(symbols, spot_result.signal, spot or 0.0)
                        for opt_sym in relevant:
                            synthetic = SignalResult(
                                symbol=opt_sym, signal=spot_result.signal, time=spot_result.time,
                                price=spot_result.price, sl=spot_result.sl, target=spot_result.target,
                                confidence=spot_result.confidence, htf_confirmed=spot_result.htf_confirmed,
                                volume=spot_result.volume, structure=spot_result.structure,
                                zone=spot_result.zone, confluence=spot_result.confluence,
                                context="FALLBACK: derived from NIFTY spot SMC breakout"
                            )
                            results.append(synthetic)
                            active.append(synthetic)

                fresh_signals = []
                for sig in active:
                    if sig.time and self.cooldown_ok(sig.symbol, sig.time):
                        fresh_signals.append(sig)
                        self.last_alert[sig.symbol] = sig.time
                        self.trade_logger.log(sig, trend_bias or "none", spot or 0.0, fut_price, sig.htf_confirmed)

                if fresh_signals:
                    msg = self.format_alert(fresh_signals, fut_price)
                    logger.info(f"ALERT: {msg.replace(chr(10), ' | ')}")
                    self.notifier.send(msg)

                self.print_scan_table(results, spot or 0.0, trend_bias or "N/A", expiry)

                if self.cycle_count % self.cfg.print_stats_every == 0:
                    stats = self.trade_logger.get_today_stats()
                    logger.info(
                        f"STATS >> Today: {stats['total']} | LONG: {stats['long']} | SHORT: {stats['short']} | "
                        f"HighConf: {stats['high_conf']} | HTF: {stats.get('htf_yes', 0)}")

                elapsed = time.time() - cycle_start
                sleep_sec = max(1, 60 - ist_now().second)
                if elapsed >= sleep_sec:
                    logger.warning(f"Scan cycle took {elapsed:.1f}s, skipping sleep")
                    continue
                logger.info(f"Scan took {elapsed:.1f}s | Sleeping {sleep_sec}s")
                time.sleep(sleep_sec)
        finally:
            logger.info("Scanner stopped.")

    # ── test mode ───────────────────────────────────────────────────
    def test_symbol(self, symbol: str):
        logger.info(f"=== TEST MODE: {symbol} ===")
        logger.info(f"Current IST: {ist_now()}")
        q = self.client.quotes([symbol])
        if q:
            logger.info(f"Quotes raw:\n{json.dumps(q, indent=2)[:1200]}")
        else:
            logger.warning("Quotes returned None")

        for res, days in [("1", 1), ("5", 2), ("D", 10)]:
            df = self.client.fetch_history(symbol, res, days=days, cont_flag="0")
            if not df.empty:
                logger.info(f"{res} history OK. Rows: {len(df)}")
                logger.info(df.tail(5).to_string())
            else:
                logger.error(f"{res} history EMPTY.")

        logger.info("--- v3.0 SMC scan on 1-min history ---")
        result = self.scanner.scan(symbol, trend_bias=None)
        logger.info(f"Signal: {result.signal} | {result.context}")
        logger.info(f"structure={result.structure} zone={result.zone} confluence={result.confluence}")
        logger.info("=== TEST COMPLETE ===")

    def run(self, use_websocket: bool):
        logger.info("SMC Breakout Scanner v3.2 Started. Press Ctrl+C to stop.")
        logger.info(f"Mode: {'WEBSOCKET (scan-on-close)' if use_websocket else 'REST polling'}")
        logger.info(f"Version: {VERSION} | SMC library: swing_len={self.cfg.smc_swing_length} close_break={self.cfg.close_break}")
        logger.info(f"Filters: premium_discount={self.cfg.premium_discount_filter} "
                    f"bos_confirm={self.cfg.require_bos_confirm} trend={self.cfg.trend_filter}")
        logger.info(f"Confluence: liq={self.cfg.liq_confluence} fvg={self.cfg.fvg_confluence} "
                    f"ob={self.cfg.ob_confluence} htf={self.cfg.htf_confirm}({self.cfg.htf_resolution}m)")
        logger.info(f"CSV logging to: {os.path.abspath(self.cfg.csv_log_dir)} | Option refresh: {self.cfg.option_refresh_minutes}m")

        if use_websocket:
            self.run_ws_mode()
        else:
            self.run_rest_mode()
        self.trade_logger.close()


def main():
    parser = argparse.ArgumentParser(description="SMC Breakout Scanner v3.2")
    parser.add_argument("--test-symbol", type=str, default=None, help="Test a single symbol and exit")
    parser.add_argument("--rest", action="store_true", help="Force legacy REST polling mode (no WebSocket)")
    args = parser.parse_args()

    if smc is None:
        logger.error("smartmoneyconcepts not installed. Run: pip install smartmoneyconcepts")
        sys.exit(1)

    cfg = load_config()
    if ("YOUR_" in cfg.app_id or "YOUR_" in cfg.access_token or not cfg.app_id or not cfg.access_token):
        logger.error("Fyers credentials not configured! Check config.yaml and token.txt")
        sys.exit(1)

    logger.setLevel(logging.DEBUG if cfg.debug_mode else logging.INFO)
    for h in logger.handlers:
        h.setLevel(logging.DEBUG if cfg.debug_mode else logging.INFO)

    orch = ScannerOrchestrator(cfg)
    if args.test_symbol:
        orch.test_symbol(args.test_symbol)
        sys.exit(0)

    use_ws = cfg.use_websocket and not args.rest
    if use_ws and data_ws is None:
        logger.warning("fyers_apiv3 websocket unavailable — falling back to --rest mode")
        use_ws = False

    try:
        orch.run(use_ws)
    finally:
        orch.trade_logger.close()


if __name__ == "__main__":
    main()
