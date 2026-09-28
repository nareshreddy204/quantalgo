# risk_manager.py
import logging
import pandas as pd

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, config):
        self.config = config
        self.risk_per_trade = config.get("risk_per_trade", 0.02)
        self.max_capital_per_trade = config.get("max_capital_per_trade", 0.02)  # 2% of capital
        self.max_daily_loss = config.get("max_daily_loss", 0.05)  # 5% of capital
        self.max_open_positions = config.get("max_open_positions", 5)
        self.max_trades_per_day = config.get("max_trades_per_day", 20)
        self.default_sl_pct = config.get("default_sl_pct", 0.02)  # 2% stop loss
        self.default_tp_pct = config.get("default_tp_pct", 0.04)  # 4% take profit
        
        self.daily_pnl = 0
        self.total_trades_today = 0
        self.capital = config.get("capital", 100000)
    
    def calculate_position_size(self, entry_price, stop_loss_price):
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            return 0
        
        # Now safely uses self.max_capital_per_trade
        risk_amount = self.capital * self.max_capital_per_trade
        quantity = int(risk_amount / risk_per_share)
        
        lot_size = self._get_lot_size()
        quantity = (quantity // lot_size) * lot_size
        
        return max(quantity, lot_size) if quantity > 0 else 0
    
    # def calculate_sl_tp(self, entry_price, side):
    #     """Calculate stop loss and take profit prices"""
    #     if side == 1:  # Buy
    #         sl = entry_price * (1 - self.default_sl_pct)
    #         tp = entry_price * (1 + self.default_tp_pct)
    #     else:  # Sell
    #         sl = entry_price * (1 + self.default_sl_pct)
    #         tp = entry_price * (1 - self.default_tp_pct)
        
    #     return sl, tp

    def calculate_sl_tp(self, price, side, df=None, strategy_name=None):
        if strategy_name == "ema_bounce" and df is not None and 'atr' in df.columns:
            atr = df['atr'].iloc[-1]
            if pd.notna(atr):
                sl_distance = atr * 1.0
                tp_distance = atr * 0.5
                
                if side == 1:
                    return price - sl_distance, price + tp_distance
                else:
                    return price + sl_distance, price - tp_distance

        sl_pct = self.default_sl_pct
        tp_pct = self.default_tp_pct

        if side == 1:
            return price * (1 - sl_pct), price * (1 + tp_pct)
        else:
            return price * (1 + sl_pct), price * (1 - tp_pct)
    
    def can_take_trade(self, current_positions):
        """Check if we can take a new trade"""
        # print(f"[DEBUG] can_take_trade - positions: {current_positions}")
        # Check daily loss limit
        if self.daily_pnl <= -self.capital * self.max_daily_loss:
            logger.warning("Daily loss limit reached")
            return False
        
        # Check max positions
        if len(current_positions) >= self.max_open_positions:
            logger.warning("Max open positions reached")
            return False
        
        # Check max trades per day
        if self.total_trades_today >= self.max_trades_per_day:
            logger.warning("Max trades per day reached")
            return False
        
        return True
    
    def update_daily_pnl(self, pnl):
        """Update daily P&L"""
        self.daily_pnl += pnl
        self.total_trades_today += 1
    
    def _get_lot_size(self):
        """Get lot size (can be made configurable per symbol)"""
        return 1  # Default for equities, adjust for F&O