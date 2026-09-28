import pandas as pd
import numpy as np
from indicators import Indicators
import ta

import numpy as np
import pandas as pd

def add_supertrend(df, period=10, multiplier=3):
    """Pure pandas Supertrend calculation"""
    if "supertrend_direction" in df.columns:
        return df
        
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    # Calculate ATR if not present
    if "atr" in df.columns:
        atr = df["atr"]
    else:
        tr = pd.DataFrame({
            'hl': high - low,
            'hc': (high - close.shift()).abs(),
            'lc': (low - close.shift()).abs()
        }).max(axis=1)
        atr = tr.ewm(alpha=1/period, min_periods=period).mean()
        
    hl2 = (high + low) / 2
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    
    st_dir = pd.Series(1, index=df.index) # Default to 1 (up)
    
    for i in range(1, len(df)):
        if close.iloc[i] > final_upper.iloc[i-1]:
            st_dir.iloc[i] = 1
        elif close.iloc[i] < final_lower.iloc[i-1]:
            st_dir.iloc[i] = -1
        else:
            st_dir.iloc[i] = st_dir.iloc[i-1]
            
            if st_dir.iloc[i] == 1 and lower_band.iloc[i] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = final_lower.iloc[i-1]
            elif st_dir.iloc[i] == -1 and upper_band.iloc[i] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = final_upper.iloc[i-1]
                
    df["supertrend_direction"] = st_dir
    return df


