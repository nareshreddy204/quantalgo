# research_agent_ultimate.py
"""
NARESH QUANT RESEARCH - Master Research Agent
Sweeps multiple strategies × timeframes × SL/TP parameters using multiprocessing.
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
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import pandas as pd
import requests
import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from backtester_enhanced import EnhancedBacktester
from data_fetcher import DataFetcher
from fyers_auth import FyersAuth
from strategy4 import Strategy

warnings.filterwarnings("ignore")

# ==========================================
# CONSTANTS
# ==========================================
INITIAL_CAPITAL = 100_000
PARAM_GRID = {
    "sl_mult": [1.0, 1.5, 2.0],
    "tp_mult": [1.0, 2.0, 3.0, 5.0, 10.0],
}
STRATEGIES_TO_TEST = [
    "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
    "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze",
    "vwap_bounce", "macd_crossover", "supertrend_only", "smc",
    "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam",
    "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break",
    "nk_notes"
]
WORKER_TIMEOUT_SEC = 900
TOP_N_REPORT = 15

# Dark-theme palette for PNG report
DARK_BG = "#121212"
HEADER_BG = "#1f2937"
ROW_BG_1 = "#1a1d24"
ROW_BG_2 = "#111318"
GRID_COLOR = "#374151"
TEXT_LIGHT = "#e5e7eb"
ACCENT_CYAN = "#38bdf8"
ACCENT_GREEN = "#4ade80"
ACCENT_RED = "#f87171"
ACCENT_YELLOW = "#facc15"

# ==========================================
# LOGGING & CONSOLE
# ==========================================
log_filename = f"research_{datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    handlers=[logging.FileHandler(log_filename, delay=True)],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
console = Console(record=True)


# ==========================================
# HELPERS
# ==========================================
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


def resample_ohlc(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    if str(tf) == "1":
        return df
    return df.resample(f"{tf}min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names and index on a freshly-loaded OHLC dataframe."""
    # If index is already a DatetimeIndex (common with API fetches), reset it to a column
    if isinstance(df.index, pd.DatetimeIndex) and "datetime" not in df.columns:
        df = df.reset_index().rename(columns={"index": "datetime"})
    elif df.index.name == "datetime" and "datetime" not in df.columns:
        df = df.reset_index()

    # Check for common datetime column names if it's still missing
    if "datetime" not in df.columns:
        for col in ("date", "time", "timestamp"):
            if col in df.columns:
                df = df.rename(columns={col: "datetime"})
                break
                
    # Final safety check
    if "datetime" not in df.columns:
        raise KeyError("Could not find a valid datetime column in the data.")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    df.columns = [c.lower() for c in df.columns]
    if "volume" not in df.columns:
        df["volume"] = 0
    return df


# ==========================================
# MULTIPROCESSING WORKER
# ==========================================
def run_strategy_batch(args):
    df_signals, symbol, strategy_name, combos, min_trades = args
    backtester = EnhancedBacktester()
    results: List[Dict[str, Any]] = []

    for params in combos:
        try:
            result = backtester.run_backtest(
                df=df_signals.copy(),
                symbol=symbol,
                strategy_name=strategy_name,
                params=params,
                initial_capital=INITIAL_CAPITAL,
                sl_multiplier=params["sl_mult"],
                tp_multiplier=params["tp_mult"],
                use_atr_sl=True,
                use_time_filter=True,
                use_trend_filter=True,
            )
            if result and result.get("total_trades", 0) > min_trades:
                result["strategy"] = strategy_name
                result["params_tested"] = params
                results.append(result)
        except Exception as e:
            logging.error(
                f"Error in {strategy_name} | Params: {params} | Error: {e}\n"
                f"{traceback.format_exc()}"
            )

    return results


