# telegram_alerts.py
import requests
import logging

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
    
    def send_message(self, message):
        """Send a message to Telegram"""
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            # ✅ Changed data= to json=
            response = requests.post(self.base_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                print("[TELEGRAM] ✅ Trade alert sent")
            else:
                print(f"[TELEGRAM] ❌ Error {response.status_code}: {response.text}")
                
        except Exception as e:
            print(f"[TELEGRAM] ❌ Failed: {e}")
    
    def send_trade_alert(self, symbol, side, price, sl, tp, quantity):
        """Format and send a trade alert"""
        if side == 1:
            icon = "[BULL]"
            color = "green"
            action = "BUY"
        else:
            icon = "[BEAR]"
            color = "red"
            action = "SELL"
            
        message = f"""
<b>{icon} {action} ALERT - KRONOS</b>
<b>Symbol:</b> <code>{symbol}</code>
<b>Side:</b> <font color="{color}">{action}</font>
<b>Qty:</b> {quantity}
<b>Price:</b> {price:.2f}
<b>Stop Loss:</b> {sl:.2f}
<b>Target:</b> {tp:.2f}
<i>Mode: PAPER TRADING</i>
"""
        self.send_message(message.strip())

    def send_close_alert(self, symbol, exit_price, entry_price, quantity, pnl):
        """Format and send a position close alert"""
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        icon = "[PROFIT]" if pnl >= 0 else "[LOSS]"
        
        message = f"""
<b>{icon} POSITION CLOSED - KRONOS</b>
<b>Symbol:</b> <code>{symbol}</code>
<b>Entry:</b> {entry_price:.2f}
<b>Exit:</b> {exit_price:.2f}
<b>P&L:</b> <b>{pnl_str}</b>
<i>Mode: PAPER TRADING</i>
"""
        self.send_message(message.strip())