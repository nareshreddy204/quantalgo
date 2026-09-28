"""
═══════════════════════════════════════════════════════════════
BB TRAP SCANNER v1.0 — Cash Segment Stock Scanner
═══════════════════════════════════════════════════════════════
Pattern: Bollinger Band Trap (Bearish Reversal)
  Candle 1: Close > Upper BB(20,2) AND Close > Open  (bullish breakout)
  Candle 2: Close < Open AND Close < VWAP            (bearish trap below VWAP)

Timeframe: 1-Hour (configurable)
Segment: NSE Cash (equity stocks)
══════════════════════════════════════════════════════════════
"""

import os
import sys
import csv
import yaml
import logging
import threading
import time
import warnings
import signal
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import requests

warnings.filterwarnings("ignore")

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    fyersModel = None

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ═════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════
def setup_logging() -> logging.Logger:
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%H:%M:%S"
    logger = logging.getLogger("BBTrapScanner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_format, date_format))

    fh = logging.FileHandler("bb_trap_scanner.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


logger = setup_logging()


# ═════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════
@dataclass
class Config:
    app_id: str
    access_token: str
    bot_token: str
    chat_id: str

    # Pattern Parameters
    bb_period: int = 20
    bb_std_dev: int = 2
    timeframe: str = "60"  # Fyers resolution: "60" = 1 hour
    lookback_days: int = 5

    # Filters
    min_volume: int = 100000
    max_results: int = 10

    # Execution
    max_workers: int = 4
    csv_log_dir: str = "bb_trap_logs"
    scan_interval_sec: int = 300  # 5 minutes between scans

    # Stock Universe (NSE Cash symbols)
    symbols: List[str] = None

    def __post_init__(self):
        if self.symbols is None:
            # Default: liquid large-cap NSE stocks
            self.symbols = [
                "NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ",
                "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ", "NSE:SBIN-EQ",
                "NSE:HINDUNILVR-EQ", "NSE:ITC-EQ", "NSE:LT-EQ",
                "NSE:AXISBANK-EQ", "NSE:KOTAKBANK-EQ", "NSE:MARUTI-EQ",
                "NSE:BAJFINANCE-EQ", "NSE:ASIANPAINT-EQ", "NSE:SUNPHARMA-EQ"
            ]


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
        bb_period=sc.get("bb_period", 20),
        bb_std_dev=sc.get("bb_std_dev", 2),
        timeframe=str(sc.get("timeframe", "60")),
        lookback_days=sc.get("lookback_days", 5),
        min_volume=sc.get("min_volume", 100000),
        max_results=sc.get("max_results", 10),
        max_workers=sc.get("max_workers", 4),
        csv_log_dir=sc.get("csv_log_dir", "bb_trap_logs"),
        scan_interval_sec=sc.get("scan_interval_sec", 300),
        symbols=sc.get("symbols", None)
    )


# ═════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════
@dataclass
class BBTrapSignal:
    """Represents a detected BB Trap pattern."""
    symbol: str
    signal_time: datetime
    breakout_time: datetime
    trap_time: datetime
    breakout_close: float
    breakout_bb_upper: float
    breakout_open: float
    trap_close: float
    trap_open: float
    trap_vwap: float
    trap_low: float
    trap_high: float
    price_change_pct: float
    volume_breakout: int
    volume_trap: int


# ═════════════════════════════════════════════════════════════
# INDICATORS
# ═════════════════════════════════════════════════════════════
def bollinger_bands(series: pd.Series, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands: middle (SMA), upper, lower."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return middle, upper, lower


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculate Volume Weighted Average Price (VWAP) reset per trading session.
    typical_price = (high + low + close) / 3
    """
    df = df.copy()
    df["date"] = df["Datetime"].dt.date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = typical * df["volume"]

    vwap_vals = np.full(len(df), np.nan)
    for _, group in df.groupby("date"):
        cum_tp_vol = group["tp_vol"].cumsum().values
        cum_vol = group["volume"].cumsum().values
        with np.errstate(divide='ignore', invalid='ignore'):
            v = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
        vwap_vals[group.index] = v

    return pd.Series(vwap_vals, index=df.index)


# ═════════════════════════════════════════════════════════════
# BB TRAP DETECTION ENGINE
# ═════════════════════════════════════════════════════════════
def detect_bb_trap(df: pd.DataFrame, symbol: str, cfg: Config) -> Optional[BBTrapSignal]:
    """
    Detects the BB Trap pattern on configured timeframe:

    Pattern Rules:
    - Candle 1 (Breakout): Close > Upper BB(20,2) AND Close > Open
    - Candle 2 (Trap):     Close < Open AND Close < VWAP

    Returns the most recent trap signal if found.
    """
    if df.empty or len(df) < cfg.bb_period + 5:
        return None

    df = df.copy().reset_index(drop=True)
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    df = df.sort_values("Datetime").reset_index(drop=True)

    # Calculate indicators
    df["bb_middle"], df["bb_upper"], df["bb_lower"] = bollinger_bands(
        df["close"], period=cfg.bb_period, std_dev=cfg.bb_std_dev
    )
    df["vwap"] = session_vwap(df)

    # Need valid indicators
    df_valid = df.dropna(subset=["bb_upper", "vwap"]).reset_index(drop=True)
    if len(df_valid) < 3:
        return None

    # Walk through candles to find the pattern
    # Candle at i-1 = Breakout, Candle at i = Trap
    for i in range(1, len(df_valid)):
        prev = df_valid.iloc[i - 1]
        curr = df_valid.iloc[i]

        # Volume filter
        if prev["volume"] < cfg.min_volume or curr["volume"] < cfg.min_volume:
            continue

        # Breakout candle (candle i-1): Close > Open AND Close > Upper BB
        breakout_bullish = prev["close"] > prev["open"]
        breakout_above_bb = prev["close"] > prev["bb_upper"]

        # Trap candle (candle i): Close < Open AND Close < VWAP
        trap_bearish = curr["close"] < curr["open"]
        trap_below_vwap = curr["close"] < curr["vwap"]

        if breakout_bullish and breakout_above_bb and trap_bearish and trap_below_vwap:
            price_change = ((curr["close"] - prev["close"]) / prev["close"]) * 100
            return BBTrapSignal(
                symbol=symbol,
                signal_time=curr["Datetime"],
                breakout_time=prev["Datetime"],
                trap_time=curr["Datetime"],
                breakout_close=prev["close"],
                breakout_bb_upper=prev["bb_upper"],
                breakout_open=prev["open"],
                trap_close=curr["close"],
                trap_open=curr["open"],
                trap_vwap=curr["vwap"],
                trap_low=curr["low"],
                trap_high=curr["high"],
                price_change_pct=price_change,
                volume_breakout=int(prev["volume"]),
                volume_trap=int(curr["volume"])
            )

    return None


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
        self._min_interval = 0.12  # ~8 req/sec

    def _throttle(self):
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.time()

    def history_with_retry(self, symbol: str, resolution: str,
                           range_from: int, range_to: int,
                           cont_flag: str = "0", max_retries: int = 3) -> Optional[dict]:
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

    def fetch_history(self, symbol: str, resolution: str, days: int = 5) -> pd.DataFrame:
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
                range_to=int(cursor.timestamp())
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


# ═════════════════════════════════════════════════════════════
# CSV DATA LOADER (for backtesting)
# ═════════════════════════════════════════════════════════════
def load_csv_data(filepath: str) -> pd.DataFrame:
    """Load historical data from CSV file."""
    try:
        df = pd.read_csv(filepath)
        required = ["Datetime", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                logger.error(f"CSV missing required column: {col}")
                return pd.DataFrame()
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        return df.sort_values("Datetime").reset_index(drop=True)
    except Exception as e:
        logger.error(f"Failed to load CSV {filepath}: {e}")
        return pd.DataFrame()


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
# TRADE LOGGER
# ═════════════════════════════════════════════════════════════
class TradeLogger:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.columns = [
            "date", "time", "symbol", "signal", "breakout_time", "trap_time",
            "breakout_close", "bb_upper", "trap_open", "trap_close",
            "trap_vwap", "price_change_pct", "volume_breakout", "volume_trap"
        ]
        self._lock = threading.Lock()
        self._file_path = None
        self._writer = None
        self._file = None
        self._init_daily_file()

    def _init_daily_file(self):
        today = datetime.now().strftime("%Y%m%d")
        self._file_path = os.path.join(self.log_dir, f"bb_trap_signals_{today}.csv")
        file_exists = os.path.exists(self._file_path)
        self._file = open(self._file_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.columns)
        if not file_exists:
            self._writer.writeheader()
            logger.info(f"Created new BB Trap log: {self._file_path}")

    def log(self, signal: BBTrapSignal):
        with self._lock:
            expected_path = os.path.join(
                self.log_dir, f"bb_trap_signals_{datetime.now().strftime('%Y%m%d')}.csv"
            )
            if expected_path != self._file_path:
                if self._file:
                    self._file.close()
                self._init_daily_file()

            row = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "symbol": signal.symbol,
                "signal": "BB_TRAP",
                "breakout_time": signal.breakout_time.strftime("%Y-%m-%d %H:%M"),
                "trap_time": signal.trap_time.strftime("%Y-%m-%d %H:%M"),
                "breakout_close": f"{signal.breakout_close:.2f}",
                "bb_upper": f"{signal.breakout_bb_upper:.2f}",
                "trap_open": f"{signal.trap_open:.2f}",
                "trap_close": f"{signal.trap_close:.2f}",
                "trap_vwap": f"{signal.trap_vwap:.2f}",
                "price_change_pct": f"{signal.price_change_pct:+.2f}",
                "volume_breakout": str(signal.volume_breakout),
                "volume_trap": str(signal.volume_trap),
            }
            self._writer.writerow(row)
            self._file.flush()

    def close(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None


# ═════════════════════════════════════════════════════════════
# SCANNER ORCHESTRATOR
# ═════════════════════════════════════════════════════════════
class BBTrapScanner:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = FyersClient(config.app_id, config.access_token)
        self.notifier = TelegramNotifier(config.bot_token, config.chat_id)
        self.trade_logger = TradeLogger(config.csv_log_dir)
        self.running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        self.running = False

    def is_market_open(self, now: datetime) -> bool:
        t = now.time()
        return pd.Timestamp("09:15:00").time() <= t <= pd.Timestamp("15:30:00").time()

    def scan_symbol(self, symbol: str) -> Optional[BBTrapSignal]:
        """Fetch data and scan a single symbol."""
        df = self.client.fetch_history(symbol, self.cfg.timeframe, days=self.cfg.lookback_days)
        if df.empty or len(df) < self.cfg.bb_period + 2:
            return None
        return detect_bb_trap(df, symbol, self.cfg)

    def scan_csv(self, filepath: str, symbol: str) -> Optional[BBTrapSignal]:
        """Scan a symbol from a local CSV file (for backtesting)."""
        df = load_csv_data(filepath)
        if df.empty:
            return None
        return detect_bb_trap(df, symbol, self.cfg)

    def format_alert(self, signals: List[BBTrapSignal]) -> str:
        lines = ["<b>═══ BB TRAP ALERT ═══</b>", ""]
        for s in signals:
            lines.append(
                f"<b>{s.symbol}</b> | BB Trap Detected"
            )
            lines.append(f"Breakout: {s.breakout_time.strftime('%d-%b %H:%M')} | Close: Rs {s.breakout_close:.2f} | BB Upper: Rs {s.breakout_bb_upper:.2f}")
            lines.append(f"Trap:     {s.trap_time.strftime('%d-%b %H:%M')} | Open: Rs {s.trap_open:.2f} | Close: Rs {s.trap_close:.2f} | VWAP: Rs {s.trap_vwap:.2f}")
            lines.append(f"Change:   {s.price_change_pct:+.2f}% | Vol(Bo): {s.volume_breakout:,} | Vol(Trap): {s.volume_trap:,}")
            lines.append("")
        return "\n".join(lines)

    def run_once(self, force: bool = False):
        """Single scan cycle over the stock universe."""
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        logger.info("--- BB Trap Scan Cycle ---")

        if not force and not self.is_market_open(now):
            logger.info("Market closed. Use --now to force scan outside market hours.")
            return

        results: List[BBTrapSignal] = []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futures = {executor.submit(self.scan_symbol, sym): sym for sym in self.cfg.symbols}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"Scan error: {e}")

        if results:
            # Sort by price change magnitude (most severe traps first)
            results.sort(key=lambda x: abs(x.price_change_pct), reverse=True)
            top_results = results[:self.cfg.max_results]

            for sig in top_results:
                self.trade_logger.log(sig)

            msg = self.format_alert(top_results)
            logger.info(f"ALERT: {msg.replace(chr(10), ' | ')}")
            if self.notifier.send(msg):
                logger.info(f"Alert sent for {len(top_results)} symbols")
            else:
                logger.warning("Failed to send Telegram alert")
        else:
            logger.info(f"No BB Trap patterns found across {len(self.cfg.symbols)} symbols")

    def run_csv_backtest(self, csv_files: Dict[str, str]):
        """
        Run scanner on local CSV files for backtesting.
        csv_files: {symbol_name: filepath}
        """
        logger.info("=" * 70)
        logger.info("BB TRAP SCANNER — CSV BACKTEST MODE")
        logger.info("=" * 70)

        results = []
        for symbol, filepath in csv_files.items():
            signal = self.scan_csv(filepath, symbol)
            if signal:
                results.append(signal)
                self.trade_logger.log(signal)
                logger.info(f"BB TRAP | {symbol} | {signal.trap_time} | Change: {signal.price_change_pct:+.2f}%")
            else:
                logger.info(f"No pattern | {symbol}")

        if results:
            msg = self.format_alert(results)
            print("\n" + "=" * 70)
            print("BACKTEST RESULTS")
            print("=" * 70)
            print(msg.replace("<b>", "").replace("</b>", "").replace("\n", "\n"))
            self.notifier.send(msg)
        else:
            print("\nNo BB Trap patterns detected in backtest data.")

        logger.info("=" * 70)

    def run(self, force: bool = False):
        """Main live scanning loop."""
        mode = "FORCED" if force else "LIVE"
        logger.info(f"BB Trap Scanner v1.0 Started [{mode} MODE]. Press Ctrl+C to stop.")
        logger.info(f"Symbols: {len(self.cfg.symbols)}")
        logger.info(f"Timeframe: {self.cfg.timeframe}-min | BB({self.cfg.bb_period},{self.cfg.bb_std_dev})")
        logger.info(f"CSV logging to: {os.path.abspath(self.cfg.csv_log_dir)}")
        if force:
            logger.info("--now flag active: scanning outside market hours")

        try:
            while self.running:
                try:
                    self.run_once(force=force)
                except Exception as e:
                    logger.error(f"Main loop error: {e}", exc_info=True)

                if self.running:
                    logger.info(f"Sleeping {self.cfg.scan_interval_sec}s until next scan...")
                    time.sleep(self.cfg.scan_interval_sec)
        finally:
            self.trade_logger.close()
            logger.info("Scanner stopped.")


# ═════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════
if __name__ == "__main__":
    cfg = load_config()

    import sys
    args = sys.argv[1:]
    force_now = "--now" in args
    backtest_mode = "--backtest" in args

    # Remove flags from args for processing
    clean_args = [a for a in args if a not in ("--now", "--backtest")]

    if backtest_mode:
        # Example: python bb_trap_scanner.py --backtest RELIANCE_1h.csv:RELIANCE TCS_1h.csv:TCS
        csv_files = {}
        for arg in clean_args:
            if ":" in arg:
                filepath, symbol = arg.rsplit(":", 1)
                csv_files[symbol] = filepath

        if not csv_files:
            # Default backtest files
            csv_files = {
                "RELIANCE": "RELIANCE_1h.csv",
                "TCS": "TCS_1h.csv"
            }

        scanner = BBTrapScanner(cfg)
        scanner.run_csv_backtest(csv_files)
    else:
        # Live mode
        if "YOUR_" in cfg.app_id or "YOUR_" in cfg.access_token:
            logger.error("Fyers credentials not configured! Check config.yaml and token.txt")
            sys.exit(1)

        scanner = BBTrapScanner(cfg)
        scanner.run(force=force_now)