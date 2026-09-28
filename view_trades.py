import pandas as pd
from strategycopy2 import Strategy
from backtester_enhanced import EnhancedBacktester

print("Loading data...")
# Path to your 1m options data
df = pd.read_csv("./data/otpion/NSE_BANKNIFTY26JUL57000PE_1m_100d.csv", index_col="datetime", parse_dates=["datetime"])

print("Generating Smart EMA signals...")
strat = Strategy("smart_ema")
df_signals = strat.generate_signals(df.copy())
df_signals['atr'] = strat.calculate_atr(df_signals)

# Drop NaNs for the backtester
df_signals.dropna(subset=['close', 'atr'], inplace=True)

print("Running backtest with winning parameters (1.0x SL / 10.0x TP)...")
backtester = EnhancedBacktester()

# Run the specific backtest
result = backtester.run_backtest(
    df=df_signals,
    symbol="NSE:BANKNIFTY26JUL57000PE",
    strategy_name="smart_ema",
    params={'sl_mult': 1.0, 'tp_mult': 10.0},
    sl_multiplier=1.0,
    tp_multiplier=10.0,
    use_atr_sl=True,
    use_time_filter=True,
    use_trend_filter=True
)

# Extract the trades DataFrame
if 'trades_df' in result:
    trades_df = result['trades_df']
    
    # Save to CSV
    trades_df.to_csv("smart_ema_trades.csv", index=False)
    print(f"\n✅ Success! Saved {len(trades_df)} trades to 'smart_ema_trades.csv'.")
    print("Open this CSV in Excel to see every entry and exit.")
    
    # Also print a quick summary to the console
    print("\n--- Trade Log Preview ---")
    print(trades_df[['entry_date', 'exit_date', 'entry_price', 'exit_price', 'pnl', 'reason']].head(10))
else:
    print("\n[Yellow] The backtester did not return a 'trades_df' key.")