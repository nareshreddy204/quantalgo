import yaml
import os
import numpy as np
import pandas as pd
import pandas_ta as ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

console = Console()

# ==========================================================
# LOCAL DATA CACHE CONFIGURATION
# ==========================================================
DATA_DIR = r"C:\Users\bnare\Desktop\new way\data\options"

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

def get_local_filepath(symbol, tf):
    safe_symbol = symbol.replace(":", "_").replace("/", "_")
    filename = f"{safe_symbol}_{tf}m.csv"
    return os.path.join(DATA_DIR, filename)

def load_local_data(symbol, tf):
    filepath = get_local_filepath(symbol, tf)
    if not os.path.exists(filepath):
        return None
    try:
        df = pd.read_csv(filepath)
        if df.empty:
            return None

        datetime_candidates = [c for c in df.columns 
                               if c.lower() in ("datetime", "date", "timestamp", "time")]
        if datetime_candidates:
            dt_col = datetime_candidates[0]
            df[dt_col] = pd.to_datetime(df[dt_col])
            df.set_index(dt_col, inplace=True)
        else:
            df.index = pd.to_datetime(df.index)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        df.columns = [c.lower() for c in df.columns]
        required = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if required:
            df = df[required]
        df = df.sort_index()
        return df
    except Exception as e:
        console.print(f"[red]Error reading local file {filepath}: {e}[/red]")
        return None

def save_local_data(df, symbol, tf):
    filepath = get_local_filepath(symbol, tf)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df_to_save = df.copy()
    if not isinstance(df_to_save.index, pd.DatetimeIndex):
        df_to_save.index = pd.to_datetime(df_to_save.index)
    df_to_save.index.name = "datetime"
    df_to_save.to_csv(filepath)
    console.print(f"[green]💾 Saved {len(df_to_save)} candles → {filepath}[/green]")

def load_or_fetch_data(data_fetcher, symbol, tf, days, force_refresh=False):
    if not force_refresh:
        local_df = load_local_data(symbol, tf)
        if local_df is not None and len(local_df) > 0:
            console.print(f"[green]📂 Loaded {len(local_df)} candles from local cache[/green]")
            return local_df
        console.print(f"[yellow]⚠ Local file not found for {symbol} {tf}m. Fetching from API...[/yellow]")
    else:
        console.print(f"[cyan]🔄 Force refresh: fetching {symbol} {tf}m from API...[/cyan]")

    raw_df = data_fetcher.get_historical_data(symbol=symbol, resolution=tf, days=days)
    if raw_df is None or len(raw_df) == 0:
        return raw_df
    save_local_data(raw_df, symbol, tf)
    return raw_df

# ==========================================================
# STRATEGY
# ==========================================================
def calculate_dhan_super_scalper(df, atr_period=14, atr_multiplier=1.0,
                                  fast_ema_len=21, slow_ema_len=65,
                                  fast_rsi_len=25, slow_rsi_len=100):
    df = df.copy()
    df.columns = df.columns.str.capitalize()

    df['hl2'] = (df['High'] + df['Low']) / 2.0
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        (df['High'] - df['Low']).abs(),
        (df['High'] - prev_close).abs(),
        (df['Low'] - prev_close).abs()
    ], axis=1).max(axis=1)

    df['atr'] = ta.wma(tr, length=atr_period)
    df['fast_ema'] = ta.ema(df['Close'], length=fast_ema_len)
    df['slow_ema'] = ta.ema(df['Close'], length=slow_ema_len)
    df['fast_rsi'] = ta.rsi(df['Close'], length=fast_rsi_len)
    df['slow_rsi'] = ta.rsi(df['Close'], length=slow_rsi_len)
    df['slow_rsi'] = df['slow_rsi'].fillna(50)

    df['upper_band_raw'] = df['hl2'] + atr_multiplier * df['atr']
    df['lower_band_raw'] = df['hl2'] - atr_multiplier * df['atr']

    final_upper = np.full(len(df), np.nan)
    final_lower = np.full(len(df), np.nan)
    trend = np.zeros(len(df), dtype=int)

    for i in range(len(df)):
        if i == 0 or np.isnan(df['atr'].iloc[i]):
            final_upper[i] = df['upper_band_raw'].iloc[i]
            final_lower[i] = df['lower_band_raw'].iloc[i]
            trend[i] = 1
            continue

        prev_close_val = df['Close'].iloc[i - 1]
        prev_up = final_upper[i - 1]
        prev_lo = final_lower[i - 1]
        cur_up = df['upper_band_raw'].iloc[i]
        cur_lo = df['lower_band_raw'].iloc[i]

        if np.isnan(prev_up) or cur_up < prev_up or prev_close_val > prev_up:
            final_upper[i] = cur_up
        else:
            final_upper[i] = prev_up

        if np.isnan(prev_lo) or cur_lo > prev_lo or prev_close_val < prev_lo:
            final_lower[i] = cur_lo
        else:
            final_lower[i] = prev_lo

        if prev_close_val > final_upper[i - 1] if not np.isnan(final_upper[i-1]) else False:
            trend[i] = 1
        elif prev_close_val < final_lower[i - 1] if not np.isnan(final_lower[i-1]) else False:
            trend[i] = -1
        else:
            if trend[i-1] == 1 and df['Close'].iloc[i] < final_lower[i]:
                trend[i] = -1
            elif trend[i-1] == -1 and df['Close'].iloc[i] > final_upper[i]:
                trend[i] = 1
            else:
                trend[i] = trend[i-1] if trend[i-1] != 0 else 1

    df['final_upper'] = final_upper
    df['final_lower'] = final_lower
    df['trend'] = trend
    df['trend_prev'] = df['trend'].shift(1)

    df['buy_signal'] = (
        (df['trend'] == 1) & (df['trend_prev'] == -1) &
        (df['fast_ema'] > df['slow_ema']) & (df['fast_rsi'] > df['slow_rsi'])
    )
    df['sell_signal'] = (
        (df['trend'] == -1) & (df['trend_prev'] == 1) &
        (df['fast_ema'] < df['slow_ema']) & (df['fast_rsi'] < df['slow_rsi'])
    )
    return df

