# research_agent3.py
import time
import re
import yaml
import os
import itertools
import pandas as pd
import numpy as np
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from concurrent.futures import ProcessPoolExecutor, as_completed
import logging
import traceback

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategycopy2 import Strategy
from backtester_enhanced import EnhancedBacktester

logging.basicConfig(
    filename=f"research_{datetime.now():%Y%m%d_%H%M%S}.log",
    level=logging.INFO
)

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

# ==========================================
# WORKER FUNCTION FOR MULTIPROCESSING
# ==========================================
def run_strategy_batch(args):
    """This function runs in a separate CPU core, handling all SL/TP combos for a single strategy"""
    df_signals, symbol, strategy_name, combos, min_trades = args
    
    backtester = EnhancedBacktester()
    results = []
    
    for params in combos:
        try:
            result = backtester.run_backtest(
                df=df_signals.copy(),
                symbol=symbol,
                strategy_name=strategy_name,  
                params=params,
                sl_multiplier=params['sl_mult'],
                tp_multiplier=params['tp_mult'],
                use_atr_sl=True,
                use_time_filter=True,
                use_trend_filter=True
            )
            
            if result and result.get("total_trades", 0) > min_trades:
                result["strategy"] = strategy_name
                result["params_tested"] = params
                results.append(result)
                
        except Exception as e:
            logging.error(f"Error in run_strategy_batch for {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}")
            
    return results


