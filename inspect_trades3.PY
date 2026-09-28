import os
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, FloatPrompt, IntPrompt
from strategy4 import Strategy
from backtester_enhanced import EnhancedBacktester

console = Console()

def main():
    # 1. Define available strategies
    available_strategies = [
        "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
        "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze", 
        "vwap_bounce", "macd_crossover", "supertrend_only", "smc", 
        "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
        "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break",
        "nk_notes"
    ]
    
    # 2. Interactive Prompts
    console.print("[bold cyan]🤖 KRONOS BACKTEST ANALYZER[/bold cyan]")
    console.print("[dim]Select a strategy to backtest:[/dim]")
    for i, s in enumerate(available_strategies, 1):
        console.print(f"  [{i}] {s}")
        
    choice_idx = int(Prompt.ask("Enter the number of the strategy", default="10")) - 1
    strategy_name = available_strategies[choice_idx]
    
    # Ask for timeframe to resample the 1m data into
    timeframe = Prompt.ask("Enter timeframe to resample 1m data into (e.g., 5, 15, 60)", default="15")
    
    sl_mult = FloatPrompt.ask("Enter Stop Loss multiplier (e.g., 1.5)", default=1.5)
    tp_mult = FloatPrompt.ask("Enter Take Profit multiplier (e.g., 3.0)", default=3.0)
    
    # 3. Smart File Search
    data_dir = "data"
    if not os.path.exists(data_dir):
        console.print(f"[red]Data folder '{data_dir}' not found![/red]")
        return

    all_csvs = []
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".csv"):
                all_csvs.append(os.path.join(root, file))

    if not all_csvs:
        console.print(f"[red]No CSV files found inside the '{data_dir}' folder.[/red]")
        return

    console.print(f"\n[bold yellow]Found {len(all_csvs)} CSV files in total.[/bold yellow]")
    search_term = input("🔍 Type a keyword to search (e.g., NIFTY26AUG24000CE) or press Enter to list all: ").strip()
    
    if search_term:
        filtered_csvs = [f for f in all_csvs if search_term.lower() in f.lower()]
    else:
        filtered_csvs = all_csvs

    if not filtered_csvs:
        console.print(f"[red]No CSV files match your keyword '{search_term}'.[/red]")
        return

    console.print(f"\n[bold yellow]Matching CSV files ({len(filtered_csvs)} found):[/bold yellow]")
    for i, f in enumerate(filtered_csvs):
        console.print(f"  [{i + 1}] {f}")

    file_choice = IntPrompt.ask(
        "Enter the number of the file you want to test",
        default=1,
        choices=[str(i + 1) for i in range(len(filtered_csvs))],
    )
    
    file_path = filtered_csvs[file_choice - 1]
    
    # Auto-generate symbol name from filename
    base_name = os.path.basename(file_path)
    symbol = base_name.split("_1m")[0].split("_5m")[0].split("_15m")[0].split("_60m")[0]
    
    console.print(f"\n[cyan]Loading 1m data from: {file_path}[/cyan]")
    
    try:
        df = pd.read_csv(file_path, parse_dates=["datetime"])
    except Exception as e:
        console.print(f"[red]Error loading file: {e}[/red]")
        return

    # Ensure datetime is the index for resampling
    df.set_index("datetime", inplace=True)
    
    # 4. Resample 1m data into the target timeframe
    console.print(f"[cyan]Resampling data to {timeframe}m...[/cyan]")
    df = df.resample(f'{timeframe}min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
        
    # 5. Clean flat/illiquid candles
    flat_candles = (df['volume'] == 0) & (df['high'] == df['low'])
    df = df[~flat_candles].copy()
    console.print(f"[green]Loaded {len(df)} valid {timeframe}m candles.[/green]")

    # 6. Initialize the chosen Strategy
    strat = Strategy(strategy_name)
    df_signals = strat.generate_signals(df.copy())
    df_signals['atr'] = strat.calculate_atr(df_signals)
    
    # Safety net for options ATR
    min_atr_value = 0.05
    df_signals['atr'] = df_signals['atr'].fillna(min_atr_value).replace(0, min_atr_value)

    # 7. Run Backtester
    backtester = EnhancedBacktester()
    
    # Define starting capital so we can use it in the summary
    starting_capital = 10000  # ₹10 Lakhs
    
    console.print(f"[bold yellow]Running backtest for {strategy_name} on {timeframe}m (SL: {sl_mult}x / TP: {tp_mult}x)...[/bold yellow]")
    
    result = backtester.run_backtest(
        df=df_signals.copy(),
        symbol=symbol,
        strategy_name=strategy_name,
        params={'sl_mult': sl_mult, 'tp_mult': tp_mult},
        initial_capital=starting_capital,  
        sl_multiplier=sl_mult,
        tp_multiplier=tp_mult,
        use_atr_sl=True,
        use_time_filter=True,
        use_trend_filter=True
    )

    if not result:
        console.print("[red]Backtester returned no results.[/red]")
        return

    # 8. Extract the Trades DataFrame
    trades_df = result.get("trades_df", pd.DataFrame())
    
    if trades_df.empty:
        console.print(f"[red]No trades were taken by {strategy_name} with these parameters.[/red]")
        return

    console.print(f"[bold green]Found {len(trades_df)} trades![/bold green]\n")

    # 9. Create Folder and Save to CSV dynamically with Summary at the bottom
    folder_name = "trade_logs"
    os.makedirs(folder_name, exist_ok=True)
    
    # Extract summary metrics
    total_pnl = result.get('total_pnl', 0)
    final_capital = result.get('final_capital', starting_capital + total_pnl)
    
    # Build summary rows safely as dictionaries to avoid Pandas dtype errors
    columns = trades_df.columns.tolist()
    
    rows = []
    # Blank separator row
    rows.append({col: '' for col in columns})
    # Summary header
    rows.append({col: f'--- SUMMARY ({timeframe}m) ---' if col == 'entry_date' else '' for col in columns})
    # Starting Capital
    rows.append({col: 'Starting Capital:' if col == 'entry_date' else f"{starting_capital:,.2f}" if col == 'pnl' else '' for col in columns})
    # Total PnL
    rows.append({col: 'Total Net P&L:' if col == 'entry_date' else f"{total_pnl:,.2f}" if col == 'pnl' else '' for col in columns})
    # Final Capital
    rows.append({col: 'Final Capital:' if col == 'entry_date' else f"{final_capital:,.2f}" if col == 'pnl' else '' for col in columns})
    
    summary_df = pd.DataFrame(rows, columns=columns)
    
    # Combine trades and summary
    final_csv_df = pd.concat([trades_df, summary_df], ignore_index=True)
    
    # Include timeframe in the filename
    output_csv = os.path.join(folder_name, f"{strategy_name}_{timeframe}m_trades.csv")
    final_csv_df.to_csv(output_csv, index=False)
    console.print(f"[dim]Trades + Summary saved to {output_csv}[/dim]\n")
    
    # Print Summary to Console
    console.print(f"[bold cyan]Starting Capital:[/bold cyan] ₹{starting_capital:,.2f}")
    console.print(f"[bold green]Total Net P&L:[/bold green] ₹{total_pnl:,.2f}")
    console.print(f"[bold magenta]Final Capital:[/bold magenta] ₹{final_capital:,.2f}\n")

    # 10. Display Trades in Rich Table
    table = Table(title=f"📊 Trade-by-Trade Breakdown: {strategy_name} ({timeframe}m)", show_lines=True, box=None)
    
    # Add columns dynamically
    for col in trades_df.columns:
        style = "bold green" if "pnl" in col.lower() or "profit" in col.lower() else "white"
        if "time" in col.lower() or "date" in col.lower():
            style = "cyan"
        table.add_column(str(col), style=style, overflow="fold")

    # Add rows to table
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
