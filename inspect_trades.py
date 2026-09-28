import pandas as pd
from rich.console import Console
from rich.table import Table
from strategycopy3 import Strategy
from backtester_enhanced import EnhancedBacktester

console = Console()

def main():
    # 1. Load the exact options data
    file_path = r"C:\Users\bnare\Desktop\new way\data\otpion\NSE_BANKNIFTY26JUL55900CE_1m_100d.csv"
    console.print(f"[cyan]Loading data from: {file_path}[/cyan]")
    df = pd.read_csv(file_path, parse_dates=["datetime"])
    
    # 2. Clean flat/illiquid candles
    flat_candles = (df['volume'] == 0) & (df['high'] == df['low'])
    df = df[~flat_candles].copy()
    df.set_index("datetime", inplace=True)
    console.print(f"[green]Loaded {len(df)} valid candles.[/green]")

    # 3. Initialize the winning Strategy
    strat = Strategy("sma")
    df_signals = strat.generate_signals(df.copy())
    df_signals['atr'] = strat.calculate_atr(df_signals)
    
    # Safety net for options ATR
    min_atr_value = 0.05
    df_signals['atr'] = df_signals['atr'].fillna(min_atr_value).replace(0, min_atr_value)

    # 4. Run Backtester
    backtester = EnhancedBacktester()
    
    console.print("[bold yellow]Running backtest for sma (SL: 1.0x / TP: 1.5x)...[/bold yellow]")
    
    result = backtester.run_backtest(
        df=df_signals.copy(),
        symbol="BANKNIFTY26JUL55900CE",
        strategy_name="sma",
        params={'sl_mult': 1.0, 'tp_mult': 1.5},
        sl_multiplier=1.0,
        tp_multiplier=1.5,
        use_atr_sl=True,
        use_time_filter=True,
        use_trend_filter=True
    )

    if not result:
        console.print("[red]Backtester returned no results.[/red]")
        return

    # 5. Extract the Trades DataFrame
    trades_df = result.get("trades_df", pd.DataFrame())
    
    if trades_df.empty:
        console.print("[red]No trades were taken by the backtester.[/red]")
        return

    console.print(f"[bold green]Found {len(trades_df)} trades![/bold green]\n")

    # 6. Save to CSV immediately
    output_csv = "sma_trades.csv"
    trades_df.to_csv(output_csv, index=False)
    console.print(f"[dim]Trades saved to {output_csv}[/dim]\n")

    # 7. Display Trades in Rich Table
    # Dynamically create columns based on what is actually in the dataframe
    table = Table(title="📊 Trade-by-Trade Breakdown: sma", show_lines=True, box=None)
    
    # Add columns dynamically (you can customize formatting if you know exact column names)
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
            # Format numbers to 2 decimal places if they are floats
            if isinstance(val, float):
                row_values.append(f"{val:.2f}")
            else:
                row_values.append(str(val))
        table.add_row(*row_values)

    console.print(table)

if __name__ == "__main__":
    main()