# order_manager.py
import time
from datetime import datetime

class OrderManager:
    def __init__(self, fyers_client):
        self.fyers = fyers_client
        self.order_history = []
    
    def place_order(self, symbol, side, quantity, order_type="MARKET", 
                stop_loss=None, take_profit=None, 
                product_type="INTRADAY"):
    
        # SAFETY: Add paper trading mode
        PAPER_TRADING = True  # Set to False for REAL trading
        
        if PAPER_TRADING:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f" [PAPER TRADE] Would place: {side} {quantity} {symbol} @ MARKET, SL={stop_loss}, TP={take_profit}")
            return {"s": "ok", "id": f"PAPER_{int(time.time())}"}
        
        # Real order below...
        order_data = {
            "symbol": symbol,
            "qty": quantity,
            "type": order_type,
            "side": side,  # 1 = Buy, -1 = Sell
            "productType": product_type,
            "limitPrice": price if order_type == "LIMIT" else 0,
            "stopPrice": 0,
            "disclosedQty": 0,
            "validity": "DAY",
            "offlineOrder": "False",
            "stopLoss": stop_loss or 0,
            "takeProfit": take_profit or 0
        }
        
        response = self.fyers.place_order(order_data)
        
        order_record = {
            "timestamp": datetime.now().isoformat(),
            "request": order_data,
            "response": response
        }
        self.order_history.append(order_record)
        
        return response
    
    def place_sl_order(self, symbol, side, quantity, stop_loss, limit_price=None):
        """Place Stop Loss order (SL-M or SL)"""
        order_type = "SL" if limit_price else "SL-M"
        return self.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=limit_price,
            stop_loss=stop_loss
        )
    
    def cancel_order(self, order_id):
        """Cancel an existing order"""
        cancel_data = {"id": order_id}
        return self.fyers.cancel_order(cancel_data)
    
    def modify_order(self, order_id, quantity=None, price=None, stop_loss=None, order_type=None):
        """Modify an existing order"""
        modify_data = {"id": order_id}
        if quantity:
            modify_data["qty"] = quantity
        if price:
            modify_data["limitPrice"] = price
        if stop_loss:
            modify_data["stopLoss"] = stop_loss
        if order_type:
            modify_data["type"] = order_type
        
        return self.fyers.modify_order(modify_data)
    
    def get_order_status(self, order_id):
        """Get status of a specific order"""
        return self.fyers.order_status({"id": order_id})
    
    def get_order_book(self):
        """Get all orders for the day"""
        return self.fyers.orderbook()
    
    def get_positions(self):
        """Get current positions"""
        return self.fyers.positions()
    
    def get_holdings(self):
        """Get holdings (for CNC/delivery)"""
        return self.fyers.holdings()