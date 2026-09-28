import yaml
import os
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

console = Console()

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    data_fetcher = DataFetcher(auth.fyers)
    
    # =========================================================================
    # ⚙️ CONFIGURE WHAT YOU WANT TO DOWNLOAD HERE ⚙️
    # =========================================================================
    
    # SYMBOLS = [
    #     "NSE:NIFTY50-INDEX",
    #     "NSE:INFY-EQ",
    #     # "NSE:NIFTY50-INDEX",
    #     "NSE:SHREECEM-EQ",
    #     "NSE:TCS-EQ",
    #     "NSE:BAJFINANCE-EQ",
    #     "NSE:M&M-EQ",
    #     "NSE:ULTRACEMCO-EQ",
    #     "NSE:KOTAKBANK-EQ",
    #     "NSE:AXISBANK-EQ",
    #     "NSE:EICHERMOT-EQ",
    #     "NSE:TECHM-EQ",
    #     "NSE:ICICIBANK-EQ",
    #     "NSE:BAJAJFINSV-EQ",
    #     "NSE:HDFCBANK-EQ",
    #     "NSE:WIPRO-EQ",
    #     "NSE:INDUSINDBK-EQ",
    #     "NSE:HCLTECH-EQ",
    #     "NSE:BHARTIARTL-EQ",
    #     "NSE:SBIN-EQ",
    #     "NSE:CIPLA-EQ",
    #     "NSE:BAJAJ-AUTO-EQ",
    #     "NSE:DIVISLAB-EQ",
    #     "NSE:NTPC-EQ",
    #     "NSE:HDFCLIFE-EQ",
    #     "NSE:JSWSTEEL-EQ",
    #     "NSE:TATASTEEL-EQ",
    #     "NSE:ONGC-EQ",
    #     "NSE:POWERGRID-EQ",
    #     "NSE:UPL-EQ",
    #     "NSE:DRREDDY-EQ",
    #     "NSE:BPCL-EQ",
    #     "NSE:ITC-EQ",
    #     "NSE:COALINDIA-EQ",
    #     "NSE:HINDUNILVR-EQ",
    #     "NSE:BRITANNIA-EQ",
    #     "NSE:ADANIPORTS-EQ",
    #     "NSE:HINDALCO-EQ",
    #     "NSE:TATACONSUM-EQ",
    #     "NSE:SUNPHARMA-EQ",
    #     "NSE:HEROMOTOCO-EQ",
    #     "NSE:GRASIM-EQ",
    #     "NSE:SBILIFE-EQ",
    #     "NSE:RELIANCE-EQ",
    #     "NSE:APOLLOHOSP-EQ",
    #     "NSE:TITAN-EQ",
    #     "NSE:MARUTI-EQ",
    #     "NSE:ASIANPAINT-EQ",
    #     "NSE:NESTLEIND-EQ",
    #     "NSE:LT-EQ",
    # ]
    # SYMBOLS = [  "MCX:ALUMINI26JULFUT", "MCX:ALUMINIUM26JULFUT", "MCX:CARDAMOM26JULFUT", "MCX:COPPER26JULFUT", "MCX:COTTON26JULFUT", "MCX:COTTONOIL26JULFUT", "MCX:CRUDEOIL26JULFUT", "MCX:CRUDEOILM26JULFUT", "MCX:ELECDMBL26JULFUT", "MCX:GOLD26AUGFUT", "MCX:GOLDGUINEA26JULFUT", "MCX:GOLDM26AUGFUT", "MCX:GOLDPETAL26JULFUT", "MCX:GOLDTEN26JULFUT", "MCX:KAPAS26NOVFUT", "MCX:LEAD26JULFUT", "MCX:LEADMINI26JULFUT", "MCX:MCXBULLDEX26JULFUT", "MCX:MCXMETLDEX26JULFUT", "MCX:MENTHAOIL26JULFUT", "MCX:NATGASMINI26JULFUT", "MCX:NATURALGAS26JULFUT", "MCX:NICKEL26AUGFUT", "MCX:SILVER26SEPFUT", "MCX:SILVER10026JULFUT", "MCX:SILVERM26AUGFUT", "MCX:SILVERMIC26AUGFUT", "MCX:STEELREBAR26JULFUT", "MCX:ZINC26JULFUT", "MCX:ZINCMINI26JULFUT" ]
    # SYMBOLS = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX","NSE:FINNIFTY-INDEX",]
    SYMBOLS = ["NSE:NIFTYBANK-INDEX", "NSE:BANKNIFTY26JULFUT", "NSE:BANKNIFTY26JUL55900CE", "NSE:BANKNIFTY26JUL55900PE", "NSE:BANKNIFTY26JUL56000CE", "NSE:BANKNIFTY26JUL56000PE", "NSE:BANKNIFTY26JUL56100PE", "NSE:BANKNIFTY26JUL56100CE", "NSE:BANKNIFTY26JUL56200CE", "NSE:BANKNIFTY26JUL56200PE", "NSE:BANKNIFTY26JUL56300PE", "NSE:BANKNIFTY26JUL56300CE", "NSE:BANKNIFTY26JUL56400PE", "NSE:BANKNIFTY26JUL56400CE", "NSE:BANKNIFTY26JUL56500CE", "NSE:BANKNIFTY26JUL56500PE", "NSE:BANKNIFTY26JUL56600PE", "NSE:BANKNIFTY26JUL56600CE", "NSE:BANKNIFTY26JUL56700CE", "NSE:BANKNIFTY26JUL56700PE", "NSE:BANKNIFTY26JUL56800PE", "NSE:BANKNIFTY26JUL56800CE", "NSE:BANKNIFTY26JUL56900CE"]
    # Format: ("Resolution", Days_to_Download)
    # Note: Minute data maxes out at 100 days per chunk (script handles chunking automatically)
    TIMEFRAMES = [
        ("1", 100),     # 365 days of 1-minute data
        # ("2", 365),     # 365 days of 2-minute data
        # ("3", 365),     # 365 days of 3-minute data
        # ("5", 365),    # 365 days of 5-minute data
        # ("15", 365),   # 365 days of 15-minute data
        # ("30", 365),   # 365 days of 30-minute data
        # ("45", 365),   # 365 days of 45-minute data
        # ("60", 365),   # 365 days of 1-hour data
        # ("120", 365),  # 365 days of 2-hour data
        # ("180", 365),  # 365 days of 3-hour data
        # ("240", 365),  # 365 days of 4-hour data
        # ("D", 365),    # 1 year of Daily data
        # ("1W", 365),   # 1 year of Weekly data
    ]
    
    # =========================================================================

    total_jobs = len(SYMBOLS) * len(TIMEFRAMES)
    downloaded_data = []
    
    console.clear()
    console.print(Panel(
        f"[bold white]Downloading {total_jobs} datasets...\n"
        f"Symbols: {len(SYMBOLS)}  |  Timeframes: {len(TIMEFRAMES)}\n"
        f"[dim]Data will be saved in the './data' folder as CSV files.[/dim]",
        title="KRONOS BULK DATA DOWNLOADER", box=box.DOUBLE
    ))
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        
        task = progress.add_task("[cyan]Downloading...", total=total_jobs)
        
        for symbol in SYMBOLS:
            for resolution, days in TIMEFRAMES:
                # Create a clean label for display
                label = f"{symbol} ({resolution}m - {days}d)"
                progress.update(task, description=f"[cyan]Fetching {label}...")
                
                try:
                    df = data_fetcher.get_historical_data(
                        symbol=symbol,
                        resolution=resolution,
                        days=days
                    )
                    
                    if df is not None and len(df) > 0:
                        status = f"[bold green]✓ {len(df)} candles[/bold green]"
                        downloaded_data.append({
                            "symbol": symbol,
                            "tf": resolution,
                            "days": days,
                            "candles": len(df),
                            "status": "Success"
                        })
                    else:
                        status = "[bold red]✗ Failed/Empty[/bold red]"
                        downloaded_data.append({
                            "symbol": symbol,
                            "tf": resolution,
                            "days": days,
                            "candles": 0,
                            "status": "Failed"
                        })
                        
                except Exception as e:
                    status = f"[bold red]✗ Error: {str(e)[:20]}[/bold red]"
                    downloaded_data.append({
                            "symbol": symbol,
                            "tf": resolution,
                            "days": days,
                            "candles": 0,
                            "status": "Error"
                        })
                
                # Small sleep to prevent hitting Fyers API rate limits
                time.sleep(0.3)
                progress.advance(task)

    # =========================================================================
    # PRINT FINAL SUMMARY TABLE
    # =========================================================================
    console.print("\n")
    summary_table = Table(title="Download Summary", box=box.HEAVY, show_lines=True, title_style="bold green")
    summary_table.add_column("Symbol", style="bold cyan", min_width=25, no_wrap=True)
    summary_table.add_column("Timeframe", justify="center", width=10)
    summary_table.add_column("Days", justify="center", width=6)
    summary_table.add_column("Candles", justify="right", width=9)
    summary_table.add_column("Status", justify="center", width=15)
    
    for d in downloaded_data:
        status_str = d["status"]
        if status_str == "Success":
            status_str = f"[bold green]{status_str}[/bold green]"
        else:
            status_str = f"[bold red]{status_str}[/bold red]"
            
        summary_table.add_row(
            d["symbol"],
            d["tf"],
            str(d["days"]),
            str(d["candles"]),
            status_str
        )
        
    console.print(summary_table)
    
    # Calculate folder size
    data_dir = "./data"
    total_size = 0
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            total_size += os.path.getsize(os.path.join(data_dir, f))
            
    size_mb = total_size / (1024 * 1024)
    console.print(Panel(f"[bold green]✅ Download Complete![/bold green]\nTotal Cache Size: [bold]{size_mb:.2f} MB[/bold]", box=box.DOUBLE))