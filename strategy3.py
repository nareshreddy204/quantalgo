import pandas as pd
import numpy as np
from indicators import Indicators
import ta

class Strategy:
    def __init__(self, name, params=None):
        self.name = name
        self.params = params or {}
        self.indicators = Indicators()

    def detect_fvg(self, df):
        """
        Detects Bullish and Bearish Fair Value Gaps (FVG) / Imbalances.
        """
        df = df.copy()
        
        # Bullish FVG: Candle 1 High is lower than Candle 3 Low
        df['bullish_fvg'] = df['low'] - df['high'].shift(2)
        df['is_bullish_fvg'] = np.where(df['bullish_fvg'] > 0, True, False)
        
        # Bearish FVG: Candle 1 Low is higher than Candle 3 High
        df['bearish_fvg'] = df['low'].shift(2) - df['high']
        df['is_bearish_fvg'] = np.where(df['bearish_fvg'] > 0, True, False)
        
        return df

    def detect_liquidity_swings(self, df, lookback=5):
        """
        Detects recent swing highs and swing lows for liquidity tracking.
        """
        df = df.copy()
        
        # Swing High
        df['swing_high'] = df['high'].rolling(window=2*lookback+1, center=True).max()
        df['is_swing_high'] = np.where(df['high'] == df['swing_high'], True, False)
        
        # Swing Low
        df['swing_low'] = df['low'].rolling(window=2*lookback+1, center=True).min()
        df['is_swing_low'] = np.where(df['low'] == df['swing_low'], True, False)
        
        # Forward fill the swing levels to use as dynamic support/resistance
        df['recent_liquidity_high'] = df['high'].where(df['is_swing_high']).ffill()
        df['recent_liquidity_low'] = df['low'].where(df['is_swing_low']).ffill()
        
        return df

    def generate_signals(self, df):
        """
        Generates buy (1) and sell (-1) signals based on the strategy name.
        """
        # Pre-calculate SMC features
        df = self.detect_fvg(df)
        df = self.detect_liquidity_swings(df, lookback=self.params.get('lookback', 5))
        
        # Initialize signal and reason columns
        df['signal'] = 0
        df['reason'] = ''

        # ---------------------------------------------------------
        # Strategy 1 & 2: FVG / Imbalance
        # ---------------------------------------------------------
        if self.name in ['fvg', 'imbalance']:
            # Buy Signal: Bullish FVG forms
            bullish_cond = df['is_bullish_fvg'] == True
            df.loc[bullish_cond, 'signal'] = 1
            df.loc[bullish_cond, 'reason'] = 'Bullish FVG'
            
            # Sell Signal: Bearish FVG forms
            bearish_cond = df['is_bearish_fvg'] == True
            df.loc[bearish_cond, 'signal'] = -1
            df.loc[bearish_cond, 'reason'] = 'Bearish FVG'

        # ---------------------------------------------------------
        # Strategy 3: Liquidity Swings
        # ---------------------------------------------------------
        elif self.name == 'liquidity_swings':
            # Buy Signal: Price dips below recent liquidity low (Stop Hunt) and rejects
            # Simplified logic: low goes below recent liquidity low, but close is above it
            buy_cond = (df['low'] < df['recent_liquidity_low'].shift(1)) & \
                       (df['close'] > df['recent_liquidity_low'].shift(1))
            
            df.loc[buy_cond, 'signal'] = 1
            df.loc[buy_cond, 'reason'] = 'Liquidity Grab Low'

            # Sell Signal: Price spikes above recent liquidity high and rejects
            sell_cond = (df['high'] > df['recent_liquidity_high'].shift(1)) & \
                        (df['close'] < df['recent_liquidity_high'].shift(1))
            
            df.loc[sell_cond, 'signal'] = -1
            df.loc[sell_cond, 'reason'] = 'Liquidity Grab High'
            
        return df