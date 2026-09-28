# run_backtest.py
import yaml
import os
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from backtester import Backtester

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

def print_trade_log(results):
    """Prints the trade log for a specific result set"""
    trades_table = Table(title="Trade Log (Last 10 Trades)", box=box.ROUNDED, show_lines=True, title_style="bold magenta")
    trades_table.add_column("Entry Date", style="dim", width=16, no_wrap=True)
    trades_table.add_column("Exit Date", style="dim", width=16, no_wrap=True)
    trades_table.add_column("Entry", justify="right", width=9)
    trades_table.add_column("Exit", justify="right", width=9)
    trades_table.add_column("Pts", justify="right", width=7)
    trades_table.add_column("P&L", justify="right", width=10)
    trades_table.add_column("Reason", justify="center", width=10, no_wrap=True) # Fixed wrapping
    
    for _, row in results["trades_df"].tail(10).iterrows():
        pnl_color = "bold green" if row["pnl"] >= 0 else "bold red"
        pts_color = "green" if row["points"] >= 0 else "red"
        reason_color = "red" if row["reason"] == "SL Hit" else ("green" if row["reason"] == "TP Hit" else "yellow")
        
        trades_table.add_row(
            row["entry_date"],
            row["exit_date"],
            f"{row['entry_price']:.2f}",
            f"{row['exit_price']:.2f}",
            f"[{pts_color}]{row['points']:+.2f}[/{pts_color}]",
            f"[{pnl_color}]{row['pnl']}[/{pnl_color}]",
            f"[{reason_color}]{row['reason']}[/{reason_color}]"
        )
        
    console.print(trades_table)

if __name__ == "__main__":
    config = load_config()
    
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    data_fetcher = DataFetcher(auth.fyers)
    backtester = Backtester(auth.fyers)
    
    # ==========================================
    # BACKTEST SETTINGS
    # ==========================================
    test_symbol = "NSE:NIFTYBANK-INDEX"
    test_resolution = "15"
    test_days = 180
    initial_capital = 100000
    sl_pct = 0.02
    tp_pct = 0.04
    
    strategies_to_test = [
        {"name": "ema_crossover", "params": {}},
        {"name": "supertrend_macd", "params": {}},
        {"name": "liquidity_swings", "params": {"length": 14, "area": "Wick Extremity"}},
        {"name": "liquidity_swings", "params": {"length": 10, "area": "Full Range"}},
        {"name": "smart_ema", "params": {}},
    ]
    # ==========================================

    console.clear()
    console.print(f"[bold cyan]Fetching {test_days} days of {test_resolution}m data for {test_symbol} ONCE...[/bold cyan]")
    
    base_df = data_fetcher.get_historical_data(
        symbol=test_symbol,
        resolution=test_resolution,
        days=test_days
    )
    
    if base_df is None:
        console.print("[bold red]Failed to fetch data. Exiting.[/bold red]")
        exit()
        
    console.print(f"[bold green]Successfully fetched {len(base_df)} candles. Testing strategies...[/bold green]\n")
    
    all_results = []
    
    for i, strat in enumerate(strategies_to_test):
        strat_display_name = strat["name"]
        if strat.get("params"):
            strat_display_name += f" ({strat['params'].get('length', '')} {strat['params'].get('area', '')})"
            
        console.print(f"[bold yellow][{i+1}/{len(strategies_to_test)}] Testing {strat_display_name}...[/bold yellow]")
        
        results = backtester.run_from_dataframe(
            df=base_df,
            symbol=test_symbol,
            strategy_name=strat["name"],
            params=strat["params"],
            initial_capital=initial_capital,
            sl_pct=sl_pct,
            tp_pct=tp_pct
        )
        
        if results:
            results["display_name"] = strat_display_name
            all_results.append(results)
        else:
            all_results.append({
                "display_name": strat_display_name,
                "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "max_drawdown_pct": 0, "total_points": 0, "total_pnl": 0,
                "return_pct": 0, "trades_df": None
            })

    # --- SORT BY BEST PERFORMANCE ---
    all_results.sort(key=lambda x: (x.get('profit_factor', 0), x.get('return_pct', 0)), reverse=True)
    
    # --- RENDER COMPARISON TABLE ---
    console.clear()
    console.print(Panel(
        f"[bold white]Symbol: {test_symbol}  |  Resolution: {test_resolution}m  |  Period: {test_days} days\n"
        f"Risk: {sl_pct*100:.0f}% SL / {tp_pct*100:.0f}% TP  |  Capital: {initial_capital:,.0f}[/bold white]", 
        title="STRATEGY COMPARISON BACKTEST", box=box.DOUBLE
    ))
    
    comp_table = Table(title="Performance Ranking (Best to Worst)", box=box.HEAVY, title_style="bold cyan", show_lines=True)
    comp_table.add_column("Rank", style="bold", justify="center", width=5)
    comp_table.add_column("Strategy", style="bold", min_width=35, no_wrap=True) # FIXED MISSING COLUMN
    comp_table.add_column("Trades", justify="center", width=7)
    comp_table.add_column("Win %", justify="right", width=8)
    comp_table.add_column("Prof Factor", justify="right", width=12)
    comp_table.add_column("Max DD", justify="right", width=8)
    comp_table.add_column("Points", justify="right", width=9)
    comp_table.add_column("Net P&L", justify="right", width=12)
    comp_table.add_column("Return %", justify="right", width=10)
    
    for rank, res in enumerate(all_results, 1):
        is_winner = (rank == 1 and res["total_trades"] > 0)
        
        pnl = res["total_pnl"]
        ret = res["return_pct"]
        pts = res["total_points"]
        
        # Formatting strings cleanly
        pnl_str = f"{pnl:,.0f}"
        ret_str = f"{ret:.2f}%"
        pts_str = f"{pts:+.2f}"
        
        # Apply colors
        if is_winner:
            name_str = f"[bold green]🥇 {res['display_name']}[/bold green]"
            pnl_str = f"[bold green]{pnl_str}[/bold green]"
            ret_str = f"[bold green]{ret_str}[/bold green]"
            pts_str = f"[bold green]{pts_str}[/bold green]"
        else:
            name_str = res['display_name']
            if pnl < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
                ret_str = f"[red]{ret_str}[/red]"
                pts_str = f"[red]{pts_str}[/red]"
            else:
                pnl_str = f"[green]{pnl_str}[/green]"
                ret_str = f"[green]{ret_str}[/green]"
                pts_str = f"[green]{pts_str}[/green]"
            
        dd_str = f"[red]{res['max_drawdown_pct']:.2f}%[/red]" if res['max_drawdown_pct'] > 5 else f"{res['max_drawdown_pct']:.2f}%"
        
        comp_table.add_row(
            f"{rank}",
            name_str,
            f"{res['total_trades']}",
            f"{res['win_rate']:.2f}%",
            f"{res['profit_factor']:.2f}",
            dd_str,
            pts_str,
            pnl_str,
            ret_str
        )
        
    console.print(comp_table)
    
    # --- PRINT TRADE LOG FOR THE WINNER ---
    if all_results and all_results[0]["trades_df"] is not None:
        console.print(f"\n[bold green]Showing Trade Log for Winner: {all_results[0]['display_name']}[/bold green]")
        print_trade_log(all_results[0])
    else:
        console.print("[bold red]No strategies generated any trades.[/bold red]")