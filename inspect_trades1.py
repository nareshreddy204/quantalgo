import os
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, FloatPrompt
from strategycopy3 import Strategy
from backtester_enhanced import EnhancedBacktester

console = Console()

def main():
    # 1. Define available strategies
    available_strategies = [
        "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
        "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze", 
        "vwap_bounce", "macd_crossover", "supertrend_only", "smc", 
        "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
        "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break"
    ]
    
    # 2. Interactive Prompts
    console.print("[bold cyan]🤖 KRONOS BACKTEST ANALYZER[/bold cyan]")
    console.print("[dim]Select a strategy to backtest:[/dim]")
    for i, s in enumerate(available_strategies, 1):
        console.print(f"  [{i}] {s}")
        
    choice_idx = int(Prompt.ask("Enter the number of the strategy", default="12")) - 1
    strategy_name = available_strategies[choice_idx]
    
    sl_mult = FloatPrompt.ask("Enter Stop Loss multiplier (e.g., 1.5)", default=1.5)
    tp_mult = FloatPrompt.ask("Enter Take Profit multiplier (e.g., 3.0)", default=3.0)
    
    # 3. Load the data
    file_path = r"C:\Users\bnare\Desktop\new way\data\otpion\NSE_BANKNIFTY26JUL55900CE_1m_100d.csv"
    console.print(f"\n[cyan]Loading data from: {file_path}[/cyan]")
    
    try:
        df = pd.read_csv(file_path, parse_dates=["datetime"])
    except Exception as e:
        console.print(f"[red]Error loading file: {e}[/red]")
        return
        
    # 4. Clean flat/illiquid candles
    flat_candles = (df['volume'] == 0) & (df['high'] == df['low'])
    df = df[~flat_candles].copy()
    df.set_index("datetime", inplace=True)
    console.print(f"[green]Loaded {len(df)} valid candles.[/green]")

    # 5. Initialize the chosen Strategy
    strat = Strategy(strategy_name)
    df_signals = strat.generate_signals(df.copy())
    df_signals['atr'] = strat.calculate_atr(df_signals)
    
    # Safety net for options ATR
    min_atr_value = 0.05
    df_signals['atr'] = df_signals['atr'].fillna(min_atr_value).replace(0, min_atr_value)

    # 6. Run Backtester
    backtester = EnhancedBacktester()
    
    console.print(f"[bold yellow]Running backtest for {strategy_name} (SL: {sl_mult}x / TP: {tp_mult}x)...[/bold yellow]")
    
    result = backtester.run_backtest(
        df=df_signals.copy(),
        symbol="BANKNIFTY26JUL55900CE",
        strategy_name=strategy_name,
        params={'sl_mult': sl_mult, 'tp_mult': tp_mult},
        initial_capital=1000000,  # ₹10 Lakhs to ensure it can afford options lots
        sl_multiplier=sl_mult,
        tp_multiplier=tp_mult,
        use_atr_sl=True,
        use_time_filter=True,
        use_trend_filter=True
    )

    if not result:
        console.print("[red]Backtester returned no results.[/red]")
        return

    # 7. Extract the Trades DataFrame
    trades_df = result.get("trades_df", pd.DataFrame())
    
    if trades_df.empty:
        console.print(f"[red]No trades were taken by {strategy_name} with these parameters.[/red]")
        return

    console.print(f"[bold green]Found {len(trades_df)} trades![/bold green]\n")

    # 8. Create Folder and Save to CSV dynamically
    folder_name = "trade_logs"
    os.makedirs(folder_name, exist_ok=True)
    
    output_csv = os.path.join(folder_name, f"{strategy_name}_trades.csv")
    trades_df.to_csv(output_csv, index=False)
    console.print(f"[dim]Trades saved to {output_csv}[/dim]\n")

    # 9. Display Trades in Rich Table
    table = Table(title=f"📊 Trade-by-Trade Breakdown: {strategy_name}", show_lines=True, box=None)
    
    # Add columns dynamically
    for col in trades_df.columns:
        style = "bold green" if "pnl" in col.lower() or "profit" in col.lower() else "white"
        if "time" in col.lower() or "date" in col.lower():
            style = "cyan"
        table.add_column(str(col), style=style, overflow="fold")

    # Add rows
    for idx, row in trades_df.iterrows():
        row_values = []
        for col in trades_df.columns:
            val = row[col]
            if isinstance(val, float):
                row_values.append(f"{val:.2f}")
            else:
                row_values.append(str(val))
        table.add_row(*row_values)

    console.print(table)

if __name__ == "__main__":
    main()