# pip install fyers_api pandas numpy pyyaml requests

import os
import yaml
import pandas as pd
import numpy as np
import concurrent.futures
import requests
import time
import warnings
from datetime import datetime, timedelta
warnings.filterwarnings("ignore")

from fyers_apiv3 import fyersModel

# ═══════════════════════════════════════════════════════════════
# 1. CONFIG LOADER (FYERS + TELEGRAM)
# ═══════════════════════════════════════════════════════════════
def load_config():
    config = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f) or {}
    
    # Fyers Creds
    fyers_cfg = config.get("fyers", {})
    app_id = fyers_cfg.get("app_id", "YOUR_APP_ID")
    
    # Telegram Creds
    tg_cfg = config.get("telegram", {})
    bot_token = tg_cfg.get("bot_token", "YOUR_BOT_TOKEN")
    chat_id = tg_cfg.get("chat_id", "YOUR_CHAT_ID")
    
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            access_token = f.read().strip()
    else:
        access_token = "YOUR_ACCESS_TOKEN"
        
    return app_id, access_token, bot_token, chat_id

APP_ID, ACCESS_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = load_config()

if "YOUR_" in APP_ID or "YOUR_" in ACCESS_TOKEN:
    print("⚠️ WARNING: Fyers credentials not found in config.yaml!")
    exit()

if "YOUR_" in TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_CHAT_ID:
    print("⚠️ WARNING: Telegram credentials not found in config.yaml!")

fyers = fyersModel.FyersModel(client_id=APP_ID, token=ACCESS_TOKEN, is_async=False, log_path="")

def send_telegram_message(message):
    if "YOUR_" in TELEGRAM_BOT_TOKEN or "YOUR_" in TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

# ═══════════════════════════════════════════════════════════════
# 2. FYERS DATA FETCHER
# ═══════════════════════════════════════════════════════════════
def fetch_fyers_history(symbol, resolution, days, cont_flag="1"):
    end_dt, start_dt = datetime.now(), datetime.now() - timedelta(days=days)
    all_rows, cursor = [], end_dt
    while cursor > start_dt:
        win_start = cursor - timedelta(days=50)
        if win_start < start_dt: win_start = start_dt
        resp = fyers.history({
            "symbol": symbol, "resolution": resolution, "date_format": "0",
            "range_from": int(win_start.timestamp()), "range_to": int(cursor.timestamp()),
            "cont_flag": cont_flag
        })
        if resp.get("s") != "ok": return pd.DataFrame()
        if not resp.get("candles"): break
        all_rows.extend(resp.get("candles"))
        cursor = win_start - timedelta(seconds=1)
    if not all_rows: return pd.DataFrame()
    df = pd.DataFrame(all_rows, columns=["epoch","open","high","low","close","volume"]).drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
    df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
    return df[["Datetime","open","high","low","close","volume"]].dropna()

# ═══════════════════════════════════════════════════════════════
# 3. SMC PARAMETERS & HELPERS
# ═══════════════════════════════════════════════════════════════
SWING_LEN, DISPLACEMENT_ATR, ATR_PERIOD, SWEEP_LOOKBACK = 3, 0.5, 14, 3

def calc_atr(df, p=14):
    return pd.concat([df['high']-df['low'], np.abs(df['high']-df['close'].shift()), np.abs(df['low']-df['close'].shift())], axis=1).max(axis=1).rolling(p).mean()

def find_pivots(df, l=3, r=3):
    df['pivot_high'], df['pivot_low'] = np.nan, np.nan
    h, lws = df['high'].values, df['low'].values
    for i in range(l, len(df)-r):
        if h[i] == h[i-l:i+r+1].max(): df.loc[df.index[i], 'pivot_high'] = h[i]
        if lws[i] == lws[i-l:i+r+1].min(): df.loc[df.index[i], 'pivot_low'] = lws[i]
    return df

