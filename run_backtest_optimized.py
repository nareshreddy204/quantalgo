# run_backtest_optimized.py
import yaml
import os
import time
from itertools import product
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
import pandas as pd

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from backtester_enhanced import EnhancedBacktester

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

def run_parameter_optimization(df, symbol, strategy_name, base_params, initial_capital):
    """Find optimal SL/TP multipliers"""
    
    backtester = EnhancedBacktester()
    
    # Grid search over SL and TP multipliers
    sl_multipliers = [1.0, 1.2, 1.5, 1.8, 2.0, 2.5]
    tp_multipliers = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    
    results = []
    
    for sl_mult in sl_multipliers:
        for tp_mult in tp_multipliers:
            # Skip if R:R is less than 1:1
            if tp_mult / sl_mult < 1.0:
                continue
                
            result = backtester.run_backtest(
                df=df.copy(),
                symbol=symbol,
                strategy_name=strategy_name,
                params=base_params,
                initial_capital=initial_capital,
                sl_multiplier=sl_mult,
                tp_multiplier=tp_mult,
                use_atr_sl=True,
                use_time_filter=True,
                use_trend_filter=True
            )
            
            if result and result.get("total_trades", 0) > 10:  # Minimum 10 trades
                results.append({
                    "sl_mult": sl_mult,
                    "tp_mult": tp_mult,
                    "rr_ratio": round(tp_mult / sl_mult, 2),
                    **{k: v for k, v in result.items() if k != "trades_df"}
                })
    
    if not results:
        return None
        
    results_df = pd.DataFrame(results)
    
    # Score = (Win Rate * 0.4) + (Profit Factor * 10 * 0.3) + (Return% * 0.3)
    # Normalize to 0-100 scale
    results_df["win_score"] = (results_df["win_rate"] / 100) * 40
    results_df["pf_score"] = (results_df["profit_factor"].clip(0, 5) / 5) * 30
    results_df["ret_score"] = (results_df["return_pct"].clip(-20, 50) + 20) / 70 * 30
    results_df["total_score"] = results_df["win_score"] + results_df["pf_score"] + results_df["ret_score"]
    
    # Sort by score
    results_df = results_df.sort_values("total_score", ascending=False)
    
    return results_df

