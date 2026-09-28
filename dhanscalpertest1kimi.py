import yaml
import os
import pandas as pd
import pandas_ta as ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from datetime import time

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

def calculate_signals(df):
    """Original Dhan Super Scalper logic — unchanged."""
    
    df.columns = df.columns.str.capitalize()
    
    required_cols = ['Open', 'High', 'Low', 'Close']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found. Available: {df.columns.tolist()}")

    atr_period = 14
    fast_ema_len = 21
    slow_ema_len = 65
    fast_rsi_len = 25
    slow_rsi_len = 100

    df['atr'] = ta.atr(df['High'], df['Low'], df['Close'], length=atr_period)
    df['fast_ema'] = ta.ema(df['Close'], length=fast_ema_len)
    df['slow_ema'] = ta.ema(df['Close'], length=slow_ema_len)
    df['fast_rsi'] = ta.rsi(df['Close'], length=fast_rsi_len)
    df['slow_rsi'] = ta.rsi(df['Close'], length=slow_rsi_len)
    df['slow_rsi'] = df['slow_rsi'].fillna(50)

    # EMA slope: 3-bar momentum. Must be clearly directional.
    df['fast_ema_slope'] = df['fast_ema'].diff(3)
    
    # Candle body strength (avoid doji entries)
    df['body'] = (df['Close'] - df['Open']).abs()

    df['buy_signal'] = (
        (df['fast_rsi'] > df['slow_rsi']) & 
        (df['fast_rsi'].shift(1) <= df['slow_rsi'].shift(1)) &
        (df['fast_ema'] > df['slow_ema'])
    )
    
    df['sell_signal'] = (
        (df['fast_rsi'] < df['slow_rsi']) & 
        (df['fast_rsi'].shift(1) >= df['slow_rsi'].shift(1)) &
        (df['fast_ema'] < df['slow_ema'])
    )
    
    return df

def is_trade_allowed_time(timestamp):
    """No entries in opening chop (9:15-9:45) or illiquid close (after 14:45)."""
    t = timestamp.time() if hasattr(timestamp, 'time') else timestamp
    if isinstance(t, str):
        t = pd.to_datetime(t).time()
    
    avoid_start = time(9, 45)   # Widened to 9:45 — your 9:40 entry lost big
    avoid_end = time(14, 45)    # Earlier cutoff — after 14:45 options decay spikes
    market_close = time(15, 30)
    
    if t < avoid_start:
        return False
    if avoid_end <= t <= market_close:
        return False
    return True

