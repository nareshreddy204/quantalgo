# live_bot.py
import os
import yaml
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
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
        
        # Initialize Fyers & Data Fetcher
        auth = FyersAuth(self.config["fyers"]["app_id"], self.config["fyers"]["secret_key"])
        auth.set_access_token(self.config["fyers"]["access_token"])
        self.data_fetcher = DataFetcher(auth.fyers)
        
        # Strategy Settings
        self.symbol = self.config.get("symbol", "NSE:BANKNIFTY26JUL56900PE")
        self.timeframe = int(self.config.get("resolution", 1))
        self.strategy_name = self.config.get("strategy", "supertrend_macd")
        self.sl_mult = float(self.config.get("sl_atr_multiplier", 2.0))
        self.tp_mult = float(self.config.get("tp_atr_multiplier", 2.0))
        
        self.strategy = Strategy(self.strategy_name)
        
        # Paper Trading State
        self.position = "FLAT"  # FLAT, LONG, SHORT
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.trade_history = []
        self.current_candle_time = None
        
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
        # Simple check: 9:15 AM to 3:30 PM, Mon-Fri
        if now.weekday() >= 5:
            return False
        market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_start <= now <= market_end

    def fetch_latest_data(self):
        try:
            # Fetch last 5 days of data to warm up indicators
            df = self.data_fetcher.get_historical_data(self.symbol, self.timeframe, days=5)
            if df is None or df.empty:
                console.print("[red]Warning: Fyers returned empty data for this symbol.[/red]")
            return df
        except Exception as e:
            console.print(f"[bold red]API Error fetching data: {e}[/bold red]")
            logging.error(f"Error fetching data: {e}")
            return None

    def generate_live_signal(self, df):
        df = self.strategy.generate_signals(df.copy())
        df['atr'] = self.strategy.calculate_atr(df)
        
        # We only care about the last CLOSED candle (index -2)
        # The last candle (index -1) is still forming
        last_closed = df.iloc[-2]
        current_ltp = df.iloc[-1]['close']
        
        signal = int(last_closed['signal'])
        atr = float(last_closed['atr'])
        
        return signal, atr, current_ltp, last_closed.name

    def check_exit_conditions(self, current_ltp):
        if self.position == "LONG":
            if current_ltp <= self.stop_loss:
                self.close_position("SL Hit", current_ltp)
            elif current_ltp >= self.take_profit:
                self.close_position("TP Hit", current_ltp)
                
        elif self.position == "SHORT":
            if current_ltp >= self.stop_loss:
                self.close_position("SL Hit", current_ltp)
            elif current_ltp <= self.take_profit:
                self.close_position("TP Hit", current_ltp)

    def open_position(self, signal, atr, price):
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
        console.print(f"[bold red]🔒 CLOSED {self.position}[/bold red] ({reason}) @ {price:.2f} | P&L: {pnl_str}")
        logging.info(f"CLOSED {self.position} ({reason}) @ {price} | P&L: {pnl}")
        
        self.position = "FLAT"
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

    def render_dashboard(self, df, current_ltp):
        table = Table(title=f"KRONOS LIVE BOT - {self.symbol} ({self.timeframe}m)", border_style="cyan")
        
        table.add_column("Metric", style="bold cyan")
        table.add_column("Value", style="white")
        
        unrealized_pnl = 0.0
        if self.position == "LONG":
            unrealized_pnl = current_ltp - self.entry_price
        elif self.position == "SHORT":
            unrealized_pnl = self.entry_price - current_ltp
            
        pnl_color = "green" if unrealized_pnl >= 0 else "red"
        
        table.add_row("Status", "🟢 RUNNING" if self.is_market_open() else "🔴 MARKET CLOSED")
        table.add_row("Current Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        table.add_row("Current Price", f"{current_ltp:.2f}")
        table.add_row("Position", self.position)
        table.add_row("Entry Price", f"{self.entry_price:.2f}" if self.position != "FLAT" else "N/A")
        table.add_row("Stop Loss", f"{self.stop_loss:.2f}" if self.position != "FLAT" else "N/A")
        table.add_row("Take Profit", f"{self.take_profit:.2f}" if self.position != "FLAT" else "N/A")
        table.add_row("Unrealized P&L", f"[{pnl_color}]{unrealized_pnl:.2f}[/{pnl_color}]")
        
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        total_color = "green" if total_pnl >= 0 else "red"
        table.add_row("Realized P&L (Today)", f"[{total_color}]{total_pnl:.2f}[/{total_color}]")
        table.add_row("Trades Today", str(len(self.trade_history)))
        
        console.clear()
        console.print(table)
        
        if self.trade_history:
            history_table = Table(title="Trade History", border_style="dim")
            history_table.add_column("Time")
            history_table.add_column("Type")
            history_table.add_column("Entry")
            history_table.add_column("Exit")
            history_table.add_column("P&L")
            history_table.add_column("Reason")
            
            for t in self.trade_history[-5:]:  # Show last 5 trades
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

    def run(self):
        console.print("[yellow]Starting Live Bot... Press Ctrl+C to stop.[/yellow]")
        
        try:
            while True:
                now = datetime.now()
                
                # Check if market is open
                if not self.is_market_open():
                    # Sleep until next check if market is closed
                    next_check = now + timedelta(minutes=1)
                    console.print(f"[dim]Market closed. Sleeping until {next_check.strftime('%H:%M:%S')}...[/dim]", end="\r")
                    time.sleep(60)
                    continue
                
                # Calculate seconds until next timeframe boundary
                # E.g., if time is 10:07, next boundary is 10:15. We wait until 10:15:05 to ensure candle closes
                minute = now.minute
                target_minute = ((minute // self.timeframe) + 1) * self.timeframe
                
                if target_minute >= 60:
                    next_candle_time = now.replace(hour=now.hour + 1, minute=0, second=5, microsecond=0)
                else:
                    next_candle_time = now.replace(minute=target_minute, second=5, microsecond=0)
                
                wait_seconds = (next_candle_time - now).total_seconds()
                
                # ⚠️ FIX: 'if False' was causing infinite loops. Reverted to normal.
                if wait_seconds > 0:
                    # Sleep in small increments so we can check exit conditions (SL/TP) periodically
                    sleep_counter = 0
                    while sleep_counter < wait_seconds:
                        # Check SL/TP every 5 seconds
                        if self.position != "FLAT":
                            try:
                                df_temp = self.fetch_latest_data()
                                if df_temp is not None and not df_temp.empty:
                                    current_ltp = df_temp.iloc[-1]['close']
                                    self.check_exit_conditions(current_ltp)
                            except Exception:
                                pass
                        
                        # Sleep in 5-second chunks so we don't overshoot the candle close time
                        sleep_time = min(5, wait_seconds - sleep_counter)
                        if sleep_time <= 0:
                            break
                            
                        time.sleep(sleep_time)
                        sleep_counter += sleep_time
                        
                        # Countdown display
                        remaining = int(wait_seconds - sleep_counter)
                        if remaining >= 0:
                            console.print(f"[cyan]Next candle check in {remaining}s... Position: {self.position}    [/cyan]", end="\r")
                
                # Candle has closed, fetch latest data and check signals
                console.print("\n[bold blue]Candle closed. Fetching data & generating signal...[/bold blue]")
                df = self.fetch_latest_data()
                
                if df is None or df.empty:
                    console.print("[red]Failed to fetch data. Retrying next cycle.[/red]")
                    continue
                
                # Generate Signal
                signal, atr, current_ltp, candle_time = self.generate_live_signal(df)
                
                # If we already processed this candle, skip
                if candle_time == self.current_candle_time:
                    continue
                self.current_candle_time = candle_time
                
                console.print(f"[dim]Signal: {signal} | ATR: {atr:.2f} | LTP: {current_ltp:.2f}[/dim]")
                
                # Exit if opposite signal generated (Strategy Reversal)
                if self.position == "LONG" and signal == -1:
                    self.close_position("Reverse Signal", current_ltp)
                elif self.position == "SHORT" and signal == 1:
                    self.close_position("Reverse Signal", current_ltp)
                
                # Enter new position if flat
                if self.position == "FLAT" and signal != 0:
                    self.open_position(signal, atr, current_ltp)
                
                # Check SL/TP immediately after candle close
                self.check_exit_conditions(current_ltp)
                
                # Update Dashboard
                self.render_dashboard(df, current_ltp)
                
        except KeyboardInterrupt:
            console.print("\n[bold red]Bot stopped by user.[/bold red]")
            if self.position != "FLAT":
                console.print(f"[yellow]WARNING: Bot stopped while in a {self.position} position. Manual intervention required.[/yellow]")

if __name__ == "__main__":
    bot = PaperTradingEngine()
    bot.run()