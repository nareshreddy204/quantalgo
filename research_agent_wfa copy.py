# research_agent_wfa.py
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
from strategy_copy1 import Strategy
from backtester_enhanced import EnhancedBacktester

# Logging setup
log_filename = f"research_wfa_{datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_filename, delay=True)]
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
# WORKER FUNCTION FOR WFA MULTIPROCESSING
# ==========================================
def run_strategy_batch_wfa(args):
    """Runs backtest on BOTH Train and Test sets to check for overfitting"""
    df_signals, symbol, strategy_name, combos, min_trades, train_ratio = args
    
    backtester = EnhancedBacktester()
    results = []
    
    # Split data chronologically
    split_idx = int(len(df_signals) * train_ratio)
    df_train = df_signals.iloc[:split_idx].copy()
    df_test = df_signals.iloc[split_idx:].copy()
    
    for params in combos:
        try:
            # 1. Run on Train Set
            res_train = backtester.run_backtest(
                df=df_train.copy(),
                symbol=symbol,
                strategy_name=strategy_name,  
                params=params,
                sl_multiplier=params['sl_mult'],
                tp_multiplier=params['tp_mult'],
                use_atr_sl=True,
                use_time_filter=True,
                use_trend_filter=True
            )
            
            # Only proceed if strategy actually traded enough in Train set
            if res_train and res_train.get("total_trades", 0) > min_trades:
                
                # 2. Run on Test Set (Unseen Data)
                res_test = backtester.run_backtest(
                    df=df_test.copy(),
                    symbol=symbol,
                    strategy_name=strategy_name,  
                    params=params,
                    sl_multiplier=params['sl_mult'],
                    tp_multiplier=params['tp_mult'],
                    use_atr_sl=True,
                    use_time_filter=True,
                    use_trend_filter=True
                )
                
                test_trades = res_test.get("total_trades", 0) if res_test else 0
                test_pf = res_test.get("profit_factor", 0.0) if res_test else 0.0
                test_pnl = res_test.get("total_pnl", 0.0) if res_test else 0.0
                
                # Verdict logic
                verdict = "❌ Overfit"
                if test_trades >= 5: # Must have at least 5 trades in test set to be valid
                    if test_pf >= 1.0 and test_pnl > 0:
                        verdict = "✅ Robust"
                    elif test_pf >= 0.8 and test_pnl > -50:
                        verdict = "⚠️ Degraded"
                
                combined_result = {
                    "strategy": strategy_name,
                    "params_tested": params,
                    "train_trades": res_train.get("total_trades", 0),
                    "train_pf": res_train.get("profit_factor", 0.0),
                    "train_pnl": res_train.get("total_pnl", 0.0),
                    "test_trades": test_trades,
                    "test_pf": test_pf,
                    "test_pnl": test_pnl,
                    "verdict": verdict
                }
                results.append(combined_result)
                
        except Exception as e:
            import traceback
            logging.error(f"Error in WFA for {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}")
            
    return results