def backtest(df, max_risk_atr=2.0, min_reward_atr=2.5, cooldown_bars=3):
    """
    EMA-structured risk management:
    - SL: Max of (Fast EMA ± 0.5 ATR) OR (entry ± max_risk_atr * ATR)
    - Trail: Fast EMA ± 0.3 ATR (lets winners run, exits on trend break)
    - Filter: EMA slope must be directional + candle body > 0.3 ATR
    """
    
    in_position = False
    position_type = None
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    entry_time = None
    last_signal_bar = -999
    
    trades = []
    daily_pnl = 0
    current_date = None
    
    for i, (index, row) in enumerate(df.iterrows()):
        date = index.date() if hasattr(index, 'date') else pd.to_datetime(index).date()
        if date != current_date:
            current_date = date
            daily_pnl = 0
        
        if pd.isna(row['atr']) or pd.isna(row['fast_ema']):
            continue
        
        current_time = index.time() if hasattr(index, 'time') else pd.to_datetime(index).time()
        is_eod = (current_time.hour == 15 and current_time.minute >= 25) or current_time.hour > 15
        
        # === EXIT LOGIC ===
        if in_position:
            if position_type == 'LONG':
                # Dynamic trail: Fast EMA - 0.3 ATR as floor
                # The charts show price bounces OFF the Fast EMA in trends
                ema_floor = row['fast_ema'] - (row['atr'] * 0.3)
                if ema_floor > stop_loss:
                    stop_loss = ema_floor
                
                if row['Low'] <= stop_loss:
                    pnl = stop_loss - entry_price
                    reason = "Trail Hit" if stop_loss > (entry_price - row['atr'] * max_risk_atr) else "SL Hit"
                    trades.append({"Time": entry_time, "Type": "LONG", "Entry": entry_price, 
                                  "Exit": stop_loss, "PnL": pnl, "Reason": reason})
                    daily_pnl += pnl
                    in_position = False
                elif row['High'] >= take_profit:
                    pnl = take_profit - entry_price
                    trades.append({"Time": entry_time, "Type": "LONG", "Entry": entry_price,
                                  "Exit": take_profit, "PnL": pnl, "Reason": "TP Hit"})
                    daily_pnl += pnl
                    in_position = False
                elif is_eod:
                    pnl = row['Close'] - entry_price
                    trades.append({"Time": entry_time, "Type": "LONG", "Entry": entry_price,
                                  "Exit": row['Close'], "PnL": pnl, "Reason": "EOD Square Off"})
                    daily_pnl += pnl
                    in_position = False
                    
            elif position_type == 'SHORT':
                ema_ceiling = row['fast_ema'] + (row['atr'] * 0.3)
                if ema_ceiling < stop_loss:
                    stop_loss = ema_ceiling
                
                if row['High'] >= stop_loss:
                    pnl = entry_price - stop_loss
                    reason = "Trail Hit" if stop_loss < (entry_price + row['atr'] * max_risk_atr) else "SL Hit"
                    trades.append({"Time": entry_time, "Type": "SHORT", "Entry": entry_price,
                                  "Exit": stop_loss, "PnL": pnl, "Reason": reason})
                    daily_pnl += pnl
                    in_position = False
                elif row['Low'] <= take_profit:
                    pnl = entry_price - take_profit
                    trades.append({"Time": entry_time, "Type": "SHORT", "Entry": entry_price,
                                  "Exit": take_profit, "PnL": pnl, "Reason": "TP Hit"})
                    daily_pnl += pnl
                    in_position = False
                elif is_eod:
                    pnl = entry_price - row['Close']
                    trades.append({"Time": entry_time, "Type": "SHORT", "Entry": entry_price,
                                  "Exit": row['Close'], "PnL": pnl, "Reason": "EOD Square Off"})
                    daily_pnl += pnl
                    in_position = False
        
        # === ENTRY LOGIC ===
        if not in_position:
            if not is_trade_allowed_time(index):
                continue
            
            if i - last_signal_bar < cooldown_bars:
                continue
            
            # FILTER 1: EMA must be sloping (kills flat/chop zones)
            min_slope = row['atr'] * 0.15
            if row['buy_signal'] and row['fast_ema_slope'] < min_slope:
                continue
            if row['sell_signal'] and row['fast_ema_slope'] > -min_slope:
                continue
            
            # FILTER 2: Signal bar must have conviction (body > 0.3 ATR)
            if row['body'] < row['atr'] * 0.3:
                continue
            
            # FILTER 3: Don't chase. Price must be within 1.5 ATR of Fast EMA.
            # The charts show best entries are when price kisses the EMA, not when extended.
            ema_dist = abs(row['Close'] - row['fast_ema']) / row['atr']
            if ema_dist > 1.5:
                continue
            
            if row['buy_signal']:
                in_position = True
                position_type = 'LONG'
                entry_price = row['Close']
                
                # Dynamic SL: worse of EMA-floor or max risk
                ema_sl = row['fast_ema'] - (row['atr'] * 0.5)
                fixed_sl = entry_price - (row['atr'] * max_risk_atr)
                stop_loss = max(ema_sl, fixed_sl)
                
                take_profit = entry_price + (row['atr'] * min_reward_atr)
                entry_time = index
                last_signal_bar = i
                
            elif row['sell_signal']:
                in_position = True
                position_type = 'SHORT'
                entry_price = row['Close']
                
                ema_sl = row['fast_ema'] + (row['atr'] * 0.5)
                fixed_sl = entry_price + (row['atr'] * max_risk_atr)
                stop_loss = min(ema_sl, fixed_sl)
                
                take_profit = entry_price - (row['atr'] * min_reward_atr)
                entry_time = index
                last_signal_bar = i

    return pd.DataFrame(trades)


