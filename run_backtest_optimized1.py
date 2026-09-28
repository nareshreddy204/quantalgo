import yaml
import os
import time
import pandas as pd
import numpy as np
from datetime import time as dt_time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

class HighWinRateBacktester:
    def __init__(self):
        self.slippage_pts = 0.5
        
    def is_good_trading_time(self, dt):
        """Filter out the worst choppy times"""
        t = dt.time()
        if t < dt_time(9, 30): return False      # Opening 15 mins
        if dt_time(12, 0) <= t <= dt_time(13, 0): return False # Lunch
        if t >= dt_time(15, 15): return False     # Closing 15 mins
        return True

    def generate_pullback_signals(self, df):
        """
        HIGH WIN RATE STRATEGY: Mean Reversion Pullback
        Logic: Only buy when the broader trend is UP, but price has pulled back (RSI dipped).
        We buy when the immediate bounce starts.
        """
        df = df.copy()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['rsi'] = self.calculate_rsi(df['close'], 14)
        df['atr'] = self.calculate_atr(df, 14)
        
        df['signal'] = 0
        
        for i in range(51, len(df)):
            prev = df.iloc[i-1]
            curr = df.iloc[i]
            
            # 1. Trend Filter: Close must be above 50 SMA (Overall uptrend)
            is_uptrend = curr['close'] > curr['sma_50']
            
            # 2. Pullback Filter: RSI must have just crossed above 35 from below (Oversold bounce)
            rsi_bounce = (prev['rsi'] < 35) and (curr['rsi'] >= 35)
            
            # 3. Momentum Filter: Current close must be higher than previous close (Bullish candle)
            bullish_candle = curr['close'] > prev['close']
            
            if is_uptrend and rsi_bounce and bullish_candle:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                
        return df

    def calculate_rsi(self, close, period):
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def calculate_atr(self, df, period=14):
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def run_backtest(self, df, symbol, initial_capital=100000, 
                     sl_mult=1.5, tp_mult=1.0, use_time_filter=True):
        
        df = self.generate_pullback_signals(df)
        
        capital = initial_capital
        peak_capital = initial_capital
        max_drawdown = 0
        position = None
        trades = []
        
        # Small TP and Wider SL mathematically forces a higher win rate
        for i in range(1, len(df) - 1):
            row = df.iloc[i]
            next_row = df.iloc[i + 1]
            
            # --- EXIT LOGIC ---
            if position is not None:
                exit_price = None
                exit_reason = ""
                
                if row["low"] <= position["sl"]:
                    exit_price = position["sl"] - self.slippage_pts
                    exit_reason = "SL Hit"
                elif row["high"] >= position["tp"]:
                    exit_price = position["tp"] - self.slippage_pts
                    exit_reason = "TP Hit"
                elif row.name.time() >= dt_time(15, 10):
                    exit_price = row["close"]
                    exit_reason = "EOD Close"
                
                if exit_price:
                    points = exit_price - position["entry_price"]
                    pnl = points * position["quantity"]
                    capital += pnl
                    
                    if capital > peak_capital: peak_capital = capital
                    dd = (peak_capital - capital) / peak_capital
                    if dd > max_drawdown: max_drawdown = dd
                    
                    trades.append({
                        "entry_date": position["entry_date"].strftime('%Y-%m-%d %H:%M'),
                        "exit_date": df.index[i].strftime('%Y-%m-%d %H:%M'),
                        "entry_price": round(position["entry_price"], 2),
                        "exit_price": round(exit_price, 2),
                        "points": round(points, 2),
                        "pnl": round(pnl, 2),
                        "reason": exit_reason
                    })
                    position = None
            
            # --- ENTRY LOGIC ---
            if position is None and row["signal"] == 1:
                if use_time_filter and not self.is_good_trading_time(next_row.name):
                    continue
                    
                if pd.isna(row['atr']): continue
                
                entry_price = next_row["open"] + self.slippage_pts
                sl = entry_price - (row["atr"] * sl_mult)
                tp = entry_price + (row["atr"] * tp_mult) # TP is smaller than SL
                
                risk_per_trade = capital * 0.02
                qty = int(risk_per_trade / (entry_price - sl))
                qty = max(1, min(qty, int((capital * 0.95) / entry_price)))
                
                if qty > 0:
                    position = {
                        "entry_price": entry_price,
                        "entry_date": next_row.name,
                        "quantity": qty,
                        "sl": sl,
                        "tp": tp
                    }
        
        if not trades:
            return None
            
        trades_df = pd.DataFrame(trades)
        wins = trades_df[trades_df["pnl"] > 0]
        losses = trades_df[trades_df["pnl"] < 0]
        
        gross_profit = wins["pnl"].sum() if len(wins) > 0 else 0
        gross_loss = abs(losses["pnl"].sum()) if len(losses) > 0 else 1
        
        return {
            "total_trades": len(trades_df),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round((len(wins) / len(trades_df)) * 100, 2),
            "total_points": round(trades_df["points"].sum(), 2),
            "total_pnl": round(trades_df["pnl"].sum(), 2),
            "final_capital": round(capital, 2),
            "return_pct": round(((capital - initial_capital) / initial_capital) * 100, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
            "trades_df": trades_df
        }

if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    data_fetcher = DataFetcher(auth.fyers)
    backtester = HighWinRateBacktester()
    
    # ==========================================
    # HIGH WIN RATE SETTINGS
    # ==========================================
    test_symbol = "NSE:NIFTY50-INDEX"
    test_resolution = "2"
    test_days = 365
    initial_capital = 100000
    
    # We test small TP vs larger SL to force high win rate
    # Format: (SL_Multiplier, TP_Multiplier)
    parameters_to_test = [
        {"sl": 1.5, "tp": 0.5, "label": "SL:1.5x / TP:0.5x (Aggressive WR)"},
        {"sl": 2.0, "tp": 1.0, "label": "SL:2.0x / TP:1.0x (Balanced WR)"},
        {"sl": 2.5, "tp": 1.0, "label": "SL:2.5x / TP:1.0x (Safe WR)"},
        {"sl": 3.0, "tp": 1.5, "label": "SL:3.0x / TP:1.5x (Very Safe WR)"},
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
        
    console.print(f"[bold green]Fetched {len(base_df)} candles.[/bold green]")
    console.print("[bold yellow]Running Mean-Reversion Pullback Strategy (Optimized for Win Rate)...\n[/bold yellow]")
    
    all_results = []
    
    for params in parameters_to_test:
        result = backtester.run_backtest(
            df=base_df.copy(),
            symbol=test_symbol,
            initial_capital=initial_capital,
            sl_mult=params["sl"],
            tp_mult=params["tp"],
            use_time_filter=True
        )
        
        if result:
            result["label"] = params["label"]
            all_results.append(result)
        else:
            all_results.append({
                "label": params["label"],
                "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "max_drawdown_pct": 0, "total_points": 0, "total_pnl": 0,
                "return_pct": 0, "trades_df": None
            })

    # --- SORT BY HIGHEST WIN RATE ---
    all_results.sort(key=lambda x: x.get('win_rate', 0), reverse=True)
    
    # --- RENDER RESULTS ---
    console.clear()
    console.print(Panel(
        f"[bold white]Symbol: {test_symbol}  |  TF: {test_resolution}m  |  Period: {test_days} days\n"
        f"Strategy: Mean-Reversion Pullback (Trend + RSI Bounce)",
        title="HIGH WIN RATE BACKTEST RESULTS", box=box.DOUBLE
    ))
    
    table = Table(title="Ranked by Win Rate", box=box.HEAVY, show_lines=True, title_style="bold cyan")
    table.add_column("Rank", style="bold", justify="center", width=5)
    table.add_column("Parameters", style="bold", min_width=35)
    table.add_column("Trades", justify="center", width=7)
    table.add_column("Win %", justify="right", width=8)
    table.add_column("Profit Factor", justify="right", width=14)
    table.add_column("Max DD", justify="right", width=8)
    table.add_column("Points", justify="right", width=9)
    table.add_column("Net P&L", justify="right", width=11)
    
    for rank, res in enumerate(all_results, 1):
        is_winner = (rank == 1 and res["total_trades"] > 0)
        
        wr = res["win_rate"]
        pnl = res["total_pnl"]
        pts = res["total_points"]
        
        wr_str = f"{wr:.2f}%"
        pnl_str = f"{pnl:,.0f}"
        pts_str = f"{pts:+.2f}"
        
        if is_winner:
            name_str = f"[bold green]🥇 {res['label']}[/bold green]"
            wr_str = f"[bold green]{wr_str}[/bold green]"
            pnl_str = f"[bold green]{pnl_str}[/bold green]"
        else:
            name_str = res['label']
            if wr >= 55: wr_str = f"[green]{wr_str}[/green]"
            else: wr_str = f"[red]{wr_str}[/red]"
            
            if pnl < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
                pts_str = f"[red]{pts_str}[/red]"
        
        dd_str = f"[red]{res['max_drawdown_pct']:.2f}%[/red]" if res['max_drawdown_pct'] > 5 else f"{res['max_drawdown_pct']:.2f}%"
        
        table.add_row(
            f"{rank}",
            name_str,
            f"{res['total_trades']}",
            wr_str,
            f"{res['profit_factor']:.2f}",
            dd_str,
            pts_str,
            pnl_str
        )
        
    console.print(table)
    
    # Print trade log for highest win rate
    if all_results and all_results[0]["trades_df"] is not None:
        winner = all_results[0]
        console.print(f"\n[bold green]Trade Log for Highest Win Rate Setup: {winner['label']}[/bold green]")
        
        log_table = Table(box=box.ROUNDED, show_lines=True)
        log_table.add_column("Entry Date", style="dim", width=16)
        log_table.add_column("Exit Date", style="dim", width=16)
        log_table.add_column("Entry", justify="right", width=10)
        log_table.add_column("Exit", justify="right", width=10)
        log_table.add_column("Pts", justify="right", width=8)
        log_table.add_column("P&L", justify="right", width=10)
        log_table.add_column("Reason", justify="center", width=10)
        
        for _, row in winner["trades_df"].iterrows():
            pnl_color = "bold green" if row["pnl"] >= 0 else "bold red"
            pts_color = "green" if row["points"] >= 0 else "red"
            reason_color = "red" if row["reason"] == "SL Hit" else "green"
            
            log_table.add_row(
                row["entry_date"], row["exit_date"],
                f"{row['entry_price']:.2f}", f"{row['exit_price']:.2f}",
                f"[{pts_color}]{row['points']:+.2f}[/{pts_color}]",
                f"[{pnl_color}]{row['pnl']}[/{pnl_color}]",
                f"[{reason_color}]{row['reason']}[/{reason_color}]"
            )
        console.print(log_table)