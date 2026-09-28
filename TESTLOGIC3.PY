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

fyers = fyersModel.FyersModel(client_id=APP_ID, token=ACCESS_TOKEN, is_async=False, log_path="")

# ═══════════════════════════════════════════════════════════════
# 2. CONFIG & PARAMETERS
# ═══════════════════════════════════════════════════════════════
RESOLUTION   = "1"
DAYS_HISTORY = 25  # 1 Month Stress Test
CONT_FLAG    = "1"

SWING_LEN          = 3         
DISPLACEMENT_ATR   = 0.5      
RISK_REWARD        = 2.0
ATR_PERIOD         = 14
INITIAL_CAPITAL    = 100_000
RISK_PER_TRADE     = 0.01  
SWEEP_LOOKBACK     = 3         

# ═══════════════════════════════════════════════════════════════
# 3. FYERS DATA FETCHER
# ═══════════════════════════════════════════════════════════════
def fetch_fyers_history(symbol, resolution, days, cont_flag="1"):
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    all_rows, cursor = [], end_dt

    while cursor > start_dt:
        win_end = cursor
        win_start = cursor - timedelta(days=50)
        if win_start < start_dt: win_start = start_dt
        payload = {
            "symbol": symbol, "resolution": resolution, "date_format": "0",
            "range_from": int(win_start.timestamp()), "range_to": int(win_end.timestamp()),
            "cont_flag": cont_flag
        }
        resp = fyers.history(payload)
        if resp.get("s") != "ok": return pd.DataFrame()
        candles = resp.get("candles", [])
        if not candles: break
        all_rows.extend(candles)
        cursor = win_start - timedelta(seconds=1)

    if not all_rows: return pd.DataFrame()
    df = pd.DataFrame(all_rows, columns=["epoch","open","high","low","close","volume"])
    df = df.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
    df["Datetime"] = pd.to_datetime(df["epoch"], unit="s") + pd.Timedelta(hours=5, minutes=30)
    return df[["Datetime","open","high","low","close","volume"]].dropna()

# ═══════════════════════════════════════════════════════════════
# 4. HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low']  - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def find_pivots(df, left=3, right=3):
    df['pivot_high'] = np.nan
    df['pivot_low']  = np.nan
    highs, lows = df['high'].values, df['low'].values
    for i in range(left, len(df) - right):
        if highs[i] == highs[i-left:i+right+1].max(): df.loc[df.index[i], 'pivot_high'] = highs[i]
        if lows[i] == lows[i-left:i+right+1].min(): df.loc[df.index[i], 'pivot_low'] = lows[i]
    return df

