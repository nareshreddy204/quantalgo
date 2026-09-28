from __future__ import annotations

import argparse
import csv
import logging
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import requests
import yaml
from zoneinfo import ZoneInfo

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

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger("SMCScanner")

MARKET_OPEN_TIME = dtime(9, 15)
MARKET_CLOSE_TIME = dtime(15, 30)

# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

@dataclass
class Config:
    app_id: str
    access_token: str
    telegram_token: str
    telegram_chat_id: str
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1"
    htf_timeframe: str = "5"
    history_days: int = 5
    max_buffer_rows: int = 1500
    swing_length: int = 10
    use_websocket: bool = True
    close_break: bool = True
    require_bos: bool = True
    premium_discount_filter: bool = True
    require_fvg: bool = False
    require_order_block: bool = False
    require_liquidity: bool = False
    require_htf: bool = False
    structure_lookback: int = 8
    atr_period: int = 14
    displacement_atr_mult: float = 0.60
    risk_reward: float = 2.0
    min_confidence_score: int = 3
    cooldown_minutes: int = 10
    min_volume: int = 0
    enforce_market_hours: bool = True
    log_directory: str = "trade_logs"
    debug: bool = False


def load_config(filename: str = "config.yaml") -> Config:
    with open(filename, "r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    fyers = raw.get("fyers", {})
    telegram = raw.get("telegram", {})
    scanner = raw.get("scanner", {})

    symbols = scanner.get("symbols", [])
    if isinstance(symbols, str):
        symbols = [symbols]

    if not symbols:
        raise ValueError("scanner.symbols must contain at least one symbol")

    token = fyers.get("access_token", "")
    if not token and Path("token.txt").exists():
        token = Path("token.txt").read_text(encoding="utf-8").strip()

    return Config(
        app_id=str(fyers.get("app_id", "")),
        access_token=str(token),
        telegram_token=str(telegram.get("bot_token", "")),
        telegram_chat_id=str(telegram.get("chat_id", "")),
        symbols=symbols,
        timeframe=str(scanner.get("timeframe", "1")),
        htf_timeframe=str(scanner.get("htf_timeframe", "5")),
        history_days=int(scanner.get("history_days", 5)),
        max_buffer_rows=int(scanner.get("max_buffer_rows", 1500)),
        swing_length=int(scanner.get("swing_length", 10)),
        use_websocket=bool(scanner.get("use_websocket", True)),
        close_break=bool(scanner.get("close_break", True)),
        require_bos=bool(scanner.get("require_bos", True)),
        premium_discount_filter=bool(scanner.get("premium_discount_filter", True)),
        require_fvg=bool(scanner.get("require_fvg", False)),
        require_order_block=bool(scanner.get("require_order_block", False)),
        require_liquidity=bool(scanner.get("require_liquidity", False)),
        require_htf=bool(scanner.get("require_htf", False)),
        structure_lookback=int(scanner.get("structure_lookback", 8)),
        atr_period=int(scanner.get("atr_period", 14)),
        displacement_atr_mult=float(scanner.get("displacement_atr_mult", 0.60)),
        risk_reward=float(scanner.get("risk_reward", 2.0)),
        min_confidence_score=int(scanner.get("min_confidence_score", 3)),
        cooldown_minutes=int(scanner.get("cooldown_minutes", 10)),
        min_volume=int(scanner.get("min_volume", 0)),
        enforce_market_hours=bool(scanner.get("enforce_market_hours", True)),
        log_directory=str(scanner.get("log_directory", "trade_logs")),
        debug=bool(scanner.get("debug", False)),
    )

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

def setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("scanner.log", encoding="utf-8"),
        ],
        force=True,
    )

# ---------------------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------------------

def now_ist() -> datetime:
    return datetime.now(IST)


def parse_timeframe_minutes(timeframe: str) -> int:
    value = str(timeframe).strip().lower()
    if value in {"d", "1d", "day", "1day"}:
        return 1440
    try:
        return max(1, int(value))
    except ValueError:
        LOG.warning("Unrecognised timeframe '%s'; falling back to 1 minute", timeframe)
        return 1


