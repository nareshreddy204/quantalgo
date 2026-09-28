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

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy_copy import Strategy
from backtester_enhanced import EnhancedBacktester
import logging

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
def run_single_backtest(args):
    """This function runs in a separate CPU core"""
    df_signals, symbol, strategy_name, params = args
    
    # Instantiate backtester inside the worker to avoid pickling issues
    backtester = EnhancedBacktester()
    
    try:
        result = backtester.run_backtest(
            df=df_signals.copy(),
            symbol=symbol,
            strategy_name=strategy_name,  # Pass name in case backtester needs it for logging
            params=params,
            sl_multiplier=params['sl_mult'],
            tp_multiplier=params['tp_mult'],
            use_atr_sl=True,
            use_time_filter=True,
            use_trend_filter=True
        )
        
        if result and result.get("total_trades", 0) > 5:
            result["strategy"] = strategy_name
            result["params_tested"] = params
            return result
    except Exception as e:
        import traceback
        logging.error(f"Error in run_single_backtest for {strategy_name}: {e}")
        return {"error": str(e), "traceback": traceback.format_exc(), 
                "strategy": strategy_name, "params": params}
        
    return None


class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.results = []
        
    def run_full_sweep(self, symbol, timeframe, days=365):
        """Test ALL strategies with multiple SL/TP combinations"""
        start_time = datetime.now()
        
        # ==========================================
        # 1. DYNAMIC DAYS REDUCTION (Speed Hack)
        # ==========================================
        tf_int = int(timeframe)
        if tf_int <= 3:
            days = min(days, 60)  # 1m, 2m, 3m only fetch 60 days to save time
            console.print(f"[yellow]Timeframe {timeframe}m is very dense. Limiting to {days} days for speed.[/yellow]")
        elif tf_int <= 10:
            days = min(days, 120) # 5m, 10m limit to 120 days
            
        # ==========================================
        # 2. SMART CACHE SCAN
        # ==========================================
        console.print(f"[cyan]Scanning ./data folder for {symbol} {timeframe}m data...[/cyan]")
        safe_symbol = symbol.replace(":", "_")
        
        matching_files = []
        for root, dirs, files in os.walk("./data"):
            for file in files:
                if file.startswith(safe_symbol) and f"_{timeframe}m_" in file and file.endswith(".csv"):
                    matching_files.append(os.path.join(root, file))
        
        df = None
        if matching_files:
            def get_days_from_filename(filepath):
                match = re.search(r'_(\d+)d\.csv$', filepath)
                return int(match.group(1)) if match else 0
                
            matching_files.sort(key=get_days_from_filename, reverse=True)
            cache_file = matching_files[0]
            file_days = get_days_from_filename(cache_file)
            
            console.print(f"[bold green]✓ Found cached data: {cache_file} ({file_days} days)[/bold green]")
            try:
                # df = pd.read_csv(cache_file, index_col="datetime", parse_dates=True)
                pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
                
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
                df = None
                
        if df is None:
            console.print(f"[yellow]No local cache found. Fetching {days} days from API...[/yellow]")
            df = self.data_fetcher.get_historical_data(symbol, timeframe, days)

        # ==========================================
        # 3. PRE-CALCULATE SIGNALS (Massive Speedup)
        # ==========================================
        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
        # console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
        strategies_to_test = [
            "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
            "supertrend_macd", "rsi_reversal", "bollinger_squeeze", 
            "vwap_bounce", "macd_crossover", "supertrend_only", 
            "liquidity", "smc", "imbalance", "liquidity_grab", 
            "order_block", "fvg", "session_levels", "composite_smc",
            "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
            "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break"
        ]
        
        param_grid = {
            'sl_mult': [1.0, 1.5, 2.0],
            'tp_mult': [1.0, 2.0, 3.0]
        }
        
        keys = param_grid.keys()
        values = param_grid.values()
        combinations = list(itertools.product(*values))
        
        tasks = []
        for strategy_name in strategies_to_test:
            try:
                # Generate signals ONLY ONCE per strategy
                strat = Strategy(strategy_name)
                df_signals = strat.generate_signals(df.copy())
                df_signals['atr'] = strat.calculate_atr(df_signals)
                
                # OPTIMIZATION: Keep only essential columns to speed up multiprocessing memory transfer
                essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
                df_signals = df_signals[cols_to_keep]
                
                # Create tasks for each SL/TP combo
                for combo in combinations:
                    params = dict(zip(keys, combo))
                    tasks.append((df_signals, symbol, strategy_name, params))
                    
            except Exception as e:
                console.print(f"[red]Error preparing {strategy_name}: {e}[/red]")

        total_tests = len(tasks)
        console.print(f"[yellow]Running {total_tests} backtests using Multicore CPU...[/yellow]\n")
        
        # ==========================================
        # 4. RUN BACKTESTS IN PARALLEL (Multicore)
        # ==========================================
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Backtesting...", total=total_tests)
            
            # Use ProcessPoolExecutor to use all CPU cores
            max_workers = max(1, (os.cpu_count() or 2) - 1)
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # with ProcessPoolExecutor() as executor:
                futures = {executor.submit(run_single_backtest, task): task for task in tasks}
                
                try:
                    result = future.result(timeout=300)
                except Exception as e:
                    console.print(f"[red]Worker failed: {e}[/red]")
                    result = None
                    if result:
                        result["timeframe"] = timeframe
                        self.results.append(result)
                        
                    progress.advance(task_id)

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

        df = pd.DataFrame(self.results)
        
        profitable = df[df['profit_factor'] > 1.1].copy()
        
        if profitable.empty:
            console.print("[bold red]No profitable setups found across all strategies![/bold red]")
            df = df.sort_values(by='profit_factor', ascending=False).head(10)
        else:
            df = profitable.sort_values(by=['profit_factor', 'return_pct'], ascending=[False, False]).head(15)
        
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
            # pf_str = f"[bold green]{row['profit_factor']:.2f}[/bold green]" if row['profit_factor'] > 1.2 else f"{row['profit_factor']:.2f}"
            pnl_str = f"[green]+{row['total_pnl']:,.0f}[/green]" if row['total_pnl'] > 0 else f"[red]{row['total_pnl']:,.0f}[/red]"
            wr_str = f"{row['win_rate']:.2f}%"
            
            table.add_row(
                f"{rank}",
                row['strategy'],
                row['timeframe'],
                f"SL: {row['params_tested'].get('sl_mult')}x / TP: {row['params_tested'].get('tp_mult')}x",
                f"{row['total_trades']}",
                wr_str,
                pf_str,
                f"{row['max_drawdown_pct']:.2f}%",
                pnl_str
            )
            
        console.print(table)
        
        best = df.iloc[0]
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  strategy: \"{best['strategy']}\"\n"
            f"  resolution: \"{best['timeframe']}\"\n"
            f"  sl_atr_multiplier: {best['params_tested'].get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best['params_tested'].get('tp_mult')}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))

if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)
    
    # ==========================================
    # DEFINE MULTIPLE TIMEFRAMES TO TEST
    # ==========================================
    timeframes_to_test = ["1","2","3","5","10","15","60","120","180","240"]
    
    for tf in timeframes_to_test:
        console.print(f"\n[bold blue]{'='*50}[/bold blue]")
        console.print(f"[bold blue]STARTING SWEEP FOR {tf}m TIMEFRAME[/bold blue]")
        console.print(f"[bold blue]{'='*50}[/bold blue]")
        
        agent.run_full_sweep(
            symbol="NSE:NIFTYBANK-INDEX",
            timeframe=tf,
            days=365
        )
    
    console.print(f"\n[bold magenta]Generating Consolidated Report for all timeframes...[/bold magenta]")
    agent.generate_report()