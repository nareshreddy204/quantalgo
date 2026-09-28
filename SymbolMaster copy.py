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
        self.mcx_df = None
        self._loaded = False

    def load(self):
        console.print("[cyan]Loading Symbol Master...[/cyan]")
        self.cm_df = self._fetch_segment("NSE_CM")
        self.fo_df = self._fetch_segment("NSE_FO")
        self.mcx_df = self._fetch_segment("MCX")
        self._loaded = True
        
        cm_count = len(self.cm_df) if self.cm_df is not None else 0
        fo_count = len(self.fo_df) if self.fo_df is not None else 0
        mcx_count = len(self.mcx_df) if self.mcx_df is not None else 0
        console.print(f"[green]✓ Loaded {cm_count} CM, {fo_count} FO, {mcx_count} MCX symbols[/green]\n")

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
        column_names = [
            "Fytoken",                # Col 0
            "Description",            # Col 1
            "ExchangeInstrumentType", # Col 2 (0=EQ, 10=INDEX, 11=FUT, 12=OPT)
            "MinimumLotSize",         # Col 3
            "TickSize",               # Col 4
            "ISIN",                   # Col 5
            "TradingHours",           # Col 6
            "ExpiryDate",             # Col 7
            "EpochExpiry",            # Col 8
            "fyers_symbol",           # Col 9 (e.g. MCX:GOLD26AUGFUT)
            "Extra10",                # Col 10
            "Extra11",                # Col 11
            "Extra12",                # Col 12
            "Symbol",                 # Col 13 (e.g. GOLD, CRUDEOIL)
            "Extra14",                # Col 14
            "Series",                 # Col 15
            "Extra16",                # Col 16
            "Extra17",                # Col 17
            "Extra18",                # Col 18
            "Extra19",                # Col 19
            "Extra20"                 # Col 20
        ]

        try:
            df = pd.read_csv(io.StringIO(text), header=None, low_memory=False, dtype=str)

            # Skip header row if present
            if str(df.iloc[0, 0]).strip().startswith("Fytoken"):
                df = df.iloc[1:].reset_index(drop=True)

            num_cols = min(len(df.columns), len(column_names))
            df.columns = column_names[:num_cols] + [f"Col_{i}" for i in range(num_cols, len(df.columns))]

            for col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()

            return df

        except Exception as e:
            console.print(f"[red]Error parsing {segment} master: {e}[/red]")
            return pd.DataFrame()

    def get_stocks(self) -> pd.DataFrame:
        if self.cm_df is None or self.cm_df.empty:
            return pd.DataFrame()
            
        df = self.cm_df.copy()
        df = df[df["ExchangeInstrumentType"] == "0"]
        df = df[df["fyers_symbol"].str.endswith("-EQ")]
        return df[["Symbol", "fyers_symbol", "Description"]].drop_duplicates().reset_index(drop=True)

    def get_indices(self) -> pd.DataFrame:
        if self.cm_df is None or self.cm_df.empty:
            return pd.DataFrame()
            
        df = self.cm_df.copy()
        idx_df = df[(df["ExchangeInstrumentType"] == "10") | (df["fyers_symbol"].str.endswith("-INDEX"))].copy()
        return idx_df[["Symbol", "fyers_symbol", "Description"]].drop_duplicates().reset_index(drop=True)

    def get_options(self,
                    underlying: str = "BANKNIFTY",
                    expiry_type: str = "all",        
                    option_type: str = "all",        
                    strike_min: float = None,
                    strike_max: float = None) -> pd.DataFrame:
                        
        if self.fo_df is None or self.fo_df.empty:
            return pd.DataFrame()

        df = self.fo_df.copy()
        df = df[df["Symbol"].str.upper() == underlying.upper()]
        df = df[df["fyers_symbol"].str.contains("CE|PE", regex=True)]

        if option_type.upper() != "ALL":
            df = df[df["fyers_symbol"].str.endswith(option_type.upper())]

        if "EpochExpiry" in df.columns:
            df["Expiry"] = pd.to_datetime(pd.to_numeric(df["EpochExpiry"], errors="coerce"), unit="s")
        else:
            df["Expiry"] = pd.to_datetime(df["ExpiryDate"], errors="coerce")

        if expiry_type.lower() != "all" and not df.empty:
            df["year_month"] = df["Expiry"].dt.to_period("M")
            monthly_dates = df.groupby("year_month")["Expiry"].transform("max")
            df["expiry_kind"] = "weekly"
            df.loc[df["Expiry"] == monthly_dates, "expiry_kind"] = "monthly"

            if expiry_type.lower() == "monthly":
                df = df[df["expiry_kind"] == "monthly"]
            elif expiry_type.lower() == "weekly":
                df = df[df["expiry_kind"] == "weekly"]

        df["StrikePrice"] = df["fyers_symbol"].str.extract(r'(\d+)(?:CE|PE)$')[0].astype(float)

        if strike_min is not None:
            df = df[df["StrikePrice"] >= strike_min]
        if strike_max is not None:
            df = df[df["StrikePrice"] <= strike_max]

        df = df.sort_values(["Expiry", "StrikePrice"]).reset_index(drop=True)
        cols = ["fyers_symbol", "Symbol", "Expiry", "StrikePrice", "Description"]
        return df[[c for c in cols if c in df.columns]]

    def get_futures(self, underlying: str = "BANKNIFTY") -> pd.DataFrame:
        if self.fo_df is None or self.fo_df.empty:
            return pd.DataFrame()
            
        df = self.fo_df.copy()
        df = df[df["Symbol"].str.upper() == underlying.upper()]
        df = df[df["fyers_symbol"].str.contains("FUT")]
        
        if "EpochExpiry" in df.columns:
            df["Expiry"] = pd.to_datetime(pd.to_numeric(df["EpochExpiry"], errors="coerce"), unit="s")
        else:
            df["Expiry"] = pd.to_datetime(df["ExpiryDate"], errors="coerce")

        return df[["fyers_symbol", "Symbol", "Expiry", "Description"]].sort_values("Expiry").reset_index(drop=True)

    # ==================== MCX / COMMODITY METHODS ====================

    def get_mcx_futures(self, commodity: str = "CRUDEOIL") -> pd.DataFrame:
        """Fetch Futures contracts for MCX commodities (e.g. GOLD, SILVER, CRUDEOIL, NATURALGAS)"""
        if self.mcx_df is None or self.mcx_df.empty:
            return pd.DataFrame()

        df = self.mcx_df.copy()
        df = df[df["Symbol"].str.upper() == commodity.upper()]
        df = df[df["fyers_symbol"].str.contains("FUT")]

        if "EpochExpiry" in df.columns:
            df["Expiry"] = pd.to_datetime(pd.to_numeric(df["EpochExpiry"], errors="coerce"), unit="s")
        else:
            df["Expiry"] = pd.to_datetime(df["ExpiryDate"], errors="coerce")

        return df[["fyers_symbol", "Symbol", "Expiry", "Description"]].sort_values("Expiry").reset_index(drop=True)

    def get_mcx_options(self,
                        commodity: str = "GOLD",
                        option_type: str = "all",
                        strike_min: float = None,
                        strike_max: float = None) -> pd.DataFrame:
        """Fetch Options contracts for MCX commodities"""
        if self.mcx_df is None or self.mcx_df.empty:
            return pd.DataFrame()

        df = self.mcx_df.copy()
        df = df[df["Symbol"].str.upper() == commodity.upper()]
        df = df[df["fyers_symbol"].str.contains("CE|PE", regex=True)]

        if option_type.upper() != "ALL":
            df = df[df["fyers_symbol"].str.endswith(option_type.upper())]

        if "EpochExpiry" in df.columns:
            df["Expiry"] = pd.to_datetime(pd.to_numeric(df["EpochExpiry"], errors="coerce"), unit="s")
        else:
            df["Expiry"] = pd.to_datetime(df["ExpiryDate"], errors="coerce")

        df["StrikePrice"] = df["fyers_symbol"].str.extract(r'(\d+)(?:CE|PE)$')[0].astype(float)

        if strike_min is not None:
            df = df[df["StrikePrice"] >= strike_min]
        if strike_max is not None:
            df = df[df["StrikePrice"] <= strike_max]

        df = df.sort_values(["Expiry", "StrikePrice"]).reset_index(drop=True)
        cols = ["fyers_symbol", "Symbol", "Expiry", "StrikePrice", "Description"]
        return df[[c for c in cols if c in df.columns]]

    def list_mcx_commodities(self) -> list:
        """List unique available commodities on MCX"""
        if self.mcx_df is None or self.mcx_df.empty:
            return []
        commodities = self.mcx_df["Symbol"].dropna().unique().tolist()
        return sorted([c for c in commodities if c and c.isalpha()])

    def list_option_underlyings(self) -> list:
        if self.fo_df is None or self.fo_df.empty:
            return []
        underlyings = self.fo_df["Symbol"].dropna().unique().tolist()
        return sorted([u for u in underlyings if u and u.isalpha()])


