# indicators.py
import pandas as pd
import numpy as np

# ==========================================
# Alpha Zoo Registry (Standalone Functions)
# ==========================================
def momentum_return(close, lookback: int):
    return close.pct_change(lookback)

def volatility(close, lookback: int):
    return close.pct_change().rolling(lookback).std()

def volume_zscore(volume, lookback: int):
    mean = volume.rolling(lookback).mean()
    std = volume.rolling(lookback).std()
    return (volume - mean) / std

def rsi(close, window: int):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# Alpha Zoo registry
ALPHA_FUNCTIONS = {
    "momentum_return": momentum_return,
    "volatility": volatility,
    "volume_zscore": volume_zscore,
    "rsi": rsi,
}


# ==========================================
# LuxAlgo Liquidity Swings (Standalone)
# ==========================================
def add_liquidity_swings(df, length=14, area_type='Wick Extremity', filter_by='Count'):
    """
    Python translation of LuxAlgo's Liquidity Swings indicator.
    """
    df = df.copy()
    
    # 1. Identify Pivot Highs and Lows
    df['is_ph'] = (df['high'] == df['high'].rolling(window=2*length+1, center=True).max())
    df['is_pl'] = (df['low'] == df['low'].rolling(window=2*length+1, center=True).min())
    
    # Shift the confirmation to the bar where the pivot is actually confirmed (length bars later)
    df['ph_trigger'] = df['is_ph'].shift(length)
    df['pl_trigger'] = df['is_pl'].shift(length)
    
    # Initialize state columns
    df['ph_level'] = np.nan
    df['pl_level'] = np.nan
    df['ph_swept'] = False
    df['pl_swept'] = False
    df['ph_count'] = 0
    df['pl_count'] = 0
    df['ph_vol'] = 0.0
    df['pl_vol'] = 0.0
    
    # State variables (equivalent to Pine Script 'var')
    active_ph_top = np.nan
    active_ph_btm = np.nan
    ph_count = 0
    ph_vol = 0.0
    ph_crossed = False
    
    active_pl_top = np.nan
    active_pl_btm = np.nan
    pl_count = 0
    pl_vol = 0.0
    pl_crossed = False
    
    # 2. Iterate through data (equivalent to Pine Script's sequential execution)
    for i in range(len(df)):
        # -------------------------------
        # PIVOT HIGH LOGIC
        # -------------------------------
        if df['ph_trigger'].iloc[i]:
            active_ph_top = df['high'].iloc[i - length]
            
            if area_type == 'Wick Extremity':
                active_ph_btm = max(df['close'].iloc[i - length], df['open'].iloc[i - length])
            else:
                active_ph_btm = df['low'].iloc[i - length]
                
            ph_count = 0
            ph_vol = 0.0
            ph_crossed = False
            
        if not np.isnan(active_ph_top) and not ph_crossed:
            if df['close'].iloc[i] > active_ph_top:
                ph_crossed = True
            else:
                if df['low'].iloc[i] < active_ph_top and df['high'].iloc[i] > active_ph_btm:
                    ph_count += 1
                    ph_vol += df['volume'].iloc[i]
        
        if not np.isnan(active_ph_top):
            df.loc[df.index[i], 'ph_level'] = active_ph_top if not ph_crossed else np.nan
            df.loc[df.index[i], 'ph_swept'] = ph_crossed
            df.loc[df.index[i], 'ph_count'] = ph_count
            df.loc[df.index[i], 'ph_vol'] = ph_vol

        # -------------------------------
        # PIVOT LOW LOGIC
        # -------------------------------
        if df['pl_trigger'].iloc[i]:
            active_pl_btm = df['low'].iloc[i - length]
            
            if area_type == 'Wick Extremity':
                active_pl_top = min(df['close'].iloc[i - length], df['open'].iloc[i - length])
            else:
                active_pl_top = df['high'].iloc[i - length]
                
            pl_count = 0
            pl_vol = 0.0
            pl_crossed = False
            
        if not np.isnan(active_pl_btm) and not pl_crossed:
            if df['close'].iloc[i] < active_pl_btm:
                pl_crossed = True
            else:
                if df['low'].iloc[i] < active_pl_top and df['high'].iloc[i] > active_pl_btm:
                    pl_count += 1
                    pl_vol += df['volume'].iloc[i]
        
        if not np.isnan(active_pl_btm):
            df.loc[df.index[i], 'pl_level'] = active_pl_btm if not pl_crossed else np.nan
            df.loc[df.index[i], 'pl_swept'] = pl_crossed
            df.loc[df.index[i], 'pl_count'] = pl_count
            df.loc[df.index[i], 'pl_vol'] = pl_vol

    # Cleanup temporary columns
    df.drop(columns=['is_ph', 'is_pl', 'ph_trigger', 'pl_trigger'], inplace=True)
    
    return df


