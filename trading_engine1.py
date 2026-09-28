import logging
import time
import json
import os
from datetime import datetime, date
import requests
import yaml

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
        logging.StreamHandler()  # [FIX 13 from review] Added console handler
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
        
        self.data_fetcher = DataFetcher(self.auth.fyers)
        self.order_manager = OrderManager(self.auth.fyers)
        self.risk_manager = RiskManager(config.get("risk", {}))
        
        self.positions = {}
        self.symbols = config["symbols"]
        self.strategies = {}
        # Inside TradingEngine.__init__
        self.cached_data = {}  # Stores historical dataframes in memory
        self.dashboard = Dashboard()
        
        # [FIX 7] Path for persisting risk manager state
        self.risk_state_file = os.path.join(log_dir, "risk_state.json")
        self._load_risk_state()

        # [FIX 6] Updated NSE/BSE & MCX Indian Market Holidays for 2026
        self.holidays = {
            "2026-01-26",  # Republic Day
            "2026-02-17",  # Mahashivratri
            "2026-03-03",  # Holi
            "2026-03-20",  # Id-Ul-Fitr
            "2026-04-03",  # Good Friday
            "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
            "2026-05-01",  # Maharashtra Day
            "2026-05-27",  # Bakri Id / Eid-ul-Adha
            "2026-06-25",  # Muharram
            "2026-08-15",  # Independence Day
            "2026-10-02",  # Mahatma Gandhi Jayanti
            "2026-10-20",  # Dussehra
            "2026-11-09",  # Diwali / Laxmi Pujan
            "2026-11-10",  # Diwali Balipratipada
            "2026-11-24",  # Gurunanak Jayanti
            "2026-12-25",  # Christmas
        }

        for symbol_config in self.symbols:
            symbol = symbol_config["symbol"]
            strategy_name = symbol_config.get("strategy", "ema_crossover")
            params = symbol_config.get("params", {})
            self.strategies[symbol] = Strategy(strategy_name, params)

        # [FIX 4] Rehydrate positions from broker on startup
        self._rehydrate_positions()

    def send_telegram_alert(self, message):
        cfg = self.config.get("telegram", {})
        if not cfg.get("enabled"):
            return
        
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        try:
            requests.post(url, json={"chat_id": cfg['chat_id'], "text": message, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    # [FIX 7] Load risk counters from disk
    def _load_risk_state(self):
        if os.path.exists(self.risk_state_file):
            try:
                with open(self.risk_state_file, "r") as f:
                    state = json.load(f)
                if state.get("date") == date.today().isoformat():
                    self.risk_manager.daily_pnl = state.get("daily_pnl", 0)
                    self.risk_manager.total_trades_today = state.get("total_trades_today", 0)
                    logger.info("Loaded daily risk state from disk.")
            except Exception as e:
                logger.error(f"Error loading risk state: {e}")

    # [FIX 7] Save risk counters to disk
    def _save_risk_state(self):
        try:
            state = {
                "date": date.today().isoformat(),
                "daily_pnl": self.risk_manager.daily_pnl,
                "total_trades_today": self.risk_manager.total_trades_today
            }
            with open(self.risk_state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"Error saving risk state: {e}")

    # [FIX 4] Reconciliation logic
    def _rehydrate_positions(self):
        try:
            broker_positions = self.order_manager.get_positions().get("net", [])
            for pos in broker_positions:
                symbol = pos.get("symbol")
                qty = pos.get("quantity", 0)
                if symbol and qty > 0:
                    entry_price = pos.get("avg_price", 0)
                    # Set a default SL/TP based on risk config, NOT from broker
                    sl = entry_price * (1 - self.config.get("risk", {}).get("default_sl_pct", 0.02))
                    tp = entry_price * (1 + self.config.get("risk", {}).get("default_tp_pct", 0.04))
                    
                    self.positions[symbol] = {
                        "side": "long",
                        "entry_price": entry_price,
                        "quantity": qty,
                        "sl": sl,
                        "tp": tp,
                        "order_id": pos.get("id")
                    }
                    logger.info(f"Rehydrated position {symbol} @ {entry_price}, SL: {sl:.2f}, TP: {tp:.2f}")
        except Exception as e:
            logger.error(f"Failed to rehydrate positions: {e}")

    def run(self):
        self.send_telegram_alert("🚀 <b>naresh Trading Engine online!</b> Live monitoring started.")
        logger.info("Starting Trading Engine...")
        
        # Pre-fetch historical data ONCE
        logger.info("Pre-loading historical data...")
        for symbol_config in self.symbols:
            symbol = symbol_config["symbol"]
            df = self.data_fetcher.get_historical_data(
                symbol=symbol,
                resolution=self.config.get("resolution", "5"),
                days=self.config.get("lookback_days", 60),
                force_refresh=True
            )
            if df is not None:
                self.cached_data[symbol] = df
        logger.info("Historical data loaded. Starting live loop...")
        
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
            # 1. Get data from memory (NO API CALL!)
            df = self.cached_data.get(symbol)
            if df is None or len(df) == 0:
                return None
            
            # 2. Fetch live LTP quote from Fyers API
            current_price = df.iloc[-1]["close"]
            try:
                quote_res = self.auth.fyers.quotes(data={"symbols": symbol})
                if isinstance(quote_res, dict) and quote_res.get("s") == "ok":
                    d_list = quote_res.get("d", [])
                    if isinstance(d_list, list) and len(d_list) > 0:
                        first_item = d_list[0]
                        if first_item.get("s") == "ok":
                            current_price = first_item.get("v", {}).get("lp", current_price)
            except Exception as e:
                logger.error(f"Failed to fetch live quote for {symbol}: {e}")

            # 3. Update latest candle close with real-time LTP
            df.loc[df.index[-1], "close"] = current_price

            # 4. Generate strategy signals on the live updated dataframe
            strategy = self.strategies[symbol]
            df = strategy.generate_signals(df)

            # 5. Force calculate dashboard indicators if the strategy didn't
            if "ema_9" not in df.columns:
                df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
            if "ema_21" not in df.columns:
                df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
            if "rsi" not in df.columns:
                delta = df["close"].diff()
                gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
                loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
                rs = gain / loss
                df["rsi"] = 100 - (100 / (1 + rs))
                
            latest = df.iloc[-1]
            signal = latest["signal"]

            # 6. Execute orders based on signal
            if signal == 1 and symbol not in self.positions:
                slippage_pct = self.config.get("slippage_pct", 0.0005) 
                execution_price = current_price * (1 + slippage_pct)
                self.execute_buy(symbol, execution_price, quantity, df=df)
            elif signal == -1 and symbol in self.positions:
                self.execute_sell(symbol, current_price, df=df)

            self.update_positions(symbol, current_price)
            
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
            
            # ==========================================
            # DYNAMIC STRATEGY METRICS
            # ==========================================
            strat_info = []
            
            if "supertrend_direction" in latest:
                st_dir = "UP" if latest["supertrend_direction"] == 1 else "DOWN"
                strat_info.append(f"ST: {st_dir}")
            
            if "macd" in latest and "macd_signal" in latest:
                macd_val = latest["macd"]
                sig_val = latest["macd_signal"]
                macd_dir = "UP" if macd_val > sig_val else "DOWN"
                strat_info.append(f"MACD: {macd_dir}")
                
            if "bb_upper" in latest and "bb_lower" in latest:
                bb_pos = "UPPER" if current_price > latest["bb_upper"] else ("LOWER" if current_price < latest["bb_lower"] else "MID")
                strat_info.append(f"BB: {bb_pos}")
                
            if "vwap" in latest:
                vwap_pos = "ABV" if current_price > latest["vwap"] else "BLW"
                strat_info.append(f"VWAP: {vwap_pos}")
                
            if "confirmed_ph" in latest or "confirmed_pl" in latest:
                if latest.get("confirmed_pl", False): strat_info.append("SSL SWEEP")
                if latest.get("confirmed_ph", False): strat_info.append("BSL SWEEP")

            strategy_info_str = " | ".join(strat_info) if strat_info else "N/A"
            
            return {
                "symbol": symbol,
                "price": current_price,
                "signal": signal,
                "ema_9": latest.get("ema_9", 0),
                "ema_21": latest.get("ema_21", 0),
                "rsi": latest.get("rsi", 0),
                "strategy_info": strategy_info_str,
                "status_data": status_data
            }
            
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def execute_buy(self, symbol, price, quantity, df=None):
        positions = self.order_manager.get_positions()
        
        if not self.risk_manager.can_take_trade(positions.get("net", [])):
            return
        
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

        # [FIX 1] Added df=None parameter to match execute_buy and caller signature
    def execute_sell(self, symbol, price, df=None):
        if symbol not in self.positions:
            return
        
        position = self.positions[symbol]
        quantity = position["quantity"]
        
        # [FIX 4] Apply slippage on sell (worse price)
        slippage_pct = self.config.get("slippage_pct", 0.0005)
        execution_price = price * (1 - slippage_pct)
        
        pnl = (execution_price - position["entry_price"]) * quantity
        
        logger.info(f"SELL {symbol} @ {execution_price:.2f} (raw: {price:.2f})")
        
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        icon = "🟢" if pnl >= 0 else "🔴"
        
        message = f"""<b>{icon} POSITION CLOSED - nareshS</b>
<b>Symbol:</b> <code>{symbol}</code>
<b>Entry:</b> {position['entry_price']:.2f}
<b>Exit:</b> {execution_price:.2f}
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
            self._save_risk_state()  # [FIX 7] Save state to disk after P&L update
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
        
    def is_market_hours(self):
        now = datetime.now()
        
        if now.weekday() >= 5:
            return False

        # [FIX 6] Holiday calendar check
        if now.strftime("%Y-%m-%d") in self.holidays:
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
    
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()

    engine = TradingEngine(config)
    
    if engine.is_market_hours():
        print("Market is OPEN. Starting live engine...")
        engine.run()
    else:
        print("⚠️ Market is CLOSED. The engine will not run.")
        print("Indian market hours: 9:15 AM to 3:30 PM (Mon-Fri).")
        print("If you have MCX symbols, it runs till 11:30 PM.")