# ═══════════════════════════════════════════════════════════════
# 5. BACKTEST FUNCTION (Breakout Logic)
# ═══════════════════════════════════════════════════════════════
def run_backtest(symbol):
    global trades
    df = fetch_fyers_history(symbol, RESOLUTION, DAYS_HISTORY)
    if df.empty or len(df) < 30: return
        
    df['atr'] = calc_atr(df, ATR_PERIOD)
    df = find_pivots(df, left=SWING_LEN, right=SWING_LEN)
    
    last_swing_high, last_swing_low = np.nan, np.nan
    idm_level, idm_is_bullish = np.nan, False
    bull_sweep_active, bull_sweep_bar = False, -999
    bear_sweep_active, bear_sweep_bar = False, -999

    for i in range(SWING_LEN * 2, len(df)):
        if not np.isnan(df['pivot_high'].iloc[i]): last_swing_high = df['pivot_high'].iloc[i]
        if not np.isnan(df['pivot_low'].iloc[i]): last_swing_low = df['pivot_low'].iloc[i]

        if (not np.isnan(last_swing_high) and df['close'].iloc[i] > last_swing_high and df['close'].iloc[i-1] <= last_swing_high):
            idm_level, idm_is_bullish = last_swing_low, True
        if (not np.isnan(last_swing_low) and df['close'].iloc[i] < last_swing_low and df['close'].iloc[i-1] >= last_swing_low):
            idm_level, idm_is_bullish = last_swing_high, False

        if not np.isnan(idm_level):
            if idm_is_bullish:
                if (df['low'].iloc[i-1] > idm_level and df['low'].iloc[i] < idm_level):
                    bull_sweep_active, bull_sweep_bar = True, i
            else:
                if (df['high'].iloc[i-1] < idm_level and df['high'].iloc[i] > idm_level):
                    bear_sweep_active, bear_sweep_bar = True, i

        if i - bull_sweep_bar > SWEEP_LOOKBACK: bull_sweep_active = False
        if i - bear_sweep_bar > SWEEP_LOOKBACK: bear_sweep_active = False

        body, atr_val = abs(df['close'].iloc[i] - df['open'].iloc[i]), df['atr'].iloc[i]
        current_time = df['Datetime'].iloc[i].time()
        is_morning = current_time >= pd.Timestamp("09:15:00").time() and current_time <= pd.Timestamp("11:30:00").time()
        is_afternoon = current_time >= pd.Timestamp("13:30:00").time() and current_time <= pd.Timestamp("15:15:00").time()

        if body > atr_val * DISPLACEMENT_ATR and (is_morning or is_afternoon):
            if (df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i] > df['high'].iloc[i-1] and bull_sweep_active):
                entry_price = df['close'].iloc[i] + 0.50
                stop_loss = df['low'].iloc[i] - df['atr'].iloc[i] * 0.5
                risk_per_share = entry_price - stop_loss
                if risk_per_share > 0:
                    risk_amt = INITIAL_CAPITAL * RISK_PER_TRADE
                    lots = max(1, int((risk_amt / risk_per_share) / 75))
                    take_profit = entry_price + (risk_per_share * RISK_REWARD) - 0.50
                    for j in range(i+1, len(df)):
                        if df['low'].iloc[j] <= stop_loss:
                            trades.append({'Symbol': symbol, 'Result': 'SL', 'Pnl': -risk_amt}); break
                        elif df['high'].iloc[j] >= take_profit:
                            trades.append({'Symbol': symbol, 'Result': 'TP', 'Pnl': risk_amt * RISK_REWARD}); break
                bull_sweep_active = False
                
            elif (df['close'].iloc[i] < df['open'].iloc[i] and df['close'].iloc[i] < df['low'].iloc[i-1] and bear_sweep_active):
                entry_price = df['close'].iloc[i] - 0.50
                stop_loss = df['high'].iloc[i] + df['atr'].iloc[i] * 0.5
                risk_per_share = stop_loss - entry_price
                if risk_per_share > 0:
                    risk_amt = INITIAL_CAPITAL * RISK_PER_TRADE
                    lots = max(1, int((risk_amt / risk_per_share) / 75))
                    take_profit = entry_price - (risk_per_share * RISK_REWARD) + 0.50
                    for j in range(i+1, len(df)):
                        if df['high'].iloc[j] >= stop_loss:
                            trades.append({'Symbol': symbol, 'Result': 'SL', 'Pnl': -risk_amt}); break
                        elif df['low'].iloc[j] <= take_profit:
                            trades.append({'Symbol': symbol, 'Result': 'TP', 'Pnl': risk_amt * RISK_REWARD}); break
                bear_sweep_active = False

# ═══════════════════════════════════════════════════════════════
# 6. MAIN RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    spot_df = fetch_fyers_history("NSE:NIFTY50-INDEX", "1", 1)
    if spot_df.empty: exit()
    atm_strike = int(round(spot_df['close'].iloc[-1] / 50.0) * 50)

    today = datetime.now()
    days_ahead = 1 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    elif days_ahead == 0 and today.hour >= 16: days_ahead += 7
    exp_date = today + timedelta(days=days_ahead)
    prefix = f"NSE:NIFTY{exp_date.strftime('%y')}{str(int(exp_date.strftime('%m')))}{exp_date.strftime('%d')}"

    SYMBOLS = [f"{prefix}{s}{'CE' if t=='C' else 'PE'}" for s in [atm_strike-100, atm_strike-50, atm_strike, atm_strike+50, atm_strike+100] for t in ['C','P']]
    
    print(f"Testing {len(SYMBOLS)} symbols over {DAYS_HISTORY} days...")
    trades = []
    for sym in SYMBOLS:
        print(f"Processing {sym}...")
        run_backtest(sym)
            
    print("\n" + "="*60)
    print("SMC BREAKOUT BACKTEST SUMMARY")
    print("="*60)
    if trades:
        df_tr = pd.DataFrame(trades)
        summary = df_tr.groupby('Symbol').agg(Trades=('Result','count'), WinRate=('Result', lambda x: (x=='TP').mean()*100), TotalPnl=('Pnl','sum')).reset_index()
        print(summary.to_string(index=False))
        print(f"\nTotal Trades: {len(df_tr)} | Overall Win Rate: {(df_tr['Result']=='TP').mean()*100:.2f}% | Net Pnl: ₹{df_tr['Pnl'].sum():.2f}")
    else:
        print("No trades generated.")