def floor_to_interval(ts: datetime, minutes: int) -> datetime:
    minutes = max(1, min(1440, minutes))
    minute_of_day = ts.hour * 60 + ts.minute
    floored = (minute_of_day // minutes) * minutes
    return ts.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)


def is_market_open(ts: Optional[datetime] = None) -> bool:
    ts = ts or now_ist()
    if ts.weekday() >= 5:
        return False
    return MARKET_OPEN_TIME <= ts.time() <= MARKET_CLOSE_TIME

# ---------------------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------------------

def normalise_candles(candles: list[list[Any]]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(
            columns=["Datetime", "open", "high", "low", "close", "volume"]
        )

    df = pd.DataFrame(
        candles,
        columns=["epoch", "open", "high", "low", "close", "volume"],
    )

    df["Datetime"] = (
        pd.to_datetime(df["epoch"], unit="s", utc=True)
        .dt.tz_convert(IST)
        .dt.tz_localize(None)
    )

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df[["Datetime", "open", "high", "low", "close", "volume"]]

    df[["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)

    return (
        df.dropna()
        .drop_duplicates("Datetime")
        .sort_values("Datetime")
        .reset_index(drop=True)
    )


def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()

# ---------------------------------------------------------------------
# CANDLE BUFFER
# ---------------------------------------------------------------------

class CandleBuffer:
    def __init__(self, max_rows: int):
        self.max_rows = max_rows
        self._data: dict[str, pd.DataFrame] = {}
        self._lock = threading.RLock()

    def seed(self, symbol: str, df: pd.DataFrame) -> None:
        with self._lock:
            self._data[symbol] = (
                df.tail(self.max_rows)
                .drop_duplicates("Datetime")
                .sort_values("Datetime")
                .reset_index(drop=True)
            )

    def get(self, symbol: str) -> pd.DataFrame:
        with self._lock:
            df = self._data.get(symbol)
            return df.copy() if df is not None else pd.DataFrame()

    def last_datetime(self, symbol: str) -> Optional[pd.Timestamp]:
        with self._lock:
            df = self._data.get(symbol)
            if df is None or df.empty:
                return None
            return df["Datetime"].iloc[-1]

    def add_closed_candle(self, symbol: str, candle: dict[str, Any]) -> bool:
        required = ["Datetime", "open", "high", "low", "close", "volume"]
        if any(key not in candle for key in required):
            return False

        try:
            new_row = pd.DataFrame(
                [{
                    "Datetime": candle["Datetime"],
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle["volume"]),
                }]
            )
        except (TypeError, ValueError):
            return False

        with self._lock:
            old = self._data.get(symbol)

            if old is None or old.empty:
                self._data[symbol] = new_row
                return True

            last_dt = old["Datetime"].iloc[-1]
            candle_dt = candle["Datetime"]

            if last_dt == candle_dt:
                for col in ("open", "high", "low", "close", "volume"):
                    old.at[old.index[-1], col] = new_row[col].iloc[0]
                self._data[symbol] = old
                return False

            if last_dt > candle_dt:
                return False

            merged = (
                pd.concat([old, new_row], ignore_index=True)
                .drop_duplicates("Datetime")
                .sort_values("Datetime")
                .tail(self.max_rows)
                .reset_index(drop=True)
            )
            self._data[symbol] = merged
            return True

# ---------------------------------------------------------------------
# FYERS REST CLIENT
# ---------------------------------------------------------------------

class FyersClient:
    def __init__(self, config: Config):
        if fyersModel is None:
            raise RuntimeError("Install fyers-apiv3 before running the scanner")

        if not config.app_id or not config.access_token:
            raise ValueError("Fyers app_id and access_token are required")

        self.client = fyersModel.FyersModel(
            client_id=config.app_id,
            token=config.access_token,
            is_async=False,
            log_path="",
        )
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._min_interval = 0.35

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.time() - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.time()

    def _history_request(
        self,
        symbol: str,
        resolution: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.DataFrame, Optional[datetime]]:
        start_ts = int(start.replace(tzinfo=IST).timestamp()) if start.tzinfo is None else int(start.timestamp())
        end_ts = int(end.replace(tzinfo=IST).timestamp()) if end.tzinfo is None else int(end.timestamp())

        payload = {
            "symbol": symbol,
            "resolution": str(resolution),
            "date_format": "0",
            "range_from": start_ts,
            "range_to": end_ts,
            "cont_flag": "1",
        }

        for attempt in range(3):
            try:
                self._throttle()
                response = self.client.history(payload)
                if response and response.get("s") == "ok":
                    return normalise_candles(response.get("candles", [])), None

                if response and response.get("s") == "no_data":
                    next_time_raw = response.get("nextTime")
                    if next_time_raw:
                        try:
                            next_time = datetime.fromtimestamp(int(next_time_raw))
                            LOG.debug(
                                "History no_data for %s; next available at %s",
                                symbol,
                                next_time,
                            )
                            return pd.DataFrame(), next_time
                        except (ValueError, TypeError, OSError):
                            pass

                LOG.warning("History failed for %s: %s", symbol, response)
            except Exception as exc:
                LOG.warning(
                    "History attempt %s failed for %s: %s",
                    attempt + 1,
                    symbol,
                    exc,
                )
            time.sleep(1 + attempt)

        return pd.DataFrame(), None

    def history(self, symbol: str, resolution: str, days: int) -> pd.DataFrame:
        end = now_ist().replace(tzinfo=None)
        start = end - timedelta(days=days)
        step = timedelta(minutes=parse_timeframe_minutes(resolution))

        chunk_days = 5 if step <= timedelta(minutes=15) else 30
        frames: list[pd.DataFrame] = []
        cursor = start
        guard = 0

        while cursor < end and guard < 60:
            guard += 1
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            df_chunk, next_time = self._history_request(symbol, resolution, cursor, chunk_end)

            if not df_chunk.empty:
                frames.append(df_chunk)
                cursor = df_chunk["Datetime"].max().to_pydatetime() + step
            elif next_time is not None:
                cursor = next_time
            else:
                cursor = chunk_end

        if not frames:
            return pd.DataFrame()

        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates("Datetime")
            .sort_values("Datetime")
            .reset_index(drop=True)
        )

    def quote(self, symbol: str) -> Optional[float]:
        try:
            self._throttle()
            response = self.client.quotes({"symbols": symbol})
            if response.get("s") != "ok":
                return None

            data = response.get("d", [])
            if not data:
                return None

            values = data[0].get("v", {})
            return float(values.get("lp") or values.get("ltp") or 0)
        except Exception as exc:
            LOG.warning("Quote failed for %s: %s", symbol, exc)
            return None

# ---------------------------------------------------------------------
# SMC ANALYSIS
# ---------------------------------------------------------------------

@dataclass
class Signal:
    symbol: str
    direction: str
    timestamp: datetime
    entry: float
    stop_loss: float
    target: float
    confidence: str
    score: int
    structure: str
    zone: str
    confluence: str


def recent_valid_value(df: pd.DataFrame, column: str, lookback: int) -> Any:
    if df is None or df.empty or column not in df.columns:
        return None
    tail = df[column].tail(max(1, lookback)).dropna()
    if tail.empty:
        return None
    return tail.iloc[-1]


def confidence_for_score(score: int) -> str:
    if score >= 5:
        return "high"
    if score >= 4:
        return "medium-high"
    return "medium"


def analyse_smc(
    symbol: str,
    df: pd.DataFrame,
    config: Config,
    htf_evaluator: Optional[Callable[[str], Optional[bool]]] = None,
) -> Optional[Signal]:
    if smc is None:
        raise RuntimeError(
            "Install smartmoneyconcepts before running the scanner"
        )

    minimum_rows = max(
        config.swing_length * 3,
        config.atr_period + 10,
        80,
    )

    if len(df) < minimum_rows:
        LOG.debug("%s: insufficient candles (%d)", symbol, len(df))
        return None

    df = (
        df.copy()
        .drop_duplicates("Datetime")
        .sort_values("Datetime")
        .reset_index(drop=True)
    )

    # Indices (like NSE:NIFTY50-INDEX) do not report volume; bypass filter for them
    if config.min_volume > 0 and not symbol.endswith("-INDEX"):
        if float(df["volume"].iloc[-1]) < config.min_volume:
            return None

    ohlc = df[["open", "high", "low", "close"]].copy()
    ohlc["volume"] = df["volume"].fillna(0)
    ohlc = ohlc.astype(float)

    swings = smc.swing_highs_lows(ohlc, swing_length=config.swing_length)
    structure = smc.bos_choch(ohlc, swings, close_break=config.close_break)
    fvg = smc.fvg(ohlc)
    order_blocks = smc.ob(ohlc, swings)
    liquidity = smc.liquidity(ohlc, swings)

    last = len(df) - 1
    close = float(df["close"].iloc[last])
    high = float(df["high"].iloc[last])
    low = float(df["low"].iloc[last])
    open_ = float(df["open"].iloc[last])

    atr_value = calculate_atr(df, config.atr_period).iloc[last]

    if pd.isna(atr_value) or atr_value <= 0:
        return None

    body = abs(close - open_)

    bullish_displacement = (
        close > open_ and body >= atr_value * config.displacement_atr_mult
    )
    bearish_displacement = (
        close < open_ and body >= atr_value * config.displacement_atr_mult
    )

    lookback = max(1, config.structure_lookback)
    bos_value = recent_valid_value(structure, "BOS", lookback)
    choch_value = recent_valid_value(structure, "CHOCH", lookback)

    bullish_structure = bos_value == 1 or choch_value == 1
    bearish_structure = bos_value == -1 or choch_value == -1

    if not bullish_structure and not bearish_structure:
        return None

    bullish = bullish_structure and bullish_displacement
    bearish = bearish_structure and bearish_displacement

    if config.require_bos:
        if bullish and bos_value != 1:
            bullish = False
        if bearish and bos_value != -1:
            bearish = False

    if not bullish and not bearish:
        return None

    if bullish and bearish:
        LOG.debug("%s: conflicting structure, no signal", symbol)
        return None

    sh_vals = swings["Level"][swings["HighLow"] == 1].dropna()
    sl_vals = swings["Level"][swings["HighLow"] == -1].dropna()
    if len(sh_vals) == 0 or len(sl_vals) == 0:
        return None

    swing_high = float(sh_vals.iloc[-1])
    swing_low = float(sl_vals.iloc[-1])

    if swing_high <= swing_low:
        return None

    equilibrium = (swing_high + swing_low) / 2

    if close < equilibrium:
        zone = "discount"
    elif close > equilibrium:
        zone = "premium"
    else:
        zone = "equilibrium"

    if config.premium_discount_filter:
        if bullish and zone != "discount":
            return None
        if bearish and zone != "premium":
            return None

    direction = "LONG" if bullish else "SHORT"
    structure_name = (
        ("BOS↑" if bos_value == 1 else "CHoCH↑")
        if bullish
        else ("BOS↓" if bos_value == -1 else "CHoCH↓")
    )

    score = 1
    confluence: list[str] = ["displacement"]

    # FVG confirmation
    fvg_confirmed = False
    if "FVG" in fvg.columns:
        recent_fvg = fvg["FVG"].tail(5).fillna(0)
        if bullish and (recent_fvg == 1).any():
            fvg_confirmed = True
        if bearish and (recent_fvg == -1).any():
            fvg_confirmed = True

    if fvg_confirmed:
        score += 1
        confluence.append("FVG")
    elif config.require_fvg:
        return None

    # Order-block confirmation
    ob_confirmed = False
    if "OB" in order_blocks.columns:
        recent_ob = order_blocks["OB"].tail(5).fillna(0)
        if bullish and (recent_ob == 1).any():
            ob_confirmed = True
        if bearish and (recent_ob == -1).any():
            ob_confirmed = True

    if ob_confirmed:
        score += 1
        confluence.append("OB")
    elif config.require_order_block:
        return None

    # Liquidity confirmation
    liquidity_confirmed = False
    if "Liquidity" in liquidity.columns:
        recent_liquidity = liquidity["Liquidity"].tail(8).fillna(0)
        if bullish and (recent_liquidity == 1).any():
            liquidity_confirmed = True
        if bearish and (recent_liquidity == -1).any():
            liquidity_confirmed = True

    if liquidity_confirmed:
        score += 1
        confluence.append("liquidity")
    elif config.require_liquidity:
        return None

    # HTF confirmation via callback
    htf_ok: Optional[bool] = None
    if htf_evaluator is not None:
        try:
            htf_ok = bool(htf_evaluator(direction))
        except Exception as exc:
            LOG.debug("%s: HTF evaluation failed: %s", symbol, exc)
            htf_ok = None

    if htf_ok:
        score += 1
        confluence.append("HTF")

    if config.require_htf and htf_ok is not True:
        return None

    if score < config.min_confidence_score:
        return None

    if bullish:
        stop_loss = min(low - atr_value * 0.20, swing_low)
        risk = close - stop_loss
        target = close + risk * config.risk_reward
    else:
        stop_loss = max(high + atr_value * 0.20, swing_high)
        risk = stop_loss - close
        target = close - risk * config.risk_reward

    if risk <= 0:
        return None

    return Signal(
        symbol=symbol,
        direction=direction,
        timestamp=df["Datetime"].iloc[last],
        entry=close,
        stop_loss=float(stop_loss),
        target=float(target),
        confidence=confidence_for_score(score),
        score=score,
        structure=structure_name,
        zone=zone,
        confluence=",".join(confluence),
    )

# ---------------------------------------------------------------------
# LOGGING AND TELEGRAM
# ---------------------------------------------------------------------

class SignalLogger:
    FIELDS = [
        "date",
        "time",
        "symbol",
        "direction",
        "entry",
        "stop_loss",
        "target",
        "confidence",
        "score",
        "structure",
        "zone",
        "confluence",
    ]

    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def write(self, signal_data: Signal) -> None:
        filename = (
            self.directory / f"signals_{now_ist().strftime('%Y%m%d')}.csv"
        )
        exists = filename.exists()

        row = {
            "date": signal_data.timestamp.strftime("%Y-%m-%d"),
            "time": signal_data.timestamp.strftime("%H:%M:%S"),
            "symbol": signal_data.symbol,
            "direction": signal_data.direction,
            "entry": f"{signal_data.entry:.2f}",
            "stop_loss": f"{signal_data.stop_loss:.2f}",
            "target": f"{signal_data.target:.2f}",
            "confidence": signal_data.confidence,
            "score": signal_data.score,
            "structure": signal_data.structure,
            "zone": signal_data.zone,
            "confluence": signal_data.confluence,
        }

        with self.lock:
            with filename.open("a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.FIELDS)
                if not exists:
                    writer.writeheader()
                writer.writerow(row)


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.enabled = bool(
            token and chat_id and "YOUR_" not in token and "YOUR_" not in chat_id
        )
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, signal_data: Signal) -> None:
        if not self.enabled:
            return

        message = (
            f"<b>{signal_data.direction}</b>\n"
            f"<b>Symbol:</b> {signal_data.symbol}\n"
            f"<b>Entry:</b> {signal_data.entry:.2f}\n"
            f"<b>SL:</b> {signal_data.stop_loss:.2f}\n"
            f"<b>Target:</b> {signal_data.target:.2f}\n"
            f"<b>Confidence:</b> {signal_data.confidence}\n"
            f"<b>Score:</b> {signal_data.score}\n"
            f"<b>Structure:</b> {signal_data.structure}\n"
            f"<b>Zone:</b> {signal_data.zone}\n"
            f"<b>Confluence:</b> {signal_data.confluence}"
        )

        try:
            response = requests.post(
                self.url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if response.status_code != 200:
                LOG.warning(
                    "Telegram HTTP %s: %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception as exc:
            LOG.warning("Telegram failed: %s", exc)

# ---------------------------------------------------------------------
# SCANNER ENGINE
# ---------------------------------------------------------------------

class Scanner:
    def __init__(self, config: Config):
        self.config = config
        self.client = FyersClient(config)
        self.buffers = CandleBuffer(config.max_buffer_rows)
        self.csv_logger = SignalLogger(config.log_directory)
        self.telegram = Telegram(
            config.telegram_token,
            config.telegram_chat_id,
        )
        self.last_signal: dict[str, tuple[str, float]] = {}
        self.running = True

        self.tf_minutes = parse_timeframe_minutes(config.timeframe)
        self._forming: dict[str, dict[str, Any]] = {}
        self._vol_baseline: dict[str, float] = {}
        self._htf_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._last_tick_log: dict[str, float] = {}

        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="smc-worker",
            daemon=True,
        )
        self._worker.start()

    def seed_history(self) -> None:
        bucket = floor_to_interval(now_ist().replace(tzinfo=None), self.tf_minutes)
        for symbol in self.config.symbols:
            LOG.info("Loading history: %s", symbol)
            df = self.client.history(
                symbol=symbol,
                resolution=self.config.timeframe,
                days=self.config.history_days,
            )
            if df.empty:
                LOG.warning("No history received: %s", symbol)
            else:
                df = df[df["Datetime"] < bucket]
                self.buffers.seed(symbol, df)
                LOG.info("%s seeded with %d candles", symbol, len(df))

            htf_df = self.client.history(
                symbol=symbol,
                resolution=self.config.htf_timeframe,
                days=self.config.history_days,
            )
            if not htf_df.empty:
                self._htf_cache[symbol] = (time.time(), htf_df)

    def _htf_data(self, symbol: str) -> Optional[pd.DataFrame]:
        refresh_after = max(
            30.0,
            parse_timeframe_minutes(self.config.htf_timeframe) * 30.0,
        )
        cached = self._htf_cache.get(symbol)
        now = time.time()

        if cached is not None and now - cached[0] < refresh_after:
            return cached[1]

        df = self.client.history(
            symbol=symbol,
            resolution=self.config.htf_timeframe,
            days=self.config.history_days,
        )
        if df.empty:
            return cached[1] if cached is not None else None

        self._htf_cache[symbol] = (now, df)
        return df

    def htf_aligned(self, symbol: str, direction: str) -> bool:
        df = self._htf_data(symbol)
        if df is None or len(df) < 60:
            return False

        try:
            swings = smc.swing_highs_lows(df, swing_length=self.config.swing_length)
            structure = smc.bos_choch(df, swings, close_break=self.config.close_break)
            lookback = max(self.config.structure_lookback, 10)
            bos = recent_valid_value(structure, "BOS", lookback)
            choch = recent_valid_value(structure, "CHOCH", lookback)
            wanted = 1 if direction == "LONG" else -1
            return bos == wanted or choch == wanted
        except Exception as exc:
            LOG.warning("HTF analysis failed for %s: %s", symbol, exc)
            return False

    def _htf_evaluator(self, symbol: str) -> Callable[[str], bool]:
        return lambda direction: self.htf_aligned(symbol, direction)

    def process_closed_candle(self, symbol: str) -> None:
        df = self.buffers.get(symbol)
        if df.empty:
            return

        candle_time = df["Datetime"].iloc[-1].to_pydatetime()
        if (
            self.config.enforce_market_hours
            and self.tf_minutes < 1440
            and not is_market_open(candle_time)
        ):
            LOG.debug("%s: candle outside market hours skipped", symbol)
            return

        try:
            signal_data = analyse_smc(
                symbol=symbol,
                df=df,
                config=self.config,
                htf_evaluator=self._htf_evaluator(symbol),
            )
            if signal_data is None:
                return

            if self.is_duplicate(signal_data):
                LOG.debug("Duplicate signal suppressed: %s", symbol)
                return

            self.last_signal[symbol] = (signal_data.direction, time.time())
            self.csv_logger.write(signal_data)
            self.telegram.send(signal_data)

            LOG.info(
                "SIGNAL -> %s %s | entry=%.2f sl=%.2f target=%.2f confidence=%s score=%d",
                signal_data.direction,
                signal_data.symbol,
                signal_data.entry,
                signal_data.stop_loss,
                signal_data.target,
                signal_data.confidence,
                signal_data.score,
            )
        except Exception:
            LOG.exception("Processing failed for %s", symbol)

    def is_duplicate(self, signal_data: Signal) -> bool:
        previous = self.last_signal.get(signal_data.symbol)
        if previous is None:
            return False

        previous_direction, previous_time = previous
        return (
            previous_direction == signal_data.direction
            and time.time() - previous_time < self.config.cooldown_minutes * 60
        )

    def _worker_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self.running:
                    break
                continue

            if item is None:
                break

            try:
                self.process_closed_candle(item)
            except Exception:
                LOG.exception("Worker failed processing %s", item)
            finally:
                self._queue.task_done()

    def run_rest(self) -> None:
        LOG.info("Starting REST polling mode")
        poll_seconds = max(5, min(60, self.tf_minutes * 30))
        announced_closed = False

        while self.running:
            try:
                if self.config.enforce_market_hours and not is_market_open():
                    if not announced_closed:
                        LOG.info("Market closed; idling until the next session")
                        announced_closed = True
                    time.sleep(30)
                    continue

                if announced_closed:
                    LOG.info("Market open; resuming polling")
                    announced_closed = False

                bucket = floor_to_interval(
                    now_ist().replace(tzinfo=None), self.tf_minutes
                )

                for symbol in self.config.symbols:
                    df = self.client.history(
                        symbol=symbol,
                        resolution=self.config.timeframe,
                        days=self.config.history_days,
                    )
                    if df.empty:
                        continue

                    last_dt = self.buffers.last_datetime(symbol)
                    mask = df["Datetime"] < bucket
                    if last_dt is not None:
                        mask &= df["Datetime"] > last_dt

                    for _, row in df[mask].iterrows():
                        if self.buffers.add_closed_candle(symbol, row.to_dict()):
                            self._queue.put(symbol)

                time.sleep(poll_seconds)
            except Exception:
                LOG.exception("REST loop error")
                time.sleep(poll_seconds)

    @staticmethod
    def _parse_timestamp(raw: Any) -> float:
        if raw is None:
            return time.time()

        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
        else:
            text = str(raw).strip()
            try:
                value = float(text)
            except ValueError:
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%d/%m/%Y %H:%M:%S",
                    "%H:%M:%S",
                ):
                    try:
                        parsed = datetime.strptime(text, fmt)
                    except ValueError:
                        continue
                    if fmt == "%H:%M:%S":
                        parsed = datetime.combine(now_ist().date(), parsed.time())
                    return parsed.replace(tzinfo=IST).timestamp()

                return time.time()

        if value > 1e15:
            value /= 1e9
        elif value > 1e12:
            value /= 1e3
        return value

    @staticmethod
    def extract_tick(message: Any) -> Optional[dict[str, Any]]:
        if not isinstance(message, dict):
            return None

        symbol = (
            message.get("symbol")
            or message.get("symbol_name")
            or message.get("n")
        )
        price_raw = (
            message.get("ltp")
            or message.get("lp")
            or message.get("last_traded_price")
            or message.get("close")
        )

        if not symbol or price_raw is None:
            return None

        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            return None

        if price <= 0:
            return None

        raw_ts = (
            message.get("timestamp")
            or message.get("feed_time")
            or message.get("exch_feed_time")
            or message.get("last_traded_time")
        )
        timestamp = Scanner._parse_timestamp(raw_ts)

        try:
            volume = float(
                message.get("vol_traded_today")
                or message.get("volume")
                or message.get("v")
                or 0.0
            )
        except (TypeError, ValueError):
            volume = 0.0

        return {
            "symbol": symbol,
            "price": price,
            "timestamp": timestamp,
            "volume": volume,
        }

    def build_candle_from_tick(
        self,
        tick: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        symbol = tick["symbol"]
        dt = floor_to_interval(
            datetime.fromtimestamp(tick["timestamp"], tz=IST).replace(tzinfo=None),
            self.tf_minutes,
        )

        current = self._forming.get(symbol)
        cum_volume = tick["volume"]

        def new_forming() -> dict[str, Any]:
            self._vol_baseline[symbol] = cum_volume
            return {
                "Datetime": dt,
                "open": tick["price"],
                "high": tick["price"],
                "low": tick["price"],
                "close": tick["price"],
                "volume": 0.0,
            }

        if current is None:
            self._forming[symbol] = new_forming()
            return symbol, {}

        if current["Datetime"] == dt:
            current["high"] = max(current["high"], tick["price"])
            current["low"] = min(current["low"], tick["price"])
            current["close"] = tick["price"]
            base = self._vol_baseline.get(symbol, cum_volume)
            current["volume"] = max(0.0, cum_volume - base)
            return symbol, {}

        if current["Datetime"] < dt:
            closed = dict(current)
            self._forming[symbol] = new_forming()
            return symbol, closed

        return symbol, {}

    def on_message(self, message: Any) -> None:
        ticks: list[dict[str, Any]] = []

        if isinstance(message, list):
            for item in message:
                t = self.extract_tick(item)
                if t:
                    ticks.append(t)
        elif isinstance(message, dict):
            if message.get("s") == "error":
                LOG.error("WebSocket payload error: %s", message)
                return
            t = self.extract_tick(message)
            if t:
                ticks.append(t)

        now = time.time()
        for tick in ticks:
            symbol = tick["symbol"]

            # Log periodic heartbeat every 15s to confirm live ticks without flooding stdout
            last_log = self._last_tick_log.get(symbol, 0.0)
            if now - last_log >= 15.0:
                LOG.info("Tick update: %s @ %.2f", symbol, tick["price"])
                self._last_tick_log[symbol] = now

            sym, closed_candle = self.build_candle_from_tick(tick)
            if closed_candle:
                LOG.info(
                    "Candle completed for %s [%s] -> O: %.2f H: %.2f L: %.2f C: %.2f",
                    sym,
                    closed_candle["Datetime"].strftime("%H:%M:%S"),
                    closed_candle["open"],
                    closed_candle["high"],
                    closed_candle["low"],
                    closed_candle["close"],
                )
                if self.buffers.add_closed_candle(sym, closed_candle):
                    self._queue.put(sym)

    def on_error(self, message: Any) -> None:
        LOG.error("WebSocket error: %s", message)

    def on_close(self, message: Any) -> None:
        LOG.warning("WebSocket closed: %s", message)

    def on_open(self) -> None:
        LOG.info(
            "WebSocket connected; subscribing to %d symbols",
            len(self.config.symbols),
        )
        self.ws.subscribe(
            symbols=self.config.symbols,
            data_type="SymbolUpdate",
        )
        # Note: Do not call self.ws.keep_running() here as it deadlocks the socket thread.

    def run_websocket(self) -> None:
        if data_ws is None:
            LOG.warning("fyers-apiv3 not installed; falling back to REST mode")
            self.run_rest()
            return

        LOG.info("Starting WebSocket mode")
        self.ws = data_ws.FyersDataSocket(
            access_token=f"{self.config.app_id}:{self.config.access_token}",
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=self.on_open,
            on_close=self.on_close,
            on_error=self.on_error,
            on_message=self.on_message,
        )
        self.ws.connect()

        while self.running:
            time.sleep(1)

        LOG.info("WebSocket mode stopped")

    def stop(self) -> None:
        self.running = False
        self._queue.put(None)
        try:
            if hasattr(self, "ws"):
                self.ws.close()
        except Exception:
            pass
        if self._worker.is_alive():
            self._worker.join(timeout=10)

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--rest",
        action="store_true",
        help="Use REST polling instead of WebSocket",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.debug)

    scanner = Scanner(config)
    scanner.seed_history()

    def shutdown_handler(signum: int, frame: Any) -> None:
        LOG.info("Shutdown requested")
        scanner.stop()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    if args.rest or not config.use_websocket:
        scanner.run_rest()
    elif data_ws is None:
        LOG.warning("fyers-apiv3 (WebSocket) not installed; using REST mode")
        scanner.run_rest()
    else:
        scanner.run_websocket()


if __name__ == "__main__":
    main()