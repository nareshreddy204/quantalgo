# research_agent3.py
import time
import re
import yaml
import os
import glob
import itertools
import pandas as pd
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy_copy import Strategy
from backtester_enhanced import EnhancedBacktester

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.backtester = EnhancedBacktester()
        self.results = []
        
    def run_full_sweep(self, symbol, timeframe, days=365):
        """Test ALL strategies with multiple SL/TP combinations"""
        start_time = datetime.now()
        
                # ==========================================
        # 1. SMART CACHE SCAN (Scan ALL subfolders)
        # ==========================================
        console.print(f"[cyan]Scanning ./data folder for {symbol} {timeframe}m data...[/cyan]")
        safe_symbol = symbol.replace(":", "_")
        
        matching_files = []
        # Recursively walk through ./data and all subfolders (indices, mcx, etc.)
        for root, dirs, files in os.walk("./data"):
            for file in files:
                # Look for files like NSE_NIFTYBANK-INDEX_5m_365d.csv
                if file.startswith(safe_symbol) and f"_{timeframe}m_" in file and file.endswith(".csv"):
                    matching_files.append(os.path.join(root, file))
        
        df = None
        if matching_files:
            # Extract the number of days from the filenames (e.g., "365d.csv" -> 365)
            def get_days_from_filename(filepath):
                match = re.search(r'_(\d+)d\.csv$', filepath)
                return int(match.group(1)) if match else 0
                
            # Sort files so the one with the MOST days (e.g., 365) is first!
            matching_files.sort(key=get_days_from_filename, reverse=True)
            
            cache_file = matching_files[0]
            file_days = get_days_from_filename(cache_file)
            
            console.print(f"[bold green]✓ Found cached data: {cache_file} ({file_days} days)[/bold green]")
            try:
                df = pd.read_csv(cache_file, index_col="datetime", parse_dates=True)
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
                df = None
                
        # If not found in cache, fetch 365 days from API
        if df is None:
            console.print(f"[yellow]No local cache found. Fetching 365 days from API...[/yellow]")
            df = self.data_fetcher.get_historical_data(symbol, timeframe, 365)

        # ==========================================
        # 2. RUN THE 162 BACKTESTS
        # ==========================================
        console.print(f"[cyan]Loaded {len(df)} candles. Starting backtest sweep...[/cyan]")
        
        strategies_to_test = [
            "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
            "supertrend_macd", "rsi_reversal", "bollinger_squeeze", 
            "vwap_bounce", "macd_crossover", "supertrend_only", 
            "liquidity", "smc", "imbalance", "liquidity_grab", 
            "order_block", "fvg", "session_levels", "composite_smc"
        ]
        
        param_grid = {
            'sl_mult': [1.0, 1.5, 2.0],
            'tp_mult': [1.0, 2.0, 3.0]
        }
        
        keys = param_grid.keys()
        values = param_grid.values()
        combinations = list(itertools.product(*values))
        
        total_tests = len(strategies_to_test) * len(combinations)
        console.print(f"[yellow]Running {total_tests} backtests...[/yellow]\n")
        
        test_count = 0
        for strategy_name in strategies_to_test:
            for combo in combinations:
                test_count += 1
                params = dict(zip(keys, combo))
                
                try:
                    result = self.backtester.run_backtest(
                        df=df.copy(),
                        symbol=symbol,
                        strategy_name=strategy_name,
                        params={},
                        sl_multiplier=params['sl_mult'],
                        tp_multiplier=params['tp_mult'],
                        use_atr_sl=True,
                        use_time_filter=True,
                        use_trend_filter=True
                    )
                    
                    if result and result.get("total_trades", 0) > 5:
                        result["strategy"] = strategy_name
                        result["timeframe"] = timeframe  # <--- CRITICAL: Save the timeframe!
                        result["params_tested"] = params
                        self.results.append(result)
                        
                except Exception as e:
                    pass
                
                console.print(f"  Test {test_count}/{total_tests} | {strategy_name} | SL:{params['sl_mult']} TP:{params['tp_mult']}", end="\r")
                
        end_time = datetime.now()
        elapsed = end_time - start_time
        total_seconds = int(elapsed.total_seconds())
        mins = total_seconds // 60
        secs = total_seconds % 60
        
        console.print(" " * 100, end="\r")
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
            "[bold white]MASTER RESEARCH AGENT REPORT\nTop Performing Strategies (365 Days Data)[/bold white]",
            title="🤖 KRONOS QUANT RESEARCH", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Best Setups Found", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Strategy", style="bold cyan", min_width=20)
        table.add_column("TF", justify="center", width=4)  # <--- ADDED TIMEFRAME COLUMN
        table.add_column("Params (SL / TP)", style="dim white", min_width=20)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            pf_str = f"[bold green]{row['profit_factor']:.2f}[/bold green]" if row['profit_factor'] > 1.2 else f"{row['profit_factor']:.2f}"
            pnl_str = f"[green]+{row['total_pnl']:,.0f}[/green]" if row['total_pnl'] > 0 else f"[red]{row['total_pnl']:,.0f}[/red]"
            wr_str = f"{row['win_rate']:.2f}%"
            
            table.add_row(
                f"{rank}",
                row['strategy'],
                row['timeframe'],  # <--- PRINT TIMEFRAME
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
            f"  resolution: \"{best['timeframe']}\"\n"  # <--- ADDED TIMEFRAME TO ACTION ITEM
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
    timeframes_to_test = ["1","2","3","5","10", "15", "60","120","180","240"]  # 5m, 15m, 1H
    
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