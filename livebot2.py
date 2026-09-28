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

# Setup Logging
logging.basicConfig(
    filename=f"live_bot_{datetime.now():%Y%m%d_%H%M%S}.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

console = Console()


class EMACrossStrategy:
    """EMA 9/34 Crossover Strategy with High/Low source"""
    
    def __init__(self):
        self.ema_fast = 9
        self.ema_slow = 34
    
    def calculate_atr(self, df, period=14):
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr
    
    def generate_signals(self, df):
        # EMA on HIGH source
        df['ema_9_high'] = df['high'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_34_high'] = df['high'].ewm(span=self.ema_slow, adjust=False).mean()
        
        # EMA on LOW source
        df['ema_9_low'] = df['low'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_34_low'] = df['low'].ewm(span=self.ema_slow, adjust=False).mean()
        
        # Detect crossovers
        # BUY: EMA 9 (High) crosses ABOVE EMA 34 (High)
        df['bull_cross'] = (
            (df['ema_9_high'] > df['ema_34_high']) & 
            (df['ema_9_high'].shift(1) <= df['ema_34_high'].shift(1))
        )
        
        # SELL: EMA 9 (Low) crosses BELOW EMA 34 (Low)
        df['bear_cross'] = (
            (df['ema_9_low'] < df['ema_34_low']) & 
            (df['ema_9_low'].shift(1) >= df['ema_34_low'].shift(1))
        )
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['bull_cross'], 'signal'] = 1   # Buy
        df.loc[df['bear_cross'], 'signal'] = -1  # Sell
        
        # ATR for TP calculation
        df['atr'] = self.calculate_atr(df)
        
        # Dynamic SL levels
        df['sl_long'] = df['ema_34_low']    # SL for LONG position
        df['sl_short'] = df['ema_34_high']  # SL for SHORT position
        
        return df


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
        self.tp_mult = float(self.config.get("tp_atr_multiplier", 2.0))
        
        # Use our EMA strategy instead
        self.strategy = EMACrossStrategy()
        
        # Paper Trading State
        self.position = "FLAT"
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.dynamic_sl = 0.0  # Tracks EMA 34 SL
        self.trade_history = []
        self.current_candle_time = None
        self.last_ltp = 0.0
        self.last_api_call_time = 0
        self.last_ltp_call_time = 0
        
        console.print(Panel(
            f"[bold green]PAPER TRADING ENGINE INITIALIZED[/bold green]\n"
            f"Symbol: {self.symbol} | TF: {self.timeframe}m\n"
            f"Strategy: [bold cyan]EMA 9/34 Cross (High/Low Source)[/bold cyan]\n"
            f"Buy: EMA9(High) > EMA34(High) | Sell: EMA9(Low) < EMA34(Low)\n"
            f"SL: EMA 34 (opposite source) | TP: {self.tp_mult}x ATR",
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
        """Fetch real-time LTP using Quotes API"""
        current_time = time.time()
        if (current_time - self.last_ltp_call_time) < 1:
            return self.last_ltp if self.last_ltp > 0 else None
        
        try:
            self.last_ltp_call_time = current_time
            response = self.data_fetcher.fyers.quotes({
                "symbols": self.symbol
            })
            
            if response.get('s') == 'ok' and response.get('d'):
                data = response['d'][0]
                v = data.get('v', {})
                ltp = float(v.get('lp', 0))
                
                if ltp > 0:
                    self.last_ltp = ltp
                    return ltp
            
            return None
        except Exception as e:
            logging.error(f"Error fetching LTP: {e}")
            return None

    def fetch_latest_data(self, force=False):
        """Fetch historical data - need more days for EMA 34 to warm up"""
        current_time = time.time()
        if not force and (current_time - self.last_api_call_time) < 3:
            return None
        
        try:
            self.last_api_call_time = current_time
            # Fetch 10 days to ensure EMA 34 is properly warmed up
            df = self.data_fetcher.get_historical_data(self.symbol, self.timeframe, days=10)
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            console.print(f"[bold red]API Error: {e}[/bold red]")
            logging.error(f"Error fetching data: {e}")
            return None

    def generate_live_signal(self, df):
        """Generate signal using EMA crossover strategy"""
        try:
            df = self.strategy.generate_signals(df.copy())
            
            last_closed = df.iloc[-2]
            
            signal = int(last_closed.get('signal', 0))
            atr = float(last_closed.get('atr', 0))
            
            # Get dynamic SL levels
            sl_long = float(last_closed.get('sl_long', 0))
            sl_short = float(last_closed.get('sl_short', 0))
            
            # Get EMA values for display
            ema_9_high = float(last_closed.get('ema_9_high', 0))
            ema_34_high = float(last_closed.get('ema_34_high', 0))
            ema_9_low = float(last_closed.get('ema_9_low', 0))
            ema_34_low = float(last_closed.get('ema_34_low', 0))
            
            # Use REAL-TIME LTP
            ltp = self.fetch_ltp()
            if ltp is None:
                ltp = float(df.iloc[-1]['close'])
            
            return {
                'signal': signal,
                'atr': atr,
                'ltp': ltp,
                'candle_time': last_closed.name,
                'sl_long': sl_long,
                'sl_short': sl_short,
                'ema_9_high': ema_9_high,
                'ema_34_high': ema_34_high,
                'ema_9_low': ema_9_low,
                'ema_34_low': ema_34_low
            }
        except Exception as e:
            logging.error(f"Error generating signal: {e}")
            return None

    def update_dynamic_sl(self, sl_long, sl_short):
        """Update trailing SL based on EMA 34"""
        if self.position == "LONG" and sl_long > 0:
            # For LONG, SL is EMA 34 (Low) - only trail up, never down
            if sl_long > self.stop_loss:
                self.stop_loss = sl_long
                self.dynamic_sl = sl_long
        elif self.position == "SHORT" and sl_short > 0:
            # For SHORT, SL is EMA 34 (High) - only trail down, never up
            if sl_short < self.stop_loss:
                self.stop_loss = sl_short
                self.dynamic_sl = sl_short

    def check_exit_conditions(self, current_ltp):
        if self.position == "FLAT" or current_ltp <= 0:
            return False
            
        exited = False
        if self.position == "LONG":
            if current_ltp <= self.stop_loss:
                self.close_position("EMA SL Hit", current_ltp)
                exited = True
            elif current_ltp >= self.take_profit:
                self.close_position("TP Hit", current_ltp)
                exited = True
                
        elif self.position == "SHORT":
            if current_ltp >= self.stop_loss:
                self.close_position("EMA SL Hit", current_ltp)
                exited = True
            elif current_ltp <= self.take_profit:
                self.close_position("TP Hit", current_ltp)
                exited = True
        
        return exited

    def open_position(self, signal, atr, price, sl_long, sl_short):
        if atr <= 0 or price <= 0:
            console.print("[red]Invalid ATR or price, skipping entry[/red]")
            return
            
        self.position = "LONG" if signal == 1 else "SHORT"
        self.entry_price = price
        
        if self.position == "LONG":
            # SL = EMA 34 (Low source)
            self.stop_loss = sl_long
            self.dynamic_sl = sl_long
            # TP = Entry + (ATR * multiplier)
            self.take_profit = price + (self.tp_mult * atr)
        else:
            # SL = EMA 34 (High source)
            self.stop_loss = sl_short
            self.dynamic_sl = sl_short
            # TP = Entry - (ATR * multiplier)
            self.take_profit = price - (self.tp_mult * atr)
            
        console.print(f"[bold green]🚀 OPENED {self.position}[/bold green] @ {price:.2f}")
        console.print(f"   [red]SL (EMA 34): {self.stop_loss:.2f}[/red] | [green]TP (ATR): {self.take_profit:.2f}[/green]")
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
        self.dynamic_sl = 0.0

    def render_dashboard(self, current_ltp, ema_data=None):
        console.clear()
        
        table = Table(title=f"KRONOS - {self.symbol} ({self.timeframe}m) | EMA 9/34 Cross", border_style="cyan")
        table.add_column("Metric", style="bold cyan", width=22)
        table.add_column("Value", style="white", width=30)
        
        unrealized_pnl = 0.0
        if self.position == "LONG":
            unrealized_pnl = current_ltp - self.entry_price
        elif self.position == "SHORT":
            unrealized_pnl = self.entry_price - current_ltp
            
        pnl_color = "green" if unrealized_pnl >= 0 else "red"
        
        table.add_row("Status", "🟢 RUNNING" if self.is_market_open() else "🔴 CLOSED")
        table.add_row("Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        table.add_row("LTP (Live)", f"[bold]{current_ltp:.2f}[/bold]")
        table.add_row("Position", f"[bold]{self.position}[/bold]")
        
        # Show EMA values
        if ema_data:
            table.add_row("EMA 9 (High)", f"{ema_data.get('ema_9_high', 0):.2f}")
            table.add_row("EMA 34 (High)", f"{ema_data.get('ema_34_high', 0):.2f}")
            table.add_row("EMA 9 (Low)", f"{ema_data.get('ema_9_low', 0):.2f}")
            table.add_row("EMA 34 (Low)", f"{ema_data.get('ema_34_low', 0):.2f}")
        
        if self.position != "FLAT":
            table.add_row("Entry Price", f"{self.entry_price:.2f}")
            table.add_row("SL (EMA 34)", f"[red]{self.stop_loss:.2f}[/red]")
            table.add_row("TP (ATR)", f"[green]{self.take_profit:.2f}[/green]")
            table.add_row("Unrealized P&L", f"[{pnl_color}]{unrealized_pnl:.2f}[/{pnl_color}]")
        else:
            table.add_row("Entry / SL / TP", "N/A")
            table.add_row("Unrealized P&L", "0.00")
        
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        total_color = "green" if total_pnl >= 0 else "red"
        table.add_row("Realized P&L", f"[{total_color}]{total_pnl:.2f}[/{total_color}]")
        table.add_row("Trades", str(len(self.trade_history)))
        
        console.print(table)
        
        if self.trade_history:
            history_table = Table(title="Trade History", border_style="dim")
            history_table.add_column("Time", width=8)
            history_table.add_column("Type", width=6)
            history_table.add_column("Entry", width=10)
            history_table.add_column("Exit", width=10)
            history_table.add_column("P&L", width=10)
            history_table.add_column("Reason", width=14)
            
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
        
        self.last_ema_data = None
        
        try:
            # Initial data fetch
            console.print("[cyan]Fetching initial data...[/cyan]")
            
            ltp = self.fetch_ltp()
            if ltp:
                console.print(f"[green]Live LTP: {ltp:.2f}[/green]")
            
            df = self.fetch_latest_data(force=True)
            
            if df is not None:
                result = self.generate_live_signal(df)
                if result:
                    self.last_ema_data = result
                    self.render_dashboard(result['ltp'], result)
            
            while True:
                now = datetime.now()
                
                if not self.is_market_open():
                    console.clear()
                    console.print(f"[dim red]Market closed. {now.strftime('%H:%M:%S')}[/dim red]")
                    time.sleep(60)
                    continue
                
                wait_seconds = self.get_seconds_to_next_candle()
                
                if wait_seconds < 0:
                    time.sleep(3)
                    wait_seconds = 0
                
                if wait_seconds > 0:
                    elapsed = 0.0
                    last_sl_check = 0.0
                    sl_check_interval = 10
                    last_display_update = 0.0
                    display_interval = 15
                    
                    while elapsed < wait_seconds:
                        remaining = wait_seconds - elapsed
                        sleep_time = min(5.0, remaining)
                        
                        if sleep_time <= 0:
                            break
                        
                        time.sleep(sleep_time)
                        elapsed += sleep_time
                        
                        # Check SL/TP using live LTP
                        if self.position != "FLAT" and (elapsed - last_sl_check) >= sl_check_interval:
                            ltp = self.fetch_ltp()
                            if ltp and ltp > 0:
                                if self.check_exit_conditions(ltp):
                                    self.render_dashboard(ltp, self.last_ema_data)
                                    break
                                last_sl_check = elapsed
                        
                        # Update countdown
                        if (elapsed - last_display_update) >= display_interval:
                            ltp = self.fetch_ltp()
                            if ltp and ltp > 0:
                                remaining_int = int(wait_seconds - elapsed)
                                pos_color = "green" if self.position == "LONG" else ("red" if self.position == "SHORT" else "white")
                                console.print(f"[dim cyan]{remaining_int}s | [{pos_color}]{self.position}[/{pos_color}] | LTP: {ltp:.2f}[/dim cyan]")
                            last_display_update = elapsed
                
                time.sleep(2)
                
                # Candle closed
                console.print("\n[bold blue]⏰ Candle closed. Fetching data...[/bold blue]")
                df = self.fetch_latest_data(force=True)
                
                if df is None or df.empty:
                    console.print("[red]Failed to fetch data.[/red]")
                    continue
                
                result = self.generate_live_signal(df)
                
                if result is None:
                    console.print("[red]Invalid signal data.[/red]")
                    continue
                
                signal = result['signal']
                candle_time = result['candle_time']
                
                # Store EMA data for display
                self.last_ema_data = result
                
                if candle_time == self.current_candle_time:
                    console.print("[dim]Same candle, skipping...[/dim]")
                    
                    # Even if same candle, update trailing SL
                    if self.position != "FLAT":
                        self.update_dynamic_sl(result['sl_long'], result['sl_short'])
                    continue
                
                self.current_candle_time = candle_time
                
                # Log EMA values
                console.print(f"[dim]EMA 9H: {result['ema_9_high']:.2f} | EMA 34H: {result['ema_34_high']:.2f}[/dim]")
                console.print(f"[dim]EMA 9L: {result['ema_9_low']:.2f} | EMA 34L: {result['ema_34_low']:.2f}[/dim]")
                console.print(f"[dim]Signal: {signal} | ATR: {result['atr']:.2f} | LTP: {result['ltp']:.2f}[/dim]")
                
                # Update trailing SL first
                if self.position != "FLAT":
                    old_sl = self.stop_loss
                    self.update_dynamic_sl(result['sl_long'], result['sl_short'])
                    if self.stop_loss != old_sl:
                        console.print(f"[yellow]📈 Trailing SL updated: {old_sl:.2f} → {self.stop_loss:.2f}[/yellow]")
                
                # Exit on reverse signal
                if self.position == "LONG" and signal == -1:
                    self.close_position("Reverse Signal", result['ltp'])
                elif self.position == "SHORT" and signal == 1:
                    self.close_position("Reverse Signal", result['ltp'])
                
                # Enter new position
                if self.position == "FLAT" and signal != 0:
                    self.open_position(
                        signal, 
                        result['atr'], 
                        result['ltp'],
                        result['sl_long'],
                        result['sl_short']
                    )
                
                # Check SL/TP
                self.check_exit_conditions(result['ltp'])
                
                # Dashboard
                self.render_dashboard(result['ltp'], result)
                
        except KeyboardInterrupt:
            console.print("\n[bold red]Bot stopped by user.[/bold red]")
            if self.position != "FLAT":
                console.print(f"[yellow]⚠️  WARNING: {self.position} @ {self.entry_price:.2f} | SL: {self.stop_loss:.2f} | TP: {self.take_profit:.2f}[/yellow]")
        except Exception as e:
            console.print(f"[bold red]Unexpected error: {e}[/bold red]")
            logging.exception("Fatal error")
            raise

if __name__ == "__main__":
    bot = PaperTradingEngine()
    bot.run()