class Strategy:
    def __init__(self, name, params=None):
        self.name = name
        self.params = params or {}
        self.indicators = Indicators()
    
    def prepare_data(self, df):
        """Only add indicators needed for the active strategy"""
        
        # EMA Crossover and Smart EMA need 9 and 21 EMA
        if self.name in ["ema_crossover", "smart_ema", "supertrend_macd"]:
            df = self.indicators.ema(df, 9)
            df = self.indicators.ema(df, 21)
            
        # Smart EMA also needs the 200 EMA
        if self.name == "smart_ema":
            df = self.indicators.ema(df, 200)
            
        # RSI Reversal and Supertrend MACD need RSI
        if self.name in ["rsi_reversal", "supertrend_macd", "smc"]:
            df = self.indicators.rsi(df, 14)

        # Bollinger Squeeze needs Bollinger Bands
        if self.name == "bollinger_squeeze":
            df = self.indicators.bollinger_bands(df, 20, 2)
        
        # VWAP Bounce needs VWAP
        if self.name == "vwap_bounce":
            df = self.indicators.vwap(df)
            
        # MACD calculation fallback
        if self.name in ["macd_crossover", "supertrend_macd"]:
            if "macd" not in df.columns:
                ema_12 = df["close"].ewm(span=12, adjust=False).mean()
                ema_26 = df["close"].ewm(span=26, adjust=False).mean()
                df["macd"] = ema_12 - ema_26
                df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

        # ==========================================
        # GLOBAL SUPERTREND FIX
        # ==========================================
        # Calculate Supertrend for ANY strategy that requires it
        if self.name in ["supertrend_macd", "supertrend_only", "smc", "order_block", "composite_smc"]:
            df = add_supertrend(df)

        # Liquidity Swings needs Liquidity Swings
        if self.name == "liquidity_swings":
            length = self.params.get("length", 14)
            area = self.params.get("area", "Wick Extremity")
            df = self.indicators.liquidity_swings(df, length=length, area_type=area)
            
        return df
    
    def calculate_atr(self, df, period=14):
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    

    def generate_signals(self, df):
        """Generate buy/sell signals - STICKY version"""
        df = self.prepare_data(df)  # <-- PUT THIS BACK!
        df["signal"] = 0
        
        """Generate buy/sell signals"""
        
        if self.name == "ema_crossover":
            df["crossover_buy"] = (df["ema_9"] > df["ema_21"]) & (df["ema_9"].shift(1) <= df["ema_21"].shift(1))
            df["crossover_sell"] = (df["ema_9"] < df["ema_21"]) & (df["ema_9"].shift(1) >= df["ema_21"].shift(1))
            return self._make_sticky(df, "crossover_buy", "crossover_sell")  # ← Make sticky!
        elif self.name == "smart_ema":
            df["signal"] = 0
            
            # BUY CONDITION: 9 crosses above 21, BUT price MUST be above 200 EMA
            buy_cond = (
                (df["ema_9"] > df["ema_21"]) & 
                (df["ema_9"].shift(1) <= df["ema_21"].shift(1)) & 
                (df["close"] > df["ema_200"]) # <--- THE MAGIC FILTER
            )
            
            # SELL CONDITION
            sell_cond = (
                (df["ema_9"] < df["ema_21"]) & 
                (df["ema_9"].shift(1) >= df["ema_21"].shift(1))
            )
            df.loc[buy_cond, "signal"] = 1
            df.loc[sell_cond, "signal"] = -1

        elif self.name == "ema_bounce":
            df = self.generate_ema_bounce_signals(df)

        # NEW: LIQUIDITY SWINGS STRATEGY
        elif self.name == "liquidity_swings":
            length = self.params.get("length", 14)
            area = self.params.get("area", "Wick Extremity")
            
            df = self.indicators.liquidity_swings(df, length=length, area_type=area)
            
            df["signal"] = 0
            df.loc[df['confirmed_pl'], 'signal'] = 1
            df.loc[df['confirmed_ph'], 'signal'] = -1

        elif self.name == "supertrend_macd":
            rsi_limit = self.params.get("rsi_limit", 70)
            df = add_supertrend(df)
            df["st_buy"] = (df["supertrend_direction"] == 1) & (df["macd"] > df["macd_signal"]) & (df["rsi"] < rsi_limit)
            df["st_sell"] = (df["supertrend_direction"] == -1) & (df["macd"] < df["macd_signal"]) & (df["rsi"] > (100 - rsi_limit))
            
            return self._make_sticky(df, "st_buy", "st_sell")
        
        # ✅ ADD NEW STRATEGIES HERE
        elif self.name == "rsi_reversal":
            oversold = self.params.get("oversold", 30)
            overbought = self.params.get("overbought", 70)
            
            df["rsi_buy"] = (df["rsi"] > oversold) & (df["rsi"].shift(1) <= oversold)
            df["rsi_sell"] = (df["rsi"] < overbought) & (df["rsi"].shift(1) >= overbought)
            
            return self._make_sticky(df, "rsi_buy", "rsi_sell")
        
        elif self.name == "bollinger_squeeze":
            period = self.params.get("bb_period", 20)
            
            df["bb_buy"] = (df["close"] <= df["bb_lower"]) & (df["close"].shift(1) > df["bb_lower"].shift(1))
            df["bb_sell"] = (df["close"] >= df["bb_upper"]) & (df["close"].shift(1) < df["bb_upper"].shift(1))
            
            return self._make_sticky(df, "bb_buy", "bb_sell")

        elif self.name == "vwap_bounce":
            buffer = self.params.get("buffer", 0.002)
            
            df["vwap_buy"] = (df["close"] > df["vwap"] * (1 + buffer)) & (df["close"].shift(1) <= df["vwap"] * (1 + buffer))
            df["vwap_sell"] = (df["close"] < df["vwap"] * (1 - buffer)) & (df["close"].shift(1) >= df["vwap"] * (1 - buffer))
            
            return self._make_sticky(df, "vwap_buy", "vwap_sell")

        elif self.name == "macd_crossover":
            # df["macd_buy"] = (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
                # Calculate MACD if it doesn't exist
            if "macd" not in df.columns:
                ema_12 = df["close"].ewm(span=12, adjust=False).mean()
                ema_26 = df["close"].ewm(span=26, adjust=False).mean()
                df["macd"] = ema_12 - ema_26
                df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
                
            df["macd_buy"] = (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
            
            df["macd_sell"] = (df["macd"] < df["macd_signal"]) & (df["macd"].shift(1) >= df["macd_signal"].shift(1))
            
            return self._make_sticky(df, "macd_buy", "macd_sell")

        elif self.name == "supertrend_only":
            df = add_supertrend(df)
            df["st_only_buy"] = (df["supertrend_direction"] == 1) & (df["supertrend_direction"].shift(1) != 1)
            
            df["st_only_sell"] = (df["supertrend_direction"] == -1) & (df["supertrend_direction"].shift(1) != -1)
            
            return self._make_sticky(df, "st_only_buy", "st_only_sell")
        
        elif self.name == "liquidity":
            lookback = self.params.get("lookback", 20)
            min_touches = self.params.get("min_touches", 3)
            vol_lookback = self.params.get("vol_lookback", 20)
            
            df["recent_high"] = df["high"].rolling(window=lookback).max()
            df["recent_low"] = df["low"].rolling(window=lookback).min()
            df["touches_high"] = (df["high"] >= df["recent_high"] * 0.999).rolling(window=lookback).sum()
            df["touches_low"] = (df["low"] <= df["recent_low"] * 1.001).rolling(window=lookback).sum()
            df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
            
            df["liq_buy"] = (
                (df["close"] > df["recent_high"]) &
                (df["touches_high"] >= min_touches) &
                (df["close"].shift(1) <= df["recent_high"].shift(1)) &
                (df["volume"] > df["avg_volume"])
            )
            
            df["liq_sell"] = (
                (df["close"] < df["recent_low"]) &
                (df["touches_low"] >= min_touches) &
                (df["close"].shift(1) >= df["recent_low"].shift(1)) &
                (df["volume"] > df["avg_volume"])
            )
            
            return self._make_sticky(df, "liq_buy", "liq_sell")
        
        elif self.name == "smc":
            lookback = self.params.get("lookback", 20)
            rsi_oversold = self.params.get("rsi_oversold", 30)
            rsi_overbought = self.params.get("rsi_overbought", 70)
            vol_lookback = self.params.get("vol_lookback", 20)
            
            df["recent_high"] = df["high"].rolling(window=lookback).max()
            df["recent_low"] = df["low"].rolling(window=lookback).min()
            df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
            
            df["smc_buy"] = (
                (df["supertrend_direction"] == 1) &
                (df["supertrend_direction"].shift(1) == -1) &
                (df["rsi"].shift(1) < rsi_oversold) &
                (df["close"] > df["recent_high"]) &
                (df["close"].shift(1) <= df["recent_high"].shift(1)) &
                (df["volume"] > df["avg_volume"])
            )
            
            df["smc_sell"] = (
                (df["supertrend_direction"] == -1) &
                (df["supertrend_direction"].shift(1) == 1) &
                (df["rsi"].shift(1) > rsi_overbought) &
                (df["close"] < df["recent_low"]) &
                (df["close"].shift(1) >= df["recent_low"].shift(1)) &
                (df["volume"] > df["avg_volume"])
            )
            
            return self._make_sticky(df, "smc_buy", "smc_sell")
        
        elif self.name == "imbalance":
            gap_pct = self.params.get("gap_pct", 0.005)
            volume_multiplier = self.params.get("volume_multiplier", 1.5)
            vol_lookback = self.params.get("vol_lookback", 20)
            
            df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
            
            df["imb_buy"] = (
                (df["low"] > df["high"].shift(1) * (1 + gap_pct)) &
                (df["volume"] > df["avg_volume"] * volume_multiplier)
            )
            
            df["imb_sell"] = (
                (df["high"] < df["low"].shift(1) * (1 - gap_pct)) &
                (df["volume"] > df["avg_volume"] * volume_multiplier)
            )
            
            return self._make_sticky(df, "imb_buy", "imb_sell")
        
        elif self.name == "liquidity_grab":
            lookback = self.params.get("lookback", 20)
            reversal_buffer = self.params.get("reversal_buffer", 0.001)
            
            df["recent_high"] = df["high"].rolling(window=lookback).max()
            df["recent_low"] = df["low"].rolling(window=lookback).min()
            
            df["bullish_grab"] = (
                (df["high"] > df["recent_high"] * (1 + reversal_buffer)) &
                (df["close"] < df["recent_high"])
            )
            
            df["bearish_grab"] = (
                (df["low"] < df["recent_low"] * (1 - reversal_buffer)) &
                (df["close"] > df["recent_low"])
            )
            
            df["grab_buy"] = (
                df["bullish_grab"].shift(1) &
                (df["close"] > df["open"])
            )
            
            df["grab_sell"] = (
                df["bearish_grab"].shift(1) &
                (df["close"] < df["open"])
            )
            
            return self._make_sticky(df, "grab_buy", "grab_sell")
        
        elif self.name == "order_block":
            min_body_pct = self.params.get("min_body_pct", 1.0)
            volume_multiplier = self.params.get("volume_multiplier", 1.5)
            lookback = self.params.get("lookback", 10)
            
            df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
            df["avg_volume"] = df["volume"].rolling(window=lookback).mean()
            
            df["bullish_ob"] = (
                (df["close"] > df["open"]) &
                (df["body_size"] >= min_body_pct) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["supertrend_direction"].shift(1) == -1)
            )
            
            df["bearish_ob"] = (
                (df["close"] < df["open"]) &
                (df["body_size"] >= min_body_pct) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["supertrend_direction"].shift(1) == 1)
            )
            
            df["bullish_ob_low"] = df["low"].where(df["bullish_ob"]).ffill()
            df["bearish_ob_high"] = df["high"].where(df["bearish_ob"]).ffill()
            
            df["ob_buy"] = (
                (df["supertrend_direction"].shift(1) == -1) &
                (df["low"] <= df["bullish_ob_low"] * 1.001) &
                (df["close"] > df["open"]) & 
                (df["close"].shift(1) > df["bullish_ob_low"].shift(1))
            )
            
            df["ob_sell"] = (
                (df["high"] >= df["bearish_ob_high"] * 0.999) &
                (df["close"] < df["open"]) &
                (df["close"].shift(1) < df["bearish_ob_high"].shift(1))
            )
            
            return self._make_sticky(df, "ob_buy", "ob_sell")
        
        elif self.name == "fvg":
            fvg_buffer = self.params.get("fvg_buffer", 0.001)
            
            df["bullish_fvg"] = df["low"] > df["high"].shift(1) * (1 + fvg_buffer)
            df["bearish_fvg"] = df["high"] < df["low"].shift(1) * (1 - fvg_buffer)
            
            df["bullish_fvg_low"] = df["low"].where(df["bullish_fvg"]).ffill()
            df["bearish_fvg_high"] = df["high"].where(df["bearish_fvg"]).ffill()
            
            df["fvg_buy"] = (
                (df["low"] <= df["bullish_fvg_low"] * 1.001) &
                (df["close"] > df["open"]) &
                (df["close"].shift(1) > df["bullish_fvg_low"].shift(1))
            )

            df["fvg_sell"] = (
                (df["high"] >= df["bearish_fvg_high"] * 0.999) &
                (df["close"] < df["open"]) &
                (df["close"].shift(1) < df["bearish_fvg_high"].shift(1))
            )
            return self._make_sticky(df, "fvg_buy", "fvg_sell")
        
        elif self.name == "session_levels":
            try:
                or_minutes = self.params.get("or_minutes", 15)
                
                df_daily = df.resample("1D").agg({
                    "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
                }).ffill()
                
                df["yesterday_high"] = df_daily["high"].shift(1).reindex(df.index, method='ffill')
                df["yesterday_low"] = df_daily["low"].shift(1).reindex(df.index, method='ffill')
                
                df["time_only"] = df.index.time
                or_start = pd.Timestamp("09:15:00").time()
                or_end = (pd.Timestamp("09:15:00") + pd.Timedelta(minutes=or_minutes)).time()
                
                or_mask = (df["time_only"] >= or_start) & (df["time_only"] < or_end)
                
                if or_mask.any():
                    or_df = df[or_mask].copy()
                    or_df["date"] = or_df.index.date
                    or_high_map = or_df.groupby("date")["high"].max()
                    or_low_map = or_df.groupby("date")["low"].min()
                    
                    df["or_high"] = df.index.date.map(or_high_map).ffill()
                    df["or_low"] = df.index.date.map(or_low_map).ffill()
                else:
                    df["or_high"] = df["open"]
                    df["or_low"] = df["low"]
                
                df["session_buy"] = (
                    (df["close"] > df["or_high"]) &
                    (df["close"].shift(1) <= df["or_high"].shift(1))
                ) | (
                    (df["close"] > df["yesterday_high"]) &
                    (df["close"].shift(1) <= df["yesterday_high"].shift(1))
                )
                
                df["session_sell"] = (
                    (df["close"] < df["or_low"]) &
                    (df["close"].shift(1) >= df["or_low"].shift(1))
                ) | (
                    (df["close"] < df["yesterday_low"]) &
                    (df["close"].shift(1) >= df["yesterday_low"].shift(1))
                )
                
                return self._make_sticky(df, "session_buy", "session_sell")
                
            except Exception:
                df["session_buy"] = False
                df["session_sell"] = False
                return self._make_sticky(df, "session_buy", "session_sell")
        
           

        
        elif self.name == "composite_smc":
            try:
                min_confirmations = self.params.get("min_confirmations", 2)
                lookback = self.params.get("lookback", 20)
                gap_pct = self.params.get("gap_pct", 0.005)
                fvg_buffer = self.params.get("fvg_buffer", 0.001)
                min_body_pct = self.params.get("min_body_pct", 1.0)
                volume_multiplier = self.params.get("volume_multiplier", 1.5)
                reversal_buffer = self.params.get("reversal_buffer", 0.001)
                
                df["avg_volume"] = df["volume"].rolling(window=20).mean()
                df["recent_high"] = df["high"].rolling(window=lookback).max()
                df["recent_low"] = df["low"].rolling(window=lookback).min()
                
                # 1. Imbalance Logic (Raw)
                df["raw_imb_buy"] = (
                    (df["low"] > df["high"].shift(1) * (1 + gap_pct)) &
                    (df["volume"] > df["avg_volume"] * volume_multiplier) &
                    (df["close"] > df["open"])
                )
                df["raw_imb_sell"] = (
                    (df["high"] < df["low"].shift(1) * (1 - gap_pct)) &
                    (df["volume"] > df["avg_volume"] * volume_multiplier) &
                    (df["close"] < df["open"])
                )
                
                # 2. Liquidity Grab Logic (Raw)
                df["raw_grab_buy"] = (
                    (df["high"] > df["recent_high"] * (1 + reversal_buffer)).shift(1) &
                    (df["close"] < df["recent_high"]).shift(1) &
                    (df["close"] > df["open"])
                )
                df["raw_grab_sell"] = (
                    (df["low"] < df["recent_low"] * (1 - reversal_buffer)).shift(1) &
                    (df["close"] > df["recent_low"]).shift(1) &
                    (df["close"] < df["open"])
                )
                
                # 3. Order Block Logic (Raw)
                df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
                df["raw_ob_buy"] = (
                    (df["close"] > df["open"]) &
                    (df["body_size"] >= min_body_pct) &
                    (df["volume"] > df["avg_volume"] * volume_multiplier) &
                    (df["supertrend_direction"].shift(1) == -1)
                )
                df["raw_ob_sell"] = (
                    (df["close"] < df["open"]) &
                    (df["body_size"] >= min_body_pct) &
                    (df["volume"] > df["avg_volume"] * volume_multiplier) &
                    (df["supertrend_direction"].shift(1) == 1)
                )
                
                # 4. FVG Logic (Raw)
                df["raw_fvg_buy"] = (
                    (df["low"] > df["high"].shift(1) * (1 + fvg_buffer)) &
                    (df["close"] > df["open"])
                )
                df["raw_fvg_sell"] = (
                    (df["high"] < df["low"].shift(1) * (1 - fvg_buffer)) &
                    (df["close"] < df["open"])
                )
                
                # Count confirmations
                df["smc_buy_signals"] = (
                    df["raw_imb_buy"].astype(int) +
                    df["raw_grab_buy"].astype(int) +
                    df["raw_ob_buy"].astype(int) +
                    df["raw_fvg_buy"].astype(int)
                )
                
                df["smc_sell_signals"] = (
                    df["raw_imb_sell"].astype(int) +
                    df["raw_grab_sell"].astype(int) +
                    df["raw_ob_sell"].astype(int) +
                    df["raw_fvg_sell"].astype(int)
                )
                
                # Composite buy: enough confirmations + trend filter
                df["composite_buy"] = (
                    (df["smc_buy_signals"] >= min_confirmations) &
                    (df["supertrend_direction"] == 1)
                )
                
                # Composite sell: enough confirmations + trend filter
                df["composite_sell"] = (
                    (df["smc_sell_signals"] >= min_confirmations) &
                    (df["supertrend_direction"] == -1)
                )
                
                # Clean up raw columns
                raw_cols = [c for c in df.columns if "raw_" in c or "smc_" in c or "body_size" in c]
                df.drop(columns=raw_cols, inplace=True, errors="ignore")
                
                return self._make_sticky(df, "composite_buy", "composite_sell")
                
            except Exception as e:
                df["composite_buy"] = False
                df["composite_sell"] = False
                return self._make_sticky(df, "composite_buy", "composite_sell")

        return df

    # ============================================
    #  EXISTING STRATEGIES
    # ============================================
    
    def _ema_crossover(self, df):
        fast = self.params.get("fast_ema", 9)
        slow = self.params.get("slow_ema", 21)
        
        
        
        df["crossover_buy"] = (df["ema_9"] > df["ema_21"]) & (df["ema_9"].shift(1) <= df["ema_21"].shift(1))
        df["crossover_sell"] = (df["ema_9"] < df["ema_21"]) & (df["ema_9"].shift(1) >= df["ema_21"].shift(1))
        
        return self._make_sticky(df, "crossover_buy", "crossover_sell")
    
    def _supertrend_macd(self, df):
        rsi_limit = self.params.get("rsi_limit", 70)
        
        df["st_buy"] = (df["supertrend_direction"] == 1) & (df["macd"] > df["macd_signal"]) & (df["rsi"] < rsi_limit)
        df["st_sell"] = (df["supertrend_direction"] == -1) & (df["macd"] < df["macd_signal"]) & (df["rsi"] > (100 - rsi_limit))
        
        return self._make_sticky(df, "st_buy", "st_sell")

    # ============================================
    #  NEW STRATEGIES - ADD BELOW
    # ============================================

    def _rsi_reversal(self, df):
        """Buy when RSI crosses above oversold, Sell when crosses below overbought"""
        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)
        
        df["rsi_buy"] = (df["rsi"] > oversold) & (df["rsi"].shift(1) <= oversold)
        df["rsi_sell"] = (df["rsi"] < overbought) & (df["rsi"].shift(1) >= overbought)
        
        return self._make_sticky(df, "rsi_buy", "rsi_sell")

    def _bollinger_squeeze(self, df):
        """Buy when price touches lower band, Sell when touches upper band"""
        period = self.params.get("bb_period", 20)
        
        df["bb_buy"] = (df["close"] <= df["bb_lower"]) & (df["close"].shift(1) > df["bb_lower"].shift(1))
        df["bb_sell"] = (df["close"] >= df["bb_upper"]) & (df["close"].shift(1) < df["bb_upper"].shift(1))
        
        return self._make_sticky(df, "bb_buy", "bb_sell")

    def _vwap_bounce(self, df):
        """Buy when price bounces above VWAP, Sell when breaks below"""
        buffer = self.params.get("buffer", 0.002)  # 0.2% buffer
        
        df["vwap_buy"] = (df["close"] > df["vwap"] * (1 + buffer)) & (df["close"].shift(1) <= df["vwap"] * (1 + buffer))
        df["vwap_sell"] = (df["close"] < df["vwap"] * (1 - buffer)) & (df["close"].shift(1) >= df["vwap"] * (1 - buffer))
        
        return self._make_sticky(df, "vwap_buy", "vwap_sell")

    def _macd_crossover(self, df):
        """Buy when MACD crosses above signal line, Sell when crosses below signal line"""
        df["macd_buy"] = (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
        df["macd_sell"] = (df["macd"] < df["macd_signal"]) & (df["macd"].shift(1) >= df["macd_signal"].shift(1))
        
        return self._make_sticky(df, "macd_buy", "macd_sell")

    def _supertrend_only(self, df):
        """Simple Supertrend - Buy on direction change to up, Sell on direction change to down"""
        df["st_only_buy"] = (df["supertrend_direction"] == 1) & (df["supertrend_direction"].shift(1) != 1)
        df["st_only_sell"] = (df["supertrend_direction"] == -1) & (df["supertrend_direction"].shift(1) != -1)
        
        return self._make_sticky(df, "st_only_buy", "st_only_sell")
    
    def _liquidity(self, df):
        """
        Liquidity strategy with volume confirmation:
        - Buy when price breaks above recent high cluster with above-avg volume.
        - Sell when price breaks below recent low cluster with above-avg volume.
        """
        lookback = self.params.get("lookback", 20)
        min_touches = self.params.get("min_touches", 3)
        vol_lookback = self.params.get("vol_lookback", 20)  # for avg volume
        
        # Recent high/low clusters
        df["recent_high"] = df["high"].rolling(window=lookback).max()
        df["recent_low"] = df["low"].rolling(window=lookback).min()
        
        # Count touches
        df["touches_high"] = (df["high"] >= df["recent_high"] * 0.999).rolling(window=lookback).sum()
        df["touches_low"] = (df["low"] <= df["recent_low"] * 1.001).rolling(window=lookback).sum()
        
        # Average volume
        df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
        
        # Buy: break above high cluster + above-avg volume
        df["liq_buy"] = (
            (df["close"] > df["recent_high"]) &
            (df["touches_high"] >= min_touches) &
            (df["close"].shift(1) <= df["recent_high"].shift(1)) &
            (df["volume"] > df["avg_volume"])
        )
        
        # Sell: break below low cluster + above-avg volume
        df["liq_sell"] = (
            (df["close"] < df["recent_low"]) &
            (df["touches_low"] >= min_touches) &
            (df["close"].shift(1) >= df["recent_low"].shift(1)) &
            (df["volume"] > df["avg_volume"])
        )
        
        return self._make_sticky(df, "liq_buy", "liq_sell")
    
    def _smc(self, df):
        """
        SMC strategy with volume confirmation:
        - Buy: downtrend reversal + oversold RSI + break above recent high + volume.
        - Sell: uptrend reversal + overbought RSI + break below recent low + volume.
        """
        lookback = self.params.get("lookback", 20)
        rsi_oversold = self.params.get("rsi_oversold", 30)
        rsi_overbought = self.params.get("rsi_overbought", 70)
        vol_lookback = self.params.get("vol_lookback", 20)
        
        df["recent_high"] = df["high"].rolling(window=lookback).max()
        df["recent_low"] = df["low"].rolling(window=lookback).min()
        df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
        
        # Buy: trend reversal + oversold + break of structure + volume
        df["smc_buy"] = (
            (df["supertrend_direction"] == 1) &
            (df["supertrend_direction"].shift(1) == -1) &
            (df["rsi"].shift(1) < rsi_oversold) &
            (df["close"] > df["recent_high"]) &
            (df["close"].shift(1) <= df["recent_high"].shift(1)) &
            (df["volume"] > df["avg_volume"])
        )
        
        # Sell: trend reversal + overbought + break of structure + volume
        df["smc_sell"] = (
            (df["supertrend_direction"] == -1) &
            (df["supertrend_direction"].shift(1) == 1) &
            (df["rsi"].shift(1) > rsi_overbought) &
            (df["close"] < df["recent_low"]) &
            (df["close"].shift(1) >= df["recent_low"].shift(1)) &
            (df["volume"] > df["avg_volume"])
        )
        
        return self._make_sticky(df, "smc_buy", "smc_sell")
    
    def _order_block(self, df):
        """
        Order Block strategy:
        - Buy on retest of a bullish order block low.
        - Sell on retest of a bearish order block high.
        """
        min_body_pct = self.params.get("min_body_pct", 1.0)
        volume_multiplier = self.params.get("volume_multiplier", 1.5)
        lookback = self.params.get("lookback", 10)
        
        df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
        df["avg_volume"] = df["volume"].rolling(window=lookback).mean()
        
        # Bullish order block: strong up candle after downtrend
        df["bullish_ob"] = (
            (df["close"] > df["open"]) &
            (df["body_size"] >= min_body_pct) &
            (df["volume"] > df["avg_volume"] * volume_multiplier) &
            (df["supertrend_direction"].shift(1) == -1)  # previous bar in downtrend
        )
        
        # Bearish order block: strong down candle after uptrend
        df["bearish_ob"] = (
            (df["close"] < df["open"]) &
            (df["body_size"] >= min_body_pct) &
            (df["volume"] > df["avg_volume"] * volume_multiplier) &
            (df["supertrend_direction"].shift(1) == 1)   # previous bar in uptrend
        )
        
        # Mark OB levels
        df["bullish_ob_low"] = df["low"].where(df["bullish_ob"]).ffill()
        df["bearish_ob_high"] = df["high"].where(df["bearish_ob"]).ffill()
        
        # Buy: price retests bullish OB low and bounces
        df["ob_buy"] = (
            (df["low"] <= df["bullish_ob_low"] * 1.001) &
            (df["close"] > df["open"]) &
            (df["close"].shift(1) > df["bullish_ob_low"].shift(1))
        )
        # Sell: price retests bearish OB high and rejects
        df["ob_sell"] = (
            (df["high"] >= df["bearish_ob_high"] * 0.999) &
            (df["close"] < df["open"]) &
            (df["close"].shift(1) < df["bearish_ob_high"].shift(1))
        )
        return self._make_sticky(df, "ob_buy", "ob_sell")
    
    def _fvg(self, df):
        """
        Fair Value Gap strategy:
        - Buy on retest of bullish FVG low.
        - Sell on retest of bearish FVG high.
        """
        fvg_buffer = self.params.get("fvg_buffer", 0.001)  # 0.1% buffer
        
        # Bullish FVG: current low > previous high
        df["bullish_fvg"] = df["low"] > df["high"].shift(1) * (1 + fvg_buffer)
        # Bearish FVG: current high < previous low
        df["bearish_fvg"] = df["high"] < df["low"].shift(1) * (1 - fvg_buffer)
        
        # Mark FVG levels
        df["bullish_fvg_low"] = df["low"].where(df["bullish_fvg"]).ffill()
        df["bearish_fvg_high"] = df["high"].where(df["bearish_fvg"]).ffill()
        
        # Buy: retest bullish FVG low + bullish close
        df["fvg_buy"] = (
            (df["low"] <= df["bullish_fvg_low"] * 1.001) &
            (df["close"] > df["open"]) &
            (df["close"].shift(1) > df["bullish_fvg_low"].shift(1))
        )
        
        # Sell: retest bearish FVG high + bearish close
        df["fvg_sell"] = (
            (df["high"] >= df["bearish_fvg_high"] * 0.999) &
            (df["close"] < df["open"]) &
            (df["close"].shift(1) < df["bearish_fvg_high"].shift(1))
        )
        return self._make_sticky(df, "fvg_buy", "fvg_sell")
    
    def _imbalance(self, df):
        """
        Imbalance strategy:
        - Buy on retest of bullish imbalance low.
        - Sell on retest of bearish imbalance high.
        """
        gap_pct = self.params.get("gap_pct", 0.005)  # 0.5% min gap
        volume_multiplier = self.params.get("volume_multiplier", 1.5)
        vol_lookback = self.params.get("vol_lookback", 20)
        
        df["avg_volume"] = df["volume"].rolling(window=vol_lookback).mean()
        
        # Bullish imbalance: gap up + high volume
        df["bullish_imb"] = (
            (df["low"] > df["high"].shift(1) * (1 + gap_pct)) &
            (df["volume"] > df["avg_volume"] * volume_multiplier)
        )
        
        # Bearish imbalance: gap down + high volume
        df["bearish_imb"] = (
            (df["high"] < df["low"].shift(1) * (1 - gap_pct)) &
            (df["volume"] > df["avg_volume"] * volume_multiplier)
        )
        
        # Mark imbalance levels
        df["bullish_imb_low"] = df["low"].where(df["bullish_imb"]).ffill()
        df["bearish_imb_high"] = df["high"].where(df["bearish_imb"]).ffill()
        
        # Buy: retest bullish imbalance low + bullish close
        df["imb_buy"] = (
            (df["low"] <= df["bullish_imb_low"] * 1.001) &
            (df["close"] > df["open"]) &
            (df["close"].shift(1) > df["bullish_imb_low"].shift(1))
        )
        # Sell: retest bearish imbalance high + bearish close
        df["imb_sell"] = (
            (df["high"] >= df["bearish_imb_high"] * 0.999) &
            (df["close"] < df["open"]) &
            (df["close"].shift(1) < df["bearish_imb_high"].shift(1))
        )
        
        return self._make_sticky(df, "imb_buy", "imb_sell")
    
    def _liquidity_grab(self, df):
        """
        Liquidity Grab strategy:
        - Buy after a bullish grab (false breakout above high).
        - Sell after a bearish grab (false breakdown below low).
        """
        lookback = self.params.get("lookback", 20)
        reversal_buffer = self.params.get("reversal_buffer", 0.001)  # 0.1% buffer
        
        df["recent_high"] = df["high"].rolling(window=lookback).max()
        df["recent_low"] = df["low"].rolling(window=lookback).min()
        
        # Bullish grab: price breaks high but closes back inside
        df["bullish_grab"] = (
            (df["high"] > df["recent_high"] * (1 + reversal_buffer)) &
            (df["close"] < df["recent_high"])
        )
        
        # Bearish grab: price breaks low but closes back inside
        df["bearish_grab"] = (
            (df["low"] < df["recent_low"] * (1 - reversal_buffer)) &
            (df["close"] > df["recent_low"])
        )
        
        # Buy: after bullish grab, next bar is bullish
        df["grab_buy"] = (
            df["bullish_grab"].shift(1) &
            (df["close"] > df["open"])
        )
        
        # Sell: after bearish grab, next bar is bearish
        df["grab_sell"] = (
            df["bearish_grab"].shift(1) &
            (df["close"] < df["open"])
        )
        
        return self._make_sticky(df, "grab_buy", "grab_sell")


    def _session_levels(self, df):
        """
        Session-based levels strategy:
        - Uses yesterday's high/low/close and opening range.
        """
        try:
            or_minutes = self.params.get("or_minutes", 15)
            
            df_daily = df.resample("1D").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).ffill()
            
            df["yesterday_high"] = df_daily["high"].shift(1).reindex(df.index, method='ffill')
            df["yesterday_low"] = df_daily["low"].shift(1).reindex(df.index, method='ffill')
            
            # Opening range high/low (first N minutes of the day)
            df["time_only"] = df.index.time
            or_start = pd.Timestamp("09:15:00").time()
            or_end = (pd.Timestamp("09:15:00") + pd.Timedelta(minutes=or_minutes)).time()
            
            or_mask = (df["time_only"] >= or_start) & (df["time_only"] < or_end)
            
            if or_mask.any():
                or_df = df[or_mask].copy()
                or_df["date"] = or_df.index.date
                or_high_map = or_df.groupby("date")["high"].max()
                or_low_map = or_df.groupby("date")["low"].min()
                
                df["or_high"] = df.index.date.map(or_high_map).ffill()
                df["or_low"] = df.index.date.map(or_low_map).ffill()
            else:
                df["or_high"] = df["open"]
                df["or_low"] = df["low"]
            
            # Buy: break above OR high or yesterday's high
            df["session_buy"] = (
                (df["close"] > df["or_high"]) &
                (df["close"].shift(1) <= df["or_high"].shift(1))
            ) | (
                (df["close"] > df["yesterday_high"]) &
                (df["close"].shift(1) <= df["yesterday_high"].shift(1))
            )
            
            # Sell: break below OR low or yesterday's low
            df["session_sell"] = (
                (df["close"] < df["or_low"]) &
                (df["close"].shift(1) >= df["or_low"].shift(1))
            ) | (
                (df["close"] < df["yesterday_low"]) &
                (df["close"].shift(1) >= df["yesterday_low"].shift(1))
            )
            
            return self._make_sticky(df, "session_buy", "session_sell")
            
        except Exception:
            df["session_buy"] = False
            df["session_sell"] = False
            return self._make_sticky(df, "session_buy", "session_sell")
    
    def _composite_smc(self, df):
        """
        Composite SMC strategy:
        - Requires at least N SMC confirmations (imbalance, grab, OB, FVG)
        - Plus Supertrend trend filter.
        """
        try:
            min_confirmations = self.params.get("min_confirmations", 2)
            lookback = self.params.get("lookback", 20)
            gap_pct = self.params.get("gap_pct", 0.005)
            fvg_buffer = self.params.get("fvg_buffer", 0.001)
            min_body_pct = self.params.get("min_body_pct", 1.0)
            volume_multiplier = self.params.get("volume_multiplier", 1.5)
            reversal_buffer = self.params.get("reversal_buffer", 0.001)
            
            df["avg_volume"] = df["volume"].rolling(window=20).mean()
            df["recent_high"] = df["high"].rolling(window=lookback).max()
            df["recent_low"] = df["low"].rolling(window=lookback).min()
            
            # 1. Imbalance Logic (Raw)
            df["raw_imb_buy"] = (
                (df["low"] > df["high"].shift(1) * (1 + gap_pct)) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["close"] > df["open"])
            )
            df["raw_imb_sell"] = (
                (df["high"] < df["low"].shift(1) * (1 - gap_pct)) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["close"] < df["open"])
            )
            
            # 2. Liquidity Grab Logic (Raw)
            df["raw_grab_buy"] = (
                (df["high"] > df["recent_high"] * (1 + reversal_buffer)).shift(1) &
                (df["close"] < df["recent_high"]).shift(1) &
                (df["close"] > df["open"])
            )
            df["raw_grab_sell"] = (
                (df["low"] < df["recent_low"] * (1 - reversal_buffer)).shift(1) &
                (df["close"] > df["recent_low"]).shift(1) &
                (df["close"] < df["open"])
            )
            
            # 3. Order Block Logic (Raw)
            df["body_size"] = (df["close"] - df["open"]).abs() / df["open"] * 100
            df["raw_ob_buy"] = (
                (df["close"] > df["open"]) &
                (df["body_size"] >= min_body_pct) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["supertrend_direction"].shift(1) == -1)
            )
            df["raw_ob_sell"] = (
                (df["close"] < df["open"]) &
                (df["body_size"] >= min_body_pct) &
                (df["volume"] > df["avg_volume"] * volume_multiplier) &
                (df["supertrend_direction"].shift(1) == 1)
            )
            
            # 4. FVG Logic (Raw)
            df["raw_fvg_buy"] = (
                (df["low"] > df["high"].shift(1) * (1 + fvg_buffer)) &
                (df["close"] > df["open"])
            )
            df["raw_fvg_sell"] = (
                (df["high"] < df["low"].shift(1) * (1 - fvg_buffer)) &
                (df["close"] < df["open"])
            )
            
            # Count confirmations
            df["smc_buy_signals"] = (
                df["raw_imb_buy"].astype(int) +
                df["raw_grab_buy"].astype(int) +
                df["raw_ob_buy"].astype(int) +
                df["raw_fvg_buy"].astype(int)
            )
            
            df["smc_sell_signals"] = (
                df["raw_imb_sell"].astype(int) +
                df["raw_grab_sell"].astype(int) +
                df["raw_ob_sell"].astype(int) +
                df["raw_fvg_sell"].astype(int)
            )
            
            # Composite buy: enough confirmations + trend filter
            df["composite_buy"] = (
                (df["smc_buy_signals"] >= min_confirmations) &
                (df["supertrend_direction"] == 1)
            )
            
            # Composite sell: enough confirmations + trend filter
            df["composite_sell"] = (
                (df["smc_sell_signals"] >= min_confirmations) &
                (df["supertrend_direction"] == -1)
            )
            
            # Clean up raw columns
            raw_cols = [c for c in df.columns if "raw_" in c or "smc_" in c or "body_size" in c]
            df.drop(columns=raw_cols, inplace=True, errors="ignore")
            
            return self._make_sticky(df, "composite_buy", "composite_sell")
            
        except Exception:
            df["composite_buy"] = False
            df["composite_sell"] = False
            return self._make_sticky(df, "composite_buy", "composite_sell")

    def _composite_smc_mtf(self, df_current, df_higher):
        """
        Composite SMC with multi-timeframe confirmation.
        df_current: e.g., 5m data
        df_higher: e.g., 15m or 1H data (aligned indices)
        """
        min_confirmations = self.params.get("min_confirmations", 2)
        higher_tf_trend = self.params.get("higher_tf_trend", True)
        
        # Run SMC rules on current TF
        df_current = self._imbalance(df_current)
        df_current = self._liquidity_grab(df_current)
        df_current = self._order_block(df_current)
        df_current = self._fvg(df_current)
        
        # Count confirmations
        df_current["smc_buy_signals"] = (
            df_current["imb_buy"].astype(int) +
            df_current["grab_buy"].astype(int) +
            df_current["ob_buy"].astype(int) +
            df_current["fvg_buy"].astype(int)
        )
        
        df_current["smc_sell_signals"] = (
            df_current["imb_sell"].astype(int) +
            df_current["grab_sell"].astype(int) +
            df_current["ob_sell"].astype(int) +
            df_current["fvg_sell"].astype(int)
        )
        # Ensure higher TF has Supertrend
        df_higher = self.indicators.supertrend(df_higher, 10, 3)
        
        # Align indices (simple forward fill)
        df_current["higher_tf_trend"] = df_higher["supertrend_direction"].reindex(df_current.index, method="ffill")
        
        # Composite buy with MTF trend
        df_current["composite_buy"] = (
            (df_current["smc_buy_signals"] >= min_confirmations) &
            (df_current["supertrend_direction"] == 1) &
            (df_current["higher_tf_trend"] == 1 if higher_tf_trend else True)
        )
        
        # Composite sell with MTF trend
        df_current["composite_sell"] = (
            (df_current["smc_sell_signals"] >= min_confirmations) &
            (df_current["supertrend_direction"] == -1) &
            (df_current["higher_tf_trend"] == -1 if higher_tf_trend else True)
        )
        
        return self._make_sticky(df_current, "composite_buy", "composite_sell")

    def _composite_smc_ml(self, df):
        """
        Composite SMC with ML filter.
        Requires a pre-trained model file smc_ml_filter.pkl.
        """
        import joblib
        
        min_confirmations = self.params.get("min_confirmations", 2)
        ml_threshold = self.params.get("ml_threshold", 0.6)
        
        # Run composite SMC logic
        df = self._composite_smc(df)
        
        # Prepare features for ML
        df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        df["vwap_dist"] = (df["close"] - df["vwap"]) / df["vwap"]
        
        features = df[["rsi", "macd_hist", "supertrend_direction", "volume_ratio", "vwap_dist"]].fillna(0)
        
        # Load model (do this once in __init__ for efficiency)
        if not hasattr(self, "ml_model"):
            self.ml_model = joblib.load("smc_ml_filter.pkl")
        
        # Predict probabilities
        proba = self.ml_model.predict_proba(features)
        df["ml_buy_prob"] = proba[:, 1]  # probability of class 1 (profitable)
        df["ml_sell_prob"] = proba[:, 1]  # same for simplicity, or train separate model for sells
        
        # Only take composite signals where ML probability is high
        df["composite_buy_ml"] = df["composite_buy"] & (df["ml_buy_prob"] >= ml_threshold)
        df["composite_sell_ml"] = df["composite_sell"] & (df["ml_sell_prob"] >= ml_threshold)
        
        return self._make_sticky(df, "composite_buy_ml", "composite_sell_ml")

    # ============================================
    #  HELPER - Ultra-fast vectorized sticky signals
    # ============================================
    
    def _make_sticky(self, df, buy_col, sell_col):
        """Ultra-fast vectorized sticky signal generator"""
        events = np.where(df[buy_col] == True, 1, np.where(df[sell_col] == True, -1, 0))
        
        # Forward fill only the actual signals (ignore the 0s)
        events_series = pd.Series(events, index=df.index)
        sticky_signals = events_series.replace(0, np.nan).ffill()
        
        # Fill any remaining NaNs (start of data) with 0
        sticky_signals = sticky_signals.fillna(0).astype(int)
        
        # Apply to dataframe
        df["signal"] = sticky_signals.values
        
        # Cleanup
        df.drop(columns=[buy_col, sell_col], inplace=True, errors="ignore")
        return df

    def get_latest_signal(self, df):
        """Get the most recent signal"""
        df = self.generate_signals(df)
        latest = df.iloc[-1]
        return {
            "signal": latest["signal"],
            "close": latest["close"],
            "timestamp": df.index[-1]
        }
    
    # Add to strategy.py - Multi-timeframe + Confirmation filters

    def add_filters(df):
        """Add common filters to improve win rate"""
        
        # 1. Trend Filter (Higher timeframe proxy using longer MA)
        df['trend_up'] = df['close'] > df['close'].rolling(50).mean()
        df['trend_down'] = df['close'] < df['close'].rolling(50).mean()
        
        # 2. Volatility Filter (Avoid low volatility chop)
        df['volatility_ratio'] = df['close'].pct_change().rolling(20).std() / df['close'].pct_change().rolling(50).std()
        df['good_volatility'] = df['volatility_ratio'] > 0.8
        
        # 3. Volume Filter (Need volume confirmation)
        df['volume_sma'] = df['volume'].rolling(20).mean()
        df['good_volume'] = df['volume'] > df['volume_sma'] * 0.8
        
        # 4. Momentum Filter (RSI not overbought/oversold at entry)
        df['good_rsi_zone'] = (df['rsi'] > 40) & (df['rsi'] < 70)
        
        # 5. No consecutive losses filter (optional - track in backtester)
        
        return df

    def apply_entry_filters(df, row_idx):
        """Check if all filters are satisfied for entry"""
        row = df.iloc[row_idx]
        
        # Must be in uptrend
        if not row.get('trend_up', True):
            return False
        
        # Must have sufficient volatility
        if not row.get('good_volatility', True):
            return False
        
        # Must have decent volume
        if not row.get('good_volume', True):
            return False
        
        # RSI must be in good zone
        if not row.get('good_rsi_zone', True):
            return False
        
        return True
    
    def generate_ema_bounce_signals(self, df):
        """
        HIGH WIN RATE STRATEGY: 20 EMA Bounce in Uptrend
        Optimized Settings: SL 1.0x ATR, TP 0.5x ATR
        """
        df = df.copy()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['atr'] = self.calculate_atr(df, 14) # Make sure calculate_atr exists in your strategy.py
        
        df['signal'] = 0
        
        for i in range(51, len(df)):
            prev = df.iloc[i-1]
            curr = df.iloc[i]
            
            # 1. Macro Trend
            is_uptrend = curr['close'] > curr['sma_50']
            
            # 2. Pullback to 20 EMA
            touched_ema = prev['low'] <= prev['ema_20']
            
            # 3. Bounce
            bounced = curr['close'] > curr['ema_20']
            bullish_candle = curr['close'] > prev['close']
            
            if is_uptrend and touched_ema and bounced and bullish_candle:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                
        return df