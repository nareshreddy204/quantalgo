# pip install fyers_api pandas numpy pyyaml

import os
import yaml
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

from fyers_apiv3 import fyersModel

# ═══════════════════════════════════════════════════════════════
# 1. FYERS AUTHENTICATION
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
# 2. SYMBOL & TIMEFRAME CONFIG
# ═══════════════════════════════════════════════════════════════
SYMBOL       = "NSE:NIFTY2690824000PE"
RESOLUTION   = "1"
DAYS_HISTORY = 5
CONT_FLAG    = "1"

# ═══════════════════════════════════════════════════════════════
# 3. FYERS DATA FETCHER
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
            print(f"Fyers history error: {resp.get('code')} - {resp.get('message')}")
            print(f"   Payload symbol used: {symbol}")
            break

        candles = resp.get("candles", [])
        if not candles:
            break

        all_rows.extend(candles)
        cursor = win_start - timedelta(seconds=1)

    if not all_rows:
        return pd.DataFrame(columns=["Datetime","open","high","low","close","volume"])

    df = pd.DataFrame(all_rows, columns=["epoch","open","high","low","close","volume"])
    df = df.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
    df["Datetime"] = pd.to_datetime(df["epoch"], unit="s")
    return df[["Datetime","open","high","low","close","volume"]].dropna()
# ═══════════════════════════════════════════════════════════════
# >>> MISSING BLOCK START <<<
# ═══════════════════════════════════════════════════════════════
df = fetch_fyers_history(SYMBOL, RESOLUTION, DAYS_HISTORY)

if df.empty:
    print("❌ No data returned. Check your SYMBOL — it may be invalid or expired.")
    print("   Example valid symbols:")
    print("     NSE:NIFTY50-INDEX        (spot index)")
    print("     NSE:RELIANCE-EQ          (stock)")
    print("     NSE:NIFTY25NOVFUT        (current month futures)")
    exit()

print(f"Data loaded: {len(df)} candles | {df['Datetime'].iloc[0]} → {df['Datetime'].iloc[-1]}")
# ═══════════════════════════════════════════════════════════════
# >>> MISSING BLOCK END <<<
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# 4. PARAMETERS
# ═══════════════════════════════════════════════════════════════
SWING_LEN          = 3         
DISPLACEMENT_ATR   = 0.5      
RISK_REWARD        = 2.0
ATR_PERIOD         = 14
INITIAL_CAPITAL    = 100_000
RISK_PER_TRADE     = 0.01     # 1% risk per trade
SWEEP_LOOKBACK     = 3         

