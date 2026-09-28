# research_agent_master.py
"""
NARESH QUANT RESEARCH — Master Research Agent (Unified)
=======================================================
Combines:
  • Multi-strategy multiprocessing sweep (ultimate series)
  • Walk-Forward Analysis with overfitting detection (wfa series)
  • Hypothesis-driven research (original research_agent.py)
  • Pluggable backtester (EnhancedBacktester / HighWinRateBacktester)
  • Rich UI + PNG export + Telegram notifications
  • Smart data loading: CSV cache / Feather / Options CSV / Fyers API

Modes:
  OPTIONS_1M | FEATHER | CSV_API | WALK_FORWARD | HYPOTHESIS
"""

from __future__ import annotations

import ast
import itertools
import logging
import os
import re
import shutil
import traceback
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, Callable

import matplotlib.pyplot as plt
import pandas as pd
import requests
import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn,
)
from rich.prompt import Confirm, IntPrompt
from rich.table import Table

# --- Project imports (try multiple backtester/strategy modules) ---
try:
    from backtester_enhanced import EnhancedBacktester
    _DEFAULT_BACKTESTER = EnhancedBacktester
except Exception:  # pragma: no cover
    _DEFAULT_BACKTESTER = None

try:
    from run_backtest_optimized1 import HighWinRateBacktester
except Exception:
    HighWinRateBacktester = None

from data_fetcher import DataFetcher
from fyers_auth import FyersAuth

# Strategy module — prefer the latest, fall back gracefully
try:
    from strategy4 import Strategy as _StrategyV4
    _Strategy = _StrategyV4
except Exception:
    try:
        from strategycopy3 import Strategy as _StrategyV3
        _Strategy = _StrategyV3
    except Exception:
        try:
            from strategy_copy1 import Strategy as _StrategyV1
            _Strategy = _StrategyV1
        except Exception:
            try:
                from strategy import Strategy as _StrategyV0
                _Strategy = _StrategyV0
            except Exception:
                _Strategy = None

warnings.filterwarnings("ignore")

# =========================================================
# CONSTANTS
# =========================================================
INITIAL_CAPITAL = 100_000
WORKER_TIMEOUT_SEC = 900
TOP_N_REPORT = 15
TOP_N_WFA = 20

PARAM_GRID = {
    "sl_mult": [1.0, 1.5, 2.0],
    "tp_mult": [1.0, 2.0, 3.0, 5.0, 10.0],
}

WFA_PARAM_GRID = {
    "sl_mult": [1.0, 1.5, 2.0],
    "tp_mult": [1.5, 2.0, 3.0],
}

EMA_PARAM_GRID = {
    "fast_ema": [8, 9, 13, 21],
    "slow_ema": [21, 34, 55, 89],
}

STRATEGIES_TO_TEST = [
    "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
    "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze",
    "vwap_bounce", "macd_crossover", "supertrend_only", "smc",
    "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam",
    "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break",
    "nk_notes",
]

EMA_STRATEGIES = {
    "ema_crossover", "smart_ema", "ema_bounce", "macd_crossover", "ema_convergence",
}

# Dark-theme palette
DARK_BG      = "#121212"
HEADER_BG    = "#1f2937"
ROW_BG_1     = "#1a1d24"
ROW_BG_2     = "#111318"
GRID_COLOR   = "#374151"
TEXT_LIGHT   = "#e5e7eb"
ACCENT_CYAN  = "#38bdf8"
ACCENT_GREEN = "#4ade80"
ACCENT_RED   = "#f87171"
ACCENT_YELLOW= "#facc15"

# =========================================================
# LOGGING & CONSOLE
# =========================================================
log_filename = f"research_{datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    handlers=[logging.FileHandler(log_filename, delay=True)],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
console = Console(record=True)

# =========================================================
# DATACLASSES
# =========================================================
@dataclass
class ResearchResult:
    """Container for a single research experiment."""
    best_config: Dict[str, Any]
    backtest_report: Dict[str, Any]
    trials: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class WFAResult:
    """Container for walk-forward analysis."""
    strategy: str
    params: Dict[str, Any]
    train_trades: int
    train_pf: float
    train_pnl: float
    test_trades: int
    test_pf: float
    test_pnl: float
    verdict: str  # "✅ Robust" | "⚠️ Degraded" | "❌ Overfit"


# =========================================================
# HELPERS
# =========================================================
def load_config() -> dict:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config


def parse_params(params: Any) -> dict:
    if isinstance(params, str):
        try:
            return ast.literal_eval(params)
        except Exception:
            return {}
    return params or {}


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime" not in df.columns:
        for col in ("date", "time", "timestamp"):
            if col in df.columns:
                df = df.rename(columns={col: "datetime"})
                break
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    df.columns = [c.lower() for c in df.columns]
    if "volume" not in df.columns:
        df["volume"] = 0
    return df


