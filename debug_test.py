# debug_test.py
import pandas as pd
from strategycopy2 import Strategy
from backtester_enhanced import EnhancedBacktester

# 1. Load Data
print("Loading data...")
# Keep the CSV path the same, it's just the symbol string we are fixing
df = pd.read_csv("./data/indices/NSE_NIFTYBANK-INDEX_15m_365d.csv", index_col="datetime", parse_dates=True)
print(f"Loaded {len(df)} candles.")

# 2. Generate Signals
print("Generating EMA Crossover signals...")
strat = Strategy("ema_crossover")
df = strat.generate_signals(df)

if 'atr' not in df.columns:
    print("ATR missing. Calculating ATR now...")
    df['atr'] = strat.calculate_atr(df, 14)

state = df['signal'].fillna(0).astype(int)
trigger = state.diff().fillna(0).astype(int)
df['signal'] = trigger.clip(-1, 1)

# 3. Run Backtester directly
print("Running EnhancedBacktester directly...")
backtester = EnhancedBacktester()

params = {'sl_mult': 1.5, 'tp_mult': 2.0}

try:
    result = backtester.run_backtest(
        df=df.copy(),
        symbol="NSE:BANKNIFTY-INDEX",     # <--- FIXED SYMBOL NAME HERE!
        strategy_name="ema_crossover",
        params=params,
        initial_capital=2000000,  
        sl_multiplier=params['sl_mult'],
        tp_multiplier=params['tp_mult'],
        use_atr_sl=True,
        use_time_filter=False,
        use_trend_filter=False
    )
    
    print("\n--- BACKTESTER RESULT ---")
    print(f"Total Trades: {result.get('total_trades', 0)}")
    print(f"Win Rate: {result.get('win_rate', 0)}%")
    print(f"Net P&L: ₹{result.get('total_pnl', 0):,.2f}")
    
except Exception as e:
    print(f"\n❌ CRASH INSIDE BACKTESTER: {e}")
    import traceback
    traceback.print_exc()