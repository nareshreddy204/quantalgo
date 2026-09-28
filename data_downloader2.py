import yaml
import os
import time
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich import box
from fyers_apiv3 import fyersModel

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
    if not os.path.exists(path):
        os.makedirs(path)

if __name__ == "__main__":
    ensure_directory(SAVE_DIR)
    
    config = load_config()
    
    # Initialize Fyers Model directly
    fyers = fyersModel.FyersModel(
        token=config["fyers"]["access_token"],
        is_async=False,
        client_id=config["fyers"]["app_id"]
    )
    
    # =========================================================================
    # ⚙️ CONFIGURE WHAT YOU WANT TO DOWNLOAD HERE ⚙️
    # =========================================================================
    # SYMBOLS = [
    #     "NSE:NIFTY50-INDEX","NSE:NIFTY26AUGFUT","NSE:NIFTY26AUG23800PE","NSE:NIFTY26AUG23800CE",
    #     "NSE:NIFTY26AUG23850PE","NSE:NIFTY26AUG23850CE","NSE:NIFTY26AUG23900CE","NSE:NIFTY26AUG23900PE",
    #     "NSE:NIFTY26AUG23950CE","NSE:NIFTY26AUG23950PE","NSE:NIFTY26AUG24000CE","NSE:NIFTY26AUG24000PE",
    #     "NSE:NIFTY26AUG24050CE","NSE:NIFTY26AUG24050PE","NSE:NIFTY26AUG24100PE","NSE:NIFTY26AUG24100CE",
    #     "NSE:NIFTY26AUG24150CE","NSE:NIFTY26AUG24150PE","NSE:NIFTY26AUG24200CE","NSE:NIFTY26AUG24200PE",
    #     "NSE:NIFTY26AUG24250CE","NSE:NIFTY26AUG24250PE","NSE:NIFTY26AUG24300CE","NSE:NIFTY26AUG24300PE",
    #     "NSE:NIFTY26AUG24350CE","NSE:NIFTY26AUG24350PE","NSE:NIFTY26AUG24400CE","NSE:NIFTY26AUG24400PE",
    #     "NSE:NIFTY26AUG24450CE","NSE:NIFTY26AUG24450PE","NSE:NIFTY26AUG24500PE","NSE:NIFTY26AUG24500CE",
    #     "NSE:NIFTY26AUG24550PE","NSE:NIFTY26AUG24550CE","NSE:NIFTY26AUG24600PE","NSE:NIFTY26AUG24600CE",
    #     "NSE:NIFTY26AUG24650PE","NSE:NIFTY26AUG24650CE","NSE:NIFTY26AUG24700PE","NSE:NIFTY26AUG24700CE",
    #     "NSE:NIFTY26AUG24750PE","NSE:NIFTY26AUG24750CE","NSE:NIFTY26AUG24800PE","NSE:NIFTY26AUG24800CE",
    #     "NSE:NIFTY26AUG24850PE","NSE:NIFTY26AUG24850CE","NSE:NIFTY26AUG24900CE","NSE:NIFTY26AUG24900PE",
    #     "NSE:NIFTY26AUG24950PE","NSE:NIFTY26AUG24950CE"
    # ]
    # WEEKLY EXPIRY
    # SYMBOLS =["NSE:NIFTY50-INDEX","NSE:NIFTY2681123800PE","NSE:NIFTY2681123800CE","NSE:NIFTY2681123850PE",
    #           "NSE:NIFTY2681123850CE","NSE:NIFTY2681123900PE","NSE:NIFTY2681123900CE","NSE:NIFTY2681123950PE",
    #           "NSE:NIFTY2681123950CE","NSE:NIFTY2681124000PE","NSE:NIFTY2681124000CE","NSE:NIFTY2681124050PE",
    #           "NSE:NIFTY2681124050CE","NSE:NIFTY2681124100CE","NSE:NIFTY2681124100PE","NSE:NIFTY2681124150CE",
    #           "NSE:NIFTY2681124150PE","NSE:NIFTY2681124200CE","NSE:NIFTY2681124200PE","NSE:NIFTY2681124250CE",
    #           "NSE:NIFTY2681124250PE","NSE:NIFTY2681124300CE","NSE:NIFTY2681124300PE","NSE:NIFTY2681124350CE",
    #           "NSE:NIFTY2681124350PE","NSE:NIFTY2681124400CE","NSE:NIFTY2681124400PE","NSE:NIFTY2681124450CE",
    #           "NSE:NIFTY2681124450PE","NSE:NIFTY2681124500CE","NSE:NIFTY2681124500PE","NSE:NIFTY2681124550CE",
    #           "NSE:NIFTY2681124550PE","NSE:NIFTY2681124600CE","NSE:NIFTY2681124600PE","NSE:NIFTY2681124650CE",
    #           "NSE:NIFTY2681124650PE","NSE:NIFTY2681124700PE","NSE:NIFTY2681124700CE","NSE:NIFTY2681124750PE",
    #           "NSE:NIFTY2681124750CE","NSE:NIFTY2681124800CE","NSE:NIFTY2681124800PE","NSE:NIFTY2681124850CE",
    #           "NSE:NIFTY2681124850PE","NSE:NIFTY2681124900CE","NSE:NIFTY2681124900PE","NSE:NIFTY2681124950CE",
    #           "NSE:NIFTY2681124950PE"
    # ]
    # banknifty options
    # SYMBOLS = [
    #     "NSE:NIFTYBANK-INDEX","NSE:BANKNIFTY26AUGFUT",
    #            "NSE:NIFTYBANK26AUG56100CE","NSE:BANKNIFTY26AUG56100PE",
    #            "NSE:BANKNIFTY26AUG56200PE","NSE:BANKNIFTY26AUG56200CE","NSE:BANKNIFTY26AUG56300CE","NSE:BANKNIFTY26AUG56300PE",
    #            "NSE:BANKNIFTY26AUG56400CE","NSE:BANKNIFTY26AUG56400PE","NSE:BANKNIFTY26AUG56500CE","NSE:BANKNIFTY26AUG56500PE",
    #            "NSE:BANKNIFTY26AUG56600PE","NSE:BANKNIFTY26AUG56600CE","NSE:BANKNIFTY26AUG56700CE","NSE:BANKNIFTY26AUG56700PE",
    #            "NSE:BANKNIFTY26AUG56800PE","NSE:BANKNIFTY26AUG56800CE","NSE:BANKNIFTY26AUG56900PE","NSE:BANKNIFTY26AUG56900CE",
    #            "NSE:BANKNIFTY26AUG57000PE","NSE:BANKNIFTY26AUG57000CE","NSE:BANKNIFTY26AUG57100CE","NSE:BANKNIFTY26AUG57100PE",
    #            "NSE:BANKNIFTY26AUG57200CE","NSE:BANKNIFTY26AUG57200PE","NSE:BANKNIFTY26AUG57300CE","NSE:BANKNIFTY26AUG57300PE",
    #            "NSE:BANKNIFTY26AUG57400CE","NSE:BANKNIFTY26AUG57400PE","NSE:BANKNIFTY26AUG57500CE","NSE:BANKNIFTY26AUG57500PE",
    #            "NSE:BANKNIFTY26AUG57600CE","NSE:BANKNIFTY26AUG57600PE","NSE:BANKNIFTY26AUG57700PE","NSE:BANKNIFTY26AUG57700CE",
    #            "NSE:BANKNIFTY26AUG57800CE","NSE:BANKNIFTY26AUG57800PE","NSE:BANKNIFTY26AUG57900CE","NSE:BANKNIFTY26AUG57900PE",
    #            "NSE:BANKNIFTY26AUG58000CE","NSE:BANKNIFTY26AUG58000PE","NSE:BANKNIFTY26AUG58100CE","NSE:BANKNIFTY26AUG58100PE",
    #            "NSE:BANKNIFTY26AUG58200CE","NSE:BANKNIFTY26AUG58200PE","NSE:BANKNIFTY26AUG58300CE","NSE:BANKNIFTY26AUG58300PE",
    #            "NSE:BANKNIFTY26AUG58400PE","NSE:BANKNIFTY26AUG58400CE"
    #            ]
            
    SYMBOLS = [
#     "NSE:NIFTYBANK26AUGFUT",      # Futures
    "NSE:NIFTY2692224600PE",  # Call Option
    "NSE:NIFTY2692224600cE"   # Put Option
]
    # Format: ("Resolution", Days_to_Download)
    TIMEFRAMES = [
        ("1", 90),     # 1-minute data
        ("5", 90),     # 5-minute data
        ("10", 90),     # 5-minute data
        ("15", 90),    # 15-minute data
    ]
    # =========================================================================

    total_jobs = len(SYMBOLS) * len(TIMEFRAMES)
    downloaded_data = []
    
    console.clear()
    console.print(Panel(
        f"[bold white]Downloading {total_jobs} datasets directly via API...\n"
        f"Symbols: {len(SYMBOLS)}  |  Timeframes: {len(TIMEFRAMES)}\n"
        f"[bold cyan]📁 Save Location: {SAVE_DIR}[/bold cyan]\n",
        title="naresh BULK DATA DOWNLOADER (FIXED)", box=box.DOUBLE
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
                    # Calculate dates
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=days)
                    
                    # Prepare API payload
                    data = {
                        "symbol": symbol,
                        "resolution": resolution,
                        "date_format": "1",
                        "range_from": start_date.strftime("%Y-%m-%d"),
                        "range_to": end_date.strftime("%Y-%m-%d"),
                        "cont_flag": "1"
                    }
                    
                    # Call Fyers API directly
                    response = fyers.history(data)
                    
                    # Check if API call was successful
                    if response.get("s") == "ok" and response.get("candles"):
                        candles = response["candles"]
                        
                        # Convert to DataFrame
                        # Convert to DataFrame (Fyers returns 6 cols for Index, 7 cols for F&O)
                        # The 7th col is Open Interest (OI)
                        if len(candles[0]) == 7:
                            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
                        else:
                            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                            
                        # Convert UNIX timestamp to datetime (UTC)
                        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
                        
                        # ✅ FIX: Convert UTC to Indian Standard Time (IST) by adding 5 hours 30 mins
                        df['datetime'] = df['datetime'] + pd.Timedelta(hours=5, minutes=30)
                        
                        df = df.set_index('datetime')
                        df.drop('timestamp', axis=1, inplace=True)
                        
                        # ✅ FIX: Clean symbol name for Windows file system (replace : with _)
                        safe_symbol = symbol.replace(":", "_")
                        filename = os.path.join(SAVE_DIR, f"{safe_symbol}_{resolution}m.csv")
                        
                        # Save to CSV
                        df.to_csv(filename)
                        
                        downloaded_data.append({
                            "symbol": symbol,
                            "tf": resolution,
                            "days": days,
                            "candles": len(df),
                            "status": "Success"
                        })
                    else:
                        # API returned an error message
                        error_msg = response.get("message", "Unknown API error")
                        downloaded_data.append({
                            "symbol": symbol,
                            "tf": resolution,
                            "days": days,
                            "candles": 0,
                            "status": f"API: {error_msg}"
                        })
                        
                except Exception as e:
                    # ✅ FIX: Print the FULL error message to console
                    console.print(f"\n[bold red]CRITICAL ERROR on {symbol}: {str(e)}[/bold red]")
                    downloaded_data.append({
                        "symbol": symbol,
                        "tf": resolution,
                        "days": days,
                        "candles": 0,
                        "status": f"Code Error: {str(e)[:40]}"
                    })
                
                time.sleep(0.3) # Rate limiting
                progress.advance(task)

    # =========================================================================
    # PRINT FINAL SUMMARY TABLE
    # =========================================================================
    console.print("\n")
    summary_table = Table(title="Download Summary", box=box.HEAVY, show_lines=True, title_style="bold green")
    summary_table.add_column("Symbol", style="bold cyan", min_width=30, no_wrap=True)
    summary_table.add_column("Timeframe", justify="center", width=10)
    summary_table.add_column("Candles", justify="right", width=9)
    summary_table.add_column("Status", justify="center", width=30)
    
    for d in downloaded_data:
        status_str = d["status"]
        if status_str == "Success":
            status_str = f"[bold green]{status_str}[/bold green]"
        else:
            status_str = f"[bold red]{status_str}[/bold red]"
            
        summary_table.add_row(
            d["symbol"],
            d["tf"],
            str(d["candles"]),
            status_str
        )
        
    console.print(summary_table)