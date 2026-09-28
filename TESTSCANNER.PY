# pip install fyers_api pandas numpy pyyaml requests

import os
import yaml
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import concurrent.futures
import requests
import time
warnings.filterwarnings("ignore")

from fyers_apiv3 import fyersModel

# ═══════════════════════════════════════════════════════════════
# 1. TELEGRAM CONFIG
# ═══════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID"

def send_telegram_message(message):
    if "YOUR_" in TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_CHAT_ID:
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
        print(f"Telegram Error: {e}")

# ═══════════════════════════════════════════════════════════════
# 2. FYERS AUTHENTICATION
# ═══════════════════════════════════════════════════════════════
def load_config():
    config = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f) or {}
    
    fyers_cfg = config.get("fyers", {})
    app_id = fyers_cfg.get("app_id", "YOUR_APP_ID")
    
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            access_token = f.read().strip()
    else:
        access_token = "YOUR_ACCESS_TOKEN"
        
    return app_id, access_token

APP_ID, ACCESS_TOKEN = load_config()

if "YOUR_" in APP_ID or "YOUR_" in ACCESS_TOKEN:
    print("⚠️ WARNING: Fyers credentials not found!")
    exit()

fyers = fyersModel.FyersModel(
    client_id    = APP_ID,
    token        = ACCESS_TOKEN,
    is_async     = False,
    log_path     = ""
)

# ═══════════════════════════════════════════════════════════════
# 3. SCANNER CONFIG
# ═══════════════════════════════════════════════════════════════
RESOLUTION   = "1"
DAYS_HISTORY = 1
CONT_FLAG    = "1"

# ═══════════════════════════════════════════════════════════════
# 4. FYERS DATA FETCHER
# ═══════════════════════════════════════════════════════════════
def fetch_fyers_history(symbol, resolution, days, cont_flag="1"):
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    all_rows = []
    cursor   = end_dt

    while cursor > start_dt:
        win_end   = cursor
        win_start = cursor - timedelta(days=50)
        if win_start < start_dt:
            win_start = start_dt

        payload = {
            "symbol"     : symbol,
            "resolution" : resolution,
            "date_format": "0",
            "range_from" : int(win_start.timestamp()),
            "range_to"   : int(win_end.timestamp()),
            "cont_flag"  : cont_flag
        }

        resp = fyers.history(payload)

        if resp.get("s") != "ok":
            return pd.DataFrame()

        candles = resp.get("candles", [])
        if not candles:
            break

        all_rows.extend(candles)
        cursor = win_start - timedelta(seconds=1)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["epoch","open","high","low","close","volume"])
    df = df.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
    df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
    return df[["Datetime","open","high","low","close","volume"]].dropna()

# ═══════════════════════════════════════════════════════════════
# 5. PARAMETERS
# ═══════════════════════════════════════════════════════════════
SWING_LEN          = 3         
DISPLACEMENT_ATR   = 0.5      
ATR_PERIOD         = 14
SWEEP_LOOKBACK     = 3         

# ═══════════════════════════════════════════════════════════════
# 6. HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def calc_atr(df, period=14):
    high_low  = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close  = np.abs(df['low']  - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def find_pivots(df, left=3, right=3):
    df['pivot_high'] = np.nan
    df['pivot_low']  = np.nan
    highs = df['high'].values
    lows  = df['low'].values

    for i in range(left, len(df) - right):
        window_h = highs[i-left:i+right+1]
        window_l = lows[i-left:i+right+1]
        if highs[i] == window_h.max():
            df.loc[df.index[i], 'pivot_high'] = highs[i]
        if lows[i] == window_l.min():
            df.loc[df.index[i], 'pivot_low'] = lows[i]
    return df

# ═══════════════════════════════════════════════════════════════
# 7. SCANNER FUNCTION
# ═══════════════════════════════════════════════════════════════
def scan_symbol(symbol):
    df = fetch_fyers_history(symbol, RESOLUTION, DAYS_HISTORY)
    
    if df.empty or len(df) < 30:
        return {"Symbol": symbol, "Signal": "No Data", "Time": None, "Price": None}
        
    df['atr'] = calc_atr(df, ATR_PERIOD)
    df = find_pivots(df, left=SWING_LEN, right=SWING_LEN)
    
    last_swing_high = np.nan
    last_swing_low  = np.nan
    idm_level       = np.nan
    idm_is_bullish  = False

    bull_sweep_active = False
    bull_sweep_bar    = -999
    bear_sweep_active = False
    bear_sweep_bar    = -999

    for i in range(SWING_LEN * 2, len(df)):
        if not np.isnan(df['pivot_high'].iloc[i]):
            last_swing_high = df['pivot_high'].iloc[i]
        if not np.isnan(df['pivot_low'].iloc[i]):
            last_swing_low = df['pivot_low'].iloc[i]

        if (not np.isnan(last_swing_high) and
            df['close'].iloc[i] > last_swing_high and
            df['close'].iloc[i-1] <= last_swing_high):
            idm_level      = last_swing_low
            idm_is_bullish = True

        if (not np.isnan(last_swing_low) and
            df['close'].iloc[i] < last_swing_low and
            df['close'].iloc[i-1] >= last_swing_low):
            idm_level      = last_swing_high
            idm_is_bullish = False

        if not np.isnan(idm_level):
            if idm_is_bullish:
                if (df['low'].iloc[i-1]  > idm_level and df['low'].iloc[i] < idm_level):
                    bull_sweep_active = True
                    bull_sweep_bar = i
            else:
                if (df['high'].iloc[i-1] < idm_level and df['high'].iloc[i] > idm_level):
                    bear_sweep_active = True
                    bear_sweep_bar = i

        if i - bull_sweep_bar > SWEEP_LOOKBACK:
            bull_sweep_active = False
        if i - bear_sweep_bar > SWEEP_LOOKBACK:
            bear_sweep_active = False

        body = abs(df['close'].iloc[i] - df['open'].iloc[i])
        atr_val = df['atr'].iloc[i]

        if body > atr_val * DISPLACEMENT_ATR:
            if (df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i] > df['high'].iloc[i-1]):
                if bull_sweep_active and i == len(df)-1:
                    return {"Symbol": symbol, "Signal": "🟢 LONG SIGNAL", "Time": df['Datetime'].iloc[i], "Price": df['close'].iloc[i]}
                    
            elif (df['close'].iloc[i] < df['open'].iloc[i] and df['close'].iloc[i] < df['low'].iloc[i-1]):
                if bear_sweep_active and i == len(df)-1:
                    return {"Symbol": symbol, "Signal": "🔴 SHORT SIGNAL", "Time": df['Datetime'].iloc[i], "Price": df['close'].iloc[i]}

    return {"Symbol": symbol, "Signal": "⚪ No Signal", "Time": None, "Price": None}