def resample_ohlc(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    if str(tf) == "1":
        return df
    return df.resample(f"{tf}min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()


def clean_flat_candles(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Remove illiquid flat candles (high==low & volume==0)."""
    if "volume" in df.columns and "high" in df.columns and "low" in df.columns:
        flat = (df["volume"] == 0) & (df["high"] == df["low"])
        removed = int(flat.sum())
        if removed:
            df = df[~flat].copy()
        return df, removed
    return df, 0


def get_strategy_class():
    if _Strategy is None:
        raise ImportError("No Strategy class found. Install strategy4 / strategycopy3 / strategy_copy1 / strategy.")
    return _Strategy


def get_backtester_class(name: str = "enhanced"):
    """Return the requested backtester class."""
    name = (name or "enhanced").lower()
    if name == "highwinrate" and HighWinRateBacktester is not None:
        return HighWinRateBacktester
    if _DEFAULT_BACKTESTER is None:
        raise ImportError("EnhancedBacktester not available.")
    return _DEFAULT_BACKTESTER


# =========================================================
# STRATEGY FACTORY + HYPOTHESIS INFERENCE
# =========================================================
def infer_strategy_type(hypothesis: str) -> str:
    """Keyword-based hypothesis → strategy mapping (LLM-ready stub)."""
    h = (hypothesis or "").lower()
    if "mean" in h and "reversion" in h:
        return "rsi_reversal"
    if "trend" in h and "breakout" in h:
        return "ema_crossover"
    if "smc" in h or "order block" in h or "imbalance" in h:
        return "smc"
    if "vwap" in h:
        return "vwap_bounce"
    if "bollinger" in h or "squeeze" in h:
        return "bollinger_squeeze"
    if "supertrend" in h:
        return "supertrend_only"
    return "ema_crossover"  # fallback


def get_param_space(strategy_type: str) -> Dict[str, list]:
    spaces = {
        "ema_crossover":  {"fast_ema": [5, 9, 12], "slow_ema": [21, 26, 50]},
        "rsi_reversal":   {"oversold": [25, 30, 35], "overbought": [65, 70, 75]},
        "composite_smc":  {"min_confirmations": [2, 3], "trend_filter": [True, False]},
        "vwap_bounce":    {"vwap_band": [0.5, 1.0, 1.5]},
        "bollinger_squeeze": {"bb_period": [20, 30], "bb_std": [1.5, 2.0, 2.5]},
        "supertrend_only": {"st_period": [7, 10, 14], "st_mult": [2.0, 3.0, 4.0]},
        "smart_ema":      {"fast_ema": [8, 9, 13], "slow_ema": [21, 34, 55]},
    }
    return spaces.get(strategy_type, {})


# =========================================================
# MULTIPROCESSING WORKERS
# =========================================================
def run_strategy_batch(args):
    """Standard sweep worker."""
    df_signals, symbol, strategy_name, combos, min_trades, backtester_name = args
    BT = get_backtester_class(backtester_name)
    backtester = BT()
    results: List[Dict[str, Any]] = []

    for params in combos:
        try:
            kw = dict(
                df=df_signals.copy(), symbol=symbol, strategy_name=strategy_name,
                params=params, initial_capital=INITIAL_CAPITAL,
                sl_multiplier=params["sl_mult"], tp_multiplier=params["tp_mult"],
                use_atr_sl=True, use_time_filter=True, use_trend_filter=True,
            )
            # HighWinRateBacktester doesn't accept strategy_name; call defensively
            if BT is HighWinRateBacktester:
                kw.pop("strategy_name", None)
                kw.pop("use_trend_filter", None)
            result = backtester.run_backtest(**kw)

            if result and result.get("total_trades", 0) > min_trades:
                result["strategy"] = strategy_name
                result["params_tested"] = params
                results.append(result)
        except Exception as e:
            logging.error(
                f"Error in {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}"
            )
    return results


def run_strategy_batch_wfa(args):
    """Walk-forward worker: runs on Train + Test sets."""
    df_signals, symbol, strategy_name, combos, min_trades, train_ratio, backtester_name = args
    BT = get_backtester_class(backtester_name)
    backtester = BT()
    results: List[Dict[str, Any]] = []

    split_idx = int(len(df_signals) * train_ratio)
    df_train = df_signals.iloc[:split_idx].copy()
    df_test  = df_signals.iloc[split_idx:].copy()

    for params in combos:
        try:
            kw_train = dict(
                df=df_train.copy(), symbol=symbol, strategy_name=strategy_name,
                params=params, sl_multiplier=params["sl_mult"],
                tp_multiplier=params["tp_mult"],
                use_atr_sl=True, use_time_filter=True, use_trend_filter=True,
            )
            if BT is HighWinRateBacktester:
                kw_train.pop("strategy_name", None)
                kw_train.pop("use_trend_filter", None)
            res_train = backtester.run_backtest(**kw_train)

            if not (res_train and res_train.get("total_trades", 0) > min_trades):
                continue

            kw_test = kw_train.copy()
            kw_test["df"] = df_test.copy()
            res_test = backtester.run_backtest(**kw_test)

            test_trades = res_test.get("total_trades", 0) if res_test else 0
            test_pf = res_test.get("profit_factor", 0.0) if res_test else 0.0
            test_pnl = res_test.get("total_pnl", 0.0) if res_test else 0.0

            verdict = "❌ Overfit"
            if test_trades >= 5:
                if test_pf >= 1.0 and test_pnl > 0:
                    verdict = "✅ Robust"
                elif test_pf >= 0.8 and test_pnl > -50:
                    verdict = "⚠️ Degraded"

            results.append({
                "strategy": strategy_name,
                "params_tested": params,
                "train_trades": res_train.get("total_trades", 0),
                "train_pf":      res_train.get("profit_factor", 0.0),
                "train_pnl":     res_train.get("total_pnl", 0.0),
                "test_trades":   test_trades,
                "test_pf":       test_pf,
                "test_pnl":      test_pnl,
                "verdict":       verdict,
            })
        except Exception as e:
            logging.error(
                f"WFA error {strategy_name} | Params: {params} | {e}\n{traceback.format_exc()}"
            )
    return results


# =========================================================
# MASTER RESEARCH AGENT
# =========================================================
class MasterResearchAgent:
    def __init__(self, config: dict, backtester_name: str = "enhanced"):
        self.config = config
        self.backtester_name = backtester_name

        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        self.data_fetcher = DataFetcher(auth.fyers)

        self.results: List[Dict[str, Any]] = []
        self.wfa_results: List[Dict[str, Any]] = []

        StratCls = get_strategy_class()
        self.strategies: Dict[str, Any] = {}
        for name in STRATEGIES_TO_TEST:
            try:
                self.strategies[name] = StratCls(name)
            except Exception as e:
                console.print(f"[red]Failed to initialize {name}: {e}[/red]")

        self._archive_previous_results()

    # ---------- Setup ----------
    def _archive_previous_results(self) -> None:
        partial_files = [
            f for f in os.listdir(".")
            if f.startswith("results_partial_") and f.endswith(".csv")
        ]
        if not partial_files:
            return
        archive_folder = f"report_{datetime.now():%Y-%m-%d}"
        os.makedirs(archive_folder, exist_ok=True)
        console.print(f"[yellow]Found {len(partial_files)} old checkpoint(s). "
                      f"Moving to ./{archive_folder}/[/yellow]")
        for file in partial_files:
            try:
                dest = os.path.join(archive_folder, file)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(file, archive_folder)
            except Exception as e:
                console.print(f"[red]Failed to move {file}: {e}[/red]")

    # ---------- Data Loading ----------
    def _load_data(self, symbol: str, timeframe: str, days: int) -> Optional[pd.DataFrame]:
        tf_int = int(timeframe)
        if tf_int <= 3:    days = min(days, 60)
        elif tf_int <= 10: days = min(days, 120)

        safe_symbol = symbol.replace(":", "_")
        pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")

        matching_files: List[str] = []
        for root, _, files in os.walk("./data"):
            for file in files:
                if pattern.match(file):
                    matching_files.append(os.path.join(root, file))

        if matching_files:
            def get_days(fp: str) -> int:
                m = pattern.search(os.path.basename(fp))
                return int(m.group(1)) if m else 0

            matching_files.sort(key=get_days, reverse=True)
            console.print("[bold yellow]Found multiple cached files. Select one:[/bold yellow]")
            for i, f in enumerate(matching_files):
                console.print(f"  [{i+1}] {os.path.basename(f)} ({get_days(f)} days)")
            choice = IntPrompt.ask(
                "Enter number", default=1,
                choices=[str(i+1) for i in range(len(matching_files))],
            )
            cache_file = matching_files[choice - 1]
            console.print(f"[bold green]✓ Loaded: {cache_file}[/bold green]")
            try:
                return pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")

        console.print(f"[yellow]No local cache. Fetching {days} days from API...[/yellow]")
        return self.data_fetcher.get_historical_data(symbol, timeframe, days)

    def _load_options_csv(self, options_folder: str, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Scan options folder for matching CSV (from research_agent_wfa copy.py)."""
        console.print(f"[cyan]Scanning for options data in: {options_folder}...[/cyan]")
        safe_symbol = symbol.replace("NSE:", "").replace(":", "_")
        target_file = None

        if os.path.exists(options_folder):
            files = [f for f in os.listdir(options_folder) if f.endswith('.csv')]
            matching_files = [f for f in files if safe_symbol in f]
            if matching_files:
                tf_matches = [f for f in matching_files if f"{timeframe}m" in f]
                target_file = os.path.join(options_folder,
                                           (tf_matches or matching_files)[0])
            elif files:
                console.print(f"[yellow]Symbol {safe_symbol} not found. "
                              f"Using first available file.[/yellow]")
                target_file = os.path.join(options_folder, files[0])

        if not (target_file and os.path.exists(target_file)):
            return None

        console.print(f"[bold green]✓ Found options data: {os.path.basename(target_file)}[/bold green]")
        try:
            df = pd.read_csv(target_file, parse_dates=["datetime"])
            df, removed = clean_flat_candles(df)
            if removed:
                console.print(f"[dim]Cleaning data: Removed {removed} flat/illiquid candles.[/dim]")
            df.set_index("datetime", inplace=True)
            return df
        except Exception as e:
            console.print(f"[red]Error reading CSV: {e}[/red]")
            return None

    # ---------- Public Sweep APIs ----------
    def run_full_sweep(self, symbol: str, timeframe: str, days: int = 100) -> None:
        self._print_sweep_header(symbol, timeframe)
        df = self._load_data(symbol, timeframe, days)
        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
        self._execute_sweep(df, symbol, timeframe)

    def run_feather_sweep(self, feather_path: str, symbol: str = "SENSEX",
                          timeframes: List[str] = ("1", "5", "15", "60")) -> None:
        console.print(f"[cyan]Loading feather data from {feather_path}...[/cyan]")
        try:
            df = pd.read_feather(feather_path)
        except Exception as e:
            console.print(f"[red]Error reading feather: {e}[/red]")
            return
        df = normalize_dataframe(df)
        for tf in timeframes:
            self._print_sweep_header(symbol, tf)
            self._execute_sweep(resample_ohlc(df, tf), symbol, tf)

    def run_walk_forward(self, symbol: str, timeframe: str,
                         days: int = 180, train_ratio: float = 0.7,
                         options_folder: Optional[str] = None) -> None:
        """WFA sweep — tries options folder first, falls back to cache/API."""
        self._print_sweep_header(symbol, timeframe, mode="WFA")
        df = None
        if options_folder:
            df = self._load_options_csv(options_folder, symbol, timeframe)
        if df is None:
            df = self._load_data(symbol, timeframe, days)
        if df is None or df.empty:
            console.print(f"[red]No data available for WFA. Skipping.[/red]")
            return
        self._execute_wfa_sweep(df, symbol, timeframe, train_ratio)

    def run_hypothesis_research(self, hypothesis: str, symbol: str,
                                lookback_days: int, target_metric: str = "sharpe") -> ResearchResult:
        """Hypothesis-driven single-strategy research (from original research_agent.py)."""
        strategy_type = infer_strategy_type(hypothesis)
        param_space = get_param_space(strategy_type)
        trials: List[Dict[str, Any]] = []
        best_metric = -float("inf")
        best_config: Optional[Dict[str, Any]] = None

        console.print(f"[cyan]Hypothesis: '{hypothesis}'[/cyan]")
        console.print(f"[cyan]Inferred strategy: {strategy_type} | "
                      f"Param space: {param_space or PARAM_GRID}[/cyan]")

        df = self._load_data(symbol, "15", lookback_days)
        if df is None or df.empty:
            console.print("[red]No data for hypothesis research.[/red]")
            return ResearchResult(best_config={}, backtest_report={}, trials=[])

        param_iter = list(self._generate_params(param_space)) or list(self._generate_params(PARAM_GRID))
        BT = get_backtester_class(self.backtester_name)
        backtester = BT()

        for params in param_iter:
            config = {"symbol": symbol, "strategy": strategy_type, "params": params}
            try:
                kw = dict(
                    df=df.copy(), symbol=symbol, strategy_name=strategy_type,
                    params=params, initial_capital=INITIAL_CAPITAL,
                    sl_multiplier=params.get("sl_mult", 1.5),
                    tp_multiplier=params.get("tp_mult", 3.0),
                    use_atr_sl=True, use_time_filter=True, use_trend_filter=True,
                )
                if BT is HighWinRateBacktester:
                    kw.pop("strategy_name", None)
                    kw.pop("use_trend_filter", None)
                report = backtester.run_backtest(**kw)
                metric = (report or {}).get("metrics", {}).get(target_metric, 0) \
                         if isinstance((report or {}).get("metrics"), dict) \
                         else (report or {}).get("profit_factor", 0)
                trials.append({"config": config, "metric": metric, "report": report})
                if metric > best_metric:
                    best_metric = metric
                    best_config = config
            except Exception as e:
                logging.error(f"Hypothesis trial failed: {e}\n{traceback.format_exc()}")

        return ResearchResult(
            best_config=best_config or {},
            backtest_report=trials[-1]["report"] if trials else {},
            trials=trials,
        )

    def _generate_params(self, param_space: Dict[str, list]):
        keys = list(param_space.keys())
        values = [param_space[k] for k in keys]
        for combo in itertools.product(*values):
            yield dict(zip(keys, combo))

    # ---------- Core Sweep Engines ----------
    def _execute_sweep(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        start_time = datetime.now()
        console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")

        combinations = [dict(zip(PARAM_GRID.keys(), c))
                        for c in itertools.product(*PARAM_GRID.values())]
        min_trades = max(5, len(df) // 1000)
        console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")

        tasks: List[tuple] = []
        for name, strat in self.strategies.items():
            console.print(f"[dim]  -> Pre-calculating [bold]{name}[/bold]...[/dim]", end=" ")
            try:
                df_signals = strat.generate_signals(df.copy())
                if "atr" not in df_signals.columns:
                    df_signals["atr"] = strat.calculate_atr(df_signals)
                df_signals.dropna(subset=["close", "atr"], inplace=True)
                tasks.append((df_signals, symbol, name, combinations, min_trades,
                              self.backtester_name))
                console.print("[green]Done[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        console.print(f"\n[yellow]Running {len(tasks)} strategy batches via Multicore CPU...[/yellow]\n")
        self._run_workers(tasks, timeframe, wfa=False)

        if self.results:
            safe_sym = symbol.replace(":", "_")
            checkpoint = f"results_partial_{safe_sym}_{timeframe}m.csv"
            pd.DataFrame(self.results).to_csv(checkpoint, index=False)
            console.print(f"[dim]Saved checkpoint: {checkpoint}[/dim]")

        self._print_elapsed(start_time)

    def _execute_wfa_sweep(self, df: pd.DataFrame, symbol: str,
                           timeframe: str, train_ratio: float) -> None:
        start_time = datetime.now()
        console.print(f"[cyan]Loaded {len(df)} candles. "
                      f"Train/Test Split: {int(train_ratio*100)}/{int((1-train_ratio)*100)}[/cyan]")

        sltp_combos = [dict(zip(WFA_PARAM_GRID.keys(), c))
                       for c in itertools.product(*WFA_PARAM_GRID.values())]
        ema_combos = [dict(zip(EMA_PARAM_GRID.keys(), c))
                      for c in itertools.product(*EMA_PARAM_GRID.values())
                      if c[0] < c[1]]

        min_trades = max(10, len(df) // 1500)
        console.print(f"[dim]Minimum trades required in Train set: {min_trades}[/dim]")

        tasks: List[tuple] = []
        for name, strat in self.strategies.items():
            try:
                if name in EMA_STRATEGIES:
                    for ema_combo in ema_combos:
                        strat.params['fast_ema'] = ema_combo['fast_ema']
                        strat.params['slow_ema'] = ema_combo['slow_ema']
                        df_signals = strat.generate_signals(df.copy())
                        df_signals['atr'] = strat.calculate_atr(df_signals)
                        task_combos = [{**ema_combo, **sltp} for sltp in sltp_combos]
                        tasks.append((df_signals, symbol, name, task_combos,
                                      min_trades, train_ratio, self.backtester_name))
                else:
                    strat.params.pop('fast_ema', None)
                    strat.params.pop('slow_ema', None)
                    df_signals = strat.generate_signals(df.copy())
                    df_signals['atr'] = strat.calculate_atr(df_signals)
                    tasks.append((df_signals, symbol, name, sltp_combos,
                                  min_trades, train_ratio, self.backtester_name))
            except Exception as e:
                console.print(f"[red]Error preparing {name}: {e}[/red]")

        console.print(f"\n[yellow]Running {len(tasks)} WFA batches via Multicore CPU...[/yellow]\n")
        self._run_workers(tasks, timeframe, wfa=True)

        if self.wfa_results:
            output_dir = datetime.now().strftime("%Y-%m-%d_WFA")
            os.makedirs(output_dir, exist_ok=True)
            file_path = os.path.join(output_dir, f"results_wfa_{timeframe}m.csv")
            pd.DataFrame(self.wfa_results).to_csv(file_path, index=False)
            console.print(f"[dim]Saved WFA checkpoint to {file_path}[/dim]")

        self._print_elapsed(start_time)

    def _run_workers(self, tasks: List[tuple], timeframe: str, wfa: bool) -> None:
        max_workers = max(1, (os.cpu_count() or 2) - 1)
        worker_fn = run_strategy_batch_wfa if wfa else run_strategy_batch
        label = "Backtesting WFA..." if wfa else "Backtesting..."

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(f"[cyan]{label}", total=len(tasks))
            try:
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(worker_fn, t) for t in tasks]
                    for future in as_completed(futures):
                        try:
                            batch = future.result(timeout=WORKER_TIMEOUT_SEC)
                            if batch:
                                for res in batch:
                                    res["timeframe"] = timeframe
                                    (self.wfa_results if wfa else self.results).append(res)
                        except Exception as e:
                            console.print(f"[red]Worker failed: {e}[/red]")
                        progress.advance(task_id)
            except BrokenProcessPool:
                console.print("[bold red]Process pool crashed. Reduce memory or worker count.[/bold red]")

    # ---------- Reporting ----------
    def generate_report(self) -> None:
        if self.wfa_results:
            self._generate_wfa_report()
        else:
            self._generate_regular_report()

    def _generate_regular_report(self) -> None:
        if not self.results:
            console.print("[bold red]No trades were taken by ANY strategy.[/bold red]")
            return
        valid = [r for r in self.results if "profit_factor" in r]
        if not valid:
            console.print("[bold red]No valid backtest results found.[/bold red]")
            return

        df = pd.DataFrame(valid)
        df["params_str"] = df["params_tested"].astype(str)
        df = df.drop_duplicates(subset=["strategy", "timeframe", "params_str"])
        df = df.drop(columns=["params_str"])

        profitable = df[df["profit_factor"] > 1.1].copy()
        if profitable.empty:
            console.print("[bold red]No profitable setups found across all strategies![/bold red]")
            df = df.sort_values(by="profit_factor", ascending=False).head(TOP_N_REPORT)
        else:
            df = profitable.sort_values(
                by=["profit_factor", "total_pnl"], ascending=[False, False]
            ).head(TOP_N_REPORT)

        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT REPORT\nTop Performing Strategies[/bold white]",
            title="🤖 NARESH QUANT RESEARCH", box=box.DOUBLE,
        ))
        self._print_results_table(df)

        best = df.iloc[0]
        best_params = parse_params(best.get("params_tested", {}))
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml:\n\n"
            f"  strategy: \"{best.get('strategy')}\"\n"
            f"  resolution: \"{best.get('timeframe')}\"\n"
            f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best_params.get('tp_mult')}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED,
        ))

        try:
            img = self._export_report_image(df)
            if img:
                self._send_telegram_image(img)
        except Exception as e:
            console.print(f"[yellow]Could not generate/send report image: {e}[/yellow]")

    def _generate_wfa_report(self) -> None:
        if not self.wfa_results:
            console.print("[bold red]No WFA results.[/bold red]")
            return
        df = pd.DataFrame(self.wfa_results)
        df = df[df["train_pf"] > 1.1].copy()
        if df.empty:
            console.print("[bold red]No profitable setups in Train set![/bold red]")
            return
        df = df.sort_values(by=["test_pf", "test_pnl"], ascending=[False, False]).head(TOP_N_WFA)

        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT: WALK-FORWARD ANALYSIS\nTrain vs Unseen Test Data[/bold white]",
            title="🤖 NARESH QUANT RESEARCH", box=box.DOUBLE,
        ))
        self._print_wfa_table(df)

        robust = df[df["verdict"] == "✅ Robust"]
        if not robust.empty:
            best = robust.iloc[0]
            bp = parse_params(best["params_tested"])
            fast = bp.get("fast_ema", ""); slow = bp.get("slow_ema", "")
            ema_str = f"\n  fast_ema: {fast}\n  slow_ema: {slow}" if fast != "" else ""
            console.print(Panel(
                f"[bold green]ACTION ITEM:[/bold green] This strategy survived Unseen Data!\n\n"
                f"  strategy: \"{best.get('strategy')}\"\n"
                f"  resolution: \"{best.get('timeframe')}\"{ema_str}\n"
                f"  sl_atr_multiplier: {bp.get('sl_mult')}\n"
                f"  tp_atr_multiplier: {bp.get('tp_mult')}\n\n"
                f"[dim]Train PF: {best['train_pf']:.2f} | Test PF: {best['test_pf']:.2f}[/dim]",
                title="🚀 BEST ROBUST SETUP FOUND", box=box.ROUNDED,
            ))
        else:
            console.print(Panel(
                "[bold red]WARNING:[/bold red] No strategies passed the Robustness check (✅ Robust).\n"
                "All strategies degraded or failed on unseen data.",
                title="⚠️ OVERFITTING DETECTED", box=box.ROUNDED,
            ))

        try:
            img = self._export_wfa_image(df)
            if img:
                self._send_telegram_image(img, caption="🤖 NARESH QUANT — WFA Robustness Report")
        except Exception as e:
            console.print(f"[yellow]Could not generate/send WFA image: {e}[/yellow]")

    # ---------- Tables ----------
    def _print_results_table(self, df: pd.DataFrame) -> None:
        table = Table(box=box.HEAVY, show_lines=True,
                      title="Best Setups Found", title_style="bold cyan")
        for col, kw in [
            ("Rank",    dict(style="bold", justify="center", width=5)),
            ("Strategy",dict(style="bold cyan", min_width=20)),
            ("TF",      dict(justify="center", width=4)),
            ("Params (SL / TP)", dict(style="dim white", min_width=20)),
            ("Trades",  dict(justify="center", width=7)),
            ("Win %",   dict(justify="right", width=8)),
            ("PF",      dict(justify="right", width=6)),
            ("Max DD",  dict(justify="right", width=8)),
            ("Net P&L", dict(justify="right", width=11)),
        ]:
            table.add_column(col, **kw)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            p = parse_params(row.get("params_tested", {}))
            pf = min(row["profit_factor"], 999.0) if pd.notna(row["profit_factor"]) else 0.0
            pf_str = f"[bold green]{pf:.2f}[/bold green]" if pf > 1.2 else f"{pf:.2f}"
            pnl = row.get("total_pnl", 0)
            pnl_str = f"[green]+{pnl:,.0f}[/green]" if pnl > 0 else f"[red]{pnl:,.0f}[/red]"
            wr = row.get("win_rate", 0)
            wr_str = f"{wr:.2f}%" if pd.notna(wr) else "N/A"
            table.add_row(
                f"{rank}",
                str(row.get("strategy", "Unknown")),
                str(row.get("timeframe", "")),
                f"SL: {p.get('sl_mult','N/A')}x / TP: {p.get('tp_mult','N/A')}x",
                f"{row.get('total_trades', 0)}",
                wr_str, pf_str,
                f"{row.get('max_drawdown_pct', 0):.2f}%",
                pnl_str,
            )
        console.print(table)

    def _print_wfa_table(self, df: pd.DataFrame) -> None:
        table = Table(box=box.HEAVY, show_lines=True,
                      title="Robustness Report", title_style="bold cyan")
        for col, kw in [
            ("Rank",       dict(style="bold", justify="center", width=5)),
            ("Strategy",   dict(style="bold cyan", min_width=20)),
            ("TF",         dict(justify="center", width=4)),
            ("Params",     dict(style="dim white", min_width=25)),
            ("Tr Trades",  dict(justify="center", width=9)),
            ("Tr PF",      dict(justify="right", width=7)),
            ("Tr P&L",     dict(justify="right", width=10)),
            ("Te Trades",  dict(justify="center", width=9)),
            ("Te PF",      dict(justify="right", width=7)),
            ("Te P&L",     dict(justify="right", width=10)),
            ("Verdict",    dict(justify="center", width=12)),
        ]:
            table.add_column(col, **kw)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            p = parse_params(row["params_tested"])
            fast = p.get("fast_ema", ""); slow = p.get("slow_ema", "")
            ema_str = f"EMA: {fast}/{slow} | " if fast != "" else ""
            params_str = f"{ema_str}SL: {p.get('sl_mult','N/A')}x / TP: {p.get('tp_mult','N/A')}x"
            tr_pf = row["train_pf"]; te_pf = row["test_pf"]
            tr_pf_str = f"[green]{tr_pf:.2f}[/green]" if tr_pf > 1.2 else f"{tr_pf:.2f}"
            te_pf_str = (f"[green]{te_pf:.2f}[/green]" if te_pf > 1.2 else
                         f"[red]{te_pf:.2f}[/red]" if te_pf < 1.0 else f"{te_pf:.2f}")
            tr_pnl = row["train_pnl"]; te_pnl = row["test_pnl"]
            tr_pnl_str = f"[green]+{tr_pnl:,.0f}[/green]" if tr_pnl > 0 else f"[red]{tr_pnl:,.0f}[/red]"
            te_pnl_str = f"[green]+{te_pnl:,.0f}[/green]" if te_pnl > 0 else f"[red]{te_pnl:,.0f}[/red]"
            v = row["verdict"]
            v_color = "green" if "Robust" in v else "yellow" if "Degraded" in v else "red"
            table.add_row(
                f"{rank}", str(row.get("strategy","Unknown")), str(row.get("timeframe","")),
                params_str,
                f"{row.get('train_trades',0)}", tr_pf_str, tr_pnl_str,
                f"{row.get('test_trades',0)}", te_pf_str, te_pnl_str,
                f"[{v_color}]{v}[/{v_color}]",
            )
        console.print(table)

    # ---------- PNG export ----------
    def _export_report_image(self, df_results: pd.DataFrame) -> Optional[str]:
        img_data = []
        for i, (_, row) in enumerate(df_results.iterrows(), 1):
            p = parse_params(row.get("params_tested", {}))
            pnl = row.get("total_pnl", 0)
            img_data.append({
                "Rank": i,
                "Strategy": str(row.get("strategy","Unknown")),
                "TF": str(row.get("timeframe","")),
                "Params (SL / TP)": (
                    f"SL: {p.get('sl_mult','N/A')}x / TP: {p.get('tp_mult','N/A')}x"
                ),
                "Trades": int(row.get("total_trades",0)),
                "Win %": f"{row.get('win_rate',0):.2f}%",
                "PF": round(row.get("profit_factor",0), 2),
                "Max DD": f"{row.get('max_drawdown_pct',0):.2f}%",
                "Net P&L": f"+{pnl:,.0f}" if pnl > 0 else f"{pnl:,.0f}",
            })
        return self._render_image(img_data, "NARESH QUANT RESEARCH - BEST SETUPS",
                                  "NARESH_report", highlight_col="Net P&L")

    def _export_wfa_image(self, df_results: pd.DataFrame) -> Optional[str]:
        img_data = []
        for i, (_, row) in enumerate(df_results.iterrows(), 1):
            p = parse_params(row["params_tested"])
            fast = p.get("fast_ema",""); slow = p.get("slow_ema","")
            ema_str = f"EMA:{fast}/{slow} | " if fast != "" else ""
            img_data.append({
                "Rank": i,
                "Strategy": str(row.get("strategy","Unknown")),
                "TF": str(row.get("timeframe","")),
                "Params": f"{ema_str}SL:{p.get('sl_mult','N/A')}x/TP:{p.get('tp_mult','N/A')}x",
                "Tr PF": round(row.get("train_pf",0),2),
                "Tr P&L": row.get("train_pnl",0),
                "Te PF": round(row.get("test_pf",0),2),
                "Te P&L": row.get("test_pnl",0),
                "Verdict": row.get("verdict",""),
            })
        return self._render_image(img_data, "NARESH QUANT RESEARCH - WFA ROBUSTNESS",
                                  "NARESH_wfa_report", highlight_col="Te P&L")

    def _render_image(self, img_data: List[dict], title: str,
                      prefix: str, highlight_col: Optional[str] = None) -> Optional[str]:
        if not img_data:
            return None
        df_img = pd.DataFrame(img_data)
        row_count = len(df_img)
        fig, ax = plt.subplots(figsize=(14, row_count * 0.45 + 1.8))
        fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(DARK_BG); ax.axis("off")
        plt.title(title, color=ACCENT_CYAN, fontsize=14, fontweight="bold", pad=20, loc="center")

        table = ax.table(cellText=df_img.values, colLabels=df_img.columns,
                         cellLoc="center", loc="center")
        table.auto_set_font_size(False); table.set_fontsize(9); table.scale(1.1, 1.8)

        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor(GRID_COLOR); cell.set_linewidth(0.8)
            if r == 0:
                cell.set_facecolor(HEADER_BG)
                cell.get_text().set_color("#f3f4f6")
                cell.get_text().set_weight("bold")
                continue
            cell.set_facecolor(ROW_BG_1 if r % 2 == 0 else ROW_BG_2)
            col_name = df_img.columns[c]
            text = cell.get_text(); text.set_color(TEXT_LIGHT)
            if highlight_col and col_name == highlight_col:
                val = str(df_img.iloc[r-1][col_name])
                text.set_color(ACCENT_GREEN if val.startswith("+") else ACCENT_RED)
                text.set_weight("bold")
            elif col_name in ("PF", "Tr PF", "Te PF"):
                text.set_color(ACCENT_YELLOW); text.set_weight("bold")
            elif col_name == "Verdict":
                v = df_img.iloc[r-1][col_name]
                text.set_color(ACCENT_GREEN if "Robust" in v else
                               ACCENT_YELLOW if "Degraded" in v else ACCENT_RED)

        img_filename = f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        plt.savefig(img_filename, bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=300)
        plt.close(fig)
        console.print(f"[bold green]🖼️ Report image saved as {img_filename}[/bold green]")
        return img_filename

    # ---------- Telegram ----------
    def _send_telegram_image(self, image_path: str,
                             caption: str = "🤖 NARESH QUANT RESEARCH - Latest Report") -> None:
        console.print("[cyan]Sending report to Telegram...[/cyan]")
        try:
            tg = self.config.get("telegram", {}) or {}
            token = tg.get("bot_token"); chat_id = tg.get("chat_id")
            if not token or not chat_id:
                console.print("[yellow]Telegram token/chat_id missing in config.yaml. Skipping.[/yellow]")
                return
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(image_path, "rb") as f:
                resp = requests.post(url, files={"photo": f},
                                     data={"chat_id": chat_id, "caption": caption,
                                           "parse_mode": "HTML"}, timeout=10)
            if resp.status_code == 200:
                console.print("[bold green]✓ Report sent to Telegram![/bold green]")
            else:
                console.print(f"[red]Telegram Error: {resp.text}[/red]")
        except Exception as e:
            console.print(f"[red]Failed to send Telegram message: {e}[/red]")

    # ---------- UI ----------
    @staticmethod
    def _print_sweep_header(symbol: str, timeframe: str, mode: str = "") -> None:
        tag = f" ({mode})" if mode else ""
        console.print(f"\n[bold blue]{'='*50}\nSWEEP FOR {symbol} {timeframe}m{tag}\n{'='*50}[/bold blue]")

    @staticmethod
    def _print_elapsed(start_time: datetime) -> None:
        secs = int((datetime.now() - start_time).total_seconds())
        console.print(f"[bold green]Sweep Complete in {secs//60}m {secs%60}s![/bold green]\n")


