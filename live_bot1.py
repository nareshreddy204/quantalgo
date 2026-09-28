# live_bot1.py
import os
import yaml
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import logging

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy_copy1 import Strategy

# Setup Logging
logging.basicConfig(
    filename=f"live_bot_{datetime.now():%Y%m%d_%H%M%S}.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

console = Console()

class PaperTradingEngine:
    def __init__(self):
        self.config = self.load_config()
        
        auth = FyersAuth(self.config["fyers"]["app_id"], self.config["fyers"]["secret_key"])
        auth.set_access_token(self.config["fyers"]["access_token"])
        self.data_fetcher = DataFetcher(auth.fyers)
        
        self.symbol = self.config.get("symbol", "NSE:BANKNIFTY26JUL56900PE")
        self.timeframe = int(self.config.get("resolution", 1))
        self.strategy_name = self.config.get("strategy", "supertrend_macd")
        self.sl_mult = float(self.config.get("sl_atr_multiplier", 2.0))
        self.tp_mult = float(self.config.get("tp_atr_multiplier", 2.0))
        
        self.strategy = Strategy(self.strategy_name)
        
        self.position = "FLAT"
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.trade_history = []
        self.current_candle_time = None
        self.last_ltp = 0.0
        self.last_api_call_time = 0
        self.last_ltp_call_time = 0
        
        console.print(Panel(
            f"[bold green]PAPER TRADING ENGINE INITIALIZED[/bold green]\n"
            f"Symbol: {self.symbol} | TF: {self.timeframe}m\n"
            f"Strategy: {self.strategy_name} | SL: {self.sl_mult}x ATR | TP: {self.tp_mult}x ATR",
            title="🤖 KRONOS LIVE BOT", border_style="green"
        ))

    def load_config(self):
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        if os.path.exists("token.txt"):
            with open("token.txt", "r") as f:
                config["fyers"]["access_token"] = f.read().strip()
        return config

    def is_market_open(self):
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_start <= now <= market_end

    def fetch_ltp(self):
        """Fetch real-time LTP using Quotes API - much faster than historical"""
        current_time = time.time()
        # Rate limit: max 1 LTP call per 1 second
        if (current_time - self.last_ltp_call_time) < 1:
            return self.last_ltp if self.last_ltp > 0 else None
        
        try:
            self.last_ltp_call_time = current_time
            response = self.data_fetcher.fyers.quotes({
                "symbols": self.symbol
            })
            
            if response.get('s') == 'ok' and response.get('d'):
                data = response['d'][0]
                # Try different possible keys for last price
                v = data.get('v', {})
                ltp = float(v.get('lp', 0))  # lp = last traded price
                
                if ltp > 0:
                    self.last_ltp = ltp
                    return ltp
            
            return None
        except Exception as e:
            logging.error(f"Error fetching LTP: {e}")
            return None

    def fetch_latest_data(self, force=False):
        """Fetch historical data for signal generation only"""
        current_time = time.time()
        if not force and (current_time - self.last_api_call_time) < 3:
            return None
        
        try:
            self.last_api_call_time = current_time
            df = self.data_fetcher.get_historical_data(self.symbol, self.timeframe, days=5)
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            console.print(f"[bold red]API Error: {e}[/bold red]")
            logging.error(f"Error fetching data: {e}")
            return None

    def generate_live_signal(self, df):
        try:
            df = self.strategy.generate_signals(df.copy())
            
            if hasattr(self.strategy, 'calculate_atr'):
                df['atr'] = self.strategy.calculate_atr(df)
            else:
                df['atr'] = self._calculate_atr_fallback(df)
            
            last_closed = df.iloc[-2]
            
            signal = int(last_closed.get('signal', 0))
            atr = float(last_closed.get('atr', 0))
            
            # Use REAL-TIME LTP instead of historical close
            ltp = self.fetch_ltp()
            if ltp is None:
                ltp = float(df.iloc[-1]['close'])  # Fallback
            
            return signal, atr, ltp, last_closed.name
        except Exception as e:
            logging.error(f"Error generating signal: {e}")
            return 0, 0, self.last_ltp, None

    def _calculate_atr_fallback(self, df, period=14):
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def check_exit_conditions(self, current_ltp):
        if self.position == "FLAT" or current_ltp <= 0:
            return False
            
        exited = False
        if self.position == "LONG":
            if current_ltp <= self.stop_loss:
                self.close_position("SL Hit", current_ltp)
                exited = True
            elif current_ltp >= self.take_profit:
                self.close_position("TP Hit", current_ltp)
                exited = True
                
        elif self.position == "SHORT":
            if current_ltp >= self.stop_loss:
                self.close_position("SL Hit", current_ltp)
                exited = True
            elif current_ltp <= self.take_profit:
                self.close_position("TP Hit", current_ltp)
                exited = True
        
        return exited

    def open_position(self, signal, atr, price):
        if atr <= 0 or price <= 0:
            console.print("[red]Invalid ATR or price, skipping entry[/red]")
            return
            
        self.position = "LONG" if signal == 1 else "SHORT"
        self.entry_price = price
        
        if self.position == "LONG":
            self.stop_loss = price - (self.sl_mult * atr)
            self.take_profit = price + (self.tp_mult * atr)
        else:
            self.stop_loss = price + (self.sl_mult * atr)
            self.take_profit = price - (self.tp_mult * atr)
            
        console.print(f"[bold green]🚀 OPENED {self.position}[/bold green] @ {price:.2f} | SL: {self.stop_loss:.2f} | TP: {self.take_profit:.2f}")
        logging.info(f"OPENED {self.position} @ {price} | SL: {self.stop_loss} | TP: {self.take_profit}")

    def close_position(self, reason, price):
        if self.position == "FLAT":
            return
            
        if self.position == "LONG":
            pnl = price - self.entry_price
        else:
            pnl = self.entry_price - price
            
        self.trade_history.append({
            "time": datetime.now(),
            "type": self.position,
            "entry": self.entry_price,
            "exit": price,
            "pnl": pnl,
            "reason": reason
        })
        
        pnl_str = f"[green]+{pnl:.2f}[/green]" if pnl > 0 else f"[red]{pnl:.2f}[/red]"
        console.print(f"[bold yellow]🔒 CLOSED {self.position}[/bold yellow] ({reason}) @ {price:.2f} | P&L: {pnl_str}")
        logging.info(f"CLOSED {self.position} ({reason}) @ {price} | P&L: {pnl}")
        
        self.position = "FLAT"
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

    def render_dashboard(self, current_ltp):
        console.clear()
        
        table = Table(title=f"KRONOS LIVE BOT - {self.symbol} ({self.timeframe}m)", border_style="cyan")
        table.add_column("Metric", style="bold cyan", width=20)
        table.add_column("Value", style="white", width=30)
        
        unrealized_pnl = 0.0
        if self.position == "LONG":
            unrealized_pnl = current_ltp - self.entry_price
        elif self.position == "SHORT":
            unrealized_pnl = self.entry_price - current_ltp
            
        pnl_color = "green" if unrealized_pnl >= 0 else "red"
        
        table.add_row("Status", "🟢 RUNNING" if self.is_market_open() else "🔴 MARKET CLOSED")
        table.add_row("Current Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        table.add_row("LTP (Live)", f"[bold]{current_ltp:.2f}[/bold]")  # Changed label
        table.add_row("Position", f"[bold]{self.position}[/bold]")
        
        if self.position != "FLAT":
            table.add_row("Entry Price", f"{self.entry_price:.2f}")
            table.add_row("Stop Loss", f"[red]{self.stop_loss:.2f}[/red]")
            table.add_row("Take Profit", f"[green]{self.take_profit:.2f}[/green]")
            table.add_row("Unrealized P&L", f"[{pnl_color}]{unrealized_pnl:.2f}[/{pnl_color}]")
        else:
            table.add_row("Entry Price", "N/A")
            table.add_row("Stop Loss", "N/A")
            table.add_row("Take Profit", "N/A")
            table.add_row("Unrealized P&L", "0.00")
        
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        total_color = "green" if total_pnl >= 0 else "red"
        table.add_row("Realized P&L", f"[{total_color}]{total_pnl:.2f}[/{total_color}]")
        table.add_row("Trades Today", str(len(self.trade_history)))
        
        console.print(table)
        
        if self.trade_history:
            history_table = Table(title="Trade History", border_style="dim")
            history_table.add_column("Time", width=8)
            history_table.add_column("Type", width=6)
            history_table.add_column("Entry", width=10)
            history_table.add_column("Exit", width=10)
            history_table.add_column("P&L", width=10)
            history_table.add_column("Reason", width=12)
            
            for t in self.trade_history[-5:]:
                pnl_str = f"[green]+{t['pnl']:.2f}[/green]" if t['pnl'] > 0 else f"[red]{t['pnl']:.2f}[/red]"
                history_table.add_row(
                    t['time'].strftime("%H:%M"),
                    t['type'],
                    f"{t['entry']:.2f}",
                    f"{t['exit']:.2f}",
                    pnl_str,
                    t['reason']
                )
            console.print(history_table)

    def get_seconds_to_next_candle(self):
        now = datetime.now()
        minute = now.minute
        
        target_minute = ((minute // self.timeframe) + 1) * self.timeframe
        
        if target_minute >= 60:
            next_hour = now.hour + 1
            if next_hour >= 24:
                next_hour = 0
            next_candle = now.replace(hour=next_hour, minute=0, second=5, microsecond=0)
        else:
            next_candle = now.replace(minute=target_minute, second=5, microsecond=0)
        
        return (next_candle - now).total_seconds()

    def run(self):
        console.print("[yellow]Starting Live Bot... Press Ctrl+C to stop.[/yellow]")
        
        try:
            # Initial data fetch
            console.print("[cyan]Fetching initial data...[/cyan]")
            
            # Fetch real LTP first
            ltp = self.fetch_ltp()
            if ltp:
                console.print(f"[green]Live LTP: {ltp:.2f}[/green]")
            else:
                console.print("[yellow]Could not fetch LTP, will use historical close as fallback[/yellow]")
            
            # Fetch historical data for signals
            df = self.fetch_latest_data(force=True)
            
            # Render dashboard with real LTP
            display_ltp = ltp if ltp else (self.last_ltp if self.last_ltp > 0 else 0)
            if display_ltp > 0:
                self.render_dashboard(display_ltp)
            else:
                console.print("[red]Failed to fetch any price data. Will retry...[/red]")
            
            while True:
                now = datetime.now()
                
                if not self.is_market_open():
                    console.clear()
                    console.print(f"[dim red]Market closed. Current time: {now.strftime('%H:%M:%S')}[/dim red]")
                    console.print("[dim]Waiting for market to open (9:15 AM - 3:30 PM, Mon-Fri)...[/dim]")
                    time.sleep(60)
                    continue
                
                wait_seconds = self.get_seconds_to_next_candle()
                
                if wait_seconds < 0:
                    time.sleep(3)
                    wait_seconds = 0
                
                if wait_seconds > 0:
                    elapsed = 0.0
                    last_sl_check = 0.0
                    sl_check_interval = 10  # Check SL/TP every 10 seconds with LTP API
                    last_display_update = 0.0
                    display_interval = 15  # Update display every 15 seconds
                    
                    while elapsed < wait_seconds:
                        remaining = wait_seconds - elapsed
                        sleep_time = min(5.0, remaining)
                        
                        if sleep_time <= 0:
                            break
                        
                        time.sleep(sleep_time)
                        elapsed += sleep_time
                        
                        # Check SL/TP using REAL-TIME LTP
                        if self.position != "FLAT" and (elapsed - last_sl_check) >= sl_check_interval:
                            ltp = self.fetch_ltp()
                            if ltp and ltp > 0:
                                if self.check_exit_conditions(ltp):
                                    self.render_dashboard(ltp)
                                    break
                                last_sl_check = elapsed
                        
                        # Update display with real LTP
                        if (elapsed - last_display_update) >= display_interval:
                            ltp = self.fetch_ltp()
                            if ltp and ltp > 0:
                                remaining_int = int(wait_seconds - elapsed)
                                pos_color = "green" if self.position == "LONG" else ("red" if self.position == "SHORT" else "white")
                                console.print(f"[dim cyan]Next candle in {remaining_int}s | [{pos_color}]{self.position}[/{pos_color}] | LTP: {ltp:.2f}[/dim cyan]")
                            last_display_update = elapsed
                
                time.sleep(2)
                
                # Candle closed - fetch historical for signals
                console.print("\n[bold blue]⏰ Candle closed. Fetching data...[/bold blue]")
                df = self.fetch_latest_data(force=True)
                
                if df is None or df.empty:
                    console.print("[red]Failed to fetch data. Retrying next cycle.[/red]")
                    continue
                
                # Generate signal (this internally calls fetch_ltp for entry price)
                signal, atr, current_ltp, candle_time = self.generate_live_signal(df)
                
                if candle_time is None:
                    console.print("[red]Invalid candle time, skipping.[/red]")
                    continue
                
                if candle_time == self.current_candle_time:
                    console.print("[dim]Same candle, skipping...[/dim]")
                    continue
                
                self.current_candle_time = candle_time
                console.print(f"[dim]Signal: {signal} | ATR: {atr:.2f} | LTP: {current_ltp:.2f} | Candle: {candle_time}[/dim]")
                
                # Exit if opposite signal
                if self.position == "LONG" and signal == -1:
                    self.close_position("Reverse Signal", current_ltp)
                elif self.position == "SHORT" and signal == 1:
                    self.close_position("Reverse Signal", current_ltp)
                
                # Enter new position if flat
                if self.position == "FLAT" and signal != 0:
                    self.open_position(signal, atr, current_ltp)
                
                # Check SL/TP immediately
                self.check_exit_conditions(current_ltp)
                
                # Update Dashboard
                self.render_dashboard(current_ltp)
                
        except KeyboardInterrupt:
            console.print("\n[bold red]Bot stopped by user.[/bold red]")
            if self.position != "FLAT":
                console.print(f"[yellow]⚠️  WARNING: Bot stopped while in {self.position} position @ {self.entry_price:.2f}[/yellow]")
                console.print(f"[yellow]    SL: {self.stop_loss:.2f} | TP: {self.take_profit:.2f}[/yellow]")
        except Exception as e:
            console.print(f"[bold red]Unexpected error: {e}[/bold red]")
            logging.exception("Fatal error in main loop")
            raise

if __name__ == "__main__":
    bot = PaperTradingEngine()
    bot.run()