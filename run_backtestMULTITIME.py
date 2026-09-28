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
    trades_table = Table(title="Trade Log (Last 10 Trades)", box=box.ROUNDED, show_lines=True, title_style="bold magenta")
    trades_table.add_column("Entry Date", style="dim", width=16, no_wrap=True)
    trades_table.add_column("Exit Date", style="dim", width=16, no_wrap=True)
    trades_table.add_column("Entry", justify="right", width=9)
    trades_table.add_column("Exit", justify="right", width=9)
    trades_table.add_column("Pts", justify="right", width=7)
    trades_table.add_column("P&L", justify="right", width=10)
    trades_table.add_column("Reason", justify="center", width=10, no_wrap=True)
    
    for _, row in results["trades_df"].tail(10).iterrows():
        pnl_color = "bold green" if row["pnl"] >= 0 else "bold red"
        pts_color = "green" if row["points"] >= 0 else "red"
        reason_color = "red" if row["reason"] == "SL Hit" else ("green" if row["reason"] == "TP Hit" else "yellow")
        
        trades_table.add_row(
            row["entry_date"], row["exit_date"],
            f"{row['entry_price']:.2f}", f"{row['exit_price']:.2f}",
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
    # ULTIMATE MATRIX SETTINGS
    # ==========================================
    test_symbol = "NSE:NIFTY50-INDEX"  # Change this to your desired symbol
    initial_capital = 100000
    sl_pct = 0.02
    tp_pct = 0.04
    test_days = 365 # Get 1 year of data for all timeframes
    
    strategies_to_test = [
        # --- CLASSIC STRATEGIES ---
        {"name": "ema_crossover", "params": {}},
        {"name": "smart_ema", "params": {}},
        {"name": "supertrend_macd", "params": {}},
        {"name": "macd_crossover", "params": {}},
        {"name": "supertrend_only", "params": {}},
        {"name": "rsi_reversal", "params": {"oversold": 30, "overbought": 70}},
        {"name": "bollinger_squeeze", "params": {}},
        {"name": "liquidity_swings", "params": {"length": 14, "area": "Wick Extremity"}},
        
        # --- SMART CONCEPTS (SMC) ---
        {"name": "vwap_bounce", "params": {}},
        {"name": "liquidity", "params": {"lookback": 20, "min_touches": 3}},
        {"name": "smc", "params": {"lookback": 20}},
        {"name": "imbalance", "params": {"gap_pct": 0.005, "volume_multiplier": 1.5}},
        {"name": "liquidity_grab", "params": {"lookback": 20}},
        {"name": "order_block", "params": {"min_body_pct": 1.0, "volume_multiplier": 1.5}},
        {"name": "fvg", "params": {"fvg_buffer": 0.001}},
        {"name": "session_levels", "params": {"or_minutes": 15}},
        
        # --- COMPOSITE ---
        {"name": "composite_smc", "params": {"min_confirmations": 2}}
    ]
    
    # TEST ACROSS THESE TIMEFRAMES
    timeframes_to_test = [
        {"resolution": "1", "label": "1 Minute"},
        {"resolution": "5", "label": "5 Minute"},
        {"resolution": "15", "label": "15 Minute"},
        {"resolution": "60", "label": "1 Hour"},
        {"resolution": "D",  "label": "Daily"},
        {"resolution": "W",  "label": "Weekly"},
        {"resolution": "M",  "label": "Monthly"},
    ]
    # ==========================================

    console.clear()
    console.print(Panel(
        f"[bold white]Symbol: {test_symbol}  |  Period: {test_days} days\n"
        f"Risk: {sl_pct*100:.0f}% SL / {tp_pct*100:.0f}% TP  |  Capital: {initial_capital:,.0f}\n"
        f"Testing {len(strategies_to_test)} strategies across {len(timeframes_to_test)} timeframes...", 
        title="MULTI-TIMEFRAME MATRIX BACKTEST", box=box.DOUBLE
    ))
    
    all_results = []
    total_tests = len(timeframes_to_test) * len(strategies_to_test)
    start_time = time.time()  # <-- ADD THIS LINE
    current_test = 0

    # 1. Loop through Timeframes
    for tf in timeframes_to_test:
        res = tf["resolution"]
        label = tf["label"]
        
        console.print(f"\n[bold cyan]Fetching {test_days} days of [bold white]{label}[/bold white] data...[/bold cyan]")
        
        # Fetch data ONCE per timeframe
        base_df = data_fetcher.get_historical_data(
            symbol=test_symbol,
            resolution=res,
            days=test_days
        )
        
        if base_df is None:
            console.print(f"[bold red]Failed to fetch {label} data. Skipping.[/bold red]")
            continue
            
        console.print(f"[dim]Fetched {len(base_df)} candles. Running strategies...[/dim]")
        
        # 2. Loop through Strategies for this Timeframe
        for strat in strategies_to_test:
            current_test += 1
            strat_display_name = strat["name"]
            if strat.get("params"):
                strat_display_name += f" ({strat['params'].get('length', '')} {strat['params'].get('area', '')})"
            
            console.print(f"  [yellow][{current_test}/{total_tests}][/yellow] Testing {label} - {strat_display_name}...")
            
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
                results["timeframe_label"] = label
                all_results.append(results)
            else:
                all_results.append({
                    "display_name": strat_display_name,
                    "timeframe_label": label,
                    "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                    "max_drawdown_pct": 0, "total_points": 0, "total_pnl": 0,
                    "return_pct": 0, "trades_df": None
                })

    # --- SORT BY BEST PERFORMANCE ---
    all_results.sort(key=lambda x: (x.get('profit_factor', 0), x.get('return_pct', 0)), reverse=True)
    
    # --- RENDER MASTER MATRIX TABLE ---
    console.clear()
    # Calculate Time Taken
    end_time = time.time()
    total_seconds = end_time - start_time
    if total_seconds < 60:
        time_str = f"{total_seconds:.2f} seconds"
    else:
        mins = int(total_seconds // 60)
        secs = total_seconds % 60
        time_str = f"{mins}m {secs:.2f}s"
    console.print(Panel(
        f"[bold white]Symbol: {test_symbol}  |  Period: {test_days} days | Risk: {sl_pct*100:.0f}% SL / {tp_pct*100:.0f}% TP\n"
        f"[bold cyan]Execution Time: {time_str}[/bold cyan]", 
        title="MULTI-TIMEFRAME MATRIX RESULTS", box=box.DOUBLE
    ))
    
    matrix_table = Table(title="Absolute Performance Ranking", box=box.HEAVY, title_style="bold cyan", show_lines=True)
    matrix_table.add_column("Rank", style="bold", justify="center", width=5)
    matrix_table.add_column("Timeframe", style="bold", justify="center", width=10, no_wrap=True) # NEW
    matrix_table.add_column("Strategy", style="bold", min_width=35, no_wrap=True)
    matrix_table.add_column("Trades", justify="center", width=6)
    matrix_table.add_column("Win %", justify="right", width=7)
    matrix_table.add_column("PF", justify="right", width=6) # Shortened Prof Factor
    matrix_table.add_column("DD%", justify="right", width=6) # Shortened Max DD
    matrix_table.add_column("Points", justify="right", width=8)
    matrix_table.add_column("Net P&L", justify="right", width=10)
    matrix_table.add_column("Ret %", justify="right", width=7)
    
    for rank, res in enumerate(all_results, 1):
        is_winner = (rank == 1 and res["total_trades"] > 0)
        
        pnl = res["total_pnl"]
        ret = res["return_pct"]
        pts = res["total_points"]
        
        pnl_str = f"{pnl:,.0f}"
        ret_str = f"{ret:.2f}%"
        pts_str = f"{pts:+.1f}"
        
        if is_winner:
            tf_str = f"[bold green]🥇 {res['timeframe_label']}[/bold green]"
            name_str = f"[bold green]{res['display_name']}[/bold green]"
            pnl_str = f"[bold green]{pnl_str}[/bold green]"
            ret_str = f"[bold green]{ret_str}[/bold green]"
            pts_str = f"[bold green]{pts_str}[/bold green]"
        else:
            tf_str = res['timeframe_label']
            name_str = res['display_name']
            if pnl < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
                ret_str = f"[red]{ret_str}[/red]"
                pts_str = f"[red]{pts_str}[/red]"
            else:
                pnl_str = f"[green]{pnl_str}[/green]"
                ret_str = f"[green]{ret_str}[/green]"
                pts_str = f"[green]{pts_str}[/green]"
            
        dd_str = f"[red]{res['max_drawdown_pct']:.1f}%[/red]" if res['max_drawdown_pct'] > 5 else f"{res['max_drawdown_pct']:.1f}%"
        
        matrix_table.add_row(
            f"{rank}",
            tf_str,
            name_str,
            f"{res['total_trades']}",
            f"{res['win_rate']:.0f}%",
            f"{res['profit_factor']:.2f}",
            dd_str,
            pts_str,
            pnl_str,
            ret_str
        )
        
    console.print(matrix_table)
    
    # --- PRINT TRADE LOG FOR THE ULTIMATE WINNER ---
    if all_results and all_results[0]["trades_df"] is not None:
        winner = all_results[0]
        console.print(f"\n[bold green]🥇 ULTIMATE WINNER: {winner['timeframe_label']} - {winner['display_name']}[/bold green]")
        print_trade_log(winner)
    else:
        console.print("[bold red]No strategies generated any trades across any timeframe.[/bold red]")