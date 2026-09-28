# Bollinger Squeeze — when the Bollinger Bands contract inside the Keltner
# Channel, volatility is coiled; the breakout out of the squeeze is the trade.
data['basis'] = data.ta.sma(length=20)
dev = 2.0 * data['close'].rolling(20).std()
data['bb_upper'] = data['basis'] + dev
data['bb_lower'] = data['basis'] - dev

atr = data.ta.atr(length=20)
data['kc_upper'] = data['basis'] + 1.5 * atr
data['kc_lower'] = data['basis'] - 1.5 * atr

add_line_series(data['bb_upper'], role="band", label="BB Upper")
add_line_series(data['bb_lower'], role="band", label="BB Lower")
add_line_series(data['basis'], role="baseline", label="Basis")

# Squeeze on = Bands inside the Keltner Channel. Highlight those bars.
squeeze_on = (data['bb_lower'] > data['kc_lower']) & (data['bb_upper'] < data['kc_upper'])
add_bar_colors(squeeze_on, color="#F59E0B")

# Fire on the first bar after the squeeze releases and price breaks a band.
squeeze_off = squeeze_on.shift(1, fill_value=False) & (~squeeze_on)
long_break  = squeeze_off & (data['close'] > data['bb_upper'])
short_break = squeeze_off & (data['close'] < data['bb_lower'])
add_markers_where(long_break, role="signal_up", text="LONG")
add_markers_where(short_break, role="signal_down", text="SHORT")

set_long_entry(long_break, sl=0.03, tp=0.06)
set_short_entry(short_break, sl=0.03, tp=0.06)

# RSI Reversal + Divergence — fade the extremes as RSI climbs back out of
# oversold / drops out of overbought, and flag momentum divergences.
data['rsi'] = data.ta.rsi(length=14)

add_line_series(data['rsi'], role="baseline", label="RSI", pane="rsi")
add_zone(70, 100, role="overbought", pane="rsi")
add_zone(0, 30, role="oversold", pane="rsi")

# Reversal signals: the bar RSI leaves the extreme band.
long_sig  = (data['rsi'] > 30) & (data['rsi'].shift(1) <= 30)
short_sig = (data['rsi'] < 70) & (data['rsi'].shift(1) >= 70)
add_markers_where(long_sig, role="signal_up", text="BUY")
add_markers_where(short_sig, role="signal_down", text="SELL")

# Divergence: price makes a lower low while RSI makes a higher low (and vice
# versa) over a 14-bar window — a classic exhaustion tell.
win = 14
bull_div = (data['low'] < data['low'].shift(win)) & (data['rsi'] > data['rsi'].shift(win)) & (data['rsi'] < 45)
bear_div = (data['high'] > data['high'].shift(win)) & (data['rsi'] < data['rsi'].shift(win)) & (data['rsi'] > 55)
add_markers_where(bull_div, position="belowBar", shape="circle", color="#22C55E", text="Bull Div")
add_markers_where(bear_div, position="aboveBar", shape="circle", color="#EF4444", text="Bear Div")

# Trade the reversal, exit back at the midline. 3% stop, 5% target.
set_long_entry(long_sig, sl=0.03, tp=0.05)
set_long_exit(data['rsi'] >= 50)

# EMA Crossover — a fast EMA cutting a slow EMA is the classic trend trigger.
data['fast'] = data.ta.ema(length=9)
data['slow'] = data.ta.ema(length=21)

add_line_series(data['fast'], role="fast_ma", label="EMA 9")
add_line_series(data['slow'], role="slow_ma", label="EMA 21")

cross_up = (data['fast'] > data['slow']) & (data['fast'].shift(1) <= data['slow'].shift(1))
cross_dn = (data['fast'] < data['slow']) & (data['fast'].shift(1) >= data['slow'].shift(1))

add_markers_where(cross_up, role="signal_up", text="BUY")
add_markers_where(cross_dn, role="signal_down", text="SELL")
add_table({"Fast": "EMA 9", "Slow": "EMA 21"}, title="EMA Crossover")

# Go long on the golden cross, flatten on the death cross. 2% stop, 4% target.
set_long_entry(cross_up, sl=0.02, tp=0.04)
set_long_exit(cross_dn)



import pandas as pd
import numpy as np

# Smart Money Concepts strategy
# Uses confirmed swing structure, liquidity sweeps, fair-value gaps, and
# displacement candles as a vectorized approximation of institutional flow.

high = data["high"]
low = data["low"]
close = data["close"]
open_ = data["open"]

swing_len = 3
atr_len = 14

# Confirmed swing points: centered rolling extrema are shifted so signals do not
# use the current bar's future information.
pivot_high_raw = high.eq(high.rolling(2 * swing_len + 1, center=True).max())
pivot_low_raw = low.eq(low.rolling(2 * swing_len + 1, center=True).min())
last_swing_high = high.where(pivot_high_raw).ffill().shift(1)
last_swing_low = low.where(pivot_low_raw).ffill().shift(1)