class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.results = []
        
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
        
    def run_full_sweep(self, symbol, timeframe, days=180, train_ratio=0.7):
        start_time = datetime.now()
        
        # Smart Cache Scan
                # ---- REPLACEMENT FOR SMART CACHE SCAN ----
        options_folder = r"C:\Users\bnare\Desktop\new way\data\otpion"
        console.print(f"[cyan]Scanning for options data in: {options_folder}...[/cyan]")
        
        # Clean the symbol to match Windows file naming conventions (remove NSE: and colons)
        safe_symbol = symbol.replace("NSE:", "").replace(":", "_")
        target_file = None
        
        if os.path.exists(options_folder):
            files = [f for f in os.listdir(options_folder) if f.endswith('.csv')]
            
            # 1. Try to find an exact match for the symbol in the filename
            matching_files = [f for f in files if safe_symbol in f]
            
            if matching_files:
                # If multiple files exist, pick the one that matches the timeframe
                # e.g., we want "1m" but not "15m"
                tf_matches = [f for f in matching_files if f"{timeframe}m" in f]
                if tf_matches:
                    target_file = os.path.join(options_folder, tf_matches[0])
                else:
                    target_file = os.path.join(options_folder, matching_files[0])
            else:
                console.print(f"[yellow]Symbol {safe_symbol} not found in folder. Using first available file.[/yellow]")
                if files:
                    target_file = os.path.join(options_folder, files[0])
        
        df = None
        if target_file and os.path.exists(target_file):
            console.print(f"[bold green]✓ Found options data: {os.path.basename(target_file)}[/bold green]")
            try:
                df = pd.read_csv(target_file, parse_dates=["datetime"])
                
                # CRITICAL FIX FOR OPTIONS DATA:
                flat_candles = (df['volume'] == 0) & (df['high'] == df['low'])
                removed_count = flat_candles.sum()
                if removed_count > 0:
                    console.print(f"[dim]Cleaning data: Removing {removed_count} flat/illiquid candles.[/dim]")
                    df = df[~flat_candles].copy()
                
                df.set_index("datetime", inplace=True)
            except Exception as e:
                console.print(f"[red]Error reading CSV: {e}[/red]")
                df = None
                
        if df is None or df.empty:
            console.print(f"[red]No valid data available. Skipping.[/red]")
            return
            
        console.print(f"[cyan]Loaded {len(df)} valid candles. Train/Test Split: {int(train_ratio*100)}/{int((1-train_ratio)*100)}[/cyan]")
        # ---- END OF REPLACEMENT ----
        # Parameter Grids
        ema_grid = {'fast_ema': [8, 9, 13, 21], 'slow_ema': [34, 21, 55, 89]}
        sltp_grid = {'sl_mult': [1.0, 1.5, 2.0], 'tp_mult': [1.5, 2.0, 3.0]}
        
        sltp_combos = [dict(zip(sltp_grid.keys(), combo)) for combo in itertools.product(*sltp_grid.values())]
        ema_combos = [dict(zip(ema_grid.keys(), combo)) for combo in itertools.product(*ema_grid.values()) if combo[0] < combo[1]]
        
        min_trades = max(10, len(df) // 1500) # Slightly higher bar for WFA
        console.print(f"[dim]Minimum trades required in Train set: {min_trades}[/dim]")
        
        tasks = []
        for strategy_name, strat in self.strategies.items():
            try:
                if strategy_name in self.ema_strategies:
                    for ema_combo in ema_combos:
                        strat.params['fast_ema'] = ema_combo['fast_ema']
                        strat.params['slow_ema'] = ema_combo['slow_ema']
                        
                        df_signals = strat.generate_signals(df.copy())
                        df_signals['atr'] = strat.calculate_atr(df_signals)
                        
                        essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                        ema_cols = [c for c in df_signals.columns if c.startswith('ema_')]
                        cols_to_keep = [c for c in essential_cols + ema_cols if c in df_signals.columns]
                        df_signals = df_signals[cols_to_keep]
                        
                        task_combos = [{**ema_combo, **sltp} for sltp in sltp_combos]
                        tasks.append((df_signals, symbol, strategy_name, task_combos, min_trades, train_ratio))
                else:
                    strat.params.pop('fast_ema', None)
                    strat.params.pop('slow_ema', None)
                    
                    df_signals = strat.generate_signals(df.copy())
                    df_signals['atr'] = strat.calculate_atr(df_signals)
                    
                    essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                    cols_to_keep = [c for c in essential_cols if c in df_signals.columns]
                    df_signals = df_signals[cols_to_keep]
                    
                    tasks.append((df_signals, symbol, strategy_name, sltp_combos, min_trades, train_ratio))
                    
            except Exception as e:
                console.print(f"[red]Error preparing {strategy_name}: {e}[/red]")

        total_tests = len(tasks)
        console.print(f"[yellow]Running {total_tests} WFA strategy batches using Multicore CPU...[/yellow]\n")
        
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(), console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Backtesting WFA...", total=total_tests)
            
            max_workers = max(1, (os.cpu_count() or 2) - 1)
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run_strategy_batch_wfa, task) for task in tasks]
                
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
            output_dir = datetime.now().strftime("%Y-%m-%d_WFA")
            os.makedirs(output_dir, exist_ok=True)
            file_path = os.path.join(output_dir, f"results_wfa_{timeframe}m.csv")
            pd.DataFrame(self.results).to_csv(file_path, index=False)
            console.print(f"[dim]Saved checkpoint to {file_path}[/dim]")

        end_time = datetime.now()
        elapsed = end_time - start_time
        total_seconds = int(elapsed.total_seconds())
        console.print(f"[bold green]WFA Sweep Complete in {total_seconds // 60}m {total_seconds % 60}s![/bold green]\n")

    def generate_report(self):
        if not self.results:
            console.print("[bold red]No trades were taken by ANY strategy.[/bold red]")
            return

        df = pd.DataFrame(self.results)
        
        # Filter for strategies that were profitable in Train
        df = df[df['train_pf'] > 1.1].copy()
        
        if df.empty:
            console.print("[bold red]No profitable setups found in the Train set![/bold red]")
            return

        # Sort by Test PF descending to find the best forward-tested strategy
        df = df.sort_values(by=['test_pf', 'test_pnl'], ascending=[False, False]).head(20)
        
        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT: WALK-FORWARD ANALYSIS\nTrain vs Unseen Test Data[/bold white]",
            title="🤖 KRONOS QUANT RESEARCH", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Robustness Report", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Strategy", style="bold cyan", min_width=20)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Params", style="dim white", min_width=25)
        
        table.add_column("Tr Trades", justify="center", width=9)
        table.add_column("Tr PF", justify="right", width=7)
        table.add_column("Tr P&L", justify="right", width=10)
        
        table.add_column("Te Trades", justify="center", width=9)
        table.add_column("Te PF", justify="right", width=7)
        table.add_column("Te P&L", justify="right", width=10)
        
        table.add_column("Verdict", justify="center", width=12)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            params = row['params_tested']
            sl = params.get('sl_mult', 'N/A')
            tp = params.get('tp_mult', 'N/A')
            fast = params.get('fast_ema', '')
            slow = params.get('slow_ema', '')
            ema_str = f"EMA: {fast}/{slow} | " if fast != '' else ""
            params_str = f"{ema_str}SL: {sl}x / TP: {tp}x"
            
            tr_pf = row['train_pf']
            te_pf = row['test_pf']
            
            tr_pf_str = f"[green]{tr_pf:.2f}[/green]" if tr_pf > 1.2 else f"{tr_pf:.2f}"
            te_pf_str = f"[green]{te_pf:.2f}[/green]" if te_pf > 1.2 else f"[red]{te_pf:.2f}[/red]" if te_pf < 1.0 else f"{te_pf:.2f}"
            
            tr_pnl = row['train_pnl']
            te_pnl = row['test_pnl']
            tr_pnl_str = f"[green]+{tr_pnl:,.0f}[/green]" if tr_pnl > 0 else f"[red]{tr_pnl:,.0f}[/red]"
            te_pnl_str = f"[green]+{te_pnl:,.0f}[/green]" if te_pnl > 0 else f"[red]{te_pnl:,.0f}[/red]"
            
            verdict_str = row['verdict']
            v_color = "green" if "Robust" in verdict_str else "yellow" if "Degraded" in verdict_str else "red"
            
            table.add_row(
                f"{rank}",
                str(row.get('strategy', 'Unknown')),
                str(row.get('timeframe', '')),
                params_str,
                f"{row.get('train_trades', 0)}",
                tr_pf_str, tr_pnl_str,
                f"{row.get('test_trades', 0)}",
                te_pf_str, te_pnl_str,
                f"[{v_color}]{verdict_str}[/{v_color}]"
            )
            
        console.print(table)
        
        # Find the best robust strategy for the Action Item
        robust_df = df[df['verdict'] == "✅ Robust"]
        if not robust_df.empty:
            best = robust_df.iloc[0]
            best_params = best['params_tested']
            fast = best_params.get('fast_ema', '')
            slow = best_params.get('slow_ema', '')
            ema_str = f"\n  fast_ema: {fast}\n  slow_ema: {slow}" if fast != '' else ""
            
            console.print(Panel(
                f"[bold green]ACTION ITEM:[/bold green] This strategy survived Unseen Data! Update config.yaml:\n\n"
                f"  strategy: \"{best.get('strategy')}\"\n"
                f"  resolution: \"{best.get('timeframe')}\"{ema_str}\n"
                f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
                f"  tp_atr_multiplier: {best_params.get('tp_mult')}\n\n"
                f"[dim]Train PF: {best['train_pf']:.2f} | Test PF: {best['test_pf']:.2f}[/dim]",
                title="🚀 BEST ROBUST SETUP FOUND", box=box.ROUNDED
            ))
        else:
            console.print(Panel(
                "[bold red]WARNING:[/bold red] No strategies passed the Robustness check (✅ Robust).\n"
                "All strategies degraded or failed on unseen data. Consider tweaking indicator parameters or SL/TP multipliers.",
                title="⚠️ OVERFITTING DETECTED", box=box.ROUNDED
            ))

if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)
    
    timeframes_to_test = ["1"]
    
    for tf in timeframes_to_test:
        console.print(f"\n[bold blue]{'='*50}[/bold blue]")
        console.print(f"[bold blue]STARTING WFA SWEEP FOR {tf}m TIMEFRAME[/bold blue]")
        console.print(f"[bold blue]{'='*50}[/bold blue]")
        
        agent.run_full_sweep(
            symbol="NSE:BANKNIFTY26JUL57000PE",  # Assuming options data is stored with this symbol
            timeframe=tf,
            days=180,
            train_ratio=0.7  # 70% Train, 30% Test
        )
    
    console.print(f"\n[bold magenta]Generating Walk-Forward Consolidated Report...[/bold magenta]")
    agent.generate_report()