# ═══════════════════════════════════════════════════════════════
# 5. HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def atr(df, period=14):
    high_low  = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close  = np.abs(df['low']  - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

df['atr'] = atr(df, ATR_PERIOD)

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

df = find_pivots(df, left=SWING_LEN, right=SWING_LEN)

# ═══════════════════════════════════════════════════════════════
# 6. CORE SMC LOGIC
# ═══════════════════════════════════════════════════════════════
df['bos_bull']    = False
df['bos_bear']    = False
df['idm']         = np.nan
df['idm_type']    = ""
df['sweep_long']  = False
df['sweep_short'] = False
df['disp_bull']   = False
df['disp_bear']   = False
df['long_signal'] = False
df['short_signal']= False

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

        df.loc[df.index[i], 'bos_bull'] = True
        idm_level      = last_swing_low
        idm_is_bullish = True
        df.loc[df.index[i], 'idm']      = idm_level
        df.loc[df.index[i], 'idm_type'] = 'bull'

    if (not np.isnan(last_swing_low) and
        df['close'].iloc[i] < last_swing_low and
        df['close'].iloc[i-1] >= last_swing_low):

        df.loc[df.index[i], 'bos_bear'] = True
        idm_level      = last_swing_high
        idm_is_bullish = False
        df.loc[df.index[i], 'idm']      = idm_level
        df.loc[df.index[i], 'idm_type'] = 'bear'

    if pd.isna(df['idm'].iloc[i]) and not np.isnan(idm_level):
        df.loc[df.index[i], 'idm']      = idm_level
        df.loc[df.index[i], 'idm_type'] = 'bull' if idm_is_bullish else 'bear'

    if not np.isnan(idm_level):
        if idm_is_bullish:
            if (df['low'].iloc[i-1]  > idm_level and
                df['low'].iloc[i]    < idm_level):
                
                df.loc[df.index[i], 'sweep_long'] = True
                bull_sweep_active = True
                bull_sweep_bar = i
                
        else:
            if (df['high'].iloc[i-1] < idm_level and
                df['high'].iloc[i]    > idm_level):
                
                df.loc[df.index[i], 'sweep_short'] = True
                bear_sweep_active = True
                bear_sweep_bar = i

    if i - bull_sweep_bar > SWEEP_LOOKBACK:
        bull_sweep_active = False
    if i - bear_sweep_bar > SWEEP_LOOKBACK:
        bear_sweep_active = False

    body     = abs(df['close'].iloc[i] - df['open'].iloc[i])
    atr_val  = df['atr'].iloc[i]

    if body > atr_val * DISPLACEMENT_ATR:
        if (df['close'].iloc[i] > df['open'].iloc[i] and
            df['close'].iloc[i] > df['high'].iloc[i-1]):
            
            df.loc[df.index[i], 'disp_bull'] = True
            
            if bull_sweep_active:
                df.loc[df.index[i], 'long_signal'] = True
                bull_sweep_active = False
                
        elif (df['close'].iloc[i] < df['open'].iloc[i] and
              df['close'].iloc[i] < df['low'].iloc[i-1]):
            
            df.loc[df.index[i], 'disp_bear'] = True
            
            if bear_sweep_active:
                df.loc[df.index[i], 'short_signal'] = True
                bear_sweep_active = False

# ═══════════════════════════════════════════════════════════════
# 7. BACKTEST ENGINE (With Proper Position Sizing)
# ═══════════════════════════════════════════════════════════════
capital      = INITIAL_CAPITAL
position     = 0
entry_price  = 0.0
stop_loss    = 0.0
take_profit  = 0.0
qty          = 0.0
trades       = []

for i in range(len(df)):
    row = df.iloc[i]

    # --- Exit Logic ---
    if position == 1: # Long
        if row['low'] <= stop_loss:
            pnl = qty * (stop_loss - entry_price)
            capital += pnl
            trades.append({'type':'Long','entry':entry_price,'exit':stop_loss,
                           'pnl_amt':pnl, 'result':'SL','time':row['Datetime']})
            position = 0
        elif row['high'] >= take_profit:
            pnl = qty * (take_profit - entry_price)
            capital += pnl
            trades.append({'type':'Long','entry':entry_price,'exit':take_profit,
                           'pnl_amt':pnl, 'result':'TP','time':row['Datetime']})
            position = 0

    elif position == -1: # Short
        if row['high'] >= stop_loss:
            pnl = qty * (entry_price - stop_loss)
            capital += pnl
            trades.append({'type':'Short','entry':entry_price,'exit':stop_loss,
                           'pnl_amt':pnl, 'result':'SL','time':row['Datetime']})
            position = 0
        elif row['low'] <= take_profit:
            pnl = qty * (entry_price - take_profit)
            capital += pnl
            trades.append({'type':'Short','entry':entry_price,'exit':take_profit,
                           'pnl_amt':pnl, 'result':'TP','time':row['Datetime']})
            position = 0

    # --- Entry Logic ---
    if position == 0:
        if row['long_signal']:
            entry_price = row['close']
            stop_loss   = row['low']  - row['atr'] * 0.5
            
            risk_per_share = entry_price - stop_loss
            if risk_per_share > 0:
                risk_amt = capital * RISK_PER_TRADE
                qty = risk_amt / risk_per_share       # Position size calculation
                take_profit = entry_price + risk_per_share * RISK_REWARD
                position    = 1
                
        elif row['short_signal']:
            entry_price = row['close']
            stop_loss   = row['high'] + row['atr'] * 0.5
            
            risk_per_share = stop_loss - entry_price
            if risk_per_share > 0:
                risk_amt = capital * RISK_PER_TRADE
                qty = risk_amt / risk_per_share       # Position size calculation
                take_profit = entry_price - risk_per_share * RISK_REWARD
                position    = -1

# ═══════════════════════════════════════════════════════════════
# 8. RESULTS
# ═══════════════════════════════════════════════════════════════
trades_df = pd.DataFrame(trades)

print("\n" + "="*60)
print("SMC 4-RULES BACKTEST (FYERS DATA)")
print("="*60)
print(f"Initial Capital : ₹{INITIAL_CAPITAL:,.0f}")
print(f"Final Capital   : ₹{capital:,.0f}")
print(f"Total Return    : {((capital/INITIAL_CAPITAL)-1)*100:.2f}%")
print(f"Total Trades    : {len(trades_df)}")

if len(trades_df) > 0:
    win_rate = (trades_df['result'] == 'TP').mean() * 100
    avg_win  = trades_df[trades_df['result']=='TP']['pnl_amt'].mean()
    avg_loss = trades_df[trades_df['result']=='SL']['pnl_amt'].mean()
    
    total_profit = trades_df[trades_df['result']=='TP']['pnl_amt'].sum()
    total_loss   = abs(trades_df[trades_df['result']=='SL']['pnl_amt'].sum())
    pf = total_profit / total_loss if total_loss > 0 else float('inf')

    print(f"Win Rate        : {win_rate:.1f}%")
    print(f"Avg Win (₹)     : ₹{avg_win:,.2f}")
    print(f"Avg Loss (₹)    : ₹{avg_loss:,.2f}")
    print(f"Profit Factor   : {pf:.2f}")
    print("\nLast 10 Trades:")
    print(trades_df.tail(10).to_string(index=False))
else:
    print("No trades generated.")

trades_df.to_csv("smc_4rules_trades_fyers.csv", index=False)