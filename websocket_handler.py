# websocket_handler.py
from fyers_apiv3.Websocket import ws_callbacks
import logging
import json
import threading

logger = logging.getLogger(__name__)

class FyersWebSocket(ws_callbacks.WsCallback):
    def __init__(self, symbols, on_message_callback):
        self.symbols = symbols
        self.on_message_callback = on_message_callback
        self.ws = None
    
    def connect(self, access_token):
        """Connect to WebSocket"""
        self.ws = ws_callbacks.FyersWebsocket(
            access_token=access_token,
            log_path="./logs",
            on_message=self.on_message,
            on_connect=self.on_connect,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # Subscribe to symbols
        self.ws.subscribe(symbols=self.symbols)
        self.ws.keep_alive()
    
    def on_message(self, message):
        """Handle incoming messages"""
        data = json.loads(message)
        if data.get("type") == "sf":
            # Tick data
            self.on_message_callback(data)
    
    def on_connect(self):
        logger.info("WebSocket connected")
    
    def on_error(self, error):
        logger.error(f"WebSocket error: {error}")
    
    def on_close(self):
        logger.info("WebSocket closed")


# Usage example
def tick_handler(data):
    """Process real-time tick data"""
    symbol = data.get("symbol")
    ltp = data.get("ltp")
    logger.debug(f"{symbol}: {ltp}")


# Run in separate thread
def start_websocket(access_token, symbols):
    ws_handler = FyersWebSocket(symbols, tick_handler)
    ws_thread = threading.Thread(target=ws_handler.connect, args=(access_token,))
    ws_thread.daemon = True
    ws_thread.start()
    return ws_handler