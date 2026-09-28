# research_agent1.py
import yaml
import os
import time
import itertools
import pandas as pd
from datetime import time as dt_time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

# Import your custom High Win Rate Backtester
from run_backtest_optimized1 import HighWinRateBacktester

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

class ResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        # Use the HighWinRateBacktester
        self.backtester = HighWinRateBacktester()
        self.results = []
        
    def run_experiment(self, symbol, timeframe, param_grid, days=365):
        """Fetch data and run a grid search for the Mean-Reversion strategy"""
        console.print(f"[cyan]Fetching {days} days of {timeframe}m data for {symbol}...[/cyan]")
        df = self.data_fetcher.get_historical_data(symbol, timeframe, days)
        
        if df is None or len(df) < 100:
            console.print(f"[red]Failed to fetch sufficient data for {symbol}. Skipping.[/red]")
            return

        # Generate all parameter combinations
        keys = param_grid.keys()
        values = param_grid.values()
        combinations = list(itertools.product(*values))
        
        total_combos = len(combinations)
        console.print(f"[yellow]Testing {total_combos} parameter combinations for Mean-Reversion Pullback...[/yellow]")
        
        for i, combo in enumerate(combinations, 1):
            params = dict(zip(keys, combo))
            
            # Run the backtest using the HighWinRateBacktester
            result = self.backtester.run_backtest(
                df=df.copy(),
                symbol=symbol,
                initial_capital=100000,
                sl_mult=params.get('sl_mult', 1.5),
                tp_mult=params.get('tp_mult', 1.0),
                use_time_filter=True
            )
            
            if result and result.get("total_trades", 0) > 5:
                result["symbol"] = symbol
                result["timeframe"] = timeframe
                result["params_tested"] = params
                self.results.append(result)
                
            console.print(f"  Progress: {i}/{total_combos} tested...", end="\r")
                
        console.print(" " * 60, end="\r")

    def generate_report(self):
        """Filter, sort, and display the best performing setups"""
        if not self.results:
            console.print("[bold red]No trades were taken by any strategy. Check your data.[/bold red]")
            return

        df = pd.DataFrame(self.results)
        
        # Sort ALL results by Profit Factor, then Win Rate
        df = df.sort_values(by=['profit_factor', 'win_rate'], ascending=[False, False]).head(10)
        
        console.clear()
        console.print(Panel(
            "[bold white]RESEARCH AGENT REPORT\nTop 10 Mean-Reversion Setups (365 Days Data)[/bold white]",
            title="🤖 KRONOS RESEARCH AGENT", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Backtest Results", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Symbol", style="bold white", min_width=18)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Params (SL / TP)", style="dim white", min_width=20)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            pf_str = f"[bold green]{row['profit_factor']:.2f}[/bold green]" if row['profit_factor'] > 1.1 else f"[red]{row['profit_factor']:.2f}[/red]"
            pnl_str = f"[green]+{row['total_pnl']:,.0f}[/green]" if row['total_pnl'] > 0 else f"[red]{row['total_pnl']:,.0f}[/red]"
            wr_str = f"[green]{row['win_rate']:.2f}%[/green]" if row['win_rate'] > 55 else f"[yellow]{row['win_rate']:.2f}%[/yellow]"
            
            table.add_row(
                f"{rank}",
                row['symbol'],
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
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml / live script with the following:\n\n"
            f"  symbol: \"{best['symbol']}\"\n"
            f"  resolution: \"{best['timeframe']}\"\n"
            f"  sl_mult: {best['params_tested'].get('sl_mult')}\n"
            f"  tp_mult: {best['params_tested'].get('tp_mult')}\n"
            f"  strategy: Mean-Reversion Pullback (HighWinRateBacktester)",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))

if __name__ == "__main__":
    config = load_config()
    agent = ResearchAgent(config)
    
    # ==========================================
    # DEFINE WHAT THE AGENT SHOULD RESEARCH
    # ==========================================
    param_grid = {
        'sl_mult': [1.5, 2.0, 2.5, 3.0],  # Test various Stop Loss multipliers
        'tp_mult': [0.5, 1.0, 1.5]         # Test various Take Profit multipliers
    }
    
    # Test on Nifty 50 using 5m data for 365 days (matches your downloaded cache!)
    agent.run_experiment(
        symbol="NSE:NIFTY50-INDEX",
        timeframe="5",   # Using 5m because that's what was downloaded in your data_downloader.py
        param_grid=param_grid,
        days=365         # Using the full 365 days!
    )
    
    # You can uncomment this to test BankNifty as well
    # agent.run_experiment(
    #     symbol="NSE:NIFTYBANK-INDEX",
    #     timeframe="15",
    #     param_grid=param_grid,
    #     days=365
    # )
    
    # 3. Generate the Final Report
    agent.generate_report()