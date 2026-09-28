# backtester_enhanced.py
import pandas as pd
import numpy as np
from strategycopy3 import Strategy
from datetime import time

class EnhancedBacktester:
    def __init__(self):
        cooldown_bars = 0
        self.commission_per_order = 0.0003  # 0.03% Fyers commission
        self.slippage_pts = 0.5  # Slippage in points
        
    def calculate_atr(self, df, period=14):
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr
    
    def is_good_trading_time(self, dt):
        """Filter out bad trading times"""
        t = dt.time()
        
        # Avoid first 15 mins (opening chaos)
        if t < time(9, 30):
            return False
        
        # Avoid lunch hour (12:00 - 1:00) - choppy
        if time(12, 0) <= t <= time(13, 0):
            return False
        
        # Avoid last 15 mins (closing volatility)
        if t >= time(15, 15):
            return False
            
        return True
    
    def is_trending(self, df, idx, lookback=50):
        """Check if market is trending using ADX logic"""
        if idx < lookback:
            return True  # Not enough data, allow trade
            
        slice_df = df.iloc[idx-lookback:idx]
        high = slice_df['high'].values
        low = slice_df['low'].values
        close = slice_df['close'].values
        
        # Simple trend check: Is price above/below moving average?
        sma = np.mean(close[-20:])
        current_price = close[-1]
        
        # Calculate price range to determine if trending
        price_range = (max(high) - min(low)) / sma
        
        # If range is too small, market is choppy
        if price_range < 0.01:  # Less than 1% range = choppy
            return False
            
        return True
    
    def run_backtest(self, df, symbol, strategy_name, params, 
                     initial_capital=100000, sl_multiplier=1.5, tp_multiplier=3.0,
                     use_atr_sl=True, use_time_filter=True, use_trend_filter=True):
        """
        Enhanced backtest with:
        - ATR-based dynamic SL/TP
        - Time filters
        - Trend filters
        - Slippage & Commission
        - Realistic entry (next bar open)
        """
        
        if df is None or len(df) < 50:
            return None
        
        df = df.copy()
        
        # Calculate ATR
        df['atr'] = self.calculate_atr(df, 14)
        
        # Calculate trend indicators
        df['sma_20'] = df['close'].rolling(20).mean()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['adx_proxy'] = (df['high'].rolling(14).max() - df['low'].rolling(14).min()) / df['atr']
        
        # Generate signals ONLY IF they haven't been pre-calculated by the Research Agent
        if 'signal' not in df.columns:
            strategy = Strategy(strategy_name, params)
            df = strategy.generate_signals(df)
        
        # Simulation
        capital = initial_capital
        peak_capital = initial_capital
        max_drawdown = 0
        position = None
        trades = []
        skipped_signals = 0
        cooldown_bars = 0
        
        for i in range(1, len(df) - 1):  # -1 because we enter on next bar
            row = df.iloc[i]
            next_row = df.iloc[i + 1]  # For realistic entry
            
            # --- EXIT LOGIC ---
            if position is not None:
                exit_price = None
                exit_reason = ""
                
                # Pessimistic: SL before TP
                if row["low"] <= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "SL Hit"
                elif row["high"] >= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "TP Hit"
                elif row["signal"] == -1:
                    exit_price = row["close"]
                    exit_reason = "Sell Signal"
                # Time-based exit: Close before 3:15 PM
                elif row.name.time() >= time(15, 10):
                    exit_price = row["close"]
                    exit_reason = "EOD Close"
                
                if exit_price:
                    # Add slippage on exit
                    cooldown_bars = 1
                    if exit_reason == "SL Hit":
                        exit_price -= self.slippage_pts  # Worse fill on SL
                    else:
                        exit_price -= self.slippage_pts * 0.5
                    
                    # Calculate P&L
                    gross_pnl = (exit_price - position["entry_price"]) * position["quantity"]
                    commission = (position["entry_price"] + exit_price) * position["quantity"] * self.commission_per_order
                    net_pnl = gross_pnl - commission
                    points = exit_price - position["entry_price"]
                    
                    capital += net_pnl
                    
                    # Track drawdown
                    if capital > peak_capital:
                        peak_capital = capital
                    dd = (peak_capital - capital) / peak_capital
                    max_drawdown = max(max_drawdown, dd)
                    
                    trades.append({
                        "entry_date": position["entry_date"].strftime('%Y-%m-%d %H:%M'),
                        "exit_date": df.index[i].strftime('%Y-%m-%d %H:%M'),
                        "symbol": symbol,
                        "entry_price": round(position["entry_price"], 2),
                        "exit_price": round(exit_price, 2),
                        "points": round(points, 2),
                        "quantity": position["quantity"],
                        "gross_pnl": round(gross_pnl, 2),
                        "commission": round(commission, 2),
                        "pnl": round(net_pnl, 2),
                        "reason": exit_reason,
                        "atr_at_entry": round(position["atr"], 2),
                        "sl_distance": round(position["sl_distance"], 2)
                    })
                    position = None
            
            # --- ENTRY LOGIC ---
            if position is None and row["signal"] == 1:
                if cooldown_bars > 0:
                    cooldown_bars -= 1
                    continue
                
                # FILTER 1: Time Filter
                if use_time_filter and not self.is_good_trading_time(next_row.name):
                    # skipped_signals += 1
                    continue
                
                # FILTER 2: Trend Filter
                if use_trend_filter and not self.is_trending(df, i):
                    skipped_signals += 1
                    continue
                
                # FILTER 3: Avoid low ATR (choppy market)
                if row["atr"] < df["atr"].rolling(50).mean().iloc[i] * 0.7:
                    skipped_signals += 1
                    continue
                
                # Calculate SL/TP based on ATR
                atr = row["atr"]
                
                if use_atr_sl:
                    sl_distance = atr * sl_multiplier
                    tp_distance = atr * tp_multiplier
                else:
                    sl_distance = row["close"] * 0.02  # 2% fixed
                    tp_distance = row["close"] * 0.04  # 4% fixed
                
                # Entry on NEXT bar open (realistic) + slippage
                entry_price = next_row["open"] + self.slippage_pts
                
                sl = entry_price - sl_distance
                tp = entry_price + tp_distance
                
                # Risk-based position sizing (max 2% risk per trade)
                                # Risk-based position sizing (max 2% risk per trade)
                risk_per_trade = capital * 0.02
                
                # Skip trade if SL distance is invalid or 0
                if pd.isna(sl_distance) or sl_distance == 0:
                    continue
                
                # 1. Determine Lot Size based on Symbol Type
                if "-INDEX" in symbol.upper():
                    # Spot Indices (NSE:NIFTYBANK-INDEX) trade 1 quantity
                    lot_size = 1
                elif "BANKNIFTY" in symbol.upper():
                    lot_size = 30
                elif "NIFTY" in symbol.upper():
                    lot_size = 65
                elif "SENSEX" in symbol.upper():
                    lot_size = 20
                else:
                    lot_size = 1  # Default for stocks or other indices
                    
                # 2. Calculate raw quantity based on risk
                raw_qty = risk_per_trade / sl_distance
                
                # 3. Round down to the nearest valid lot size
                qty = int(raw_qty // lot_size) * lot_size
                
                # 4. Cap at 95% capital (also rounded down to lot size)
                max_qty_by_capital = int(((capital * 0.95) / entry_price) // lot_size) * lot_size
                
                # 5. Ensure at least 1 lot is traded, but skip if we can't afford 1 lot
                if max_qty_by_capital < lot_size:
                    continue # Not enough capital for even 1 lot
                    
                qty = max(lot_size, min(qty, max_qty_by_capital))
                               
                if qty > 0 and entry_price > 0:
                    position = {
                        "entry_price": entry_price,
                        "entry_date": next_row.name,
                        "quantity": qty,
                        "sl": sl,
                        "tp": tp,
                        "atr": atr,
                        "sl_distance": sl_distance
                    }
        
        if not trades:
            return {"skipped_signals": skipped_signals, "total_trades": 0}
        
        # Calculate metrics
        trades_df = pd.DataFrame(trades)
        wins = trades_df[trades_df["pnl"] > 0]
        losses = trades_df[trades_df["pnl"] < 0]
        
        gross_profit = wins["pnl"].sum() if len(wins) > 0 else 0
        gross_loss = abs(losses["pnl"].sum()) if len(losses) > 0 else 1
        total_commission = trades_df["commission"].sum()
        
        return {
            "symbol": symbol,
            "strategy": strategy_name,
            "total_trades": len(trades_df),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round((len(wins) / len(trades_df)) * 100, 2),
            "total_points": round(trades_df["points"].sum(), 2),
            "total_pnl": round(trades_df["pnl"].sum(), 2),
            "gross_profit": round(gross_profit, 2),
            "total_commission": round(total_commission, 2),
            "final_capital": round(capital, 2),
            "return_pct": round(((capital - initial_capital) / initial_capital) * 100, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
            "avg_win": round(wins["pnl"].mean(), 2) if len(wins) > 0 else 0,
            "avg_loss": round(losses["pnl"].mean(), 2) if len(losses) > 0 else 0,
            "avg_sl_distance": round(trades_df["sl_distance"].mean(), 2),
            "skipped_signals": skipped_signals,
            "trades_df": trades_df
        }