# ===========================================================================
# MAIN EXECUTION BLOCK
# ===========================================================================
if __name__ == "__main__":

    finder = SymbolFinder()
    finder.load()

    # ----- STOCKS -----
    stocks = finder.get_stocks()
    console.print(f"[bold]Total EQ stocks:[/bold] {len(stocks)}")

    # ----- INDICES -----
    indices = finder.get_indices()
    console.print(f"[bold]Indices:[/bold] {len(indices)}")

    # ----- MCX COMMODITIES LIST -----
    mcx_items = finder.list_mcx_commodities()
    console.print(f"\n[bold]Available MCX Commodities ({len(mcx_items)}):[/bold] {mcx_items}")

    # ----- MCX CRUDEOIL FUTURES -----
    crude_futs = finder.get_mcx_futures("CRUDEOIL")
    console.print(f"\n[bold]CRUDEOIL MCX Futures:[/bold]")
    if not crude_futs.empty:
        console.print(crude_futs.to_string(index=False))

    # ----- MCX GOLD FUTURES -----
    gold_futs = finder.get_mcx_futures("GOLD")
    console.print(f"\n[bold]GOLD MCX Futures:[/bold]")
    if not gold_futs.empty:
        console.print(gold_futs.to_string(index=False))

    # ----- MCX GOLD OPTIONS -----
    gold_opts = finder.get_mcx_options("GOLD", option_type="CE")
    console.print(f"\n[bold]GOLD MCX CE Options:[/bold] {len(gold_opts)}")
    if not gold_opts.empty:
        console.print(gold_opts.head(10).to_string(index=False))