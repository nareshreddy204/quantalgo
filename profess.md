If your goal is to build a professional quantitative research platform, don't limit yourself to only EMA, ICT, SMC, and Liquidity. Instead, create a library of strategies from different trading philosophies and systematically test them.

1. Trend Following Strategies

These aim to capture sustained directional moves.

Strategy	Indicators	Parameters
EMA Crossover	EMA	Fast: 5–50, Slow: 20–200
SMA Crossover	SMA	20, 50, 100, 200
Triple EMA	EMA	9, 21, 50
Supertrend	ATR	ATR Period, Multiplier
Donchian Breakout	Donchian Channel	20–100
Ichimoku Cloud	Ichimoku	Standard + custom
Hull Moving Average	HMA	9–100
KAMA Trend	Kaufman AMA	Adaptive Period
Parabolic SAR	SAR	Step, Maximum
Linear Regression Trend	Regression	Window Length
2. Momentum Strategies
Strategy	Indicators
RSI Reversal	
RSI Trend	
Stochastic	
Stochastic RSI	
MACD	
TSI	
CCI	
ROC	
Momentum Oscillator	
Awesome Oscillator	

Parameters to optimize

RSI Period
RSI Buy Level
RSI Sell Level
MACD Fast
MACD Slow
Signal
3. Volatility Strategies
Strategy	Indicators
Bollinger Bands	
Keltner Channel	
ATR Breakout	
Squeeze Momentum	
Chandelier Exit	
Volatility Stop	
Historical Volatility	
Standard Deviation	

Optimize

ATR
BB Length
BB StdDev
4. Volume Strategies
Strategy	Indicators
VWAP	
Anchored VWAP	
OBV	
CMF	
MFI	
Volume Spike	
PVT	
AD Line	
5. Smart Money Concepts (SMC)

Research

BOS
CHOCH
MSS
Internal BOS
External BOS
Order Block
Breaker Block
Mitigation Block
Rejection Block
Supply & Demand
Premium & Discount
FVG
IFVG
Liquidity Sweep
Equal High
Equal Low

Parameters

Pivot Length
BOS Confirmation
OB Strength
FVG Size
6. ICT Strategies

Research

Judas Swing
Silver Bullet
Kill Zone
OTE
Power of Three (PO3)
FVG
Balanced Price Range
Liquidity Void
Daily Bias
Weekly Bias
SMT Divergence
Dealing Range
7. Liquidity Strategies

Examples

Equal High Sweep
Equal Low Sweep
Buy Side Liquidity
Sell Side Liquidity
Stop Hunt
Liquidity Grab
Inducement
Session Liquidity
Sweep + BOS
Sweep + FVG
8. Price Action Strategies
Inside Bar
Outside Bar
Pin Bar
Engulfing
Morning Star
Evening Star
Three White Soldiers
Three Black Crows
NR4
NR7
Fakey
Breakout Pullback
Trendline Break
Channel Breakout
9. Support & Resistance
Pivot Point
CPR
Camarilla
Fibonacci Pivot
Weekly Pivot
Monthly Pivot
Round Numbers
Previous High/Low
Swing High/Low
10. Breakout Strategies
Opening Range Breakout (ORB)
Initial Balance Breakout
CPR Breakout
Donchian Breakout
NR7 Breakout
Box Breakout
Volatility Breakout
Gap Breakout
Opening Drive
11. Mean Reversion
RSI Oversold
Bollinger Mean Reversion
VWAP Reversion
Keltner Reversion
Z-Score Reversion
Deviation Bands
Regression Channel
12. Statistical Strategies
Z Score
Cointegration
Pair Trading
Kalman Filter
PCA
Hidden Markov Model
Regime Detection
Bayesian Filter
13. Machine Learning
Random Forest
XGBoost
LightGBM
CatBoost
LSTM
GRU
CNN
Transformer
Reinforcement Learning
AutoEncoder
14. Candlestick Strategies
Doji
Hammer
Hanging Man
Shooting Star
Marubozu
Harami
Piercing Pattern
Dark Cloud Cover
Tweezer Top
Tweezer Bottom
15. Session-Based Strategies
London Breakout
New York Open
Asian Range Breakout
ICT Kill Zones
Power Hour
Opening Drive
Lunch Reversal
Close Reversal
16. Multi-Timeframe Strategies

Examples

Daily EMA + 15-minute Entry
Weekly Trend + Hourly Pullback
Monthly CPR + 5-minute Entry
Daily BOS + 15-minute FVG
Hourly OB + 5-minute Confirmation
17. Hybrid Strategies

Examples

EMA + RSI
EMA + MACD
EMA + Supertrend
EMA + VWAP
VWAP + CPR
VWAP + FVG
CPR + Liquidity
SMC + EMA
ICT + EMA
ICT + Liquidity
ICT + Order Block
SMC + FVG
EMA + SMC + ICT
EMA + Liquidity + FVG
Supertrend + VWAP + RSI
CPR + EMA + MACD
Donchian + ATR
Bollinger + RSI
Bollinger + MACD
Ichimoku + ATR
ADX + EMA
18. Risk Management Variations

Test every strategy with different exit and position sizing rules:

Fixed Stop Loss
ATR Stop Loss
Swing Stop Loss
Trailing Stop
Chandelier Exit
Break-even Stop
Partial Profit Booking (25%, 50%, 75%)
Risk:Reward (1:1 to 1:5)
Time-based Exit
Volatility-based Position Size
Kelly Criterion
Fixed Fractional Risk
Institutional Research Matrix

Rather than evaluating one strategy at a time, construct a research matrix:

Layer	Options
Trend Filter	EMA, SMA, Supertrend, Ichimoku
Structure	BOS, CHOCH, MSS
Entry Trigger	FVG, Order Block, Liquidity Sweep, Pullback
Confirmation	RSI, MACD, ADX, Volume Spike
Exit	ATR, Trailing Stop, Fixed RR, Chandelier
Risk	0.5%, 1%, 2% per trade
Timeframe	1m, 3m, 5m, 15m, 1h, 4h, Daily

This modular approach lets you generate and evaluate thousands of unique strategies by combining independent components instead of manually coding each one. It also makes it easy to compare which combinations are robust across different symbols, market regimes, and timeframes.