def backtest_strategy(df, sl_multiplier=1.0, tp_multiplier=1.5):
    in_position = False
    position_type = None  
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    entry_time = None
    entry_atr = 0.0
    trades = []
    
    for index, row in df.iterrows():
        if pd.isna(row['atr']) or pd.isna(row['fast_ema']) or pd.isna(row['slow_ema']):
            continue
            
        current_time = index.time()
        if current_time < pd.Timestamp("09:30").time() or current_time > pd.Timestamp("15:10").time():
            continue
            
        if in_position:
            exit_price = None
            reason = None

            if position_type == 'LONG':
                if row['Low'] <= stop_loss:
                    exit_price = stop_loss
                    reason = "SL Hit"
                elif row['High'] >= take_profit:
                    exit_price = take_profit
                    reason = "TP Hit"
            elif position_type == 'SHORT':
                if row['High'] >= stop_loss:
                    exit_price = stop_loss
                    reason = "SL Hit"
                elif row['Low'] <= take_profit:
                    exit_price = take_profit
                    reason = "TP Hit"

            if exit_price is not None:
                pnl = (exit_price - entry_price) if position_type == 'LONG' else (entry_price - exit_price)
                trades.append({
                    "Entry Time": entry_time,
                    "Exit Time": index,
                    "Type": position_type,
                    "Entry": entry_price,
                    "Exit": exit_price,
                    "ATR": entry_atr,
                    "PnL": pnl,
                    "Reason": reason
                })
                in_position = False

        if not in_position:
            if row['buy_signal']:
                in_position = True
                position_type = 'LONG'
                entry_price = row['Close']
                entry_atr = row['atr']
                stop_loss = entry_price - (entry_atr * sl_multiplier)
                take_profit = entry_price + (entry_atr * tp_multiplier)
                entry_time = index
            elif row['sell_signal']:
                in_position = True
                position_type = 'SHORT'
                entry_price = row['Close']
                entry_atr = row['atr']
                stop_loss = entry_price + (entry_atr * sl_multiplier)
                take_profit = entry_price - (entry_atr * tp_multiplier)
                entry_time = index

    return pd.DataFrame(trades)

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    data_fetcher = DataFetcher(auth.fyers)

    test_symbol = "NSE:NIFTY50-INDEX"
    test_days = 90
    FORCE_REFRESH = False

    console.print("[bold yellow]Running Multi-Timeframe Grid Search...[/bold yellow]\n")
    
    tf_range = ["1", "5", "10", "15"]
    atr_mult_range = [0.8, 1.0, 1.5]
    fast_ema_range = [13, 21, 34]
    sl_range = [1.0, 1.5]
    tp_range = [1.5, 2.0, 2.5, 3.0, 3.5]
    
    results = []
    
    for tf in tf_range:
        console.print(f"\n[cyan]=== Timeframe: {tf}m ===[/cyan]")
        raw_df = load_or_fetch_data(data_fetcher, symbol=test_symbol, tf=tf, days=test_days, force_refresh=FORCE_REFRESH)
        
        if raw_df is None or len(raw_df) == 0:
            console.print(f"[red]Failed to obtain {tf}m data. Skipping...[/red]")
            continue
            
        console.print(f"[green]✓ {len(raw_df)} candles ready. Testing combinations...[/green]")
        
        for cur_atr_mult in atr_mult_range:
            for cur_fast_ema in fast_ema_range:
                df = calculate_dhan_super_scalper(raw_df.copy(), atr_multiplier=cur_atr_mult, fast_ema_len=cur_fast_ema)
                
                for sl_mult in sl_range:
                    for tp_mult in tp_range:
                        trades_df = backtest_strategy(df, sl_multiplier=sl_mult, tp_multiplier=tp_mult)
                        if len(trades_df) == 0:
                            continue
                            
                        total_trades = len(trades_df)
                        wins = len(trades_df[trades_df['PnL'] > 0])
                        win_rate = (wins / total_trades) * 100
                        total_pnl = trades_df['PnL'].sum()
                        avg_pnl = trades_df['PnL'].mean()
                        
                        results.append({
                            "TF": tf,
                            "ATR Mult": cur_atr_mult,
                            "Fast EMA": cur_fast_ema,
                            "SL": sl_mult,
                            "TP": tp_mult,
                            "Trades": total_trades,
                            "Win%": win_rate,
                            "Total PnL": total_pnl,
                            "Avg PnL": avg_pnl,
                            "TradesDF": trades_df   # <--- SAVING ACTUAL TRADES HERE
                        })
                        
    results_df = pd.DataFrame(results)
    
    if results_df.empty:
        console.print("[red]No results to display.[/red]")
    else:
        results_df = results_df.sort_values(by="Total PnL", ascending=False).reset_index(drop=True)
        
        # Print Optimization Results Table
        opt_table = Table(title="⚙️ Top 20 Multi-Timeframe Optimization Results", box=box.HEAVY)
        opt_table.add_column("Rank", style="bold", justify="center")
        opt_table.add_column("TF", justify="center", style="magenta")
        opt_table.add_column("ATR Mult", justify="center", style="cyan")
        opt_table.add_column("Fast EMA", justify="center", style="cyan")
        opt_table.add_column("SL (x)", justify="center")
        opt_table.add_column("TP (x)", justify="center")
        opt_table.add_column("Trades", justify="center")
        opt_table.add_column("Win %", justify="center")
        opt_table.add_column("Total PnL", justify="right")
        opt_table.add_column("Avg PnL", justify="right")
        
        for idx, row in results_df.head(20).iterrows():
            pnl_color = "green" if row['Total PnL'] > 0 else "red"
            wr_color = "green" if row['Win%'] >= 40 else "yellow" if row['Win%'] >= 30 else "red"
            opt_table.add_row(
                str(idx + 1),
                f"{row['TF']}m",
                f"{row['ATR Mult']:.1f}",
                str(int(row['Fast EMA'])),
                f"{row['SL']:.1f}",
                f"{row['TP']:.1f}",
                str(row['Trades']),
                f"[{wr_color}]{row['Win%']:.2f}%[/{wr_color}]",
                f"[{pnl_color}]{row['Total PnL']:.2f}[/{pnl_color}]",
                f"[{pnl_color}]{row['Avg PnL']:.2f}[/{pnl_color}]"
            )
        console.print(opt_table)
        
        # ==========================================
        # INTERACTIVE TRADE INSPECTOR
        # ==========================================
        console.print("\n[bold blue]🔍 Trade Inspector Mode[/bold blue]")
        console.print("Enter a [bold green]Rank (1-20)[/bold green] to view detailed trades for that configuration.")
        console.print("Enter [bold red]'0' or 'q'[/bold red] to quit.\n")
        
        while True:
            user_input = input("Enter Rank to inspect: ").strip()
            
            if user_input.lower() in ['0', 'q', 'exit', 'quit']:
                break
                
            try:
                rank = int(user_input)
                if 1 <= rank <= 20:
                    selected_row = results_df.iloc[rank - 1]
                    trades_to_show = selected_row['TradesDF']
                    
                    # Create detailed trades table
                    detail_table = Table(
                        title=f"📋 Detailed Trades for Rank {rank} | TF: {selected_row['TF']}m | ATR: {selected_row['ATR Mult']} | EMA: {selected_row['Fast EMA']} | SL: {selected_row['SL']}x | TP: {selected_row['TP']}x",
                        box=box.SIMPLE_HEAVY
                    )
                    detail_table.add_column("Entry Time", style="cyan")
                    detail_table.add_column("Exit Time", style="cyan")
                    detail_table.add_column("Type", style="magenta")
                    detail_table.add_column("Entry", justify="right")
                    detail_table.add_column("Exit", justify="right")
                    detail_table.add_column("ATR", justify="right")
                    detail_table.add_column("PnL", justify="right")
                    detail_table.add_column("Reason", justify="center")
                    
                    for idx, trade in trades_to_show.iterrows():
                        pnl_color = "green" if trade['PnL'] > 0 else "red"
                        # Format times cleanly
                        entry_str = trade['Entry Time'].strftime('%Y-%m-%d %H:%M')
                        exit_str = trade['Exit Time'].strftime('%Y-%m-%d %H:%M')
                        
                        detail_table.add_row(
                            entry_str,
                            exit_str,
                            trade['Type'],
                            f"{trade['Entry']:.2f}",
                            f"{trade['Exit']:.2f}",
                            f"{trade['ATR']:.2f}",
                            f"[{pnl_color}]{trade['PnL']:.2f}[/{pnl_color}]",
                            trade['Reason']
                        )
                        
                    console.print(detail_table)
                    console.print("\n" + "="*50 + "\n")
                else:
                    console.print("[red]Please enter a number between 1 and 20.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Please enter a valid number.[/red]")