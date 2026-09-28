from email.mime import message
import logging
import time
from datetime import datetime
import html # Added for sanitization
import requests
import os

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategy4 import Strategy
from order_manager import OrderManager
from risk_manager import RiskManager
from dashboard import Dashboard
# from telegram_alerts import TelegramBot

log_dir = "./logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'trading.log'), encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, config):
        self.config = config
        
        self.auth = FyersAuth(
            app_id=config["fyers"]["app_id"],
            secret_key=config["fyers"]["secret_key"]
        )
        self.auth.set_access_token(config["fyers"]["access_token"])

        tg_config = config.get("telegram", {})
        # if tg_config.get("enabled") and tg_config.get("bot_token"):
        #     self.telegram = TelegramBot(tg_config["bot_token"], tg_config["chat_id"])
        # else:
        #     self.telegram = None
        
        self.send_telegram_alert("🚀 <b>naresh Trading Engine online!</b> Live monitoring started.")
        
        self.data_fetcher = DataFetcher(self.auth.fyers)
        self.order_manager = OrderManager(self.auth.fyers)
        self.risk_manager = RiskManager(config.get("risk", {}))
        
        self.positions = {}
        self.symbols = config["symbols"]
        self.strategies = {}
        
        self.dashboard = Dashboard()
        
        for symbol_config in self.symbols:
            symbol = symbol_config["symbol"]
            strategy_name = symbol_config.get("strategy", "ema_crossover")
            params = symbol_config.get("params", {})
            self.strategies[symbol] = Strategy(strategy_name, params)

    def send_telegram_alert(self, message):
        cfg = self.config.get("telegram", {})
        if not cfg.get("enabled"): return
        
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        try:
            requests.post(url, json={"chat_id": cfg['chat_id'], "text": message, "parse_mode": "HTML"}, timeout=10)
        except Exception as e: logger.error(f"Telegram error: {e}")

    def run(self):
        logger.info("Starting Trading Engine...")
        
        try:
            while self.is_market_hours():
                dashboard_data = []
                
                for symbol_config in self.symbols:
                    symbol = symbol_config["symbol"]
                    quantity = symbol_config.get("quantity", 1)
                    
                    data = self.process_symbol(symbol, quantity)
                    if data:
                        dashboard_data.append(data)
                
                risk_data = {
                    "daily_pnl": self.risk_manager.daily_pnl,
                    "total_trades": self.risk_manager.total_trades_today
                }
                self.dashboard.render(dashboard_data, self.positions, risk_data)
                
                time.sleep(self.config.get("loop_interval", 30))
        
        except KeyboardInterrupt:
            print("\n[EXIT] Trading engine stopped by user")
        except Exception as e:
            logger.error(f"Error in trading loop: {e}")
            raise

    def process_symbol(self, symbol, quantity):
        try:
            df = self.data_fetcher.get_historical_data(
                symbol=symbol,
                resolution=self.config.get("resolution", "5"),
                days=self.config.get("lookback_days", 60)
            )
            
            if df is None or len(df) == 0:
                return None
            
            strategy = self.strategies[symbol]
            df = strategy.generate_signals(df)
            
            latest = df.iloc[-1]
            signal = latest["signal"]
            current_price = latest["close"]
            
            if signal == 1 and symbol not in self.positions:
                slippage = 0.5
                execution_price = current_price + slippage  # For buys
                self.execute_buy(symbol, execution_price, quantity, df=df)
            elif signal == -1 and symbol in self.positions:
                self.execute_sell(symbol, current_price, df=df)

            self.update_positions(symbol, current_price)
            
            # Calculate live P&L for dashboard
            status_data = None
            if symbol in self.positions:
                pos = self.positions[symbol]
                if pos["side"] == "long":
                    points = current_price - pos["entry_price"]
                    pnl = points * pos["quantity"]
                    status_data = {
                        "entry": pos["entry_price"],
                        "points": points,
                        "pnl": pnl
                    }
            
            return {
                "symbol": symbol,
                "price": current_price,
                "signal": signal,
                "ema_9": latest.get("ema_9", 0),
                "ema_21": latest.get("ema_21", 0),
                "rsi": latest.get("rsi", 0),
                "status_data": status_data  # <-- NEW FIELD ADDED HERE
            }
            
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def execute_buy(self, symbol, price, quantity, df=None):
        positions = self.order_manager.get_positions()
        
        if not self.risk_manager.can_take_trade(positions.get("net", [])):
            return
        
        # sl, tp = self.risk_manager.calculate_sl_tp(price, 1)
        # Pass df and strategy name for ATR calculation
        strategy_name = self.strategies[symbol].name
        sl, tp = self.risk_manager.calculate_sl_tp(price, 1, df=df, strategy_name=strategy_name)

        logger.info(f"BUY {symbol} @ {price}, SL: {sl}, TP: {tp}")
        
        message = f"""<b>🟢 BUY ALERT - nareshS</b>
<b>Symbol:</b> <code>{symbol}</code>
<b>Side:</b> BUY
<b>Qty:</b> {quantity}
<b>Price:</b> {price:.2f}
<b>Stop Loss:</b> {sl:.2f}
<b>Target:</b> {tp:.2f}
<i>Mode: PAPER TRADING</i>"""
        self.send_telegram_alert(message)
        
        response = self.order_manager.place_order(
            symbol=symbol,
            side=1,
            quantity=quantity,
            order_type="MARKET",
            stop_loss=sl,
            take_profit=tp,
            product_type="INTRADAY"
        )
        
        if response and response.get("s") == "ok":
            self.positions[symbol] = {
                "side": "long",
                "entry_price": price,
                "quantity": quantity,
                "sl": sl,
                "tp": tp,
                "order_id": response.get("id")
            }
            logger.info(f"Buy order placed: {response.get('id')}")
        else:
            logger.error(f"Buy order failed: {response}")

    def execute_sell(self, symbol, price):
        if symbol not in self.positions:
            return
        
        position = self.positions[symbol]
        quantity = position["quantity"]
        pnl = (price - position["entry_price"]) * quantity
        
        logger.info(f"SELL {symbol} @ {price}")
        
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        icon = "🟢" if pnl >= 0 else "🔴"
        
        message = f"""<b>{icon} POSITION CLOSED - nareshS</b>
<b>Symbol:</b> <code>{symbol}</code>
<b>Entry:</b> {position['entry_price']:.2f}
<b>Exit:</b> {price:.2f}
<b>P&L:</b> <b>{pnl_str}</b>
<i>Mode: PAPER TRADING</i>"""
        self.send_telegram_alert(message)
        
        response = self.order_manager.place_order(
            symbol=symbol,
            side=-1,
            quantity=quantity,
            order_type="MARKET",
            product_type="INTRADAY"
        )
        
        if response and response.get("s") == "ok":
            self.risk_manager.update_daily_pnl(pnl)
            logger.info(f"Position closed. P&L: {pnl}")
            del self.positions[symbol]
        else:
            logger.error(f"Sell order failed: {response}")

    def update_positions(self, symbol, current_price):
        if symbol not in self.positions:
            return
            
        pos = self.positions[symbol]
        
        if pos["side"] == "long" and current_price <= pos["sl"]:
            self.execute_sell(symbol, current_price)
        elif pos["side"] == "long" and current_price >= pos["tp"]:
            self.execute_sell(symbol, current_price)
        #  short position handling!
        
    def is_market_hours(self):
        now = datetime.now()
        
        if now.weekday() >= 5:
            return False
        
        market_open = now.replace(hour=9, minute=15, second=0)
        market_close = now.replace(hour=15, minute=30, second=0)
        
        has_mcx_symbol = any(
            symbol_config["symbol"].startswith("MCX:")
            for symbol_config in self.symbols
        )
        
        if has_mcx_symbol:
            market_close = now.replace(hour=23, minute=30, second=0)
        
        return market_open <= now <= market_close


if __name__ == "__main__":
    print("Initializing naresh Trading Engine...")
    
    # Load your config.yaml
    import yaml
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        
    # Check for token.txt
    import os
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()

    # Start the engine
    engine = TradingEngine(config)
    
    # Check if market is open before running
    if engine.is_market_hours():
        print("Market is OPEN. Starting live engine...")
        engine.run()
    else:
        print("⚠️ Market is CLOSED. The engine will not run.")
        print("Indian market hours: 9:15 AM to 3:30 PM (Mon-Fri).")
        print("If you have MCX symbols, it runs till 11:30 PM.")