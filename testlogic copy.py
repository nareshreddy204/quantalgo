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
# 2. TIMEFRAME CONFIG
# ═══════════════════════════════════════════════════════════════
RESOLUTION   = "1"
DAYS_HISTORY = 25
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
    
    # CONVERT UTC TO IST (Add 5 hours 30 minutes)
    df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
    
    return df[["Datetime","open","high","low","close","volume"]].dropna()

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
# 6. CORE BACKTEST FUNCTION (Runs SMC logic for 1 symbol)
# ═══════════════════════════════════════════════════════════════
def run_backtest(symbol):
    print(f"\nProcessing {symbol} ...")
    df = fetch_fyers_history(symbol, RESOLUTION, DAYS_HISTORY)
    
    if df.empty:
        print(f"❌ No data returned for {symbol}. Skipping.")
        return None
        
    print(f"  Loaded {len(df)} candles")
    
    df['atr'] = calc_atr(df, ATR_PERIOD)
    df = find_pivots(df, left=SWING_LEN, right=SWING_LEN)
    
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

    # --- Backtest Engine ---
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
            current_time = row['Datetime'].time()
            # Only trade between 9:15 AM - 11:30 AM OR 1:30 PM - 3:15 PM IST
            is_morning = current_time >= pd.Timestamp("09:15:00").time() and current_time <= pd.Timestamp("11:30:00").time()
            is_afternoon = current_time >= pd.Timestamp("13:30:00").time() and current_time <= pd.Timestamp("15:15:00").time()
            
            if is_morning or is_afternoon:
                LOT_SIZE = 65
                SLIPPAGE = 0.50 
            
                if row['long_signal']:
                    entry_price = row['close'] + SLIPPAGE  # You buy higher (Ask)
                    stop_loss   = row['low']  - row['atr'] * 0.5
                    
                    risk_per_share = entry_price - stop_loss
                    if risk_per_share > 0:
                        risk_amt = capital * RISK_PER_TRADE
                        raw_qty = risk_amt / risk_per_share
                        lots = max(1, int(raw_qty / LOT_SIZE))
                        qty = lots * LOT_SIZE
                        
                        take_profit = entry_price + (risk_per_share * RISK_REWARD) - SLIPPAGE
                        position    = 1
                        
                elif row['short_signal']:
                    entry_price = row['close'] - SLIPPAGE  # You sell lower (Bid)
                    stop_loss   = row['high'] + row['atr'] * 0.5
                    
                    risk_per_share = stop_loss - entry_price
                    if risk_per_share > 0:
                        risk_amt = capital * RISK_PER_TRADE
                        raw_qty = risk_amt / risk_per_share
                        lots = max(1, int(raw_qty / LOT_SIZE))
                        qty = lots * LOT_SIZE
                        
                        take_profit = entry_price - (risk_per_share * RISK_REWARD) + SLIPPAGE
                        position    = -1

    trades_df = pd.DataFrame(trades)
    
    # --- Calculate Metrics ---
    total_return = ((capital/INITIAL_CAPITAL)-1)*100
    total_trades = len(trades_df)
    win_rate = 0
    pf = 0
    
    if total_trades > 0:
        win_rate = (trades_df['result'] == 'TP').mean() * 100
        total_profit = trades_df[trades_df['result']=='TP']['pnl_amt'].sum()
        total_loss   = abs(trades_df[trades_df['result']=='SL']['pnl_amt'].sum())
        pf = total_profit / total_loss if total_loss > 0 else float('inf')

    safe_filename = symbol.replace(":", "_") + ".csv"
    if not trades_df.empty:
        trades_df.to_csv(safe_filename, index=False)

    return {
        "Symbol": symbol,
        "Trades": total_trades,
        "Return (%)": total_return,
        "Win Rate (%)": win_rate,
        "Profit Factor": pf
    }

# ═══════════════════════════════════════════════════════════════
# 7. DYNAMIC EXPIRY & MULTI-SYMBOL RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Fetching current Nifty Spot price to calculate ATM Strike...")
    spot_df = fetch_fyers_history("NSE:NIFTY50-INDEX", "1", 1)
    
    if spot_df.empty:
        print("❌ Could not fetch Nifty Spot. Check API connection.")
        exit()
        
    current_spot = spot_df['close'].iloc[-1]
    print(f"✅ Current Nifty Spot: {current_spot}")

    # Round to nearest 50
    atm_strike = int(round(current_spot / 50.0) * 50)
    print(f"✅ ATM Strike calculated: {atm_strike}")

    # --- DYNAMIC EXPIRY CALCULATION ---
    # Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4
    # For Nifty Weekly -> Tuesday (1)
    # For Sensex Weekly -> Thursday (3)
    target_weekday = 1  # 1 for Tuesday (Nifty)
    
    today = datetime.now()
    days_ahead = target_weekday - today.weekday()
    
    # If target day has already passed this week, or if it's target day but after 3:30 PM (market closed)
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0 and today.hour >= 15 and today.minute > 30:
        days_ahead += 7
        
    expiry_date = today + timedelta(days=days_ahead)
    
    # Format as YYMDD (e.g., 26908 for 8 Sep 2026, 261008 for 8 Oct 2026)
    yy = expiry_date.strftime("%y")
    mm = str(int(expiry_date.strftime("%m")))  # Converts "09" to "9"
    dd = expiry_date.strftime("%d")
    
    expiry_str = f"{yy}{mm}{dd}"
    
    BASE_PREFIX = f"NSE:NIFTY{expiry_str}"
    print(f"✅ Dynamic Expiry calculated: {expiry_date.strftime('%d %b %Y')} -> {BASE_PREFIX}")

    # Generate strikes: ATM-100, ATM-50, ATM, ATM+50, ATM+100
    strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
    
    SYMBOLS = []
    for strike in strikes:
        SYMBOLS.append(f"{BASE_PREFIX}{strike}CE")
        SYMBOLS.append(f"{BASE_PREFIX}{strike}PE")
        
    print(f"✅ Generated {len(SYMBOLS)} dynamic symbols to test.")

    # Run Backtests
    results = []
    for sym in SYMBOLS:
        res = run_backtest(sym)
        if res:
            results.append(res)
            
    # Print Final Summary Table
    print("\n" + "="*70)
    print("DYNAMIC MULTI-SYMBOL BACKTEST SUMMARY")
    print("="*70)
    
    if results:
        summary_df = pd.DataFrame(results)
        print(summary_df.to_string(index=False))
        summary_df.to_csv("smc_dynamic_summary.csv", index=False)
        print("\n✅ Summary saved to smc_dynamic_summary.csv")
        print("✅ Individual trade logs saved as <SYMBOL_NAME>.csv")
    else:
        print("No valid data fetched for any symbol.")