import pandas as pd
import numpy as np
from indicators import Indicators

def add_supertrend(df, period=10, multiplier=3):
    """Pure pandas Supertrend calculation"""
    if "supertrend_direction" in df.columns:
        return df
        
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
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
    st_dir = pd.Series(1, index=df.index)
    
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
        """Add required technical indicators dynamically based on strategy parameters"""
        fast = self.params.get("fast_ema", 9)
        slow = self.params.get("slow_ema", 21)

        # Dynamic EMA calculation
        if self.name in ["ema_crossover", "smart_ema", "supertrend_macd", "ema_bounce"]:
            df[f"ema_{fast}"] = df["close"].ewm(span=fast, adjust=False).mean()
            df[f"ema_{slow}"] = df["close"].ewm(span=slow, adjust=False).mean()
            
        if self.name == "smart_ema":
            df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
            
        if self.name in ["rsi_reversal", "supertrend_macd", "smc", "hm_rsi_confirm"]:
            df = self.indicators.rsi(df, 14)

        if self.name in ["bollinger_squeeze", "bb_trap", "bb_blast"]:
            df = self.indicators.bollinger_bands(df, 20, 2)
        
        if self.name in ["vwap_bounce", "bb_trap", "triveni_sangam", "3_step_bullish", "vwap_fake_break"]:
            df = self.indicators.vwap(df)
            
        # Dynamic MACD calculation
        if self.name in ["macd_crossover", "supertrend_macd"]:
            fast_m = self.params.get("fast_ema", 12)
            slow_m = self.params.get("slow_ema", 26)
            signal_m = self.params.get("signal_period", 9)
            
            fast_line = df["close"].ewm(span=fast_m, adjust=False).mean()
            slow_line = df["close"].ewm(span=slow_m, adjust=False).mean()
            df["macd"] = fast_line - slow_line
            df["macd_signal"] = df["macd"].ewm(span=signal_m, adjust=False).mean()

        if self.name in ["supertrend_macd", "supertrend_only", "smc", "order_block", "composite_smc"]:
            df = add_supertrend(df)

        if self.name == "liquidity_swings":
            length = self.params.get("length", 14)
            area = self.params.get("area", "Wick Extremity")
            df = self.indicators.liquidity_swings(df, length=length, area_type=area)

        if self.name in ["triveni_sangam", "hm_rsi_confirm", "ema_convergence"]:
            df['sma_9_high'] = df['high'].rolling(9).mean()
            df['sma_20'] = df['close'].rolling(20).mean()
            
        if self.name == "ema_convergence":
            df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
            df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
            df["ema_124"] = df["close"].ewm(span=124, adjust=False).mean()
            
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
        """Generate strategy signals dynamically"""
        df = self.prepare_data(df)
        df["signal"] = 0
        
        fast = self.params.get("fast_ema", 9)
        slow = self.params.get("slow_ema", 21)
        fast_col = f"ema_{fast}"
        slow_col = f"ema_{slow}"

        if self.name == "ema_crossover":
            df["crossover_buy"] = (df[fast_col] > df[slow_col]) & (df[fast_col].shift(1) <= df[slow_col].shift(1))
            df["crossover_sell"] = (df[fast_col] < df[slow_col]) & (df[fast_col].shift(1) >= df[slow_col].shift(1))
            return self._make_sticky(df, "crossover_buy", "crossover_sell")
            
        elif self.name == "smart_ema":
            buy_cond = (
                (df[fast_col] > df[slow_col]) & 
                (df[fast_col].shift(1) <= df[slow_col].shift(1)) & 
                (df["close"] > df["ema_200"])
            )
            sell_cond = (
                (df[fast_col] < df[slow_col]) & 
                (df[fast_col].shift(1) >= df[slow_col].shift(1))
            )
            df.loc[buy_cond, "signal"] = 1
            df.loc[sell_cond, "signal"] = -1
            return df

        elif self.name == "ema_bounce":
            df = self.generate_ema_bounce_signals(df)

        elif self.name == "liquidity_swings":
            length = self.params.get("length", 14)
            area = self.params.get("area", "Wick Extremity")
            df = self.indicators.liquidity_swings(df, length=length, area_type=area)
            df["signal"] = 0
            df.loc[df['confirmed_pl'], 'signal'] = 1
            df.loc[df['confirmed_ph'], 'signal'] = -1

        elif self.name == "supertrend_macd":
            rsi_limit = self.params.get("rsi_limit", 70)
            df["st_buy"] = (df["supertrend_direction"] == 1) & (df["macd"] > df["macd_signal"]) & (df["rsi"] < rsi_limit)
            df["st_sell"] = (df["supertrend_direction"] == -1) & (df["macd"] < df["macd_signal"]) & (df["rsi"] > (100 - rsi_limit))
            return self._make_sticky(df, "st_buy", "st_sell")
        
        elif self.name == "rsi_reversal":
            oversold = self.params.get("oversold", 30)
            overbought = self.params.get("overbought", 70)
            df["rsi_buy"] = (df["rsi"] > oversold) & (df["rsi"].shift(1) <= oversold)
            df["rsi_sell"] = (df["rsi"] < overbought) & (df["rsi"].shift(1) >= overbought)
            return self._make_sticky(df, "rsi_buy", "rsi_sell")
        
        elif self.name == "bollinger_squeeze":
            df["bb_buy"] = (df["close"] <= df["bb_lower"]) & (df["close"].shift(1) > df["bb_lower"].shift(1))
            df["bb_sell"] = (df["close"] >= df["bb_upper"]) & (df["close"].shift(1) < df["bb_upper"].shift(1))
            return self._make_sticky(df, "bb_buy", "bb_sell")

        elif self.name == "vwap_bounce":
            buffer = self.params.get("buffer", 0.002)
            df["vwap_buy"] = (df["close"] > df["vwap"] * (1 + buffer)) & (df["close"].shift(1) <= df["vwap"] * (1 + buffer))
            df["vwap_sell"] = (df["close"] < df["vwap"] * (1 - buffer)) & (df["close"].shift(1) >= df["vwap"] * (1 - buffer))
            return self._make_sticky(df, "vwap_buy", "vwap_sell")

        elif self.name == "macd_crossover":
            df["macd_buy"] = (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
            df["macd_sell"] = (df["macd"] < df["macd_signal"]) & (df["macd"].shift(1) >= df["macd_signal"].shift(1))
            return self._make_sticky(df, "macd_buy", "macd_sell")

        elif self.name == "supertrend_only":
            df["st_only_buy"] = (df["supertrend_direction"] == 1) & (df["supertrend_direction"].shift(1) != 1)
            df["st_only_sell"] = (df["supertrend_direction"] == -1) & (df["supertrend_direction"].shift(1) != -1)
            return self._make_sticky(df, "st_only_buy", "st_only_sell")

        # ============================================
        # NK NOTES STRATEGIES (NOW PROPERLY LINKED)
        # ============================================
        elif self.name == "bb_trap":
            return self._bb_trap(df)
            
        elif self.name == "bb_blast":
            return self._bb_blast(df)
            
        elif self.name == "3_step_bullish":
            return self._three_step_bullish(df)
            
        elif self.name == "triveni_sangam":
            return self._triveni_sangam(df)
            
        elif self.name == "ema_convergence":
            return self._ema_convergence(df)
            
        elif self.name == "5_candle_reversal":
            return self._five_candle_reversal(df)
            
        elif self.name == "hm_rsi_confirm":
            return self._hm_rsi_confirm(df)
            
        elif self.name == "vwap_fake_break":
            return self._vwap_fake_break(df)

        return df

    # ============================================
    # NK NOTES HELPER FUNCTIONS
    # ============================================
    def _bb_trap(self, df):
        df["gap_up"] = df["open"] > df["close"].shift(1) * 1.01
        df["small_body"] = abs(df["close"] - df["open"]) < (df["high"] - df["low"]) * 0.3
        df["below_vwap"] = df["close"] < df["vwap"]
        df["at_upper_bb"] = df["high"] >= df["bb_upper"]
        
        df["bb_trap_sell"] = df["gap_up"] & df["small_body"] & df["below_vwap"] & df["at_upper_bb"]
        df["dummy_buy"] = False
        return self._make_sticky(df, "dummy_buy", "bb_trap_sell")

    def _bb_blast(self, df):
        df["blast_outside"] = df["close"].shift(1) > df["bb_upper"].shift(1)
        df["close_inside"] = df["close"] < df["bb_upper"]
        
        df["bb_blast_sell"] = df["blast_outside"] & df["close_inside"]
        df["dummy_buy"] = False
        return self._make_sticky(df, "dummy_buy", "bb_blast_sell")

    def _three_step_bullish(self, df):
        df["step_1"] = df["high"] > df["high"].shift(1)
        df["step_2"] = df["high"].shift(1) > df["high"].shift(2)
        df["step_3"] = df["high"].shift(2) > df["high"].shift(3)
        df["vwap_rising"] = df["vwap"] > df["vwap"].shift(1)
        
        df["3_step_buy"] = df["step_1"] & df["step_2"] & df["step_3"] & df["vwap_rising"]
        df["dummy_sell"] = False
        return self._make_sticky(df, "3_step_buy", "dummy_sell")

    def _triveni_sangam(self, df):
        tolerance = 0.002
        df["diff_9_20"] = abs(df["sma_9_high"] - df["sma_20"]) / df["sma_20"]
        df["diff_20_vwap"] = abs(df["sma_20"] - df["vwap"]) / df["vwap"]
        
        df["confluence"] = (df["diff_9_20"] < tolerance) & (df["diff_20_vwap"] < tolerance)
        df["above_9_sma"] = df["close"] > df["sma_9_high"]
        
        df["triveni_buy"] = df["confluence"] & df["above_9_sma"]
        df["dummy_sell"] = False
        return self._make_sticky(df, "triveni_buy", "dummy_sell")

    def _ema_convergence(self, df):
        tolerance = 0.01
        max_ema = df[["ema_20", "ema_50", "ema_124"]].max(axis=1)
        min_ema = df[["ema_20", "ema_50", "ema_124"]].min(axis=1)
        
        df["ema_converge"] = ((max_ema - min_ema) / min_ema) < tolerance
        df["above_9_sma"] = df["close"] > df["sma_9_high"]
        
        df["convergence_buy"] = df["ema_converge"] & df["above_9_sma"]
        df["dummy_sell"] = False
        return self._make_sticky(df, "convergence_buy", "dummy_sell")

    def _five_candle_reversal(self, df):
        df["range_5"] = (df["high"].rolling(5).max() - df["low"].rolling(5).min()) / df["close"]
        df["is_cluster"] = df["range_5"] < 0.005
        df["breakdown"] = df["close"] < df["low"].rolling(5).min().shift(1)
        
        df["5_candle_sell"] = df["is_cluster"].shift(1) & df["breakdown"]
        df["dummy_buy"] = False
        return self._make_sticky(df, "dummy_buy", "5_candle_sell")

    def _hm_rsi_confirm(self, df):
        df["rsi_cross_50"] = (df["rsi"] > 50) & (df["rsi"].shift(1) <= 50)
        df["break_20_sma"] = (df["close"] > df["sma_20"]) & (df["close"].shift(1) <= df["sma_20"].shift(1))
        
        df["hm_rsi_buy"] = df["rsi_cross_50"] & df["break_20_sma"]
        df["dummy_sell"] = False
        return self._make_sticky(df, "hm_rsi_buy", "dummy_sell")

    def _vwap_fake_break(self, df):
        df["signal"] = 0
        return df

    # Vectorized Sticky Generator
    def _make_sticky(self, df, buy_col, sell_col):
        events = np.where(df[buy_col] == True, 1, np.where(df[sell_col] == True, -1, 0))
        events_series = pd.Series(events, index=df.index)
        sticky_signals = events_series.replace(0, np.nan).ffill()
        sticky_signals = sticky_signals.fillna(0).astype(int)
        df["signal"] = sticky_signals.values
        df.drop(columns=[buy_col, sell_col], inplace=True, errors="ignore")
        return df

    def generate_ema_bounce_signals(self, df):
        fast = self.params.get("fast_ema", 20)
        df = df.copy()
        df['sma_50'] = df['close'].rolling(50).mean()
        df[f'ema_{fast}'] = df['close'].ewm(span=fast, adjust=False).mean()
        df['atr'] = self.calculate_atr(df, 14)
        
        df['signal'] = 0
        
        for i in range(51, len(df)):
            prev = df.iloc[i-1]
            curr = df.iloc[i]
            
            is_uptrend = curr['close'] > curr['sma_50']
            touched_ema = prev['low'] <= prev[f'ema_{fast}']
            bounced = curr['close'] > curr[f'ema_{fast}']
            bullish_candle = curr['close'] > prev['close']
            
            if is_uptrend and touched_ema and bounced and bullish_candle:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                
        return df