
"""
═══════════════════════════════════════════════════════════════════
SMC BREAKOUT SCANNER v2.9.1 — Fresh Outlook & Diagnostic Update
══════════════════════════════════════════════════════════════════
What's new:
1. CLEAN TABLE OUTPUT: Per-cycle results printed as an easy-to-read
   table instead of scattered log lines.
2. AUTO-FALLBACK MODE: If Fyers returns NO_DATA for all weekly option
   symbols (common limitation), the scanner automatically switches to
   scanning the NIFTY spot chart and suggests the matching option
   strikes with approximate SL/Target derived from underlying levels.
3. PRE-FLIGHT CHECK: On cycle 1 it validates Spot + 1 Option data
   pipeline and warns you explicitly if something is broken.
4. SYMBOL TEST MODE: python smc_scanner_v2.9.py --test-symbol SYM
5. IST-NATIVE TIMES: All market timing uses Asia/Kolkata explicitly.
6. SMART HISTORY FETCH: Broad window first, then 1-day fallback.
7. DEBUG MODE: Set debug_mode:true in config.yaml to see raw JSON.
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
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np
import concurrent.futures

warnings.filterwarnings("ignore")

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    fyersModel = None

# Force UTF-8 on Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


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


@dataclass
class Config:
    app_id: str
    access_token: str
    bot_token: str
    chat_id: str
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
    nifty_expiry_weekday: int = 3
    debug_mode: bool = False
    validate_symbols: bool = True


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
        nifty_expiry_weekday=sc.get("nifty_expiry_weekday", 3),
        debug_mode=sc.get("debug_mode", False),
        validate_symbols=sc.get("validate_symbols", True)
    )


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


class TradeLogger:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.columns = [
            "date", "time", "symbol", "signal", "entry_price", "sl", "target",
            "risk_reward", "confidence", "trend_bias", "nifty_spot", "nifty_fut",
            "basis", "volume", "htf_confirmed", "context"
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
            expected_path = os.path.join(
                self.log_dir,
                f"signals_{datetime.now().strftime('%Y%m%d')}.csv"
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
                "trend_bias": trend_bias or "none",
                "nifty_spot": f"{nifty_spot:.2f}" if nifty_spot else "",
                "nifty_fut": f"{nifty_fut:.2f}" if nifty_fut else "",
                "basis": basis,
                "volume": str(result.volume),
                "htf_confirmed": "YES" if htf_confirmed else "NO",
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


class FyersClient:
    DATA_BASE = "https://api-t1.fyers.in/data"

    def __init__(self, app_id: str, access_token: str, debug: bool = False):
        self.app_id = app_id
        self.access_token = access_token
        self.debug = debug
        if fyersModel is not None:
            self.fyers = fyersModel.FyersModel(
                client_id=app_id,
                token=access_token,
                is_async=False,
                log_path=""
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

    def _log_debug(self, msg: str, data=None):
        if self.debug:
            if data is not None:
                try:
                    snippet = json.dumps(data, indent=2)[:800]
                    logger.debug(f"{msg}\n{snippet}")
                except Exception:
                    logger.debug(f"{msg}\n{str(data)[:800]}")
            else:
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
            # Fallback HTTP
            self._throttle()
            import urllib.parse
            params = urllib.parse.urlencode(payload)
            url = f"{self.DATA_BASE}/quotes?{params}"
            headers = {
                "Authorization": self._auth_header,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
            }
            r = requests.get(url, headers=headers, timeout=10)
            resp = r.json()
            self._log_debug(f"Quotes HTTP response for {symbols[:3]}...", resp)
            if resp.get("s") == "ok":
                return resp
        except Exception as e:
            logger.warning(f"Quotes fetch failed: {e}")
        return None

    def history_with_retry(
        self,
        symbol: str,
        resolution: str,
        range_from: int,
        range_to: int,
        cont_flag: str = "0",
        max_retries: int = 3
    ) -> Optional[dict]:
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "0",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": cont_flag
        }
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

    def fetch_history(
        self,
        symbol: str,
        resolution: str,
        days: int = 3,
        cont_flag: str = "0"
    ) -> pd.DataFrame:
        end_dt = ist_now()
        start_dt = end_dt - timedelta(days=days)
        all_rows = []

        # Try broad window first, then narrow 1-day fallback
        windows = [
            (end_dt, start_dt),
            (end_dt, end_dt - timedelta(days=1))
        ]

        for win_end, win_start in windows:
            _cursor = win_end
            _rows = []
            while _cursor > win_start:
                _win_start = _cursor - timedelta(days=50)
                if _win_start < win_start:
                    _win_start = win_start

                resp = self.history_with_retry(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=int(_win_start.timestamp()),
                    range_to=int(_cursor.timestamp()),
                    cont_flag=cont_flag
                )

                if resp is None:
                    # Hard API error
                    return pd.DataFrame()

                if resp.get("s") != "ok":
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

        df = pd.DataFrame(
            all_rows,
            columns=["epoch", "open", "high", "low", "close", "volume"]
        )
        df = df.drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
        df["Datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df[["Datetime", "open", "high", "low", "close", "volume"]].dropna()

    def option_chain(self, underlying: str, strikecount: int = 5, timestamp: Optional[str] = None) -> Optional[dict]:
        payload = {
            "symbol": underlying,
            "strikecount": strikecount
        }
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
            params = urllib.parse.urlencode(payload)
            url = f"{self.DATA_BASE}/options-chain-v3?{params}"
            headers = {
                "Authorization": self._auth_header,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/json"
            }
            r = requests.get(url, headers=headers, timeout=10)
            resp = r.json()
            self._log_debug("HTTP optionchain response:", resp)
            if resp.get("s") == "ok":
                return resp
            logger.warning(f"Direct HTTP option chain error: {resp.get('message')}")
        except Exception as e:
            logger.warning(f"Direct HTTP option chain failed: {e}")

        return None


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
            window_high = highs[i - left:i + right + 1]
            window_low = lows[i - left:i + right + 1]
            if highs[i] >= np.max(window_high):
                ph[i + right] = highs[i]
            if lows[i] <= np.min(window_low):
                pl[i + right] = lows[i]

        df["pivot_high"] = ph
        df["pivot_low"] = pl
        return df

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()


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
                return datetime(year, month, day, tzinfo=ZoneInfo("Asia/Kolkata"))
        return datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Kolkata"))

    @staticmethod
    def get_futures_symbol(now: datetime, rollover_days: int = 2) -> str:
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        expiry = FuturesHelper.get_last_thursday(now.year, now.month)
        days_to_expiry = (expiry - now).days

        use_month = now.month
        use_year = now.year

        if days_to_expiry <= rollover_days:
            if now.month == 12:
                use_month = 1
                use_year = now.year + 1
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
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
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
        strikes = [atm - 100, atm - 50, atm, atm + 50, atm + 100]
        types = ["CE", "PE"]

        year_code = str(expiry.year)[-2:]
        month_code = cls.WEEKLY_MONTH_CODE[expiry.month]
        day_code = f"{expiry.day:02d}"

        prefix = f"NSE:{underlying}{year_code}{month_code}{day_code}"
        return [f"{prefix}{s}{t}" for s in strikes for t in types]

    @staticmethod
    def build_option_symbols_from_chain(client: FyersClient, underlying_index: str, spot: float, expiry: date) -> List[str]:
        try:
            resp = client.option_chain(underlying_index, strikecount=5)
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
                    resp = client.option_chain(underlying_index, strikecount=5, timestamp=str(target_epoch))
                    if resp and resp.get("s") == "ok" and "data" in resp:
                        chain = resp["data"].get("optionsChain", [])

            if not chain:
                return []

            strikes = sorted({c["strike_price"] for c in chain if isinstance(c, dict) and c.get("option_type") in ("CE", "PE")})
            if not strikes:
                return []

            atm = min(strikes, key=lambda s: abs(s - spot))
            target_strikes = {atm - 100, atm - 50, atm, atm + 50, atm + 100}

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

    @staticmethod
    def filter_symbols_for_signal(symbols: List[str], signal: str, spot: float) -> List[str]:
        atm = int(round(spot / 50.0) * 50)
        if signal == "LONG":
            want = {atm, atm + 50, atm + 100}
            filtered = [s for s in symbols if s.endswith("CE") and any(str(st) in s for st in want)]
        else:
            want = {atm, atm - 50, atm - 100}
            filtered = [s for s in symbols if s.endswith("PE") and any(str(st) in s for st in want)]

        def strike_from_sym(sym):
            digits = ''.join(filter(str.isdigit, sym.split("PE")[0].split("CE")[0]))
            try:
                return abs(int(digits) - atm) if digits else 99999
            except ValueError:
                return 99999
        return sorted(filtered, key=strike_from_sym)


class SMCScanner:
    def __init__(self, config: Config, client: FyersClient):
        self.cfg = config
        self.client = client
        self.ind = Indicators()
        self.htf_underlying = "NSE:NIFTY50-INDEX"

    def _check_htf_confirmation(self, signal_type: str) -> bool:
        if not self.cfg.htf_confirm:
            return False

        df_htf = self.client.fetch_history(self.htf_underlying, self.cfg.htf_resolution, days=3, cont_flag="0")
        if df_htf.empty or len(df_htf) < self.cfg.atr_period + 1:
            return False

        df_htf = drop_forming_candle(df_htf)
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

    def scan(self, symbol: str, trend_bias: Optional[str] = None) -> SignalResult:
        df = self.client.fetch_history(symbol, "1", days=3, cont_flag="0")
        if df.empty:
            return SignalResult(
                symbol=symbol, signal="NO_DATA", no_data_reason="EMPTY_API",
                context="History API returned empty (no candles). Check symbol format or data permissions."
            )

        df = drop_forming_candle(df)

        if df.empty or len(df) < 30:
            return SignalResult(
                symbol=symbol, signal="NO_DATA", no_data_reason="SHORT_HISTORY",
                context=f"Insufficient history after dropping forming candle (have {len(df)} rows, need 30)"
            )

        last_candle_time = df["Datetime"].iloc[-1]
        now = ist_now().replace(tzinfo=None)
        time_diff = (now - last_candle_time).total_seconds()
        if time_diff > 180:
            return SignalResult(
                symbol=symbol, signal="NO_DATA", no_data_reason="STALE",
                context=f"Stale data: last candle {last_candle_time:%H:%M} ({time_diff:.0f}s old)"
            )

        df["atr"] = self.ind.atr(df, self.cfg.atr_period)
        df = self.ind.pivots(df, left=self.cfg.swing_len, right=self.cfg.swing_len)

        last_idx = len(df) - 1
        atr_val = df["atr"].iloc[last_idx]

        if pd.isna(atr_val) or atr_val == 0:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="ATR not ready")

        lsh = lsl = np.nan
        idm_level = np.nan
        idm_is_bullish = False

        bs_active = False
        bs_bar = -999
        br_active = False
        br_bar = -999

        swing_len = self.cfg.swing_len * 2
        sweep_lookback = self.cfg.sweep_lookback
        disp_mult = self.cfg.displacement_atr_mult

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

            last_volume = df["volume"].iloc[i]
            if last_volume < self.cfg.min_volume:
                return SignalResult(
                    symbol=symbol, signal="NO_SIGNAL",
                    context=f"Low Volume Filter (vol={int(last_volume)} < {self.cfg.min_volume})"
                )

            body = abs(df["close"].iloc[i] - df["open"].iloc[i])
            candle_bullish = df["close"].iloc[i] > df["open"].iloc[i]
            candle_bearish = df["close"].iloc[i] < df["open"].iloc[i]

            if body <= atr_val * disp_mult:
                return SignalResult(
                    symbol=symbol, signal="NO_SIGNAL",
                    context=f"No Displacement (body={body:.2f} <= {disp_mult}xATR={atr_val * disp_mult:.2f})"
                )

            if candle_bullish and df["close"].iloc[i] > df["high"].iloc[i - 1] and bs_active:
                if self.cfg.trend_filter and trend_bias != "bullish":
                    return SignalResult(
                        symbol=symbol, signal="NO_SIGNAL",
                        context=f"Trend filter blocked LONG (bias={trend_bias})"
                    )

                sl = df["low"].iloc[i] - atr_val * 0.5
                target = df["close"].iloc[i] + (df["close"].iloc[i] - sl) * self.cfg.risk_reward
                htf_ok = self._check_htf_confirmation("LONG")
                conf = "high" if (df["close"].iloc[i] > df["high"].iloc[i - 2]) else "medium"
                if htf_ok and conf == "medium":
                    conf = "high"

                return SignalResult(
                    symbol=symbol,
                    signal="LONG",
                    time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i],
                    sl=sl,
                    target=target,
                    confidence=conf,
                    htf_confirmed=htf_ok,
                    volume=int(last_volume)
                )

            if candle_bearish and df["close"].iloc[i] < df["low"].iloc[i - 1] and br_active:
                if self.cfg.trend_filter and trend_bias != "bearish":
                    return SignalResult(
                        symbol=symbol, signal="NO_SIGNAL",
                        context=f"Trend filter blocked SHORT (bias={trend_bias})"
                    )

                sl = df["high"].iloc[i] + atr_val * 0.5
                target = df["close"].iloc[i] - (sl - df["close"].iloc[i]) * self.cfg.risk_reward
                htf_ok = self._check_htf_confirmation("SHORT")
                conf = "high" if (df["close"].iloc[i] < df["low"].iloc[i - 2]) else "medium"
                if htf_ok and conf == "medium":
                    conf = "high"

                return SignalResult(
                    symbol=symbol,
                    signal="SHORT",
                    time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i],
                    sl=sl,
                    target=target,
                    confidence=conf,
                    htf_confirmed=htf_ok,
                    volume=int(last_volume)
                )

        return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="Pattern not met")


class ScannerOrchestrator:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = FyersClient(config.app_id, config.access_token, debug=config.debug_mode)
        self.scanner = SMCScanner(config, self.client)
        self.notifier = TelegramNotifier(config.bot_token, config.chat_id)
        self.trade_logger = TradeLogger(config.csv_log_dir)
        self.last_alert: Dict[str, datetime] = {}
        self.running = True
        self.cycle_count = 0
        self.current_fut_symbol = None
        self._fallback_mode = False

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        self.running = False

    def is_market_open(self, now: datetime) -> bool:
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        t = now.time()
        return pd.Timestamp("09:15:00").time() <= t <= pd.Timestamp("15:40:00").time()

    def get_trend_bias(self, now: datetime) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        df_spot = self.client.fetch_history("NSE:NIFTY50-INDEX", "1", days=3, cont_flag="0")
        if df_spot.empty or len(df_spot) < 20:
            logger.warning("Failed to fetch Nifty spot")
            return None, None, None

        df_spot = drop_forming_candle(df_spot)
        if df_spot.empty or len(df_spot) < 20:
            logger.warning("Nifty spot insufficient bars after dropping forming candle")
            return None, None, None
        spot = df_spot["close"].iloc[-1]

        self.current_fut_symbol = FuturesHelper.get_futures_symbol(
            now, self.cfg.rollover_days_before_expiry
        )
        df_fut = self.client.fetch_history(self.current_fut_symbol, "1", days=3, cont_flag="1")

        if df_fut.empty or len(df_fut) < 20:
            logger.warning(f"Futures data unavailable for {self.current_fut_symbol}, falling back to spot EMA")
            df_spot["ema20"] = Indicators.ema(df_spot["close"], 20)
            last_ema = df_spot["ema20"].iloc[-1]
            bias = "bullish" if spot > last_ema else "bearish"
            logger.info(f"Spot: {spot:.2f} | EMA20: {last_ema:.2f} | Bias: {bias} (FALLBACK)")
            return bias, spot, None

        df_fut = drop_forming_candle(df_fut)
        if df_fut.empty or len(df_fut) < 20:
            logger.warning("Futures insufficient bars after dropping forming candle, falling back to spot EMA")
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

        logger.info(
            f"Spot: {spot:.2f} | Fut: {fut_price:.2f} ({self.current_fut_symbol}) | "
            f"Basis: {basis:+.2f} ({basis_pct:+.3f}%) | "
            f"VWAP: {last_fut['vwap']:.2f} | Bias: {bias}"
        )
        return bias, spot, fut_price

    def cooldown_ok(self, symbol: str, timestamp: datetime) -> bool:
        if symbol not in self.last_alert:
            return True
        elapsed = (timestamp - self.last_alert[symbol]).total_seconds() / 60
        return elapsed >= self.cfg.signal_cooldown_min

    def format_alert(self, signals: List[SignalResult], fut_price: Optional[float]) -> str:
        lines = ["SMC BREAKOUT ALERT", ""]
        fut_line = f"Nifty Fut: {fut_price:.2f}" if fut_price else "Nifty Fut: N/A"
        lines.append(fut_line + "\n")
        for s in signals:
            conf_marker = "[HIGH]" if s.confidence == "high" else "[MED]"
            htf_marker = "[HTF-OK]" if s.htf_confirmed else ""
            lines.append(
                f"{s.signal} {conf_marker} {htf_marker} on {s.symbol}\n"
                f"Price: Rs {s.price:.2f} | Time: {s.time.strftime('%H:%M:%S')}\n"
                f"Suggested SL: Rs {s.sl:.2f} | Target: Rs {s.target:.2f} (1:{self.cfg.risk_reward})\n"
                f"Volume: {s.volume}"
            )
        return "\n\n".join(lines)

    def preflight_check(self, symbols: List[str]):
        logger.info("=== PRE-FLIGHT CHECK ===")
        test_spot = self.client.fetch_history("NSE:NIFTY50-INDEX", "1", days=1, cont_flag="0")
        if test_spot.empty:
            logger.error("CRITICAL: Cannot fetch NIFTY spot history. Verify access token and Fyers subscription.")
            return False
        else:
            logger.info(f"Spot check OK | Last close: {test_spot['close'].iloc[-1]} | Rows: {len(test_spot)}")

        if not symbols:
            logger.error("CRITICAL: No symbols to scan.")
            return False

        test_sym = symbols[0]
        logger.info(f"Option check symbol: {test_sym}")

        q_resp = self.client.quotes([test_sym])
        if q_resp and q_resp.get("s") == "ok":
            q_data = q_resp.get("d", [])
            if q_data:
                logger.info(f"Quotes OK for {test_sym} | LTP: {q_data[0].get('v', {}).get('lp', 'N/A')}")
            else:
                logger.warning(f"Quotes returned ok but no data for {test_sym}")
        else:
            logger.warning(f"Quotes failed for {test_sym}. History will likely fail too.")

        test_df = self.client.fetch_history(test_sym, "1", days=1, cont_flag="0")
        if test_df.empty:
            logger.error(f"CRITICAL: History API returned EMPTY for {test_sym}.")
            logger.error("Possible causes:")
            logger.error("  - Symbol format is wrong for Fyers History API")
            logger.error("  - Fyers does not provide 1-min intraday history for this weekly expiry")
            logger.error("  - Token lacks permission for option historical data")
            logger.error("  - Instrument not yet active / illiquid")
            logger.error("Try running with --test-symbol to debug a specific symbol.")
            return False
        else:
            logger.info(f"Option history OK for {test_sym} | Rows: {len(test_df)} | Last close: {test_df['close'].iloc[-1]}")
            if len(test_df) < 30:
                logger.warning(f"Option history has only {len(test_df)} rows. Signals may show SHORT_HISTORY until 30 bars form.")
        logger.info("=== PRE-FLIGHT PASSED ===")
        return True

    def print_scan_table(self, results: List[SignalResult], spot: float, bias: str, expiry: date):
        now = ist_now().strftime("%H:%M:%S")
        header = f"\n{'─'*80}\nSMC Scan #{self.cycle_count} | {now} IST | Spot: {spot:.2f} | Bias: {bias or 'N/A'} | Exp: {expiry}\n{'─'*80}"
        logger.info(header)

        lines = []
        lines.append(f"{'Symbol':<35} {'Signal':<10} {'Conf':<8} {'HTF':<6} {'Context / Reject Reason'}")
        lines.append(f"{'-'*35} {'-'*10} {'-'*8} {'-'*6} {'-'*30}")

        counts = {"LONG": 0, "SHORT": 0, "NO_DATA": 0, "NO_SIGNAL": 0}
        nd_reasons: Dict[str, int] = {}

        for r in sorted(results, key=lambda x: x.symbol):
            counts[r.signal] = counts.get(r.signal, 0) + 1
            if r.signal == "NO_DATA":
                nd_reasons[r.no_data_reason or "UNKNOWN"] = nd_reasons.get(r.no_data_reason or "UNKNOWN", 0) + 1

            conf = r.confidence if r.signal in ("LONG", "SHORT") else "-"
            htf = "YES" if r.htf_confirmed else ("NO" if r.signal in ("LONG", "SHORT") else "-")
            ctx = r.context or ""
            if len(ctx) > 45:
                ctx = ctx[:42] + "..."
            lines.append(f"{r.symbol:<35} {r.signal:<10} {conf:<8} {htf:<6} {ctx}")

        lines.append(f"{'─'*80}")
        summary = f"Results: {counts.get('LONG', 0)} LONG | {counts.get('SHORT', 0)} SHORT | {counts.get('NO_DATA', 0)} NO_DATA | {counts.get('NO_SIGNAL', 0)} NO_SIGNAL"
        if nd_reasons:
            summary += f"   [NO_DATA breakdown: {nd_reasons}]"
        lines.append(summary)
        lines.append(f"{'─'*80}\n")
        logger.info("\n".join(lines))

    def run_once(self):
        cycle_start = time.time()
        now = ist_now()
        self.cycle_count += 1

        if not self.is_market_open(now):
            logger.info("Market closed. Sleeping 60s...")
            time.sleep(60)
            return

        trend_bias, spot, fut_price = self.get_trend_bias(now)
        if spot is None:
            logger.warning("Failed to fetch Nifty spot. Skipping cycle.")
            time.sleep(60)
            return

        expiry = SymbolGenerator.get_next_expiry(now, target_weekday=self.cfg.nifty_expiry_weekday)

        symbols = SymbolGenerator.build_option_symbols_from_chain(
            self.client, "NSE:NIFTY50-INDEX", spot, expiry
        )
        if not symbols:
            logger.warning("Option chain lookup failed, falling back to manual symbol construction")
            symbols = SymbolGenerator.build_option_symbols("NIFTY", spot, expiry)

        if not symbols:
            logger.error("No symbols to scan. Sleeping 60s...")
            time.sleep(60)
            return

        if self.cycle_count == 1 and self.cfg.validate_symbols:
            if not self.preflight_check(symbols):
                logger.error("Pre-flight failed. Scanning anyway, but expect NO_DATA. Set validate_symbols:false to skip this check.")

        results: List[SignalResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futures = {executor.submit(self.scanner.scan, sym, trend_bias): sym for sym in symbols}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Scanner thread error: {e}")

        active = [r for r in results if r.signal in ("LONG", "SHORT")]

        # Fallback mode: if ALL option symbols returned NO_DATA, scan NIFTY spot instead
        no_data_all = all(r.signal == "NO_DATA" for r in results)
        if no_data_all and not self._fallback_mode:
            logger.warning("ALL option symbols returned NO_DATA. Activating FALLBACK MODE: scanning NIFTY spot chart.")
            self._fallback_mode = True

        if self._fallback_mode:
            spot_result = self.scanner.scan("NSE:NIFTY50-INDEX", trend_bias)
            if spot_result.signal in ("LONG", "SHORT"):
                relevant = SymbolGenerator.filter_symbols_for_signal(symbols, spot_result.signal, spot or 0.0)
                for opt_sym in relevant:
                    synthetic = SignalResult(
                        symbol=opt_sym,
                        signal=spot_result.signal,
                        time=spot_result.time,
                        price=spot_result.price,
                        sl=spot_result.sl,
                        target=spot_result.target,
                        confidence=spot_result.confidence,
                        htf_confirmed=spot_result.htf_confirmed,
                        volume=spot_result.volume,
                        context="FALLBACK: Derived from NIFTY spot SMC breakout (option history unavailable)"
                    )
                    results.append(synthetic)
                    active.append(synthetic)
                logger.info(f"Fallback mode generated {len(relevant)} synthetic option signals from NIFTY spot breakout.")
            else:
                logger.info(f"Fallback mode: NIFTY spot scan returned {spot_result.signal} ({spot_result.context})")

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
                f"STATS >> Today: {stats['total']} signals | "
                f"LONG: {stats['long']} | SHORT: {stats['short']} | "
                f"HighConf: {stats['high_conf']} | HTF-Confirmed: {stats.get('htf_yes', 0)}"
            )

        elapsed = time.time() - cycle_start
        sleep_sec = max(1, 60 - ist_now().second)
        if elapsed >= sleep_sec:
            logger.warning(f"Scan cycle took {elapsed:.1f}s, skipping sleep to catch next minute")
            return

        logger.info(f"Scan took {elapsed:.1f}s | Sleeping {sleep_sec}s until next minute")
        time.sleep(sleep_sec)

    def test_symbol(self, symbol: str):
        logger.info(f"=== TEST MODE: {symbol} ===")
        now = ist_now()
        logger.info(f"Current IST: {now}")

        q = self.client.quotes([symbol])
        if q:
            logger.info(f"Quotes raw:\n{json.dumps(q, indent=2)[:1200]}")
        else:
            logger.warning("Quotes returned None")

        df1 = self.client.fetch_history(symbol, "1", days=1, cont_flag="0")
        if not df1.empty:
            logger.info(f"1-min history OK. Rows: {len(df1)}")
            logger.info(df1.tail(5).to_string())
        else:
            logger.error("1-min history EMPTY.")

        df5 = self.client.fetch_history(symbol, "5", days=2, cont_flag="0")
        if not df5.empty:
            logger.info(f"5-min history OK. Rows: {len(df5)}")
            logger.info(df5.tail(3).to_string())
        else:
            logger.error("5-min history EMPTY.")

        df_d = self.client.fetch_history(symbol, "D", days=10, cont_flag="0")
        if not df_d.empty:
            logger.info(f"1D history OK. Rows: {len(df_d)}")
            logger.info(df_d.tail(3).to_string())
        else:
            logger.error("1D history EMPTY.")

        logger.info("=== TEST COMPLETE ===")

    def run(self):
        logger.info("SMC Breakout Scanner v2.9.1 Started. Press Ctrl+C to stop.")
        logger.info(f"CSV logging to: {os.path.abspath(self.cfg.csv_log_dir)}")
        logger.info(f"HTF confirmation: {self.cfg.htf_confirm} ({self.cfg.htf_resolution}-min on NIFTY50-INDEX)")
        logger.info(f"Futures rollover: {self.cfg.rollover_days_before_expiry} days before expiry")
        logger.info(f"Debug mode: {self.cfg.debug_mode} | Validate symbols: {self.cfg.validate_symbols}")

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


def main():
    parser = argparse.ArgumentParser(description="SMC Breakout Scanner")
    parser.add_argument("--test-symbol", type=str, default=None, help="Test a single symbol and exit")
    args = parser.parse_args()

    cfg = load_config()

    if "YOUR_" in cfg.app_id or "YOUR_" in cfg.access_token:
        logger.error("Fyers credentials not configured! Check config.yaml and token.txt")
        sys.exit(1)

    # Re-init logger with debug setting from config (no global needed)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    new_logger = setup_logging(debug=cfg.debug_mode)
    # Copy handlers to module logger
    for h in list(new_logger.handlers):
        new_logger.removeHandler(h)
        logger.addHandler(h)
    logger.setLevel(new_logger.level)

    orch = ScannerOrchestrator(cfg)

    if args.test_symbol:
        orch.test_symbol(args.test_symbol)
        sys.exit(0)

    orch.run()


if __name__ == "__main__":
    main()