# =========================================================
# MODE HANDLERS
# =========================================================
def run_options_1m_mode(agent: MasterResearchAgent,
                        timeframes: Optional[List[str]] = None) -> None:
    timeframes = timeframes or ["1", "3", "5", "10", "15", "60"]
    data_dir = "data"
    if not os.path.exists(data_dir):
        console.print(f"[red]Data folder '{data_dir}' not found![/red]")
        return

    all_csvs = []
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".csv"):
                all_csvs.append(os.path.join(root, file))
    if not all_csvs:
        console.print(f"[red]No CSV files found in '{data_dir}'.[/red]")
        return

    console.print(f"\n[bold yellow]Found {len(all_csvs)} CSV files.[/bold yellow]")
    search_term = input("🔍 Type a keyword (e.g. NIFTY26JUL24150CE) or press Enter to list all: ").strip()
    filtered = [f for f in all_csvs if search_term.lower() in f.lower()] if search_term else all_csvs
    if not filtered:
        console.print(f"[red]No CSV files match '{search_term}'.[/red]")
        return

    console.print(f"\n[bold yellow]Matching CSV files ({len(filtered)}):[/bold yellow]")
    for i, f in enumerate(filtered):
        console.print(f"  [{i+1}] {f}")
    choice = IntPrompt.ask("Enter number", default=1,
                           choices=[str(i+1) for i in range(len(filtered))])
    csv_file = filtered[choice - 1]

    base_name = os.path.basename(csv_file)
    symbol_to_test = re.split(r"_(1m|5m|15m|60m)", base_name)[0]

    console.print(f"\n[cyan]Loading CSV from {csv_file}...[/cyan]")
    try:
        df = normalize_dataframe(pd.read_csv(csv_file))
        df, removed = clean_flat_candles(df)
        if removed:
            console.print(f"[dim]Removed {removed} flat candles.[/dim]")
    except Exception as e:
        console.print(f"[red]Error reading CSV: {e}[/red]")
        return

    for i, tf in enumerate(timeframes):
        agent._print_sweep_header(symbol_to_test, tf)
        agent._execute_sweep(resample_ohlc(df, tf), symbol_to_test, tf)
        if tf != timeframes[-1] and not Confirm.ask(
            f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"):
            console.print("[bold red]Sweep stopped by user.[/bold red]")
            break


def run_feather_mode(agent: MasterResearchAgent) -> None:
    feather_file = r"data\otpion\SENSEX_2026-07-23_expiry_1min.feather"
    agent.run_feather_sweep(feather_path=feather_file, symbol="SENSEX",
                            timeframes=["1", "5", "15", "60"])


def run_csv_api_mode(agent: MasterResearchAgent,
                     symbol: str = "NSE:NIFTYBANK-INDEX",
                     timeframes: Optional[List[str]] = None) -> None:
    timeframes = timeframes or ["5", "15", "60", "240"]
    for tf in timeframes:
        agent.run_full_sweep(symbol=symbol, timeframe=tf, days=365)
        if tf != timeframes[-1] and not Confirm.ask(
            f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"):
            console.print("[bold red]Sweep stopped by user.[/bold red]")
            break


def run_walk_forward_mode(agent: MasterResearchAgent,
                          symbol: str = "NSE:BANKNIFTY26JUL57000PE",
                          timeframe: str = "15", days: int = 180,
                          train_ratio: float = 0.7,
                          options_folder: Optional[str] = r"data\otpion") -> None:
    agent.run_walk_forward(symbol=symbol, timeframe=timeframe, days=days,
                           train_ratio=train_ratio, options_folder=options_folder)


def run_hypothesis_mode(agent: MasterResearchAgent) -> None:
    hypothesis = input("🧠 Enter your trading hypothesis: ").strip()
    symbol = input("📈 Symbol (e.g. NSE:NIFTYBANK-INDEX): ").strip() or "NSE:NIFTYBANK-INDEX"
    lookback = IntPrompt.ask("Lookback days", default=180)
    target = input("Target metric (default: sharpe): ").strip() or "sharpe"
    result = agent.run_hypothesis_research(hypothesis, symbol, lookback, target)
    console.print(Panel(
        f"[bold green]Best config:[/bold green]\n{result.best_config}\n"
        f"Trials run: {len(result.trials)}",
        title="🧠 HYPOTHESIS RESEARCH RESULT", box=box.ROUNDED,
    ))


# =========================================================
# MAIN ENTRYPOINT
# =========================================================
if __name__ == "__main__":
    config = load_config()
    # Choose backtester: "enhanced" or "highwinrate"
    agent = MasterResearchAgent(config, backtester_name="enhanced")

    # ====================================================
    # CHOOSE YOUR MODE HERE
    #   OPTIONS_1M | FEATHER | CSV_API | WALK_FORWARD | HYPOTHESIS
    # ====================================================
    MODE = "OPTIONS_1M"

    if MODE == "OPTIONS_1M":
        run_options_1m_mode(agent)
    elif MODE == "FEATHER":
        run_feather_mode(agent)
    elif MODE == "CSV_API":
        run_csv_api_mode(agent)
    elif MODE == "WALK_FORWARD":
        run_walk_forward_mode(agent)
    elif MODE == "HYPOTHESIS":
        run_hypothesis_mode(agent)
    else:
        console.print(f"[red]Unknown MODE: {MODE}[/red]")

    console.print("\n[bold magenta]Generating Consolidated Report...[/bold magenta]")
    agent.generate_report()