tr = pd.concat([
    high - low,
    (high - close.shift(1)).abs(),
    (low - close.shift(1)).abs(),
], axis=1).max(axis=1)
atr = tr.rolling(atr_len).mean()

# Break of structure / change of character
bull_bos = close.gt(last_swing_high) & close.shift(1).le(last_swing_high.shift(1))
bear_bos = close.lt(last_swing_low) & close.shift(1).ge(last_swing_low.shift(1))

# Liquidity sweeps: price takes a prior swing level but closes back inside it.
bull_sweep = (low < last_swing_low) & (close > last_swing_low) & (close > open_)
bear_sweep = (high > last_swing_high) & (close < last_swing_high) & (close < open_)

# Three-candle fair-value gaps, confirmed only after the gap exists.
bull_fvg = low > high.shift(2)
bear_fvg = high < low.shift(2)
bull_fvg_top = low.where(bull_fvg).ffill()
bull_fvg_bottom = high.shift(2).where(bull_fvg).ffill()
bear_fvg_top = low.shift(2).where(bear_fvg).ffill()
bear_fvg_bottom = high.where(bear_fvg).ffill()

# Displacement confirms that the break has meaningful range.
body = (close - open_).abs()
bull_displacement = (close > open_) & (body > atr * 0.8)
bear_displacement = (close < open_) & (body > atr * 0.8)

# Approximate order blocks: the last opposite candle before a displacement move.
bull_order_block = open_.where((close.shift(-1) > open_.shift(-1)) & bull_displacement.shift(-1)).ffill().shift(1)
bull_ob_low = low.where((close.shift(-1) > open_.shift(-1)) & bull_displacement.shift(-1)).ffill().shift(1)
bear_order_block = open_.where((close.shift(-1) < open_.shift(-1)) & bear_displacement.shift(-1)).ffill().shift(1)
bear_ob_high = high.where((close.shift(-1) < open_.shift(-1)) & bear_displacement.shift(-1)).ffill().shift(1)

# Entries require structure confirmation plus a sweep or imbalance reaction.
bull_zone = (close >= bull_ob_low) & (close <= bull_order_block) | ((close >= bull_fvg_bottom) & (close <= bull_fvg_top))
bear_zone = (close >= bear_fvg_bottom) & (close <= bear_fvg_top) | ((close >= bear_order_block) & (close <= bear_ob_high))
long_signal = (bull_bos | bull_sweep) & (bull_zone | bull_fvg.shift(1).fillna(False)) & (close > close.shift(1))
short_signal = (bear_bos | bear_sweep) & (bear_zone | bear_fvg.shift(1).fillna(False)) & (close < close.shift(1))

# Risk levels use recent structure and ATR. Series prices are accepted by the
# backtester and keep risk adaptive to volatility.
long_sl = (last_swing_low - atr * 0.25).where(last_swing_low.notna(), close - atr * 1.5)
short_sl = (last_swing_high + atr * 0.25).where(last_swing_high.notna(), close + atr * 1.5)
long_tp = close + (close - long_sl) * 2.0
short_tp = close - (short_sl - close) * 2.0

# Visual structure and zones
add_line_series(last_swing_high, color="#EF4444", label="Liquidity High", pane="main", dash="dash")
add_line_series(last_swing_low, color="#22C55E", label="Liquidity Low", pane="main", dash="dash")
add_fill(bull_fvg_top, bull_fvg_bottom, color="#14532D", label="Bullish FVG", pane="main")
add_fill(bear_fvg_top, bear_fvg_bottom, color="#7F1D1D", label="Bearish FVG", pane="main")
add_line_series(bull_order_block, color="#38BDF8", label="Bullish Order Block", pane="main", dash="dot")
add_line_series(bear_order_block, color="#F59E0B", label="Bearish Order Block", pane="main", dash="dot")
add_markers_where(bull_bos, color="#22C55E", position="belowBar", shape="arrowUp", text="BOS")
add_markers_where(bear_bos, color="#EF4444", position="aboveBar", shape="arrowDown", text="BOS")
add_markers_where(bull_sweep, color="#06B6D4", position="belowBar", shape="arrowUp", text="SWEEP")
add_markers_where(bear_sweep, color="#F97316", position="aboveBar", shape="arrowDown", text="SWEEP")

# ATR risk context in its own pane
add_line_series(atr, color="#A78BFA", label="ATR(14)", pane="atr")

set_long_entry(long_signal, sl=long_sl, tp=long_tp, size=1.0)
set_short_entry(short_signal, sl=short_sl, tp=short_tp, size=1.0)
set_long_exit(bear_bos | bear_sweep)
set_short_exit(bull_bos | bull_sweep)