if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    data_fetcher = DataFetcher(auth.fyers)
    
    # ==========================================
    # OPTIMIZATION SETTINGS
    # ==========================================
    test_symbol = "NSE:NIFTYBANK-INDEX"
    test_resolution = "15"
    test_days = 180
    initial_capital = 100000
    
    # Best strategies to optimize
    strategies_to_optimize = [
        {"name": "ema_crossover", "params": {}},
        {"name": "supertrend_macd", "params": {}},
        {"name": "liquidity_swings", "params": {"length": 14, "area": "Wick Extremity"}},
    ]
    # ==========================================
    
    console.clear()
    console.print(f"[bold cyan]Fetching {test_days} days of {test_resolution}m data...[/bold cyan]")
    
    base_df = data_fetcher.get_historical_data(
        symbol=test_symbol,
        resolution=test_resolution,
        days=test_days
    )
    
    if base_df is None:
        console.print("[bold red]Failed to fetch data. Exiting.[/bold red]")
        exit()
    
    console.print(f"[bold green]Fetched {len(base_df)} candles. Starting optimization...[/bold green]\n")
    
    all_optimal = []
    
    for strat in strategies_to_optimize:
        console.print(f"[bold yellow]Optimizing {strat['name']}...[/bold yellow]")
        
        opt_results = run_parameter_optimization(
            df=base_df,
            symbol=test_symbol,
            strategy_name=strat["name"],
            base_params=strat["params"],
            initial_capital=initial_capital
        )
        
        if opt_results is not None and len(opt_results) > 0:
            best = opt_results.iloc[0]
            all_optimal.append({
                "strategy": strat["name"],
                "best_sl_mult": best["sl_mult"],
                "best_tp_mult": best["tp_mult"],
                "best_rr": best["rr_ratio"],
                "win_rate": best["win_rate"],
                "profit_factor": best["profit_factor"],
                "return_pct": best["return_pct"],
                "total_trades": best["total_trades"],
                "max_dd": best["max_drawdown_pct"],
                "score": best["total_score"],
                "full_results": opt_results
            })
            
            # Show top 5 for this strategy
            console.print(f"\n  [bold green]Top 5 parameter combinations:[/bold green]")
            top5_table = Table(box=box.SIMPLE, show_lines=False)
            top5_table.add_column("SL×ATR", justify="center", width=8)
            top5_table.add_column("TP×ATR", justify="center", width=8)
            top5_table.add_column("R:R", justify="center", width=6)
            top5_table.add_column("Trades", justify="center", width=7)
            top5_table.add_column("Win%", justify="right", width=7)
            top5_table.add_column("PF", justify="right", width=6)
            top5_table.add_column("Return%", justify="right", width=8)
            top5_table.add_column("Score", justify="right", width=6)
            
            for _, r in opt_results.head(5).iterrows():
                wr_color = "green" if r["win_rate"] > 50 else "red"
                ret_color = "green" if r["return_pct"] > 0 else "red"
                
                top5_table.add_row(
                    f"{r['sl_mult']:.1f}",
                    f"{r['tp_mult']:.1f}",
                    f"{r['rr_ratio']:.2f}",
                    f"{int(r['total_trades'])}",
                    f"[{wr_color}]{r['win_rate']:.1f}%[/{wr_color}]",
                    f"{r['profit_factor']:.2f}",
                    f"[{ret_color}]{r['return_pct']:.1f}%[/{ret_color}]",
                    f"{r['total_score']:.1f}"
                )
            
            console.print(top5_table)
        else:
            console.print(f"  [red]No valid results found[/red]")
    
    # ==========================================
    # FINAL SUMMARY
    # ==========================================
    if all_optimal:
        console.clear()
        console.print(Panel(
            f"[bold white]Symbol: {test_symbol}  |  TF: {test_resolution}m  |  Period: {test_days} days\n"
            f"Optimization: ATR-based SL/TP with Time & Trend Filters",
            title="OPTIMIZATION RESULTS SUMMARY", box=box.DOUBLE
        ))
        
        summary_table = Table(title="Best Parameters Per Strategy", box=box.HEAVY, show_lines=True)
        summary_table.add_column("Strategy", style="bold", min_width=20)
        summary_table.add_column("SL×ATR", justify="center", width=8)
        summary_table.add_column("TP×ATR", justify="center", width=8)
        summary_table.add_column("R:R", justify="center", width=6)
        summary_table.add_column("Trades", justify="center", width=7)
        summary_table.add_column("Win%", justify="right", width=8)
        summary_table.add_column("PF", justify="right", width=6)
        summary_table.add_column("Return%", justify="right", width=9)
        summary_table.add_column("MaxDD%", justify="right", width=8)
        summary_table.add_column("Score", justify="right", width=7)
        
        # Sort by score
        all_optimal.sort(key=lambda x: x["score"], reverse=True)
        
        for i, opt in enumerate(all_optimal, 1):
            is_best = (i == 1)
            prefix = "🥇 " if is_best else f"{i}. "
            
            name = f"[bold green]{prefix}{opt['strategy']}[/bold green]" if is_best else f"{prefix}{opt['strategy']}"
            wr_color = "bold green" if opt["win_rate"] > 50 else "red"
            ret_color = "bold green" if opt["return_pct"] > 0 else "red"
            
            summary_table.add_row(
                name,
                f"{opt['best_sl_mult']:.1f}",
                f"{opt['best_tp_mult']:.1f}",
                f"{opt['best_rr']:.2f}",
                f"{opt['total_trades']}",
                f"[{wr_color}]{opt['win_rate']:.1f}%[/{wr_color}]",
                f"{opt['profit_factor']:.2f}",
                f"[{ret_color}]{opt['return_pct']:.1f}%[/{ret_color}]",
                f"[red]{opt['max_dd']:.1f}%[/red]",
                f"[bold]{opt['score']:.1f}[/bold]"
            )
        
        console.print(summary_table)
        
        # Print recommended settings
        best = all_optimal[0]
        console.print(Panel(
            f"[bold cyan]RECOMMENDED SETTINGS FOR {test_symbol}:[/bold cyan]\n\n"
            f"  Strategy: [bold]{best['strategy']}[/bold]\n"
            f"  SL Multiplier: [bold]{best['best_sl_mult']:.1f} × ATR(14)[/bold]\n"
            f"  TP Multiplier: [bold]{best['best_tp_mult']:.1f} × ATR(14)[/bold]\n"
            f"  Risk:Reward: [bold]1:{best['best_rr']:.2f}[/bold]\n"
            f"  Expected Win Rate: [bold green]{best['win_rate']:.1f}%[/bold green]\n"
            f"  Expected Profit Factor: [bold]{best['profit_factor']:.2f}[/bold]\n\n"
            f"[dim]Apply these in your config.yaml or trading engine[/dim]",
            title="🚀 RECOMMENDATION", box=box.DOUBLE
        ))