class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.results = []
        
        # ==========================================
        # SYNCED STRATEGY LIST
        # ==========================================
        strategies_to_test = [
            "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
            "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze", 
            "vwap_bounce", "macd_crossover", "supertrend_only", "smc", 
            "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
            "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break"
        ]
        
        self.strategies = {}
        for name in strategies_to_test:
            try:
                self.strategies[name] = Strategy(name)
            except Exception as e:
                console.print(f"[red]Failed to initialize {name}: {e}[/red]")
                
        # ==========================================
        # LOAD PREVIOUS PARTIAL RESULTS (RESUME CAPABILITY)
        # ==========================================
        self._load_partial_results()

    def _load_partial_results(self):
        """Load previous checkpoint files to resume progress without losing data."""
        partial_files = [f for f in os.listdir(".") if f.startswith("results_partial_") and f.endswith(".csv")]
        if partial_files:
            console.print(f"[yellow]Found {len(partial_files)} previous checkpoint file(s). Loading data...[/yellow]")
            for file in partial_files:
                try:
                    df = pd.read_csv(file)
                    self.results.extend(df.to_dict('records'))
                except Exception:
                    pass

    # def run_full_sweep(self, symbol, timeframe, days=365):
    #     """Test ALL strategies with multiple SL/TP combinations"""
    #     start_time = datetime.now()
        
    #     # 1. DYNAMIC DAYS REDUCTION (Speed Hack)
    #     tf_int = int(timeframe)
    #     if tf_int <= 3:
    #         days = min(days, 60)
    #         console.print(f"[yellow]Timeframe {timeframe}m is very dense. Limiting to {days} days for speed.[/yellow]")
    #     elif tf_int <= 10:
    #         days = min(days, 120)
            
    #     # 2. SMART CACHE SCAN
    #     console.print(f"[cyan]Scanning ./data folder for {symbol} {timeframe}m data...[/cyan]")
    #     safe_symbol = symbol.replace(":", "_")
        
    #     pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")
        
    #     matching_files = []
    #     for root, dirs, files in os.walk("./data"):
    #         for file in files:
    #             if pattern.match(file):
    #                 matching_files.append(os.path.join(root, file))
        
    #     df = None
    #     if matching_files:
    #         def get_days_from_filename(filepath):
    #             match = pattern.search(os.path.basename(filepath))
    #             return int(match.group(1)) if match else 0
                
    #         matching_files.sort(key=get_days_from_filename, reverse=True)
    #         cache_file = matching_files[0]
    #         file_days = get_days_from_filename(cache_file)
            
    #         console.print(f"[bold green]✓ Found cached data: {cache_file} ({file_days} days)[/bold green]")
    #         try:
    #             df = pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
    #         except Exception as e:
    #             console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
    #             df = None
                
    #     if df is None:
    #         console.print(f"[yellow]No local cache found. Fetching {days} days from API...[/yellow]")
    #         df = self.data_fetcher.get_historical_data(symbol, timeframe, days)

    #     # 3. PRE-CALCULATE SIGNALS (Massive Speedup)
    #     if df is None or df.empty:
    #         console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
    #         return
            
    #     console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
    #     param_grid = {
    #         'sl_mult': [1.0, 1.5, 2.0],
    #         'tp_mult': [1.0, 2.0, 3.0]
    #     }
        
    #     keys = param_grid.keys()
    #     values = param_grid.values()
    #     combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        
    #     # Dynamic trade filter based on data length
    #     min_trades = max(5, len(df) // 1000)
    #     console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")
        
    #     tasks = []
    #     for strategy_name, strat in self.strategies.items():
    #         try:
    #             # Generate signals ONLY ONCE per strategy
    #             df_signals = strat.generate_signals(df.copy())
                
    #             # Ensure ATR is present for the backtester
    #             if 'atr' not in df_signals.columns:
    #                 df_signals['atr'] = strat.calculate_atr(df_signals)
                
    #             # OPTIMIZATION: Drop NaN rows to prevent backtester crashing & speed up loops
    #             df_signals.dropna(subset=['close', 'atr', 'signal'], inplace=True)
                
    #             # OPTIMIZATION: Keep only essential columns to minimize IPC payload size
    #             essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
    #             cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
    #             df_signals = df_signals[cols_to_keep]
                
    #             tasks.append((df_signals, symbol, strategy_name, combinations, min_trades))
                    
    #         except Exception as e:
    #             console.print(f"[red]Error preparing {strategy_name}: {e}[/red]")

    #     total_tests = len(tasks)
    #     console.print(f"[yellow]Running {total_tests} strategy batches using Multicore CPU...[/yellow]\n")
        
    #     # 4. RUN BACKTESTS IN PARALLEL (Multicore)
    #     with Progress(
    #         SpinnerColumn(),
    #         TextColumn("[progress.description]{task.description}"),
    #         BarColumn(),
    #         TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    #         TimeRemainingColumn(),
    #         console=console,
    #     ) as progress:
    #         task_id = progress.add_task("[cyan]Backtesting...", total=total_tests)
            
    #         max_workers = max(1, (os.cpu_count() or 2) - 1)
    #         try:
    #             with ProcessPoolExecutor(max_workers=max_workers) as executor:
    #                 futures = [executor.submit(run_strategy_batch, task) for task in tasks]
                    
    #                 for future in as_completed(futures):
    #                     try:
    #                         batch_results = future.result(timeout=600)
    #                         if batch_results:
    #                             for res in batch_results:
    #                                 res["timeframe"] = timeframe
    #                                 self.results.append(res)
    #                     except Exception as e:
    #                         console.print(f"[red]Worker failed: {e}[/red]")
                            
    #                     progress.advance(task_id)
    #         except BrokenProcessPool:
    #             console.print("[bold red]Process pool crashed. Try reducing memory footprint or worker count.[/bold red]")

    #     # 15. CHECKPOINTING
    #     if self.results:
    #         pd.DataFrame(self.results).to_csv(f"results_partial_{timeframe}m.csv", index=False)
    #         console.print(f"[dim]Saved checkpoint to results_partial_{timeframe}m.csv[/dim]")

    #     end_time = datetime.now()
    #     elapsed = end_time - start_time
    #     total_seconds = int(elapsed.total_seconds())
    #     mins = total_seconds // 60
    #     secs = total_seconds % 60
        
    #     console.print(f"[bold green]Sweep Complete in {mins}m {secs}s![/bold green]\n")

    # def run_full_sweep(self, symbol, timeframe, days=365):
    #     """Test ALL strategies with multiple SL/TP combinations"""
    #     start_time = datetime.now()
        
    #     # 1. DYNAMIC DAYS REDUCTION (Speed Hack)
    #     tf_int = int(timeframe)
    #     if tf_int <= 3:
    #         days = min(days, 60)
    #         console.print(f"[yellow]Timeframe {timeframe}m is very dense. Limiting to {days} days for speed.[/yellow]")
    #     elif tf_int <= 10:
    #         days = min(days, 120)
            
    #     # 2. SMART CACHE SCAN
    #     console.print(f"[cyan]Scanning ./data folder for {symbol} {timeframe}m data...[/cyan]")
    #     safe_symbol = symbol.replace(":", "_")
        
    #     pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")
        
    #     matching_files = []
    #     for root, dirs, files in os.walk("./data"):
    #         for file in files:
    #             if pattern.match(file):
    #                 matching_files.append(os.path.join(root, file))
        
    #     df = None
    #     if matching_files:
    #         def get_days_from_filename(filepath):
    #             match = pattern.search(os.path.basename(filepath))
    #             return int(match.group(1)) if match else 0
                
    #         matching_files.sort(key=get_days_from_filename, reverse=True)
    #         cache_file = matching_files[0]
    #         file_days = get_days_from_filename(cache_file)
            
    #         console.print(f"[bold green]✓ Found cached data: {cache_file} ({file_days} days)[/bold green]")
    #         try:
    #             df = pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
    #         except Exception as e:
    #             console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
    #             df = None
                
    #     if df is None:
    #         console.print(f"[yellow]No local cache found. Fetching {days} days from API...[/yellow]")
    #         df = self.data_fetcher.get_historical_data(symbol, timeframe, days)

    #     # 3. PRE-CALCULATE SIGNALS
    #     if df is None or df.empty:
    #         console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
    #         return
            
    #     console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
    #     param_grid = {
    #         'sl_mult': [1.0, 1.5, 2.0],
    #         'tp_mult': [1.0, 2.0, 3.0]
    #     }
        
    #     keys = param_grid.keys()
    #     values = param_grid.values()
    #     combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        
    #     min_trades = max(5, len(df) // 1000)
    #     console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")
        
    #     tasks = []
    #     for strategy_name, strat in self.strategies.items():
    #         # ADDED LIVE LOGGING TO IDENTIFY HANGS
    #         console.print(f"[dim]  -> Pre-calculating [bold]{strategy_name}[/bold]...[/dim]", end=" ")
    #         try:
    #             df_signals = strat.generate_signals(df.copy())
                
    #             if 'atr' not in df_signals.columns:
    #                 df_signals['atr'] = strat.calculate_atr(df_signals)
                
    #             # Drop NaNs for essential columns
    #             df_signals.dropna(subset=['close', 'atr'], inplace=True)
                
    #             essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
    #             cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
    #             df_signals = df_signals[cols_to_keep]
                
    #             tasks.append((df_signals, symbol, strategy_name, combinations, min_trades))
    #             console.print("[green]Done[/green]")
                    
    #         except Exception as e:
    #             console.print(f"[red]Error: {e}[/red]")

    #     total_tests = len(tasks)
    #     console.print(f"\n[yellow]Running {total_tests} strategy batches using Multicore CPU...[/yellow]\n")
        
    #     # 4. RUN BACKTESTS IN PARALLEL
    #     with Progress(
    #         SpinnerColumn(),
    #         TextColumn("[progress.description]{task.description}"),
    #         BarColumn(),
    #         TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    #         TimeRemainingColumn(),
    #         console=console,
    #     ) as progress:
    #         task_id = progress.add_task("[cyan]Backtesting...", total=total_tests)
            
    #         max_workers = max(1, (os.cpu_count() or 2) - 1)
    #         try:
    #             with ProcessPoolExecutor(max_workers=max_workers) as executor:
    #                 futures = [executor.submit(run_strategy_batch, task) for task in tasks]
                    
    #                 for future in as_completed(futures):
    #                     try:
    #                         batch_results = future.result(timeout=900) # Increased timeout for 90k rows
    #                         if batch_results:
    #                             for res in batch_results:
    #                                 res["timeframe"] = timeframe
    #                                 self.results.append(res)
    #                     except Exception as e:
    #                         console.print(f"[red]Worker failed: {e}[/red]")
                            
    #                     progress.advance(task_id)
    #         except BrokenProcessPool:
    #             console.print("[bold red]Process pool crashed. Try reducing memory footprint or worker count.[/bold red]")

    #     # 15. CHECKPOINTING
    #     if self.results:
    #         pd.DataFrame(self.results).to_csv(f"results_partial_{timeframe}m.csv", index=False)
    #         console.print(f"[dim]Saved checkpoint to results_partial_{timeframe}m.csv[/dim]")

    #     end_time = datetime.now()
    #     elapsed = end_time - start_time
    #     total_seconds = int(elapsed.total_seconds())
    #     mins = total_seconds // 60
    #     secs = total_seconds % 60
        
    #     console.print(f"[bold green]Sweep Complete in {mins}m {secs}s![/bold green]\n")
    
    def run_full_sweep(self, symbol, timeframe, days=100):
        """Test ALL strategies with multiple SL/TP combinations"""
        start_time = datetime.now()
        
        tf_int = int(timeframe)
        if tf_int <= 3:
            days = min(days, 60)
            console.print(f"[yellow]Timeframe {timeframe}m is very dense. Limiting to {days} days for speed.[/yellow]")
        elif tf_int <= 10:
            days = min(days, 120)
            
        console.print(f"[cyan]Scanning ./data folder for {symbol} {timeframe}m data...[/cyan]")
        safe_symbol = symbol.replace(":", "_")
        
        pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")
        
        matching_files = []
        for root, dirs, files in os.walk("./data"):
            for file in files:
                if pattern.match(file):
                    matching_files.append(os.path.join(root, file))
        
        df = None
        if matching_files:
            # Sort by days descending just for display
            def get_days_from_filename(filepath):
                match = pattern.search(os.path.basename(filepath))
                return int(match.group(1)) if match else 0
                
            matching_files.sort(key=get_days_from_filename, reverse=True)
            
            console.print("[bold yellow]Found multiple cached files. Which one would you like to use?[/bold yellow]")
            for i, f in enumerate(matching_files):
                f_days = get_days_from_filename(f)
                console.print(f"  [{i+1}] {os.path.basename(f)} ({f_days} days)")
            
            from rich.prompt import IntPrompt
            choice = IntPrompt.ask("Enter the number of the file to use", default=1, choices=[str(i+1) for i in range(len(matching_files))])
            
            cache_file = matching_files[choice - 1]
            file_days = get_days_from_filename(cache_file)
            
            console.print(f"[bold green]✓ Selected: {cache_file} ({file_days} days)[/bold green]")
            try:
                df = pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
                df = None
                
        if df is None:
            console.print(f"[yellow]No local cache found or selected. Fetching {days} days from API...[/yellow]")
            df = self.data_fetcher.get_historical_data(symbol, timeframe, days)

        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
            
        console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
        param_grid = {
            'sl_mult': [1.0, 1.5, 2.0],
            'tp_mult': [3.0, 5.0, 10.0]
        }
        
        keys = param_grid.keys()
        values = param_grid.values()
        combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        
        min_trades = max(5, len(df) // 1000)
        console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")
        
        tasks = []
        for strategy_name, strat in self.strategies.items():
            console.print(f"[dim]  -> Pre-calculating [bold]{strategy_name}[/bold]...[/dim]", end=" ")
            try:
                df_signals = strat.generate_signals(df.copy())
                
                if 'atr' not in df_signals.columns:
                    df_signals['atr'] = strat.calculate_atr(df_signals)
                
                df_signals.dropna(subset=['close', 'atr'], inplace=True)
                
                essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
                df_signals = df_signals[cols_to_keep]
                
                tasks.append((df_signals, symbol, strategy_name, combinations, min_trades))
                console.print("[green]Done[/green]")
                    
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        total_tests = len(tasks)
        console.print(f"\n[yellow]Running {total_tests} strategy batches using Multicore CPU...[/yellow]\n")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Backtesting...", total=total_tests)
            
            max_workers = max(1, (os.cpu_count() or 2) - 1)
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(run_strategy_batch, task) for task in tasks]
                    
                    for future in as_completed(futures):
                        try:
                            batch_results = future.result(timeout=900)
                            if batch_results:
                                for res in batch_results:
                                    res["timeframe"] = timeframe
                                    self.results.append(res)
                        except Exception as e:
                            console.print(f"[red]Worker failed: {e}[/red]")
                            
                        progress.advance(task_id)
            except BrokenProcessPool:
                console.print("[bold red]Process pool crashed. Try reducing memory footprint or worker count.[/bold red]")

        if self.results:
            pd.DataFrame(self.results).to_csv(f"results_partial_{timeframe}m.csv", index=False)
            console.print(f"[dim]Saved checkpoint to results_partial_{timeframe}m.csv[/dim]")

        end_time = datetime.now()
        elapsed = end_time - start_time
        total_seconds = int(elapsed.total_seconds())
        mins = total_seconds // 60
        secs = total_seconds % 60
        
        console.print(f"[bold green]Sweep Complete in {mins}m {secs}s![/bold green]\n")
    
    def generate_report(self):
        if not self.results:
            console.print("[bold red]No trades were taken by ANY strategy.[/bold red]")
            return

        # Filter out error logs from results
        valid_results = [r for r in self.results if 'profit_factor' in r]
        if not valid_results:
            console.print("[bold red]No valid backtest results found.[/bold red]")
            return
            
        df = pd.DataFrame(valid_results)
        
        # ==========================================
        # FIX: DROP DUPLICATES BY CONVERTING DICT TO STRING
        # ==========================================
        df['params_str'] = df['params_tested'].astype(str)
        df = df.drop_duplicates(subset=['strategy', 'timeframe', 'params_str'])
        df = df.drop(columns=['params_str'])
        
        profitable = df[df['profit_factor'] > 1.1].copy()
        
        if profitable.empty:
            console.print("[bold red]No profitable setups found across all strategies![/bold red]")
            df = df.sort_values(by='profit_factor', ascending=False).head(15)
        else:
            df = profitable.sort_values(by=['profit_factor', 'total_pnl'], ascending=[False, False]).head(15)
        
        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT REPORT\nTop Performing Strategies[/bold white]",
            title="🤖 KRONOS QUANT RESEARCH", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Best Setups Found", title_style="bold cyan")
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
            pf = row['profit_factor']
            pf = min(pf, 999.0) if pd.notna(pf) else 0.0
            pf_str = f"[bold green]{pf:.2f}[/bold green]" if pf > 1.2 else f"{pf:.2f}"
            
            pnl = row.get('total_pnl', 0)
            pnl_str = f"[green]+{pnl:,.0f}[/green]" if pnl > 0 else f"[red]{pnl:,.0f}[/red]"
            
            wr = row.get('win_rate', 0)
            wr_str = f"{wr:.2f}%" if pd.notna(wr) else "N/A"
            
            params_tested = row.get('params_tested', {})
            if isinstance(params_tested, str):
                import ast
                try: params_tested = ast.literal_eval(params_tested)
                except: params_tested = {}
                
            sl_mult = params_tested.get('sl_mult', 'N/A')
            tp_mult = params_tested.get('tp_mult', 'N/A')
            
            table.add_row(
                f"{rank}",
                str(row.get('strategy', 'Unknown')),
                str(row.get('timeframe', '')),
                f"SL: {sl_mult}x / TP: {tp_mult}x",
                f"{row.get('total_trades', 0)}",
                wr_str,
                pf_str,
                f"{row.get('max_drawdown_pct', 0):.2f}%",
                pnl_str
            )
            
        console.print(table)
        
        best = df.iloc[0]
        best_params = best.get('params_tested', {})
        if isinstance(best_params, str):
            import ast
            try: best_params = ast.literal_eval(best_params)
            except: best_params = {}
            
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  strategy: \"{best.get('strategy')}\"\n"
            f"  resolution: \"{best.get('timeframe')}\"\n"
            f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best_params.get('tp_mult')}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))

from rich.prompt import Confirm

if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)
    
    # timeframes_to_test = ["1","5","10","15","60","120","180","240"]
    timeframes_to_test = ["1"]
    
    for tf in timeframes_to_test:
        console.print(f"\n[bold blue]{'='*50}[/bold blue]")
        console.print(f"[bold blue]STARTING SWEEP FOR {tf}m TIMEFRAME[/bold blue]")
        console.print(f"[bold blue]{'='*50}[/bold blue]")
        
        agent.run_full_sweep(
            symbol="NSE:BANKNIFTY26JUL57000PE",
            timeframe=tf,
            days=100
        )
        
        if tf != timeframes_to_test[-1]:
            if not Confirm.ask(f"[bold yellow]Finished {tf}m sweep. Do you want to continue to the next timeframe?[/bold yellow]"):
                console.print("[bold red]Sweep stopped by user.[/bold red]")
                break
    
    console.print(f"\n[bold magenta]Generating Consolidated Report for all timeframes...[/bold magenta]")
    agent.generate_report()