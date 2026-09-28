# research_agent.py
import yaml
import os
import time
import itertools
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy import Strategy
from backtester_enhanced import EnhancedBacktester

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
        self.backtester = EnhancedBacktester()
        self.results = []
        
    def run_experiment(self, symbol, timeframe, strategy_name, param_grid, days=180):
        """Fetch data and run a grid search for a specific strategy"""
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
        console.print(f"[yellow]Testing {total_combos} parameter combinations for {strategy_name}...[/yellow]")
        
        for i, combo in enumerate(combinations, 1):
            params = dict(zip(keys, combo))
            
            # Run the backtest
            result = self.backtester.run_backtest(
                df=df.copy(),
                symbol=symbol,
                strategy_name=strategy_name,
                params=params,
                initial_capital=100000,
                sl_multiplier=params.get('sl_mult', 1.5),
                tp_multiplier=params.get('tp_mult', 3.0),
                use_atr_sl=True,
                use_time_filter=True,
                use_trend_filter=True
            )
            
            if result and result.get("total_trades", 0) > 10: # Ignore strategies with no trades
                result["timeframe"] = timeframe
                result["params_tested"] = params
                self.results.append(result)
                
            # Print progress
            if i % 10 == 0 or i == total_combos:
                console.print(f"  Progress: {i}/{total_combos} tested...", end="\r")
                
        console.print(" " * 60, end="\r") # Clear line

    def generate_report(self):
        """Filter, sort, and display the best performing setups"""
        if not self.results:
            console.print("[bold red]No trades were taken by any strategy. Check your data.[/bold red]")
            return

        # Convert to DataFrame for easy filtering
        df = pd.DataFrame(self.results)
        
        # Sort ALL results by Profit Factor to see the best ones
        df = df.sort_values(by=['profit_factor', 'return_pct'], ascending=[False, False]).head(10)
        
        # Render Rich Table
        console.clear()
        console.print(Panel(
            "[bold white]RESEARCH AGENT REPORT\nTop 10 Setups Found (Ranked by Profit Factor)[/bold white]",
            title="🤖 KRONOS RESEARCH AGENT", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Backtest Results", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Symbol", style="bold white", min_width=18)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Strategy", style="bold cyan", min_width=18)
        table.add_column("Params", style="dim white", min_width=30)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            # Color code the results so you know which ones are actually good
            pf_str = f"[bold green]{row['profit_factor']:.2f}[/bold green]" if row['profit_factor'] > 1.1 else f"[red]{row['profit_factor']:.2f}[/red]"
            pnl_str = f"[green]+{row['total_pnl']:,.0f}[/green]" if row['total_pnl'] > 0 else f"[red]{row['total_pnl']:,.0f}[/red]"
            
            table.add_row(
                f"{rank}",
                row['symbol'],
                row['timeframe'],
                row['strategy'],
                str(row['params_tested']),
                f"{row['total_trades']}",
                f"{row['win_rate']:.2f}%",
                pf_str,
                f"{row['max_drawdown_pct']:.2f}%",
                pnl_str
            )
            
        console.print(table)
        
        # Provide actionable instructions for the #1 setup
        best = df.iloc[0]
        console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  symbol: \"{best['symbol']}\"\n"
            f"  strategy: \"{best['strategy']}\"\n"
            f"  resolution: \"{best['timeframe']}\"\n"
            f"  params: {best['params_tested']}\n"
            f"  sl_atr_multiplier: {best['params_tested'].get('sl_mult', 1.5)}\n"
            f"  tp_atr_multiplier: {best['params_tested'].get('tp_mult', 3.0)}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))

if __name__ == "__main__":
    config = load_config()
    agent = ResearchAgent(config)
    
    # ==========================================
    # DEFINE WHAT THE AGENT SHOULD RESEARCH
    # ==========================================
    
    # 1. Define the Grid Search Parameters
    # The agent will test every combination of these settings
    param_grid = {
        'sl_mult': [1.0, 1.5, 2.0],       # Test tight, medium, and wide stop losses
        'tp_mult': [0.5, 1.0, 1.5, 3.0],  # Test various take profit ratios
    }
    
    # 2. Run Experiments (You can add more symbols/timeframes here)
    # Let's find the best setup for BankNifty 15m using the EMA Bounce strategy
    agent.run_experiment(
        symbol="NSE:NIFTYBANK-INDEX",
        timeframe="5",
        strategy_name="ema_bounce",
        param_grid=param_grid,
        days=100
    )
    
    # Let's also test the Supertrend + MACD strategy on Nifty 5m
    agent.run_experiment(
        symbol="NSE:NIFTY50-INDEX",
        timeframe="5",
        strategy_name="supertrend_macd",
        param_grid=param_grid,
        days=100
    )
    
    # 3. Generate the Final Report
    agent.generate_report()