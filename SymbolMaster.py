import io
import csv
import requests
import pandas as pd
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

FYERS_MASTER_URLS = {
    "NSE_CM": "https://public.fyers.in/sym_details/NSE_CM.csv",
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "BSE_CM": "https://public.fyers.in/sym_details/BSE_CM.csv",
    "BSE_FO": "https://public.fyers.in/sym_details/BSE_FO.csv",
    "MCX": "https://public.fyers.in/sym_details/MCX_COM.csv"
}

class SymbolFinder:
    def __init__(self):
        self.cm_df = None    
        self.fo_df = None    
        self._loaded = False

    def load(self):
        console.print("[cyan]Loading Symbol Master...[/cyan]")
        self.cm_df = self._fetch_segment("NSE_CM")
        self.fo_df = self._fetch_segment("NSE_FO")
        self._loaded = True
        
        cm_count = len(self.cm_df) if self.cm_df is not None else 0
        fo_count = len(self.fo_df) if self.fo_df is not None else 0
        console.print(f"[green]✓ Loaded {cm_count} CM symbols, {fo_count} FO symbols[/green]\n")

    def _fetch_segment(self, segment: str) -> pd.DataFrame:
        url = FYERS_MASTER_URLS.get(segment)
        if not url:
            return pd.DataFrame()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        try:
            console.print(f"[cyan]Downloading {segment} master...[/cyan]")
            resp = requests.get(url, timeout=15, headers=headers)
            if resp.status_code == 200:
                console.print(f"[green]✓ Successfully fetched {segment}[/green]")
                return self._parse_master(resp.text, segment)
        except Exception as e:
            console.print(f"[red]Request failed for {segment}: {e}[/red]")
            
        console.print(f"[red]✗ Failed to download {segment} master.[/red]")
        return pd.DataFrame()

    def _parse_master(self, text: str, segment: str) -> pd.DataFrame:
        # EXACT FYERS v3 Public CSV Column Alignment
        column_names = [
            "Fytoken",                # 0
            "SymbolDetails",          # 1
            "ExchangeInstrumentType", # 2 (0=EQ, 10=INDEX, 11/12=OPT, 14/15=FUT)
            "MinimumLotSize",         # 3
            "TickSize",               # 4
            "ISIN",                   # 5
            "TradingSymbol",          # 6
            "Expiry",                 # 7
            "Symbol",                 # 8
            "StrikePrice",            # 9
            "OptionType",             # 10
            "Extra1",                 # 11
            "UnderlyingSymbol",       # 12
            "Exchange",               # 13
            "Segment",                # 14
            "Series"                  # 15
        ]

        try:
            df = pd.read_csv(io.StringIO(text), header=None, low_memory=False)

            # Skip header row if present
            if str(df.iloc[0, 0]).strip().startswith("Fytoken"):
                df = df.iloc[1:].reset_index(drop=True)

            num_cols = min(len(df.columns), len(column_names))
            df.columns = column_names[:num_cols] + [f"Col_{i}" for i in range(num_cols, len(df.columns))]

            # Clean and explicitly convert string columns
            str_cols = ["SymbolDetails", "TradingSymbol", "Symbol", "OptionType", "UnderlyingSymbol", "Series", "ExchangeInstrumentType"]
            for col in str_cols:
                if col in df.columns:
                    df[col] = df[col].fillna("").astype(str).str.strip()

            return df

        except Exception as e:
            console.print(f"[red]Error parsing {segment} master: {e}[/red]")
            return pd.DataFrame()

    def get_stocks(self) -> pd.DataFrame:
        if self.cm_df is None or self.cm_df.empty:
            return pd.DataFrame()
            
        df = self.cm_df.copy()
        # Filter Equity: Series 'EQ' or ExchangeInstrumentType == '0'
        df = df[(df["Series"] == "EQ") | (df["ExchangeInstrumentType"] == "0")]
        df["fyers_symbol"] = "NSE:" + df["Symbol"] + "-EQ"
        return df[["Symbol", "fyers_symbol", "Series"]].drop_duplicates().reset_index(drop=True)

    def get_indices(self) -> pd.DataFrame:
        if self.cm_df is None or self.cm_df.empty:
            return pd.DataFrame()
            
        df = self.cm_df.copy()
        # ExchangeInstrumentType '10' represents Index
        idx_df = df[df["ExchangeInstrumentType"] == "10"].copy()
        idx_df["fyers_symbol"] = "NSE:" + idx_df["Symbol"] + "-INDEX"
        return idx_df[["Symbol", "fyers_symbol"]].drop_duplicates().reset_index(drop=True)

    def get_options(self,
                    underlying: str = "BANKNIFTY",
                    expiry_type: str = "all",        
                    option_type: str = "all",        
                    strike_min: float = None,
                    strike_max: float = None,
                    expiry_date: str = None) -> pd.DataFrame:
                        
        if self.fo_df is None or self.fo_df.empty:
            return pd.DataFrame()

        df = self.fo_df.copy()

        if "UnderlyingSymbol" not in df.columns:
            return pd.DataFrame()

        # Match underlying symbol
        df = df[df["UnderlyingSymbol"].str.upper() == underlying.upper()]
        
        # FYERS options are ExchangeInstrumentType 11 (OPTIDX) or 12 (OPTSTK)
        df = df[df["ExchangeInstrumentType"].isin(["11", "12"])]

        if option_type.upper() != "ALL" and "OptionType" in df.columns:
            df = df[df["OptionType"].str.upper() == option_type.upper()]

        if "Expiry" in df.columns:
            df["Expiry"] = pd.to_datetime(df["Expiry"], errors="coerce")
            df = df[df["Expiry"].notna()]
        else:
            return pd.DataFrame()

        if expiry_date:
            target = pd.to_datetime(expiry_date)
            df = df[df["Expiry"].dt.date == target.date()]
        elif not df.empty:
            df["year_month"] = df["Expiry"].dt.to_period("M")
            monthly_dates = df.groupby("year_month")["Expiry"].transform("max")
            
            # vectorized conditional logic replacing df.apply
            df["expiry_kind"] = "weekly"
            df.loc[df["Expiry"] == monthly_dates, "expiry_kind"] = "monthly"

            if expiry_type.lower() == "monthly":
                df = df[df["expiry_kind"] == "monthly"]
            elif expiry_type.lower() == "weekly":
                df = df[df["expiry_kind"] == "weekly"]

        if "StrikePrice" in df.columns:
            df["StrikePrice"] = pd.to_numeric(df["StrikePrice"], errors="coerce")
            if strike_min is not None:
                df = df[df["StrikePrice"] >= strike_min]
            if strike_max is not None:
                df = df[df["StrikePrice"] <= strike_max]

        df["fyers_symbol"] = df["TradingSymbol"]
        df = df.sort_values(["Expiry", "StrikePrice", "OptionType"]).reset_index(drop=True)

        cols = ["fyers_symbol", "Symbol", "UnderlyingSymbol", "Expiry", "StrikePrice", "OptionType"]
        if "expiry_kind" in df.columns:
            cols.append("expiry_kind")
        return df[cols]

    def get_futures(self, underlying: str = "BANKNIFTY") -> pd.DataFrame:
        if self.fo_df is None or self.fo_df.empty:
            return pd.DataFrame()
            
        df = self.fo_df.copy()
        df = df[df["UnderlyingSymbol"].str.upper() == underlying.upper()]
        # Futures are ExchangeInstrumentType 14 (FUTIDX) or 15 (FUTSTK)
        df = df[df["ExchangeInstrumentType"].isin(["14", "15"])]
        df["Expiry"] = pd.to_datetime(df["Expiry"], errors="coerce")
        df["fyers_symbol"] = df["TradingSymbol"]
        return df[["fyers_symbol", "Symbol", "Expiry"]].sort_values("Expiry").reset_index(drop=True)

    def list_option_underlyings(self) -> list:
        if self.fo_df is None or self.fo_df.empty:
            return []
        underlyings = self.fo_df["UnderlyingSymbol"].dropna().unique().tolist()
        return sorted([u for u in underlyings if u and not u.isdigit()])


