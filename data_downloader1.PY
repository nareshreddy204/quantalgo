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

# =========================================================================
# 📁 CUSTOM SAVE DIRECTORY - CHANGE THIS AS NEEDED 📁
# =========================================================================
SAVE_DIR = r"C:\Users\bnare\Desktop\new way\data\options"
# =========================================================================

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

def ensure_directory(path):
    """Create directory if it doesn't exist"""
    if not os.path.exists(path):
        os.makedirs(path)
        console.print(f"[yellow]📁 Created directory: {path}[/yellow]")

if __name__ == "__main__":
    # ✅ Ensure save directory exists
    ensure_directory(SAVE_DIR)
    
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    
    # ✅ Pass custom save directory to DataFetcher
    data_fetcher = DataFetcher(auth.fyers, save_dir=SAVE_DIR)
    
    # =========================================================================
    # ⚙️ CONFIGURE WHAT YOU WANT TO DOWNLOAD HERE ⚙️
    # =========================================================================
    # MONTHLY EXPIRY
    SYMBOLS = [
        "NSE:NIFTY50-INDEX","NSE:NIFTY26AUGFUT","NSE:NIFTY26AUG23800PE","NSE:NIFTY26AUG23800CE",
        "NSE:NIFTY26AUG23850PE","NSE:NIFTY26AUG23850CE","NSE:NIFTY26AUG23900CE","NSE:NIFTY26AUG23900PE",
        "NSE:NIFTY26AUG23950CE","NSE:NIFTY26AUG23950PE","NSE:NIFTY26AUG24000CE","NSE:NIFTY26AUG24000PE",
        "NSE:NIFTY26AUG24050CE","NSE:NIFTY26AUG24050PE","NSE:NIFTY26AUG24100PE","NSE:NIFTY26AUG24100CE",
        "NSE:NIFTY26AUG24150CE","NSE:NIFTY26AUG24150PE","NSE:NIFTY26AUG24200CE","NSE:NIFTY26AUG24200PE",
        "NSE:NIFTY26AUG24250CE","NSE:NIFTY26AUG24250PE","NSE:NIFTY26AUG24300CE","NSE:NIFTY26AUG24300PE",
        "NSE:NIFTY26AUG24350CE","NSE:NIFTY26AUG24350PE","NSE:NIFTY26AUG24400CE","NSE:NIFTY26AUG24400PE",
        "NSE:NIFTY26AUG24450CE","NSE:NIFTY26AUG24450PE","NSE:NIFTY26AUG24500PE","NSE:NIFTY26AUG24500CE",
        "NSE:NIFTY26AUG24550PE","NSE:NIFTY26AUG24550CE","NSE:NIFTY26AUG24600PE","NSE:NIFTY26AUG24600CE",
        "NSE:NIFTY26AUG24650PE","NSE:NIFTY26AUG24650CE","NSE:NIFTY26AUG24700PE","NSE:NIFTY26AUG24700CE",
        "NSE:NIFTY26AUG24750PE","NSE:NIFTY26AUG24750CE","NSE:NIFTY26AUG24800PE","NSE:NIFTY26AUG24800CE",
        "NSE:NIFTY26AUG24850PE","NSE:NIFTY26AUG24850CE","NSE:NIFTY26AUG24900CE","NSE:NIFTY26AUG24900PE",
        "NSE:NIFTY26AUG24950PE","NSE:NIFTY26AUG24950CE"
    ]
    # WEEKLY EXPIRY
    # SYMBOLS =["NSE:NIFTY50-INDEX,NSE:NIFTY2680423800PE,NSE:NIFTY2680423800CE,NSE:NIFTY2680423850PE,NSE:NIFTY2680423850CE,NSE:NIFTY2680423900PE,NSE:NIFTY2680423900CE,NSE:NIFTY2680423950PE,NSE:NIFTY2680423950CE,NSE:NIFTY2680424000PE,NSE:NIFTY2680424000CE,NSE:NIFTY2680424050PE,NSE:NIFTY2680424050CE,NSE:NIFTY2680424100CE,NSE:NIFTY2680424100PE,NSE:NIFTY2680424150CE,NSE:NIFTY2680424150PE,NSE:NIFTY2680424200CE,NSE:NIFTY2680424200PE,NSE:NIFTY2680424250CE,NSE:NIFTY2680424250PE,NSE:NIFTY2680424300CE,NSE:NIFTY2680424300PE,NSE:NIFTY2680424350CE,NSE:NIFTY2680424350PE,NSE:NIFTY2680424400CE,NSE:NIFTY2680424400PE,NSE:NIFTY2680424450CE,NSE:NIFTY2680424450PE,NSE:NIFTY2680424500CE,NSE:NIFTY2680424500PE,NSE:NIFTY2680424550CE,NSE:NIFTY2680424550PE,NSE:NIFTY2680424600CE,NSE:NIFTY2680424600PE,NSE:NIFTY2680424650CE,NSE:NIFTY2680424650PE,NSE:NIFTY2680424700PE,NSE:NIFTY2680424700CE,NSE:NIFTY2680424750PE,NSE:NIFTY2680424750CE,NSE:NIFTY2680424800CE,NSE:NIFTY2680424800PE,NSE:NIFTY2680424850CE,NSE:NIFTY2680424850PE,NSE:NIFTY2680424900CE,NSE:NIFTY2680424900PE,NSE:NIFTY2680424950CE,NSE:NIFTY2680424950PE"
    # ]
    # banknifty options
    # SYMBOLS = [
    #     "NSE:NIFTYBANK-INDEX","NSE:BANKNIFTY26AUGFUT",
    #            "NSE:NIFTYBANK26AUG56100CE","NSE:BANKNIFTY26AUG56100PE",
    #            "NSE:BANKNIFTY26AUG56200PE","NSE:BANKNIFTY26AUG56200CE","NSE:BANKNIFTY26AUG56300CE","NSE:BANKNIFTY26AUG56300PE",
    #            "NSE:BANKNIFTY26AUG56400CE","NSE:BANKNIFTY26AUG56400PE","NSE:BANKNIFTY26AUG56500CE","NSE:BANKNIFTY26AUG56500PE",
            #    "NSE:BANKNIFTY26AUG56600PE","NSE:BANKNIFTY26AUG56600CE","NSE:BANKNIFTY26AUG56700CE","NSE:BANKNIFTY26AUG56700PE",
            #    "NSE:BANKNIFTY26AUG56800PE","NSE:BANKNIFTY26AUG56800CE","NSE:BANKNIFTY26AUG56900PE","NSE:BANKNIFTY26AUG56900CE",
            #    "NSE:BANKNIFTY26AUG57000PE","NSE:BANKNIFTY26AUG57000CE","NSE:BANKNIFTY26AUG57100CE","NSE:BANKNIFTY26AUG57100PE",
            #    "NSE:BANKNIFTY26AUG57200CE","NSE:BANKNIFTY26AUG57200PE","NSE:BANKNIFTY26AUG57300CE","NSE:BANKNIFTY26AUG57300PE",
            #    "NSE:BANKNIFTY26AUG57400CE","NSE:BANKNIFTY26AUG57400PE","NSE:BANKNIFTY26AUG57500CE","NSE:BANKNIFTY26AUG57500PE",
            #    "NSE:BANKNIFTY26AUG57600CE","NSE:BANKNIFTY26AUG57600PE","NSE:BANKNIFTY26AUG57700PE","NSE:BANKNIFTY26AUG57700CE",
            #    "NSE:BANKNIFTY26AUG57800CE","NSE:BANKNIFTY26AUG57800PE","NSE:BANKNIFTY26AUG57900CE","NSE:BANKNIFTY26AUG57900PE",
            #    "NSE:BANKNIFTY26AUG58000CE","NSE:BANKNIFTY26AUG58000PE","NSE:BANKNIFTY26AUG58100CE","NSE:BANKNIFTY26AUG58100PE",
            #    "NSE:BANKNIFTY26AUG58200CE","NSE:BANKNIFTY26AUG58200PE","NSE:BANKNIFTY26AUG58300CE","NSE:BANKNIFTY26AUG58300PE",
            #    "NSE:BANKNIFTY26AUG58400PE","NSE:BANKNIFTY26AUG58400CE"
            #    ]
            