if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    data_fetcher = DataFetcher(auth.fyers)
    
    # ==========================================
    # TEST ON 4M TO MATCH YOUR CHARTS
    # ==========================================
    test_symbol = "NSE:NIFTY50-INDEX"
    test_resolution = "5"   # Try "4" if your broker supports it, else "5"
    test_days = 30
    
    max_risk = 2.0      # Wider initial SL (was 1.5)
    min_reward = 2.5    # Same TP
    cooldown = 3
    # ==========================================
    
    console.print(f"[bold cyan]Fetching {test_days} days of {test_resolution}m data for {test_symbol}...[/bold cyan]")
    
    df = data_fetcher.get_historical_data(
        symbol=test_symbol,
        resolution=test_resolution,
        days=test_days
    )
    
    if df is None or len(df) == 0:
        console.print("[bold red]Failed to fetch data.[/bold red]")
        exit()
        
    console.print(f"[bold green]Fetched {len(df)} candles.[/bold green]")
    
    df = calculate_signals(df)
    
    buy_count = int(df['buy_signal'].sum())
    sell_count = int(df['sell_signal'].sum())
    console.print(f"[dim]Raw signals — Buy: {buy_count}, Sell: {sell_count}[/dim]")
    
    console.print(f"[bold yellow]Running EMA-Structured Backtest...[/bold yellow]\n")
    trades_df = backtest(df, max_risk_atr=max_risk, min_reward_atr=min_reward, cooldown_bars=cooldown)
    
    if len(trades_df) == 0:
        console.print("[red]No trades generated.[/red]")
        exit()
        
    total_trades = len(trades_df)
    wins = len(trades_df[trades_df['PnL'] > 0])
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100
    total_pnl = trades_df['PnL'].sum()
    avg_win = trades_df[trades_df['PnL'] > 0]['PnL'].mean() if wins > 0 else 0
    avg_loss = trades_df[trades_df['PnL'] <= 0]['PnL'].mean() if losses > 0 else 0
    profit_factor = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else float('inf')
    
    cumulative = trades_df['PnL'].cumsum()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max).min()
    
    reason_counts = trades_df['Reason'].value_counts()
    
    console.print(Panel(
        f"[bold white]Symbol: {test_symbol}  |  TF: {test_resolution}m  |  Period: {test_days} days\n"
        f"Max Risk: {max_risk}x ATR  |  Min Reward: {min_reward}x ATR\n"
        f"SL anchored to EMA  |  Trail: EMA ± 0.3 ATR",
        title="EMA-STRUCTURED SCALPER BACKTEST", box=box.DOUBLE
    ))
    
    summary_table = Table(title="Performance Summary", box=box.HEAVY)
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Value", justify="right")
    
    wr_color = "green" if win_rate > 50 else "red"
    pnl_color = "green" if total_pnl > 0 else "red"
    
    summary_table.add_row("Total Trades", f"{total_trades}")
    summary_table.add_row("Win Rate", f"[{wr_color}]{win_rate:.2f}%[/{wr_color}]")
    summary_table.add_row("Total PnL (Points)", f"[{pnl_color}]{total_pnl:.2f}[/{pnl_color}]")
    summary_table.add_row("Avg Win", f"[green]{avg_win:.2f}[/green]")
    summary_table.add_row("Avg Loss", f"[red]{avg_loss:.2f}[/red]")
    summary_table.add_row("Profit Factor", f"{profit_factor:.2f}")
    summary_table.add_row("Max Drawdown", f"[red]{drawdown:.2f}[/red]")
    
    console.print(summary_table)
    
    reason_table = Table(title="Exit Reasons", box=box.SIMPLE)
    reason_table.add_column("Reason", style="bold")
    reason_table.add_column("Count", justify="right")
    reason_table.add_column("Total PnL", justify="right")
    for reason in reason_counts.index:
        r_pnl = trades_df[trades_df['Reason'] == reason]['PnL'].sum()
        r_color = "green" if r_pnl > 0 else "red"
        reason_table.add_row(reason, f"{reason_counts[reason]}", f"[{r_color}]{r_pnl:.2f}[/{r_color}]")
    console.print(reason_table)
    
    trades_table = Table(title="\nLast 15 Trades", box=box.SIMPLE)
    trades_table.add_column("Entry Time", width=20)
    trades_table.add_column("Type", width=6)
    trades_table.add_column("Entry", justify="right", width=10)
    trades_table.add_column("Exit", justify="right", width=10)
    trades_table.add_column("PnL", justify="right", width=10)
    trades_table.add_column("Reason", width=15)
    
    for _, trade in trades_df.tail(15).iterrows():
        type_color = "green" if trade['Type'] == 'LONG' else "red"
        pnl_color = "green" if trade['PnL'] > 0 else "red"
        trades_table.add_row(
            str(trade['Time']),
            f"[{type_color}]{trade['Type']}[/{type_color}]",
            f"{trade['Entry']:.2f}",
            f"{trade['Exit']:.2f}",
            f"[{pnl_color}]{trade['PnL']:.2f}[/{pnl_color}]",
            trade['Reason']
        )
    console.print(trades_table)