# ==========================================
# MASTER RESEARCH AGENT
# ==========================================
class MasterResearchAgent:
    def __init__(self, config: dict):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])

        self.data_fetcher = DataFetcher(auth.fyers)
        self.results: List[Dict[str, Any]] = []

        self.strategies: Dict[str, Strategy] = {}
        for name in STRATEGIES_TO_TEST:
            try:
                self.strategies[name] = Strategy(name)
            except Exception as e:
                console.print(f"[red]Failed to initialize {name}: {e}[/red]")

        self._archive_previous_results()

    def _archive_previous_results(self) -> None:
        partial_files = [
            f for f in os.listdir(".")
            if f.startswith("results_partial_") and f.endswith(".csv")
        ]
        if not partial_files:
            return

        archive_folder = f"report_{datetime.now():%Y-%m-%d}"
        os.makedirs(archive_folder, exist_ok=True)

        console.print(
            f"[yellow]Found {len(partial_files)} old checkpoint(s). "
            f"Moving to ./{archive_folder}/[/yellow]"
        )
        for file in partial_files:
            try:
                dest = os.path.join(archive_folder, file)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(file, archive_folder)
            except Exception as e:
                console.print(f"[red]Failed to move {file}: {e}[/red]")

    def _load_data(self, symbol: str, timeframe: str, days: int) -> Optional[pd.DataFrame]:
        tf_int = int(timeframe)
        if tf_int <= 3:
            days = min(days, 60)
        elif tf_int <= 10:
            days = min(days, 120)

        safe_symbol = symbol.replace(":", "_")
        pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")

        matching_files: List[str] = []
        for root, _, files in os.walk("./data"):
            for file in files:
                if pattern.match(file):
                    matching_files.append(os.path.join(root, file))

        if matching_files:
            def get_days(filepath: str) -> int:
                m = pattern.search(os.path.basename(filepath))
                return int(m.group(1)) if m else 0

            matching_files.sort(key=get_days, reverse=True)

            console.print("[bold yellow]Found multiple cached files. Select one:[/bold yellow]")
            for i, f in enumerate(matching_files):
                console.print(f"  [{i + 1}] {os.path.basename(f)} ({get_days(f)} days)")

            choice = IntPrompt.ask(
                "Enter number",
                default=1,
                choices=[str(i + 1) for i in range(len(matching_files))],
            )
            cache_file = matching_files[choice - 1]
            console.print(f"[bold green]✓ Loaded: {cache_file}[/bold green]")
            try:
                return pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")

        console.print(f"[yellow]No local cache. Fetching {days} days from API...[/yellow]")
        return self.data_fetcher.get_historical_data(symbol, timeframe, days)

    def run_feather_sweep(
        self,
        feather_path: str,
        symbol: str = "SENSEX",
        timeframes: List[str] = ("1", "5", "15", "60"),
    ) -> None:
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

    def run_full_sweep(self, symbol: str, timeframe: str, days: int = 100) -> None:
        self._print_sweep_header(symbol, timeframe)
        df = self._load_data(symbol, timeframe, days)
        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
        self._execute_sweep(df, symbol, timeframe)

    def _execute_sweep(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        start_time = datetime.now()
        console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")

        combinations = [
            dict(zip(PARAM_GRID.keys(), combo))
            for combo in itertools.product(*PARAM_GRID.values())
        ]
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
                tasks.append((df_signals, symbol, name, combinations, min_trades))
                console.print("[green]Done[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        console.print(
            f"\n[yellow]Running {len(tasks)} strategy batches via Multicore CPU...[/yellow]\n"
        )

        self._run_workers(tasks, timeframe)

        if self.results:
            safe_sym = symbol.replace(":", "_")
            checkpoint = f"results_partial_{safe_sym}_{timeframe}m.csv"
            pd.DataFrame(self.results).to_csv(checkpoint, index=False)
            console.print(f"[dim]Saved checkpoint: {checkpoint}[/dim]")

        elapsed = datetime.now() - start_time
        secs = int(elapsed.total_seconds())
        console.print(
            f"[bold green]Sweep Complete in {secs // 60}m {secs % 60}s![/bold green]\n"
        )

    def _run_workers(self, tasks: List[tuple], timeframe: str) -> None:
        max_workers = max(1, (os.cpu_count() or 2) - 1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Backtesting...", total=len(tasks))

            try:
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(run_strategy_batch, t) for t in tasks]
                    for future in as_completed(futures):
                        try:
                            batch_results = future.result(timeout=WORKER_TIMEOUT_SEC)
                            if batch_results:
                                for res in batch_results:
                                    res["timeframe"] = timeframe
                                    self.results.append(res)
                        except Exception as e:
                            console.print(f"[red]Worker failed: {e}[/red]")
                        progress.advance(task_id)
            except BrokenProcessPool:
                console.print(
                    "[bold red]Process pool crashed. Reduce memory or worker count.[/bold red]"
                )

    def generate_report(self) -> None:
        if not self.results:
            console.print("[bold red]No trades were taken by ANY strategy.[/bold red]")
            return

        valid_results = [r for r in self.results if "profit_factor" in r]
        if not valid_results:
            console.print("[bold red]No valid backtest results found.[/bold red]")
            return

        df = pd.DataFrame(valid_results)
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
            title="🤖 NARESH QUANT RESEARCH",
            box=box.DOUBLE,
        ))

        self._print_results_table(df)

        best = df.iloc[0]
        best_params = parse_params(best.get("params_tested", {}))
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  strategy: \"{best.get('strategy')}\"\n"
            f"  resolution: \"{best.get('timeframe')}\"\n"
            f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best_params.get('tp_mult')}",
            title="🚀 BEST SETUP FOUND",
            box=box.ROUNDED,
        ))

        try:
            img_filename = self._export_report_image(df)
            if img_filename:
                self._send_telegram_image(img_filename)
        except Exception as e:
            console.print(f"[yellow]Could not generate/send report image: {e}[/yellow]")

    def _print_results_table(self, df: pd.DataFrame) -> None:
        table = Table(
            box=box.HEAVY,
            show_lines=True,
            title="Best Setups Found",
            title_style="bold cyan",
        )
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Strategy", style="bold cyan", min_width=20)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Params (SL / TP)", style="dim white", min_width=20)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            params = parse_params(row.get("params_tested", {}))
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
                f"SL: {params.get('sl_mult', 'N/A')}x / TP: {params.get('tp_mult', 'N/A')}x",
                f"{row.get('total_trades', 0)}",
                wr_str,
                pf_str,
                f"{row.get('max_drawdown_pct', 0):.2f}%",
                pnl_str,
            )

        console.print(table)

    def _export_report_image(self, df_results: pd.DataFrame) -> Optional[str]:
        img_data = []
        for i, (_, row) in enumerate(df_results.iterrows(), 1):
            params = parse_params(row.get("params_tested", {}))
            pnl = row.get("total_pnl", 0)
            pnl_str = f"+{pnl:,.0f}" if pnl > 0 else f"{pnl:,.0f}"

            img_data.append({
                "Rank": i,
                "Strategy": str(row.get("strategy", "Unknown")),
                "TF": str(row.get("timeframe", "")),
                "Params (SL / TP)": (
                    f"SL: {params.get('sl_mult', 'N/A')}x / "
                    f"TP: {params.get('tp_mult', 'N/A')}x"
                ),
                "Trades": int(row.get("total_trades", 0)),
                "Win %": f"{row.get('win_rate', 0):.2f}%",
                "PF": round(row.get("profit_factor", 0), 2),
                "Max DD": f"{row.get('max_drawdown_pct', 0):.2f}%",
                "Net P&L": pnl_str,
            })

        df_img = pd.DataFrame(img_data)
        row_count = len(df_img)
        fig, ax = plt.subplots(figsize=(14, row_count * 0.45 + 1.8))
        fig.patch.set_facecolor(DARK_BG)
        ax.set_facecolor(DARK_BG)
        ax.axis("off")

        plt.title(
            "NARESH QUANT RESEARCH - BEST SETUPS",
            color=ACCENT_CYAN,
            fontsize=14,
            fontweight="bold",
            pad=20,
            loc="center",
        )

        table = ax.table(
            cellText=df_img.values,
            colLabels=df_img.columns,
            cellLoc="center",
            loc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.1, 1.8)

        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor(GRID_COLOR)
            cell.set_linewidth(0.8)

            if r == 0:
                cell.set_facecolor(HEADER_BG)
                cell.get_text().set_color("#f3f4f6")
                cell.get_text().set_weight("bold")
                continue

            cell.set_facecolor(ROW_BG_1 if r % 2 == 0 else ROW_BG_2)
            col_name = df_img.columns[c]
            text = cell.get_text()
            text.set_color(TEXT_LIGHT)

            if col_name == "Net P&L":
                val = df_img.iloc[r - 1][col_name]
                text.set_color(ACCENT_GREEN if val.startswith("+") else ACCENT_RED)
                text.set_weight("bold")
            elif col_name == "PF":
                text.set_color(ACCENT_YELLOW)
                text.set_weight("bold")

        img_filename = f"NARESH_report_{datetime.now():%Y%m%d_%H%M%S}.png"
        plt.savefig(
            img_filename,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            dpi=300,
        )
        plt.close(fig)
        console.print(f"[bold green]🖼️ Report image saved perfectly as {img_filename}[/bold green]")
        return img_filename

    def _send_telegram_image(
        self,
        image_path: str,
        caption: str = "🤖 NARESH QUANT RESEARCH - Latest Report",
    ) -> None:
        console.print("[cyan]Sending report to Telegram...[/cyan]")
        try:
            tg = self.config.get("telegram", {})
            token = tg.get("bot_token")
            chat_id = tg.get("chat_id")

            if not token or not chat_id:
                console.print(
                    "[yellow]Telegram token or chat_id missing in config.yaml. Skipping.[/yellow]"
                )
                return

            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(image_path, "rb") as photo_file:
                files = {"photo": photo_file}
                data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
                response = requests.post(url, files=files, data=data, timeout=10)

            if response.status_code == 200:
                console.print("[bold green]✓ Report sent to Telegram successfully![/bold green]")
            else:
                console.print(f"[red]Telegram Error: {response.text}[/red]")
        except Exception as e:
            console.print(f"[red]Failed to send Telegram message: {e}[/red]")

    @staticmethod
    def _print_sweep_header(symbol: str, timeframe: str) -> None:
        console.print(
            f"\n[bold blue]{'=' * 50}\nSWEEP FOR {symbol} {timeframe}m\n{'=' * 50}[/bold blue]"
        )


