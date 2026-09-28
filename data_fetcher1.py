# data_fetcher.py
from datetime import datetime, timedelta
import pandas as pd
import logging
import time
import os

logger = logging.getLogger(__name__)

class DataFetcher:
    def __init__(self, fyers_client, category="indices"):
        self.fyers = fyers_client
        
        # Set the folder path based on the category you pass
        # Default is "indices", but you can pass "mcx" or "nifty50symbols"
        self.data_dir = f"./data/{category}"
        
        # Create folder if it doesn't exist
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        # Fyers API max days limit per resolution
        self.max_days_limit = {
            "1": 5, "2": 10, "3": 15, "5": 30,
            "15": 60, "30": 60, "60": 365, "D": 365
        }
    
    def _get_filename(self, symbol, resolution, days):
        """Generates a consistent filename for the CSV"""
        safe_symbol = symbol.replace(":", "_")
        return f"{self.data_dir}/{safe_symbol}_{resolution}m_{days}d.csv"
    
    def _fetch_single_chunk(self, symbol, resolution, start_date, end_date):
        """Fetch a single chunk of data within API limits"""
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start_date.strftime("%Y-%m-%d"),
            "range_to": end_date.strftime("%Y-%m-%d"),
            "cont_flag": "1"
        }
        
        response = self.fyers.history(data)
        
        if response.get("s") == "ok":
            candles = response.get("candles", [])
            if len(candles) == 0:
                return None
            
            df = pd.DataFrame(candles)
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.set_index("datetime").drop("timestamp", axis=1)
            return df
        else:
            logger.error(f"API Error for {symbol}: {response.get('message')}")
            return None

    def get_historical_data(self, symbol, resolution, days=30):
        """Fetch historical data (Checks CSV cache first!)"""
        
        filename = self._get_filename(symbol, resolution, days)
        
        # STEP 1: CHECK IF DATA EXISTS LOCALLY
        if os.path.exists(filename):
            logger.info(f"Loading data from CACHE: {filename}")
            try:
                df = pd.read_csv(filename, index_col="datetime", parse_dates=True)
                return df
            except Exception as e:
                logger.error(f"Failed to read cache file, re-fetching... {e}")
        
        # STEP 2: DATA NOT FOUND, FETCH FROM API
        logger.info(f"Cache miss. Fetching {days} days of {resolution}m data for {symbol} from API...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        max_days = self.max_days_limit.get(resolution, 30)
        
        if days <= max_days:
            df = self._fetch_single_chunk(symbol, resolution, start_date, end_date)
        else:
            # Chunking logic
            logger.info(f"Chunking request: {days} days exceeds {max_days}-day limit")
            all_data = []
            current_end = end_date
            
            while current_end > start_date:
                current_start = current_end - timedelta(days=max_days)
                if current_start < start_date:
                    current_start = start_date
                    
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
        
        # STEP 3: SAVE TO CSV FOR NEXT TIME
        if df is not None and len(df) > 0:
            df.to_csv(filename)
            logger.info(f"Saved to CACHE: {filename}")
        else:
            logger.error("Failed to fetch data from API.")
            
        return df
    
    def get_quote(self, symbols):
        """Get real-time quotes"""
        data = {"symbols": ",".join(symbols)}
        return self.fyers.quotes(data)
    
    def get_market_status(self):
        """Check if market is open"""
        return self.fyers.market_status()