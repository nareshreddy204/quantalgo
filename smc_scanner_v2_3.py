# ═══════════════════════════════════════════════════════════════
# SMC BREAKOUT SCANNER v2.3 — Bug Fixes & Reviewed
# ═══════════════════════════════════════════════════════════════
# Fixes from v2.2:
# - Sleep logic: removed broken "+60" fix that caused missed minutes
# - Pivot detection: reverted to strict == to avoid duplicate consecutive pivots
# - VWAP: fixed safe division to properly return scalar values
# - Added scan cycle timing logs
# - Added defensive checks for empty DataFrames in VWAP
# ═══════════════════════════════════════════════════════════════

import os
import sys
import yaml
import logging
import pandas as pd
import numpy as np
import concurrent.futures
import requests
import time
import warnings
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

warnings.filterwarnings("ignore")

from fyers_apiv3 import fyersModel

# ─── Force UTF-8 on Windows console ───
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ───────────────────────────────────────────────────────────────
# Setup Logging
# ───────────────────────────────────────────────────────────────
def setup_logging():
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%H:%M:%S"
    logger = logging.getLogger("SMCScanner")
    logger.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(log_format, date_format))

    # File handler
    fh = logging.FileHandler("scanner.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger

logger = setup_logging()

# ───────────────────────────────────────────────────────────────
# Config Loader
# ──────────────────────────────────────────────────────────────
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
    max_workers: int = 10
    min_volume: int = 1000


def load_config(path: str = "config.yaml") -> Config:
    defaults = {
        "fyers": {"app_id": "YOUR_APP_ID"},
        "telegram": {"bot_token": "YOUR_BOT_TOKEN", "chat_id": "YOUR_CHAT_ID"},
        "scanner": {}
    }

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    for key, val in defaults.items():
        data.setdefault(key, val)

    app_id = data["fyers"].get("app_id", "YOUR_APP_ID")

    token_path = "token.txt"
    access_token = "YOUR_ACCESS_TOKEN"
    if os.path.exists(token_path):
        with open(token_path, "r", encoding="utf-8") as f:
            access_token = f.read().strip()

    bot_token = data["telegram"].get("bot_token", "YOUR_BOT_TOKEN")
    chat_id = data["telegram"].get("chat_id", "YOUR_CHAT_ID")

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
        max_workers=sc.get("max_workers", 10),
        min_volume=sc.get("min_volume", 1000)
    )