# ==========================================
# MODE HANDLERS
# ==========================================
def run_dynamic_index_option_mode(agent: MasterResearchAgent) -> None:
    console.print("[bold cyan]🤖 DYNAMIC INDEX & OPTION SWEEP[/bold cyan]")
    
    # 1. Select Index
    index_choice = Prompt.ask("Select Index to test", choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"], default="BANKNIFTY")
    index_symbol_map = {
        "NIFTY": "NSE:NIFTY50-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "FINNIFTY": "NSE:FINNIFTY-INDEX",
        "SENSEX": "BSE:SENSEX-INDEX"
    }
    index_symbol = index_symbol_map[index_choice]
    
    # 2. Ask for Days and Timeframe
    days = IntPrompt.ask("How many days of historical data to test?", default=30)
    tf = Prompt.ask("Enter timeframe (e.g., 5, 15, 60)", default="15")
    
    # 3. Fetch and Sweep Index
    console.print(f"\n[yellow]Fetching {days} days of {index_symbol} data...[/yellow]")
    df_index = agent.data_fetcher.get_historical_data(index_symbol, tf, days)
    
    if df_index is not None and not df_index.empty:
        df_index = normalize_dataframe(df_index)
        agent._print_sweep_header(index_symbol, tf)
        agent._execute_sweep(df_index, index_symbol, tf)
    else:
        console.print("[red]Failed to fetch index data.[/red]")
        return
        
    # 4. Ask to test Option
    if not Confirm.ask("\n[bold yellow]Index sweep complete. Do you want to test an Option now?[/bold yellow]", default=True):
        return
        
    # 5. Get Option Details
    expiry = Prompt.ask("Enter Expiry string (e.g., 26AUG or 23817)", default="26AUG")
    opt_type = Prompt.ask("Option Type", choices=["CE", "PE"], default="CE")
    strikes_away = IntPrompt.ask("How many strikes away from Spot?", default=10)
    
    # 6. Calculate Strike Price
    spot_price = df_index['close'].iloc[-1] # Get latest close as spot price
    intervals = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100}
    interval = intervals.get(index_choice, 50)
    
    base_strike = round(spot_price / interval) * interval
    
    if opt_type == "CE":
        strike = base_strike + (strikes_away * interval)
    else:
        strike = base_strike - (strikes_away * interval)
        
    console.print(f"[cyan]Current Spot: {spot_price:.2f} | Base ATM Strike: {base_strike} | Target Strike ({strikes_away} away): {strike}[/cyan]")
    
    # 7. Construct Option Symbol
    exchange = "NSE" if index_choice != "SENSEX" else "BSE"
    option_symbol = f"{exchange}:{index_choice}{expiry}{strike}{opt_type}"
    
    console.print(f"[bold green]Generated Option Symbol: {option_symbol}[/bold green]")
    
    # 8. Fetch and Sweep Option
    console.print(f"\n[yellow]Fetching {days} days of {option_symbol} data...[/yellow]")
    df_opt = agent.data_fetcher.get_historical_data(option_symbol, tf, days)
    
    if df_opt is not None and not df_opt.empty:
        df_opt = normalize_dataframe(df_opt)
        agent._print_sweep_header(option_symbol, tf)
        agent._execute_sweep(df_opt, option_symbol, tf)
    else:
        console.print(f"[red]Failed to fetch option data for {option_symbol}. Make sure the symbol/expiry is valid.[/red]")


