import pandas as pd
import numpy as np
from indicators import Indicators

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
            
        # RSI Reversal, Supertrend MACD, SMC, HM RSI need RSI
        if self.name in ["rsi_reversal", "supertrend_macd", "smc", "hm_rsi_confirm"]:
            df = self.indicators.rsi(df, 14)

        # Bollinger Squeeze, BB Trap, BB Blast need Bollinger Bands
        if self.name in ["bollinger_squeeze", "bb_trap", "bb_blast"]:
            df = self.indicators.bollinger_bands(df, 20, 2)
        
        # VWAP strategies
        if self.name in ["vwap_bounce", "bb_trap", "triveni_sangam", "3_step_bullish", "vwap_fake_break"]:
            df = self.indicators.vwap(df)
            
                # supertrend_macd needs Supertrend and MACD
        # FIX: Added all strategies that rely on MACD and Supertrend
        if self.name in ["supertrend_macd", "macd_crossover"]:
            df = self.indicators.macd(df)
            
        if self.name in ["supertrend_macd", "supertrend_only", "smc", "order_block", "composite_smc"]:
            df = self.indicators.supertrend(df, 10, 3)
            if self.name == "supertrend_macd":
                df = self.indicators.macd(df)

        # Liquidity Swings needs Liquidity Swings
        if self.name == "liquidity_swings":
            length = self.params.get("length", 14)
            area = self.params.get("area", "Wick Extremity")
            df = self.indicators.liquidity_swings(df, length=length, area_type=area)

        # NK Notes Strategies - Extra Indicators
        if self.name in ["triveni_sangam", "hm_rsi_confirm", "ema_convergence"]:
            df['sma_9_high'] = df['high'].rolling(9).mean()
            df['sma_20'] = df['close'].rolling(20).mean()
            
        if self.name == "ema_convergence":
            df = self.indicators.ema(df, 20)
            df = self.indicators.ema(df, 50)
            df = self.indicators.ema(df, 124)
            
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
        df = self.prepare_data(df)
        df["signal"] = 0
        
        if self.name == "ema_crossover":
            df["crossover_buy"] = (df["ema_9"] > df["ema_21"]) & (df["ema_9"].shift(1) <= df["ema_21"].shift(1))
            df["crossover_sell"] = (df["ema_9"] < df["ema_21"]) & (df["ema_9"].shift(1) >= df["ema_21"].shift(1))
            return self._make_sticky(df, "crossover_buy", "crossover_sell")
            
        elif self.name == "smart_ema":
            buy_cond = (
                (df["ema_9"] > df["ema_21"]) & 
                (df["ema_9"].shift(1) <= df["ema_21"].shift(1)) & 
                (df["close"] > df["ema_200"])
            )
            sell_cond = (
                (df["ema_9"] < df["ema_21"]) & 
                (df["ema_9"].shift(1) >= df["ema_21"].shift(1))
            )
            df.loc[buy_cond, "signal"] = 1
            df.loc[sell_cond, "signal"] = -1

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
            df["grab_buy"] = df["bullish_grab"].shift(1) & (df["close"] > df["open"])
            df["grab_sell"] = df["bearish_grab"].shift(1) & (df["close"] < df["open"])
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
                (df["low"] <= df["bullish_ob_low"] * 1.001) &
                (df["close"] > df["open"]) & (0.01 < (df["close"].shift(1) > df["bullish_ob_low"].shift(1)))
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
                df["raw_fvg_buy"] = (
                    (df["low"] > df["high"].shift(1) * (1 + fvg_buffer)) &
                    (df["close"] > df["open"])
                )
                df["raw_fvg_sell"] = (
                    (df["high"] < df["low"].shift(1) * (1 - fvg_buffer)) &
                    (df["close"] < df["open"])
                )
                
                df["smc_buy_signals"] = (
                    df["raw_imb_buy"].astype(int) + df["raw_grab_buy"].astype(int) +
                    df["raw_ob_buy"].astype(int) + df["raw_fvg_buy"].astype(int)
                )
                df["smc_sell_signals"] = (
                    df["raw_imb_sell"].astype(int) + df["raw_grab_sell"].astype(int) +
                    df["raw_ob_sell"].astype(int) + df["raw_fvg_sell"].astype(int)
                )
                
                df["composite_buy"] = (df["smc_buy_signals"] >= min_confirmations) & (df["supertrend_direction"] == 1)
                df["composite_sell"] = (df["smc_sell_signals"] >= min_confirmations) & (df["supertrend_direction"] == -1)
                
                raw_cols = [c for c in df.columns if "raw_" in c or "smc_" in c or "body_size" in c]
                df.drop(columns=raw_cols, inplace=True, errors="ignore")
                return self._make_sticky(df, "composite_buy", "composite_sell")
            except Exception:
                df["composite_buy"] = False
                df["composite_sell"] = False
                return self._make_sticky(df, "composite_buy", "composite_sell")

        # ============================================
        #  NEW NK NOTES STRATEGIES
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
    #  NK NOTES STRATEGY IMPLEMENTATIONS
    # ============================================
    
    def _bb_trap(self, df):
        """BB Trap + Gap Up: Sell short on VWAP zone"""
        df["gap_up"] = df["open"] > df["close"].shift(1) * 1.01
        df["small_body"] = abs(df["close"] - df["open"]) < (df["high"] - df["low"]) * 0.3
        df["below_vwap"] = df["close"] < df["vwap"]
        df["at_upper_bb"] = df["high"] >= df["bb_upper"]
        
        df["bb_trap_sell"] = df["gap_up"] & df["small_body"] & df["below_vwap"] & df["at_upper_bb"]
        df["dummy_buy"] = False
        
        return self._make_sticky(df, "dummy_buy", "bb_trap_sell")

    def _bb_blast(self, df):
        """BB Blast Inside Re-entry: Sell when price blasts outside BB then closes back inside"""
        df["blast_outside"] = df["close"].shift(1) > df["bb_upper"].shift(1)
        df["close_inside"] = df["close"] < df["bb_upper"]
        
        df["bb_blast_sell"] = df["blast_outside"] & df["close_inside"]
        df["dummy_buy"] = False
        
        return self._make_sticky(df, "dummy_buy", "bb_blast_sell")

    def _three_step_bullish(self, df):
        """3-Step Bullish: 3 consecutive higher highs + VWAP trending up"""
        df["step_1"] = df["high"] > df["high"].shift(1)
        df["step_2"] = df["high"].shift(1) > df["high"].shift(2)
        df["step_3"] = df["high"].shift(2) > df["high"].shift(3)
        df["vwap_rising"] = df["vwap"] > df["vwap"].shift(1)
        
        df["3_step_buy"] = df["step_1"] & df["step_2"] & df["step_3"] & df["vwap_rising"]
        df["dummy_sell"] = False
        
        return self._make_sticky(df, "3_step_buy", "dummy_sell")

    def _triveni_sangam(self, df):
        """Triveni Sangam Bullish: 9 SMA High, VWAP, 20 SMA meet. Price above 9 SMA High."""
        tolerance = 0.002
        
        df["diff_9_20"] = abs(df["sma_9_high"] - df["sma_20"]) / df["sma_20"]
        df["diff_20_vwap"] = abs(df["sma_20"] - df["vwap"]) / df["vwap"]
        
        df["confluence"] = (df["diff_9_20"] < tolerance) & (df["diff_20_vwap"] < tolerance)
        df["above_9_sma"] = df["close"] > df["sma_9_high"]
        
        df["triveni_buy"] = df["confluence"] & df["above_9_sma"]
        df["dummy_sell"] = False
        
        return self._make_sticky(df, "triveni_buy", "dummy_sell")

    def _ema_convergence(self, df):
        """Convergence Buy: 20, 50, 124 EMA converge, Price sustains above 9 SMA High."""
        tolerance = 0.01
        
        max_ema = df[["ema_20", "ema_50", "ema_124"]].max(axis=1)
        min_ema = df[["ema_20", "ema_50", "ema_124"]].min(axis=1)
        
        df["ema_converge"] = ((max_ema - min_ema) / min_ema) < tolerance
        df["above_9_sma"] = df["close"] > df["sma_9_high"]
        
        df["convergence_buy"] = df["ema_converge"] & df["above_9_sma"]
        df["dummy_sell"] = False
        
        return self._make_sticky(df, "convergence_buy", "dummy_sell")

    def _five_candle_reversal(self, df):
        """5-Candle Reversal: 5 candles cluster at one level, Sell on break below cluster."""
        df["range_5"] = (df["high"].rolling(5).max() - df["low"].rolling(5).min()) / df["close"]
        df["is_cluster"] = df["range_5"] < 0.005
        df["breakdown"] = df["close"] < df["low"].rolling(5).min().shift(1)
        
        df["5_candle_sell"] = df["is_cluster"].shift(1) & df["breakdown"]
        df["dummy_buy"] = False
        
        return self._make_sticky(df, "dummy_buy", "5_candle_sell")

    def _hm_rsi_confirm(self, df):
        """HM + RSI Confirmation: RSI crosses above 50, Price breaks 20 SMA up."""
        df["rsi_cross_50"] = (df["rsi"] > 50) & (df["rsi"].shift(1) <= 50)
        df["break_20_sma"] = (df["close"] > df["sma_20"]) & (df["close"].shift(1) <= df["sma_20"].shift(1))
        
        df["hm_rsi_buy"] = df["rsi_cross_50"] & df["break_20_sma"]
        df["dummy_sell"] = False
        
        return self._make_sticky(df, "hm_rsi_buy", "dummy_sell")

    def _vwap_fake_break(self, df):
        """
        VWAP Fake Break: Price breaks VWAP but no close below. 
        According to notes: Hold (no new trade). Avoid Trap.
        """
        df["wick_below_vwap"] = df["low"] < df["vwap"]
        df["close_above_vwap"] = df["close"] > df["vwap"]
        df["vwap_fake_break"] = df["wick_below_vwap"] & df["close_above_vwap"]
        
        # Explicit "AVOID TRADE" setup, signal stays 0 (Neutral)
        df["signal"] = 0
        return df

    # ============================================
    #  HELPER - Ultra-fast vectorized sticky signals
    # ============================================
    def _make_sticky(self, df, buy_col, sell_col):
        """Ultra-fast vectorized sticky signal generator"""
        events = np.where(df[buy_col] == True, 1, np.where(df[sell_col] == True, -1, 0))
        events_series = pd.Series(events, index=df.index)
        sticky_signals = events_series.replace(0, np.nan).ffill()
        sticky_signals = sticky_signals.fillna(0).astype(int)
        df["signal"] = sticky_signals.values
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
    
    def add_filters(self, df):
        """Add common filters to improve win rate"""
        df['trend_up'] = df['close'] > df['close'].rolling(50).mean()
        df['trend_down'] = df['close'] < df['close'].rolling(50).mean()
        df['volatility_ratio'] = df['close'].pct_change().rolling(20).std() / df['close'].pct_change().rolling(50).std()
        df['good_volatility'] = df['volatility_ratio'] > 0.8
        df['volume_sma'] = df['volume'].rolling(20).mean()
        df['good_volume'] = df['volume'] > df['volume_sma'] * 0.8
        df['good_rsi_zone'] = (df['rsi'] > 40) & (df['rsi'] < 70)
        return df

    def apply_entry_filters(self, df, row_idx):
        """Check if all filters are satisfied for entry"""
        row = df.iloc[row_idx]
        if not row.get('trend_up', True): return False
        if not row.get('good_volatility', True): return False
        if not row.get('good_volume', True): return False
        if not row.get('good_rsi_zone', True): return False
        return True
    
    def generate_ema_bounce_signals(self, df):
        """HIGH WIN RATE STRATEGY: 20 EMA Bounce in Uptrend"""
        df = df.copy()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['atr'] = self.calculate_atr(df, 14)
        
        df['signal'] = 0
        
        for i in range(51, len(df)):
            prev = df.iloc[i-1]
            curr = df.iloc[i]
            
            is_uptrend = curr['close'] > curr['sma_50']
            touched_ema = prev['low'] <= prev['ema_20']
            bounced = curr['close'] > curr['ema_20']
            bullish_candle = curr['close'] > prev['close']
            
            if is_uptrend and touched_ema and bounced and bullish_candle:
                df.iloc[i, df.columns.get_loc('signal')] = 1
                
        return df