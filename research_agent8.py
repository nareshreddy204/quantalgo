# research_agent8.py
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

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy_copy import Strategy
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
    """This function runs in a separate CPU core, handling all SL/TP combos for a single strategy/EMA setup"""
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
            
            # Dynamic min_trades filter
            if result and result.get("total_trades", 0) > min_trades:
                result["strategy"] = strategy_name
                result["params_tested"] = params
                results.append(result)
                
        except Exception as e:
            import traceback
            logging.error(f"Error in run_strategy_batch for {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}")
            
    return results


class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.results = []
        
        # List strategies that use dynamic EMA parameters
        self.ema_strategies = ["ema_crossover", "smart_ema", "ema_bounce", "macd_crossover", "ema_convergence"]
        
        strategies_to_test = [
            "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
            "supertrend_macd", "rsi_reversal", "bollinger_squeeze", 
            "vwap_bounce", "macd_crossover", "supertrend_only", 
            "liquidity", "smc", "imbalance", "liquidity_grab", 
            "order_block", "fvg", "session_levels", "composite_smc",
            "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
            "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break"
        ]
        
        self.strategies = {}
        for name in strategies_to_test:
            try:
                self.strategies[name] = Strategy(name)
            except Exception as e:
                console.print(f"[red]Failed to initialize {name}: {e}[/red]")
        
    def run_full_sweep(self, symbol, timeframe, days=180):
        """Test ALL strategies with multiple EMA and SL/TP combinations"""
        start_time = datetime.now()
        
        # ==========================================
        # REMOVED THE DYNAMIC DAYS REDUCTION HACK
        # ==========================================
        # You can now test 180 days on 1m without it reverting to 60 days.
            
        # Smart Cache Scan
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
            def get_days_from_filename(filepath):
                match = pattern.search(os.path.basename(filepath))
                return int(match.group(1)) if match else 0
            
            # ==========================================
            # FIX: PRIORITIZE EXACT MATCH FOR REQUESTED DAYS
            # ==========================================
            # 1. Look for an exact match first (e.g., 180d)
            exact_matches = [f for f in matching_files if get_days_from_filename(f) == days]
            
            if exact_matches:
                cache_file = exact_matches[0]
            else:
                # 2. If no exact match, find the largest file that is <= requested days
                valid_files = [f for f in matching_files if get_days_from_filename(f) <= days]
                if valid_files:
                    valid_files.sort(key=get_days_from_filename, reverse=True)
                    cache_file = valid_files[0]
                else:
                    # 3. Fallback: just use the smallest available file
                    matching_files.sort(key=get_days_from_filename)
                    cache_file = matching_files[0]
                    
            file_days = get_days_from_filename(cache_file)
            
            console.print(f"[bold green]✓ Found cached data: {cache_file} ({file_days} days)[/bold green]")
            try:
                df = pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
                df = None
                
        if df is None:
            console.print(f"[yellow]No local cache found. Fetching {days} days from API...[/yellow]")
            df = self.data_fetcher.get_historical_data(symbol, timeframe, days)

        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
            
        console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
        # Define parameter grids separately for efficiency
        ema_grid = {
            'fast_ema': [6, 8, 9, 13, 20],
            'slow_ema': [21, 26, 34, 48, 50]
        }
        sltp_grid = {
            'sl_mult': [1.0, 1.5, 2.0],
            'tp_mult': [1.5, 2.0, 3.0]
        }
        
        # Generate SL/TP combinations
        sltp_keys = sltp_grid.keys()
        sltp_values = sltp_grid.values()
        sltp_combos = [dict(zip(sltp_keys, combo)) for combo in itertools.product(*sltp_values)]
        
        # Generate EMA combinations where fast_ema < slow_ema
        ema_keys = ema_grid.keys()
        ema_values = ema_grid.values()
        ema_combos = [
            dict(zip(ema_keys, combo)) for combo in itertools.product(*ema_values) 
            if combo[0] < combo[1]
        ]
        
        min_trades = max(5, len(df) // 1000)
        console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")
        
        
        tasks = []
        for strategy_name, strat in self.strategies.items():
            try:
                if strategy_name in self.ema_strategies:
                    # For EMA strategies: Generate signals for EACH EMA combination
                    for ema_combo in ema_combos:
                        strat.params['fast_ema'] = ema_combo['fast_ema']
                        strat.params['slow_ema'] = ema_combo['slow_ema']
                        
                        df_signals = strat.generate_signals(df.copy())
                        df_signals['atr'] = strat.calculate_atr(df_signals)
                        
                        # FIX: Keep essential columns AND any dynamically generated ema_* columns
                        essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                        ema_cols = [c for c in df_signals.columns if c.startswith('ema_')]
                        cols_to_keep = [c for c in essential_cols + ema_cols if c in df_signals.columns]
                        
                        df_signals = df_signals[cols_to_keep]
                        
                        # Append EMA params to SL/TP combos so worker/backtester has full context
                        task_combos = [{**ema_combo, **sltp} for sltp in sltp_combos]
                        
                        task_name = f"{strategy_name}_{ema_combo['fast_ema']}_{ema_combo['slow_ema']}"
                        tasks.append((df_signals, symbol, task_name, task_combos, min_trades))
                else:
                    # For non-EMA strategies: Generate signals ONCE, test all SL/TP combos
                    strat.params.pop('fast_ema', None)
                    strat.params.pop('slow_ema', None)
                    
                    df_signals = strat.generate_signals(df.copy())
                    df_signals['atr'] = strat.calculate_atr(df_signals)
                    
                    essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                    cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
                    df_signals = df_signals[cols_to_keep]
                    
                    tasks.append((df_signals, symbol, strategy_name, sltp_combos, min_trades))
                    
            except Exception as e:
                console.print(f"[red]Error preparing {strategy_name}: {e}[/red]")

        total_tests = len(tasks)
        console.print(f"[yellow]Running {total_tests} strategy batches using Multicore CPU...[/yellow]\n")
        
        # RUN BACKTESTS IN PARALLEL
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
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run_strategy_batch, task) for task in tasks]
                
                for future in as_completed(futures):
                    try:
                        batch_results = future.result(timeout=600)
                        if batch_results:
                            for res in batch_results:
                                res["timeframe"] = timeframe
                                self.results.append(res)
                    except Exception as e:
                        console.print(f"[red]Worker failed: {e}[/red]")
                        
                    progress.advance(task_id)

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

        valid_results = [r for r in self.results if 'profit_factor' in r]
        if not valid_results:
            console.print("[bold red]No valid backtest results found.[/bold red]")
            return
            
        df = pd.DataFrame(valid_results)
        
        profitable = df[df['profit_factor'] > 1.1].copy()
        
        if profitable.empty:
            console.print("[bold red]No profitable setups found across all strategies![/bold red]")
            df = df.sort_values(by='profit_factor', ascending=False).head(15)
        else:
            df = profitable.sort_values(by=['profit_factor', 'return_pct'], ascending=[False, False]).head(15)
        
        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT REPORT\nTop Performing Strategies[/bold white]",
            title="🤖 KRONOS QUANT RESEARCH", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Best Setups Found", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Strategy", style="bold cyan", min_width=25)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Params (EMA / SL / TP)", style="dim white", min_width=30)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            pf = row['profit_factor']
            pf = min(pf, 999.0) if pd.notna(pf) else 0.0
            pf_str = f"[bold green]{pf:.2f}[/bold green]" if pf > 1.2 else f"{pf:.2f}"
            
            pnl = row['total_pnl']
            pnl_str = f"[green]+{pnl:,.0f}[/green]" if pnl > 0 else f"[red]{pnl:,.0f}[/red]"
            
            wr = row['win_rate']
            wr_str = f"{wr:.2f}%" if pd.notna(wr) else "N/A"
            
            # Extract params safely
            params = row['params_tested']
            sl = params.get('sl_mult', 'N/A')
            tp = params.get('tp_mult', 'N/A')
            fast = params.get('fast_ema', '')
            slow = params.get('slow_ema', '')
            
            ema_str = f"EMA: {fast}/{slow} | " if fast != '' else ""
            params_str = f"{ema_str}SL: {sl}x / TP: {tp}x"
            
            table.add_row(
                f"{rank}",
                str(row.get('strategy', 'Unknown')),
                str(row.get('timeframe', '')),
                params_str,
                f"{row.get('total_trades', 0)}",
                wr_str,
                pf_str,
                f"{row.get('max_drawdown_pct', 0):.2f}%",
                pnl_str
            )
            
        console.print(table)
        
        best = df.iloc[0]
        best_params = best['params_tested']
        fast = best_params.get('fast_ema', '')
        slow = best_params.get('slow_ema', '')
        ema_str = f"\n  fast_ema: {fast}\n  slow_ema: {slow}" if fast != '' else ""
        
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  strategy: \"{best.get('strategy')}\"\n"
            f"  resolution: \"{best.get('timeframe')}\"{ema_str}\n"
            f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best_params.get('tp_mult')}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))

if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)
    
    # timeframes_to_test = ["10","15","60","120","180","240"]
    timeframes_to_test = ["1", "3" ]
    
    for tf in timeframes_to_test:
        console.print(f"\n[bold blue]{'='*50}[/bold blue]")
        console.print(f"[bold blue]STARTING SWEEP FOR {tf}m TIMEFRAME[/bold blue]")
        console.print(f"[bold blue]{'='*50}[/bold blue]")
        
        agent.run_full_sweep(
            symbol="NSE:NIFTYBANK-INDEX",
            timeframe=tf,
            days=180  # Explicitly requesting 180 days
        )
    
    console.print(f"\n[bold magenta]Generating Consolidated Report for all timeframes...[/bold magenta]")
    agent.generate_report()