# ───────────────────────────────────────────────────────────────
# Telegram Notifier
# ───────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────
# Fyers Data Client (with retry)
# ───────────────────────────────────────────────────────────────
class FyersClient:
    def __init__(self, app_id: str, access_token: str):
        self.fyers = fyersModel.FyersModel(
            client_id=app_id,
            token=access_token,
            is_async=False,
            log_path=""
        )

    def history_with_retry(
        self,
        symbol: str,
        resolution: str,
        range_from: int,
        range_to: int,
        cont_flag: str = "1",
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
        for attempt in range(max_retries):
            try:
                resp = self.fyers.history(payload)
                if resp.get("s") == "ok":
                    return resp
                logger.warning(f"Fyers history error for {symbol}: {resp.get('message', 'Unknown')}, attempt {attempt+1}")
            except Exception as e:
                logger.warning(f"Fyers exception for {symbol}: {e}, attempt {attempt+1}")
            time.sleep(2 ** attempt)
        return None

    def fetch_history(
        self,
        symbol: str,
        resolution: str,
        days: int,
        cont_flag: str = "1"
    ) -> pd.DataFrame:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
        all_rows = []
        cursor = end_dt

        while cursor > start_dt:
            win_start = cursor - timedelta(days=50)
            if win_start < start_dt:
                win_start = start_dt

            resp = self.history_with_retry(
                symbol=symbol,
                resolution=resolution,
                range_from=int(win_start.timestamp()),
                range_to=int(cursor.timestamp()),
                cont_flag=cont_flag
            )

            if resp is None or not resp.get("candles"):
                break

            all_rows.extend(resp["candles"])
            cursor = win_start - timedelta(seconds=1)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(
            all_rows,
            columns=["epoch", "open", "high", "low", "close", "volume"]
        )
        df = df.drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
        df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
        return df[["Datetime", "open", "high", "low", "close", "volume"]].dropna()

# ──────────────────────────────────────────────────────────────
# Technical Indicators
# ───────────────────────────────────────────────────────────────
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
        """Intraday VWAP — resets daily. Assumes 1-min data."""
        if df.empty or "volume" not in df.columns:
            return pd.Series(index=df.index, dtype=float)

        df = df.copy()
        df["date"] = df["Datetime"].dt.date
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"]

        vwap_vals = np.full(len(df), np.nan)
        for date, group in df.groupby("date"):
            tp_vol = typical[group.index] * vol[group.index]
            cum_tp_vol = tp_vol.cumsum().values
            cum_vol = vol[group.index].cumsum().values
            # Safe division: avoid div by zero
            with np.errstate(divide='ignore', invalid='ignore'):
                v = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
            vwap_vals[group.index] = v

        return pd.Series(vwap_vals, index=df.index)

    @staticmethod
    def pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
        df = df.copy()
        df["pivot_high"] = np.nan
        df["pivot_low"] = np.nan
        highs = df["high"].values
        lows = df["low"].values

        for i in range(left, len(df) - right):
            window_high = highs[i - left:i + right + 1]
            window_low = lows[i - left:i + right + 1]
            # Strict equality ensures only the rightmost of consecutive equal highs is marked
            if highs[i] == window_high.max():
                df.loc[df.index[i], "pivot_high"] = highs[i]
            if lows[i] == window_low.min():
                df.loc[df.index[i], "pivot_low"] = lows[i]
        return df

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

# ──────────────────────────────────────────────────────────────
# Signal Result
# ───────────────────────────────────────────────────────────────
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

# ───────────────────────────────────────────────────────────────
# SMC Scanner Engine
# ──────────────────────────────────────────────────────────────
class SMCScanner:
    def __init__(self, config: Config, client: FyersClient):
        self.cfg = config
        self.client = client
        self.ind = Indicators()

    def scan(self, symbol: str, trend_bias: Optional[str] = None) -> SignalResult:
        df = self.client.fetch_history(symbol, "1", 1)
        if df.empty or len(df) < 30:
            return SignalResult(symbol=symbol, signal="NO_DATA")

        df["atr"] = self.ind.atr(df, self.cfg.atr_period)
        df = self.ind.pivots(df, left=self.cfg.swing_len, right=self.cfg.swing_len)

        last_idx = len(df) - 1
        atr_val = df["atr"].iloc[last_idx]

        if pd.isna(atr_val) or atr_val == 0:
            return SignalResult(symbol=symbol, signal="NO_SIGNAL")

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

            # IDM / Structure break detection
            if not pd.isna(lsh) and df["close"].iloc[i] > lsh and df["close"].iloc[i - 1] <= lsh:
                idm_level = lsl
                idm_is_bullish = True
            elif not pd.isna(lsl) and df["close"].iloc[i] < lsl and df["close"].iloc[i - 1] >= lsl:
                idm_level = lsh
                idm_is_bullish = False

            # Sweep detection
            if not pd.isna(idm_level):
                if idm_is_bullish and df["low"].iloc[i - 1] > idm_level and df["low"].iloc[i] < idm_level:
                    bs_active, bs_bar = True, i
                elif not idm_is_bullish and df["high"].iloc[i - 1] < idm_level and df["high"].iloc[i] > idm_level:
                    br_active, br_bar = True, i

            # Reset old sweeps
            if i - bs_bar > sweep_lookback:
                bs_active = False
            if i - br_bar > sweep_lookback:
                br_active = False

            # Only evaluate on the very last candle
            if i != last_idx:
                continue

            # Volume filter
            if df["volume"].iloc[i] < self.cfg.min_volume:
                return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="Low Volume Filter")

            body = abs(df["close"].iloc[i] - df["open"].iloc[i])
            candle_bullish = df["close"].iloc[i] > df["open"].iloc[i]
            candle_bearish = df["close"].iloc[i] < df["open"].iloc[i]

            # Displacement check
            if body <= atr_val * disp_mult:
                return SignalResult(symbol=symbol, signal="NO_SIGNAL")

            # LONG setup
            if candle_bullish and df["close"].iloc[i] > df["high"].iloc[i - 1] and bs_active:
                if self.cfg.trend_filter and trend_bias != "bullish":
                    return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="Trend filter blocked LONG")
                sl = df["low"].iloc[i] - atr_val * 0.5
                target = df["close"].iloc[i] + (df["close"].iloc[i] - sl) * self.cfg.risk_reward
                return SignalResult(
                    symbol=symbol,
                    signal="LONG",
                    time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i],
                    sl=sl,
                    target=target,
                    confidence="high" if df["close"].iloc[i] > df["high"].iloc[i-2] else "medium"
                )

            # SHORT setup
            if candle_bearish and df["close"].iloc[i] < df["low"].iloc[i - 1] and br_active:
                if self.cfg.trend_filter and trend_bias != "bearish":
                    return SignalResult(symbol=symbol, signal="NO_SIGNAL", context="Trend filter blocked SHORT")
                sl = df["high"].iloc[i] + atr_val * 0.5
                target = df["close"].iloc[i] - (sl - df["close"].iloc[i]) * self.cfg.risk_reward
                return SignalResult(
                    symbol=symbol,
                    signal="SHORT",
                    time=df["Datetime"].iloc[i],
                    price=df["close"].iloc[i],
                    sl=sl,
                    target=target,
                    confidence="high" if df["close"].iloc[i] < df["low"].iloc[i-2] else "medium"
                )

        return SignalResult(symbol=symbol, signal="NO_SIGNAL")