# ═══════════════════════════════════════════════════════════════
# 8. CONTINUOUS BOT LOOP
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🤖 SMC Scanner Bot Started. Press Ctrl+C to stop.")
    
    # Cache to prevent duplicate alerts
    last_alert_time = {}
    
    while True:
        now = datetime.now()
        current_time = now.time()
        
        # Check if market is open (9:15 AM to 3:40 PM)
        market_open = pd.Timestamp("09:15:00").time()
        market_close = pd.Timestamp("15:40:00").time()
        
        if current_time < market_open or current_time > market_close:
            print(f"[{now.strftime('%H:%M:%S')}] Market closed. Sleeping for 60 seconds...")
            time.sleep(60)
            continue
            
        try:
            print(f"\n[{now.strftime('%H:%M:%S')}] Fetching current Nifty Spot price...")
            spot_df = fetch_fyers_history("NSE:NIFTY50-INDEX", "1", 1)
            
            if spot_df.empty:
                print("❌ Could not fetch Nifty Spot. Retrying in 60s.")
                time.sleep(60)
                continue
                
            current_spot = spot_df['close'].iloc[-1]
            atm_strike = int(round(current_spot / 50.0) * 50)

            # Calculate next Tuesday expiry
            target_weekday = 1  # Tuesday
            today = datetime.now()
            days_ahead = target_weekday - today.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0 and today.hour >= 15 and today.minute > 30:
                days_ahead += 7
                
            expiry_date = today + timedelta(days=days_ahead)
            yy = expiry_date.strftime("%y")
            mm = str(int(expiry_date.strftime("%m")))
            dd = expiry_date.strftime("%d")
            expiry_str = f"{yy}{mm}{dd}"
            
            BASE_PREFIX = f"NSE:NIFTY{expiry_str}"

            strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
            SYMBOLS = []
            for strike in strikes:
                SYMBOLS.append(f"{BASE_PREFIX}{strike}CE")
                SYMBOLS.append(f"{BASE_PREFIX}{strike}PE")
                
            print(f"🚀 Scanning {len(SYMBOLS)} symbols for signals...")
            
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_sym = {executor.submit(scan_symbol, sym): sym for sym in SYMBOLS}
                for future in concurrent.futures.as_completed(future_to_sym):
                    results.append(future.result())
            
            active_signals = [r for r in results if "SIGNAL" in r['Signal'] and "No" not in r['Signal']]
            
            if active_signals:
                alert_msg = "🚨 <b>SMC SCANNER ALERT</b> 🚨\n\n"
                new_signal_found = False
                
                for sig in active_signals:
                    # Check if we already alerted this exact candle
                    if last_alert_time.get(sig['Symbol']) != sig['Time']:
                        line = f"{sig['Signal']} on {sig['Symbol']}\nPrice: ₹{sig['Price']:.2f} | Time: {sig['Time'].strftime('%H:%M:%S')}"
                        print(f"  🚨 {line}")
                        alert_msg += line + "\n\n"
                        last_alert_time[sig['Symbol']] = sig['Time']
                        new_signal_found = True
                
                if new_signal_found:
                    send_telegram_message(alert_msg)
                    print("✅ Signals sent to Telegram!")
            else:
                print("⚪ No active signals on the latest 1-minute candle.")
            
            # Sleep until the next minute starts
            # Calculate seconds remaining until the next minute
            seconds_to_sleep = 60 - datetime.now().second
            print(f"💤 Sleeping for {seconds_to_sleep} seconds until next 1-min candle close...")
            time.sleep(seconds_to_sleep)
            
        except Exception as e:
            print(f"❌ Error occurred: {e}")
            print("Sleeping 30 seconds before retrying...")
            time.sleep(30)