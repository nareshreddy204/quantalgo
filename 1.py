import requests
import yaml
from rich.console import Console
from fyers_apiv3 import fyersModel

console = Console()

# 1. Load config and token
try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    with open("token.txt", "r") as f:
        access_token = f.read().strip()
except Exception as e:
    console.print(f"[red]Error loading config/token: {e}[/red]")
    exit()

# 2. Fetch Symbol Master
console.print("[bold]Step 1: Fetching NSE_FO symbol master...[/bold]")
try:
    master = requests.get("https://api-t1.fyers.in/data/master/NSE_FO").json()
except Exception as e:
    console.print(f"[red]Failed to fetch master: {e}[/red]")
    master = {'symbols': {}}

# Build a set of valid symbols
all_fo_symbols = set()
if 'symbols' in master:
    for key in master['symbols']:
        all_fo_symbols.add(f"NSE:{key}")

# Search for your specific symbols
test_symbols = [
    "NSE:NIFTY26AUGFUT",
    "NSE:NIFTY26AUG23800CE",
    "NSE:NIFTY26AUG23800PE",
]

console.print("\n[bold yellow]Checking if symbols exist in Fyers Master...[/bold yellow]")
for sym in test_symbols:
    if sym in all_fo_symbols:
        console.print(f"[green]  ✓ FOUND: {sym}[/green]")
    else:
        console.print(f"[red]  ✗ NOT FOUND: {sym}[/red]")

# Show some available NIFTY futures to see what format Fyers actually expects
console.print("\n[bold cyan]Sample of available NIFTY futures in Fyers:[/bold cyan]")
nifty_futs = sorted([s for s in all_fo_symbols if 'NIFTY' in s and 'BANK' not in s and s.endswith('FUT')])
for f in nifty_futs[:10]:
    console.print(f"  {f}")

# 3. Initialize Fyers Model
fyers = fyersModel.FyersModel(
    token=access_token,
    is_async=False,
    client_id=config["fyers"]["app_id"]
)

# 4. Try actual API call
console.print("\n[bold]Step 2: Testing historical data API call...[/bold]")
data = {
    "symbol": "NSE:NIFTY26AUGFUT",
    "resolution": "1",
    "date_format": "1",
    "range_from": "2025-07-01",  # Adjust dates if you are running this far in the future
    "range_to": "2025-07-31",
    "cont_flag": "1"
}

response = fyers.history(data)
console.print(f"[cyan]Full API Response:[/cyan]")
console.print(response)

# Also test a currently active symbol to make sure token works
console.print("\n[bold]Step 3: Testing with NIFTY50-INDEX (known good)...[/bold]")
data2 = {
    "symbol": "NSE:NIFTY50-INDEX",
    "resolution": "1",
    "date_format": "1",
    "range_from": "2025-07-01",
    "range_to": "2025-07-31",
    "cont_flag": "1"
}
response2 = fyers.history(data2)
console.print(f"[cyan]NIFTY50 Response (should have candles):[/cyan]")
if response2.get("candles"):
    console.print(f"[green]Got {len(response2['candles'])} candles. Token works perfectly![/green]")
else:
    console.print(response2)