# data_fetcher.py
from datetime import datetime, timedelta
import pandas as pd
import logging
import time
import os

logger = logging.getLogger(__name__)

class DataFetcher:
    def __init__(self, fyers_client, save_dir="save_dir"):
        self.fyers = fyers_client
        # self.data_dir = "./data" # Define data folder
        self.save_dir = save_dir  # Default save directory

        # Create folder if it doesn't exist
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
        
        # Fyers API max days limit per resolution
        # Fyers API max days limit per resolution (Updated per docs)
        self.max_days_limit = {
            "1": 100, "2": 100, "3": 100, "5": 100, "10": 100,
            "15": 100, "20": 100, "30": 100, "45": 100, "60": 100,
            "120": 100, "180": 100, "240": 100,
            "D": 366, "1W": 366, "1M": 366
        }
    
    def _get_filename(self, symbol, resolution, days):
        """Generates a consistent filename for the CSV"""
        safe_symbol = symbol.replace(":", "_")
        return f"{self.save_dir}/{safe_symbol}_{resolution}m_{days}d.csv"
    
    def _fetch_single_chunk(self, symbol, resolution, start_date, end_date):
        """Fetch a single chunk of data within API limits"""
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start_date.strftime("%Y-%m-%d"),
            "range_to": end_date.strftime("%Y-%m-%d"),
            "cont_flag": 1
        }
        
        response = self.fyers.history(data)
        
        if response.get("s") == "ok":
            candles = response.get("candles", [])
            if len(candles) == 0:
                return None
            
            df = pd.DataFrame(candles)
            # Handle both Index (6 cols) and Options (7 cols with Open Interest)
            if len(df.columns) == 7:
                df.columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
            else:
                df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            # NEW CODE (Converts UTC to Indian Standard Time):
            df["datetime"] = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], unit="s"), tz='UTC').tz_convert('Asia/Kolkata').tz_localize(None)
            df = df.set_index("datetime").drop("timestamp", axis=1)
            return df
        else:
            logger.error(f"API Error for {symbol}: {response.get('message')}")
            return None

    def get_historical_data(self, symbol, resolution="5", days=60, force_refresh=False):
        filename = self._get_filename(symbol, resolution, days)

        # Check cache ONLY if force_refresh is False
        if not force_refresh and os.path.exists(filename):
            logger.info(f"Loading data from CACHE: {filename}")
            try:
                df = pd.read_csv(filename, index_col="datetime", parse_dates=True)
                cutoff = datetime.now() - timedelta(days=days)
                return df[df.index >= cutoff]
            except Exception as e:
                logger.error(f"Failed to read cache file, re-fetching... {e}")

        # --- FETCH FROM API (force_refresh=True always reaches here) ---
        logger.info(f"Fetching {days} days of {resolution}m data for {symbol} from API...")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        max_days = self.max_days_limit.get(resolution, 30)

        if days <= max_days:
            df = self._fetch_single_chunk(symbol, resolution, start_date, end_date)
        else:
            logger.info(f"Chunking request: {days} days exceeds {max_days}-day limit")
            all_data = []
            current_end = end_date
            while current_end > start_date:
                current_start = max(current_end - timedelta(days=max_days), start_date)
                logger.info(f"Fetching chunk: {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}")
                chunk_df = self._fetch_single_chunk(symbol, resolution, current_start, current_end)
                if chunk_df is not None and len(chunk_df) > 0:
                    all_data.append(chunk_df)
                current_end = current_start - timedelta(days=1)
                time.sleep(0.2)

            if not all_data:
                return None
            df = pd.concat(all_data)
            df = df[~df.index.duplicated(keep='first')]
            df = df.sort_index()

        if df is not None and len(df) > 0:
            df.to_csv(filename)
            logger.info(f"Saved to CACHE: {filename}")

        return df
    
    def get_quote(self, symbols):
        """Get real-time quotes"""
        data = {"symbols": ",".join(symbols)}
        return self.fyers.quotes(data)
    
    def get_market_status(self):
        """Check if market is open"""
        return self.fyers.market_status()