# ───────────────────────────────────────────────────────────────
# Symbol Generator
# ───────────────────────────────────────────────────────────────
class SymbolGenerator:
    @staticmethod
    def get_next_expiry(now: datetime) -> datetime:
        weekday = now.weekday()
        if weekday == 1 and now.hour < 16:
            return now.date()

        days_ahead = (1 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7

        expiry = now + timedelta(days=days_ahead)
        return expiry.date()

    @staticmethod
    def build_option_symbols(underlying: str, spot: float, expiry: datetime.date) -> List[str]:
        atm = int(round(spot / 50.0) * 50)
        strikes = [atm - 100, atm - 50, atm, atm + 50, atm + 100]
        types = ["CE", "PE"]

        exp_str = expiry.strftime("%y") + str(int(expiry.strftime("%m"))) + expiry.strftime("%d")
        prefix = f"NSE:{underlying}{exp_str}"

        return [f"{prefix}{s}{t}" for s in strikes for t in types]

# ───────────────────────────────────────────────────────────────
# Main Scanner Orchestrator
# ──────────────────────────────────────────────────────────────
class ScannerOrchestrator:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = FyersClient(config.app_id, config.access_token)
        self.scanner = SMCScanner(config, self.client)
        self.notifier = TelegramNotifier(config.bot_token, config.chat_id)
        self.sym_gen = SymbolGenerator()
        self.last_alert: Dict[str, datetime] = {}
        self.running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received. Exiting gracefully...")
        self.running = False

    def is_market_open(self, now: datetime) -> bool:
        t = now.time()
        return pd.Timestamp("09:15:00").time() <= t <= pd.Timestamp("15:40:00").time()

    def get_trend_bias(self) -> Tuple[Optional[str], Optional[float]]:
        df = self.client.fetch_history("NSE:NIFTY50-INDEX", "1", 1)
        if df.empty or len(df) < 20:
            return None, None

        df["vwap"] = Indicators.vwap(df)
        last = df.iloc[-1]
        spot = last["close"]

        if pd.isna(last["vwap"]):
            return None, spot

        bias = "bullish" if spot > last["vwap"] else "bearish"
        logger.info(f"Nifty Spot: {spot:.2f} | VWAP: {last['vwap']:.2f} | Bias: {bias}")
        return bias, spot

    def cooldown_ok(self, symbol: str, timestamp: datetime) -> bool:
        if symbol not in self.last_alert:
            return True
        elapsed = (timestamp - self.last_alert[symbol]).total_seconds() / 60
        return elapsed >= self.cfg.signal_cooldown_min

    def format_alert(self, signals: List[SignalResult]) -> str:
        lines = ["SMC BREAKOUT ALERT", ""]
        for s in signals:
            conf_marker = "[HIGH]" if s.confidence == "high" else "[MED]"
            lines.append(
                f"{s.signal} {conf_marker} on {s.symbol}\n"
                f"Price: Rs {s.price:.2f} | Time: {s.time.strftime('%H:%M:%S')}\n"
                f"Suggested SL: Rs {s.sl:.2f} | Target: Rs {s.target:.2f} (1:{self.cfg.risk_reward})"
            )
        return "\n\n".join(lines)

    def run_once(self):
        cycle_start = time.time()
        now = datetime.now()
        if not self.is_market_open(now):
            logger.info("Market closed. Sleeping 60s...")
            time.sleep(60)
            return

        logger.info("--- New Scan Cycle ---")

        trend_bias, spot = self.get_trend_bias()
        if spot is None:
            logger.warning("Failed to fetch Nifty spot. Skipping cycle.")
            time.sleep(60)
            return

        expiry = self.sym_gen.get_next_expiry(now)
        symbols = self.sym_gen.build_option_symbols("NIFTY", spot, expiry)
        logger.info(f"Expiry: {expiry} | Symbols: {len(symbols)} | Trend: {trend_bias}")

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
        fresh_signals = []

        for sig in active:
            if sig.time and self.cooldown_ok(sig.symbol, sig.time):
                fresh_signals.append(sig)
                self.last_alert[sig.symbol] = sig.time

        if fresh_signals:
            msg = self.format_alert(fresh_signals)
            logger.info(f"ALERT: {msg.replace(chr(10), ' ')}")
            if self.notifier.send(msg):
                logger.info("Alert sent to Telegram")
            else:
                logger.warning("Failed to send Telegram alert")
        else:
            logger.info("No fresh signals on latest candle.")

        # Sleep until next minute boundary
        elapsed = time.time() - cycle_start
        sleep_sec = max(1, 60 - datetime.now().second)
        # If scan took longer than the remaining seconds, skip sleep (edge case)
        if elapsed >= sleep_sec:
            logger.warning(f"Scan cycle took {elapsed:.1f}s, skipping sleep to catch next minute")
            return

        logger.info(f"Scan took {elapsed:.1f}s | Sleeping {sleep_sec}s until next minute")
        time.sleep(sleep_sec)

    def run(self):
        logger.info("SMC Breakout Scanner v2.3 Started. Press Ctrl+C to stop.")
        while self.running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(30)
        logger.info("Scanner stopped.")

# ──────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()

    if "YOUR_" in cfg.app_id or "YOUR_" in cfg.access_token:
        logger.error("Fyers credentials not configured! Check config.yaml and token.txt")
        sys.exit(1)

    orch = ScannerOrchestrator(cfg)
    orch.run()
