# label_trades.py
import pandas as pd
import numpy as np
from backtester import Backtester
from fyers_auth import FyersAuth
from datetime import datetime, timedelta
import yaml

def load_config(config_path="config.yaml"):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

def label_trades_for_ml():
    config = load_config()
    
    auth = FyersAuth(
        app_id=config["fyers"]["app_id"],
        secret_key=config["fyers"]["secret_key"]
    )
    auth.set_access_token(config["fyers"]["access_token"])
    fyers_client = auth.fyers
    
    backtester = Backtester(fyers_client)
    
    # Backtest period
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180)
    
    all_labeled_trades = []
    
    for symbol_config in config["symbols"]:
        symbol = symbol_config["symbol"]
        strategy_name = symbol_config["strategy"]
        params = symbol_config.get("params", {})
        
        print(f"Backtesting {symbol} with {strategy_name}...")
        
        result = backtester.run_backtest(
            symbol=symbol,
            strategy_name=strategy_name,
            resolution=config["trading"]["resolution"],
            start_date=start_date,
            end_date=end_date,
            initial_capital=100000
        )
        
        if not result or "trades" not in result:
            continue
        
        trades = result["trades"]
        if not trades:
            continue
        
        # Fetch full historical data for feature extraction
        df_full = backtester.data_fetcher.get_historical_data(
            symbol=symbol,
            resolution=config["trading"]["resolution"],
            days=180  # match backtest period
        )
        if df_full is None:
            continue
        
        # Add indicators (same as Strategy.prepare_data)
        from indicators import Indicators
        ind = Indicators()
        df_full = ind.ema(df_full, 9)
        df_full = ind.ema(df_full, 21)
        df_full = ind.rsi(df_full, 14)
        df_full = ind.macd(df_full)
        df_full = ind.supertrend(df_full, 10, 3)
        df_full = ind.bollinger_bands(df_full, 20, 2)
        df_full = ind.vwap(df_full)
        df_full["volume_ratio"] = df_full["volume"] / df_full["volume"].rolling(20).mean()
        df_full["vwap_dist"] = (df_full["close"] - df_full["vwap"]) / df_full["vwap"]
        
        for trade in trades:
            entry_date = trade["entry_date"]
            exit_date = trade["exit_date"]
            pnl = trade["pnl"]
            
            # Find entry bar (closest bar after entry_date)
            entry_idx = df_full.index[df_full.index >= entry_date].min()
            if pd.isna(entry_idx):
                continue
            
            entry_row = df_full.loc[entry_idx]
            
            # Label: 1 if profitable, 0 otherwise
            label = 1 if pnl > 0 else 0
            
            # Features at entry
            features = {
                "symbol": symbol,
                "strategy": strategy_name,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "pnl": pnl,
                "label": label,
                "rsi": entry_row.get("rsi", 0),
                "macd_hist": entry_row.get("macd_hist", 0),
                "supertrend_dir": entry_row.get("supertrend_direction", 0),
                "volume_ratio": entry_row.get("volume_ratio", 0),
                "vwap_dist": entry_row.get("vwap_dist", 0),
                "ema_9": entry_row.get("ema_9", 0),
                "ema_21": entry_row.get("ema_21", 0),
                "bb_upper": entry_row.get("bb_upper", 0),
                "bb_lower": entry_row.get("bb_lower", 0),
            }
            
            all_labeled_trades.append(features)
    
    if all_labeled_trades:
        df_trades = pd.DataFrame(all_labeled_trades)
        df_trades.to_csv("labeled_trades.csv", index=False)
        print(f"Saved {len(df_trades)} labeled trades to labeled_trades.csv")
        
        # Quick stats
        print(f"Profitable trades: {len(df_trades[df_trades['label'] == 1])}")
        print(f"Loss trades: {len(df_trades[df_trades['label'] == 0])}")
    else:
        print("No trades to label.")

if __name__ == "__main__":
    label_trades_for_ml()