# ==========================================
# Main Indicators Class
# ==========================================
class Indicators:
    @staticmethod
    def sma(df, period):
        """Simple Moving Average"""
        df = df.copy()
        df[f"sma_{period}"] = df["close"].rolling(window=period).mean()
        return df
    
    @staticmethod
    def ema(df, period):
        """Exponential Moving Average"""
        df = df.copy()
        df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
        return df
    
    @staticmethod
    def rsi(df, period=14):
        """Relative Strength Index"""
        df = df.copy()
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        return df
    
    @staticmethod
    def macd(df, fast=12, slow=26, signal=9):
        """MACD"""
        df = df.copy()
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]
        return df
    
    @staticmethod
    def bollinger_bands(df, period=20, std_dev=2):
        """Bollinger Bands"""
        df = df.copy()
        df["bb_middle"] = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_middle"] + (std * std_dev)
        df["bb_lower"] = df["bb_middle"] - (std * std_dev)
        return df
    
    @staticmethod
    def vwap(df):
        """Volume Weighted Average Price"""
        df = df.copy()
        df["vwap"] = (df["volume"] * (df["high"] + df["low"] + df["close"]) / 3).cumsum() / df["volume"].cumsum()
        return df
    
    @staticmethod
    def supertrend(df, period=10, multiplier=3):
        """Supertrend Indicator - Fixed with .loc[]"""
        df = df.copy()
        
        hl2 = (df["high"] + df["low"]) / 2
        
        # Calculate ATR
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)
        
        # Initialize columns
        df["supertrend"] = np.nan
        df["supertrend_direction"] = np.nan
        
        # Get index as list for .loc access
        idx = df.index.tolist()
        
        for i in range(period, len(idx)):
            curr_idx = idx[i]
            prev_idx = idx[i - 1]
            
            if df.loc[curr_idx, "close"] > upper_band.iloc[i - 1]:
                df.loc[curr_idx, "supertrend"] = lower_band.iloc[i]
                df.loc[curr_idx, "supertrend_direction"] = 1
            elif df.loc[curr_idx, "close"] < lower_band.iloc[i - 1]:
                df.loc[curr_idx, "supertrend"] = upper_band.iloc[i]
                df.loc[curr_idx, "supertrend_direction"] = -1
            else:
                df.loc[curr_idx, "supertrend"] = df.loc[prev_idx, "supertrend"]
                df.loc[curr_idx, "supertrend_direction"] = df.loc[prev_idx, "supertrend_direction"]
        
        return df
    
    @staticmethod
    def liquidity_swings(df, length=14, area_type='Wick Extremity'):
        """
        Replicates LuxAlgo Liquidity Swings logic.
        """
        # Call the standalone function above
        df = add_liquidity_swings(df, length=length, area_type=area_type)
        
        # Map results to what strategy4.py expects
        df['confirmed_ph'] = df['ph_swept']
        df['confirmed_pl'] = df['pl_swept']
        
        return df