#     SYMBOLS = [
#     "NSE:NIFTYBANK26AUGFUT",      # Futures
#     "NSE:NIFTYBANK2682656100CE",  # Call Option
#     "NSE:NIFTYBANK2682656100PE"   # Put Option
# ]
    # Format: ("Resolution", Days_to_Download)
    TIMEFRAMES = [
        ("1", 30),     # 100 days of 1-minute data
        ("5", 30),    # Uncomment for 5-minute
        ("15", 30),   # Uncomment for 15-minute
    ]
    
    # =========================================================================

    total_jobs = len(SYMBOLS) * len(TIMEFRAMES)
    downloaded_data = []
    
    console.clear()
    console.print(Panel(
        f"[bold white]Downloading {total_jobs} datasets...\n"
        f"Symbols: {len(SYMBOLS)}  |  Timeframes: {len(TIMEFRAMES)}\n"
        f"[bold cyan]📁 Save Location: {SAVE_DIR}[/bold cyan]\n"
        f"[dim]Data will be saved as CSV files.[/dim]",
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
                    status = f"[bold red]✗ Error: {str(e)[:30]}[/bold red]"
                    downloaded_data.append({
                        "symbol": symbol,
                        "tf": resolution,
                        "days": days,
                        "candles": 0,
                        "status": "Error"
                    })
                
                time.sleep(0.3)
                progress.advance(task)

    # =========================================================================
    # PRINT FINAL SUMMARY TABLE
    # =========================================================================
    console.print("\n")
    summary_table = Table(title="Download Summary", box=box.HEAVY, show_lines=True, title_style="bold green")
    summary_table.add_column("Symbol", style="bold cyan", min_width=30, no_wrap=True)
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
    
    # ✅ Calculate folder size from CUSTOM directory
    total_size = 0
    if os.path.exists(SAVE_DIR):
        for f in os.listdir(SAVE_DIR):
            file_path = os.path.join(SAVE_DIR, f)
            if os.path.isfile(file_path):
                total_size += os.path.getsize(file_path)
            
    size_mb = total_size / (1024 * 1024)
    console.print(Panel(
        f"[bold green]✅ Download Complete![/bold green]\n"
        f"[bold cyan]📁 Location: {SAVE_DIR}[/bold cyan]\n"
        f"Total Size: [bold]{size_mb:.2f} MB[/bold]", 
        box=box.DOUBLE
    ))