# ===========================================================================
# DEMO / MAIN EXECUTION BLOCK
# ===========================================================================
if __name__ == "__main__":

    finder = SymbolFinder()
    finder.load()

    # ----- STOCKS -----
    stocks = finder.get_stocks()
    console.print(f"[bold]Total EQ stocks:[/bold] {len(stocks)}")
    if not stocks.empty:
        console.print(stocks.head(10).to_string(index=False))

    # ----- INDICES -----
    indices = finder.get_indices()
    console.print(f"\n[bold]Indices:[/bold] {len(indices)}")
    if not indices.empty:
        console.print(indices.head(10).to_string(index=False))

    # ----- Available F&O underlyings -----
    console.print(f"\n[bold]Sample F&O Underlyings:[/bold] {finder.list_option_underlyings()[:15]}")

    # ----- WEEKLY OPTIONS for BANKNIFTY -----
    weekly_opts = finder.get_options(
        underlying="BANKNIFTY",
        expiry_type="weekly",
        option_type="CE",
        strike_min=45000,
        strike_max=55000
    )
    console.print(f"\n[bold]BANKNIFTY Weekly CE options:[/bold] {len(weekly_opts)}")

    if not weekly_opts.empty:
        tbl = Table(title="Weekly CE Options", box=box.SIMPLE_HEAVY, show_lines=False)
        tbl.add_column("Fyers Symbol", style="cyan")
        tbl.add_column("Expiry", justify="center")
        tbl.add_column("Strike", justify="right")
        tbl.add_column("Type", justify="center")
        for _, r in weekly_opts.head(10).iterrows():
            tbl.add_row(
                str(r["fyers_symbol"]),
                r["Expiry"].strftime("%Y-%m-%d") if pd.notnull(r["Expiry"]) else "-",
                f"{r['StrikePrice']:.0f}",
                str(r["OptionType"])
            )
        console.print(tbl)