# ═══════════════════════════════════════════════════════════════
# 4. SCANNER LOGIC (SWEEP -> DISPLACEMENT -> ENTER)
# ═══════════════════════════════════════════════════════════════
def scan_symbol(symbol):
    df = fetch_fyers_history(symbol, "1", 1)
    if df.empty or len(df) < 30: return {"Symbol": symbol, "Signal": "No Data", "Time": None, "Price": None}
    df['atr'] = calc_atr(df)
    df = find_pivots(df)
    lsh, lsl, idm, idm_b = np.nan, np.nan, np.nan, False
    bs_a, bs_b, br_a, br_b = False, -999, False, -999

    for i in range(SWING_LEN*2, len(df)):
        if not np.isnan(df['pivot_high'].iloc[i]): lsh = df['pivot_high'].iloc[i]
        if not np.isnan(df['pivot_low'].iloc[i]): lsl = df['pivot_low'].iloc[i]
        if not np.isnan(lsh) and df['close'].iloc[i] > lsh and df['close'].iloc[i-1] <= lsh: idm, idm_b = lsl, True
        if not np.isnan(lsl) and df['close'].iloc[i] < lsl and df['close'].iloc[i-1] >= lsl: idm, idm_b = lsh, False
        if not np.isnan(idm):
            if idm_b and df['low'].iloc[i-1] > idm and df['low'].iloc[i] < idm: bs_a, bs_b = True, i
            elif not idm_b and df['high'].iloc[i-1] < idm and df['high'].iloc[i] > idm: br_a, br_b = True, i
        if i - bs_b > SWEEP_LOOKBACK: bs_a = False
        if i - br_b > SWEEP_LOOKBACK: br_a = False
        
        body, atr_val = abs(df['close'].iloc[i]-df['open'].iloc[i]), df['atr'].iloc[i]
        if body > atr_val * DISPLACEMENT_ATR:
            if df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i] > df['high'].iloc[i-1] and bs_a and i == len(df)-1:
                return {"Symbol": symbol, "Signal": "🟢 LONG", "Time": df['Datetime'].iloc[i], "Price": df['close'].iloc[i]}
            elif df['close'].iloc[i] < df['open'].iloc[i] and df['close'].iloc[i] < df['low'].iloc[i-1] and br_a and i == len(df)-1:
                return {"Symbol": symbol, "Signal": "🔴 SHORT", "Time": df['Datetime'].iloc[i], "Price": df['close'].iloc[i]}
    return {"Symbol": symbol, "Signal": "⚪ No Signal", "Time": None, "Price": None}

# ═══════════════════════════════════════════════════════════════
# 5. CONTINUOUS SCANNER LOOP
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🤖 Breakout Scanner Started. Press Ctrl+C to stop.")
    last_alert = {}
    
    while True:
        now = datetime.now()
        
        # Check if market is open (9:15 AM to 3:40 PM)
        if now.time() < pd.Timestamp("09:15:00").time() or now.time() > pd.Timestamp("15:40:00").time():
            print(f"[{now.strftime('%H:%M:%S')}] Market closed. Sleeping 60s...")
            time.sleep(60)
            continue
            
        try:
            print(f"\n[{now.strftime('%H:%M:%S')}] Scanning 10 symbols...")
            spot_df = fetch_fyers_history("NSE:NIFTY50-INDEX", "1", 1)
            if spot_df.empty: 
                time.sleep(60); continue
                
            atm = int(round(spot_df['close'].iloc[-1] / 50.0) * 50)
            
            # Calculate next Tuesday expiry
            days_ahead = 1 - now.weekday()
            if days_ahead < 0: days_ahead += 7
            elif days_ahead == 0 and now.hour >= 16: days_ahead += 7
            exp = now + timedelta(days=days_ahead)
            pfx = f"NSE:NIFTY{exp.strftime('%y')}{str(int(exp.strftime('%m')))}{exp.strftime('%d')}"
            
            # Generate symbols dynamically
            syms = [f"{pfx}{s}{t}" for s in [atm-100, atm-50, atm, atm+50, atm+100] for t in ["CE","PE"]]
            
            # Multi-thread scan
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                futs = {ex.submit(scan_symbol, s): s for s in syms}
                for f in concurrent.futures.as_completed(futs): results.append(f.result())
            
            # Check for active signals
            act = [r for r in results if "No" not in r['Signal']]
            if act:
                msg, new_sig = "🚨 <b>SMC BREAKOUT ALERT</b> 🚨\n\n", False
                for s in act:
                    if last_alert.get(s['Symbol']) != s['Time']:
                        line = f"{s['Signal']} on {s['Symbol']}\nPrice: ₹{s['Price']:.2f} | Time: {s['Time'].strftime('%H:%M:%S')}"
                        print(f"  🚨 {line}")
                        msg += line + "\n\n"
                        last_alert[s['Symbol']] = s['Time']
                        new_sig = True
                if new_sig: 
                    send_telegram_message(msg)
                    print("✅ Sent to Telegram!")
            else: 
                print(f"⚪ No active signals on the latest 1-minute candle.")
            
            # Sleep until next minute
            slp = 60 - datetime.now().second
            time.sleep(slp)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(30)