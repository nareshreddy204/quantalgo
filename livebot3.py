import yaml
import os
import numpy as np
import pandas as pd
import pandas_ta as ta
import datetime
import time
import requests
from rich.console import Console
from rich.panel import Panel

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

console = Console()

# ==========================================
# CONFIGURATION & RISK MANAGEMENT
# ==========================================
SYMBOL = "NSE:NIFTY26AUGFUT"
RESOLUTION = "1"   # 5 Minute timeframe
QUANTITY = 1       # 1 Lot
DRY_RUN = True     # Set to False to place REAL orders

# Optimized Parameters
ATR_MULT = 1.0
FAST_EMA_LEN = 21
SLOW_EMA_LEN = 65
SL_MULT = 1.0
TP_MULT = 3.5

# ==========================================
# LOAD CONFIG & TELEGRAM CREDENTIALS
# ==========================================
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

tg = config.get("telegram", {})
TELEGRAM_BOT_TOKEN = tg.get("bot_token")
TELEGRAM_CHAT_ID = tg.get("chat_id")

def send_telegram_message(message):
    """Sends a push notification to your Telegram account."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        console.print("[red]Telegram token or chat_id missing in config.yaml![/red]")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        console.print(f"[red]Failed to send Telegram message: {e}[/red]")

def load_fyers_config():
    """Loads Fyers access token from token.txt"""
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

def calculate_indicators(df):
    df = df.copy()
    df.columns = df.columns.str.capitalize()
    df['hl2'] = (df['High'] + df['Low']) / 2.0
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        (df['High'] - df['Low']).abs(),
        (df['High'] - prev_close).abs(),
        (df['Low'] - prev_close).abs()
    ], axis=1).max(axis=1)
    df['atr'] = ta.wma(tr, length=14)
    df['fast_ema'] = ta.ema(df['Close'], length=FAST_EMA_LEN)
    df['slow_ema'] = ta.ema(df['Close'], length=SLOW_EMA_LEN)
    df['fast_rsi'] = ta.rsi(df['Close'], length=25)
    df['slow_rsi'] = ta.rsi(df['Close'], length=100).fillna(50)

    df['upper_band_raw'] = df['hl2'] + ATR_MULT * df['atr']
    df['lower_band_raw'] = df['hl2'] - ATR_MULT * df['atr']

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
        prev_up, prev_lo = final_upper[i - 1], final_lower[i - 1]
        cur_up, cur_lo = df['upper_band_raw'].iloc[i], df['lower_band_raw'].iloc[i]

        final_upper[i] = cur_up if (np.isnan(prev_up) or cur_up < prev_up or prev_close_val > prev_up) else prev_up
        final_lower[i] = cur_lo if (np.isnan(prev_lo) or cur_lo > prev_lo or prev_close_val < prev_lo) else prev_lo

        if not np.isnan(final_upper[i-1]) and prev_close_val > final_upper[i-1]:
            trend[i] = 1
        elif not np.isnan(final_lower[i-1]) and prev_close_val < final_lower[i-1]:
            trend[i] = -1
        else:
            if trend[i-1] == 1 and df['Close'].iloc[i] < final_lower[i]: trend[i] = -1
            elif trend[i-1] == -1 and df['Close'].iloc[i] > final_upper[i]: trend[i] = 1
            else: trend[i] = trend[i-1] if trend[i-1] != 0 else 1

    df['final_upper'], df['final_lower'], df['trend'] = final_upper, final_lower, trend
    df['trend_prev'] = df['trend'].shift(1)

    df['buy_signal'] = (df['trend'] == 1) & (df['trend_prev'] == -1) & (df['fast_ema'] > df['slow_ema']) & (df['fast_rsi'] > df['slow_rsi'])
    df['sell_signal'] = (df['trend'] == -1) & (df['trend_prev'] == 1) & (df['fast_ema'] < df['slow_ema']) & (df['fast_rsi'] < df['slow_rsi'])
    return df

def place_order(fyers, signal_type, price, atr):
    sl_price = 0
    tp_price = 0
    
    if signal_type == "BUY":
        sl_price = price - (atr * SL_MULT)
        tp_price = price + (atr * TP_MULT)
        side = 1 # Buy
    elif signal_type == "SELL":
        sl_price = price + (atr * SL_MULT)
        tp_price = price - (atr * TP_MULT)
        side = -1 # Sell

    # Round to nearest 0.05 for Nifty
    sl_price = round(sl_price * 20) / 20
    tp_price = round(tp_price * 20) / 20

    console.print(Panel(
        f"[bold {'green' if side==1 else 'red'}]🚀 SIGNAL: {signal_type}[/bold {'green' if side==1 else 'red'}]\n"
        f"Entry: {price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}",
        title="Trade Execution"
    ))

    if DRY_RUN:
        console.print("[yellow]DRY RUN: Order NOT sent to broker.[/yellow]")
        return True

    # LIVE ORDER: Fyers Bracket Order
    data = {
        "symbol": SYMBOL,
        "qty": QUANTITY,
        "type": 2, # 2 = Market Order
        "side": side,
        "productType": "BO", # Bracket Order
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "offlineOrder": "False",
        "stopLoss": round(sl_price, 2),
        "takeProfit": round(tp_price, 2)
    }
    
    try:
        response = fyers.place_order(data=data)
        console.print(f"[cyan]Broker Response: {response}[/cyan]")
        return True
    except Exception as e:
        console.print(f"[red]Order Failed: {e}[/red]")
        return False

def check_if_in_position(fyers, symbol):
    """Checks Fyers to see if we currently hold an open position for the symbol."""
    if DRY_RUN:
        return False # In DRY_RUN, always return False so it keeps looking for new test signals
        
    try:
        positions = fyers.positions()["netPositions"]
        for pos in positions:
            if pos["symbol"] == symbol and int(pos["netQty"]) != 0:
                return True
        return False
    except Exception as e:
        console.print(f"[red]Error checking positions: {e}[/red]")
        return True # Fail safe: assume we are in position if API fails

if __name__ == "__main__":
    # Use the new function name here
    config = load_fyers_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    fyers = auth.fyers
    data_fetcher = DataFetcher(fyers)
    
    console.print(Panel(f"[bold cyan]Starting Live Bot for {SYMBOL} | TF: {RESOLUTION}m\nDRY RUN: {DRY_RUN}", title="🤖 LIVE TRADING BOT INITIALIZED"))
    
    console.print("[cyan]Fetching historical data...[/cyan]")
    df = data_fetcher.get_historical_data(symbol=SYMBOL, resolution=RESOLUTION, days=5)
    df = calculate_indicators(df)
    
    console.print("[green]Bot is live. Monitoring market...[/green]")
    
    # Track the last time we checked to prevent double-checking the same candle
    last_checked_candle_time = None
    
    try:
        while True:
            now = datetime.datetime.now()
            
            # Only check during market hours (09:15 to 15:30)
            if now.time() < datetime.time(9, 15) or now.time() >= datetime.time(15, 30):
                time.sleep(30)
                continue
            
            # Convert resolution to integer (e.g., "1" -> 1, "5" -> 5)
            tf_minutes = int(RESOLUTION)
            
            # Check if the current minute is perfectly divisible by the timeframe
            # e.g., if TF is 5, it triggers on minutes 0, 5, 10, 15...
            # e.g., if TF is 1, it triggers on every minute 0, 1, 2, 3...
            is_candle_close = (now.minute % tf_minutes == 0)
            
            # Give a 5-second buffer to ensure Fyers has updated the closed candle
            if is_candle_close and now.second >= 5:
                current_candle_time = now.replace(second=0, microsecond=0)
                
                # Only proceed if we haven't checked this specific candle yet
                if current_candle_time != last_checked_candle_time:
                    last_checked_candle_time = current_candle_time
                    
                    # Auto-Check: Are we already in a trade?
                    in_position = check_if_in_position(fyers, SYMBOL)
                    
                    if not in_position:
                        console.print(f"[dim]{now.strftime('%H:%M:%S')} - Checking for signals...[/dim]")
                        
                        # Fetch latest data
                        live_df = data_fetcher.get_historical_data(symbol=SYMBOL, resolution=RESOLUTION, days=5)
                        live_df = calculate_indicators(live_df)
                        
                        last_row = live_df.iloc[-1]
                        
                        # Time Filter (09:30 to 15:10)
                        if last_row.name.time() >= datetime.time(9, 30) and last_row.name.time() <= datetime.time(15, 10):
                            
                            if last_row['buy_signal']:
                                if place_order(fyers, "BUY", last_row['Close'], last_row['atr']):
                                    in_position = True
                                    
                            elif last_row['sell_signal']:
                                if place_order(fyers, "SELL", last_row['Close'], last_row['atr']):
                                    in_position = True
                
            # Sleep 2 seconds before checking the clock again
            time.sleep(2)
            
    except KeyboardInterrupt:
        console.print("[bold red]\nBot stopped manually.[/bold red]")