def run_feather_mode(agent: MasterResearchAgent) -> None:
    feather_file = r"data\otpion\SENSEX_2026-07-23_expiry_1min.feather"
    timeframes_to_test = ["1", "5", "15", "60"]
    agent.run_feather_sweep(
        feather_path=feather_file,
        symbol="SENSEX",
        timeframes=timeframes_to_test,
    )


def run_csv_api_mode(agent: MasterResearchAgent) -> None:
    symbol_to_test = "NSE:NIFTYBANK-INDEX"
    timeframes_to_test = ["5", "15", "60", "240"]

    for tf in timeframes_to_test:
        agent.run_full_sweep(symbol=symbol_to_test, timeframe=tf, days=365)

        if tf != timeframes_to_test[-1]:
            if not Confirm.ask(
                f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"
            ):
                console.print("[bold red]Sweep stopped by user.[/bold red]")
                break


# ==========================================
# MAIN ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)

    # Choose your mode: "DYNAMIC_INDEX_OPTION" | "FEATHER" | "CSV_API"
    MODE = "DYNAMIC_INDEX_OPTION"

    if MODE == "DYNAMIC_INDEX_OPTION":
        run_dynamic_index_option_mode(agent)
    elif MODE == "FEATHER":
        run_feather_mode(agent)
    elif MODE == "CSV_API":
        run_csv_api_mode(agent)
    else:
        console.print(f"[red]Unknown MODE: {MODE}[/red]")

    console.print("\n[bold magenta]Generating Consolidated Report...[/bold magenta]")
    agent.generate_report()