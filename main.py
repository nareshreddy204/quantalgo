# main.py
import yaml
import os
from trading_engine import TradingEngine

def load_config(config_path="config.yaml"):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    # AUTO-LOAD TOKEN: Override yaml token with token.txt if it exists
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
            
    return config
def main():
    config = load_config()
    
    # ✅ TEST TELEGRAM CONFIG
    tg = config.get("telegram", {})
    print(f"Telegram enabled: {tg.get('enabled')}")
    print(f"Bot token set: {bool(tg.get('bot_token'))}")
    print(f"Chat ID set: {bool(tg.get('chat_id'))}")
    
    engine = TradingEngine(config)
    engine.run()

# def main():
#     # Load configuration
#     config = load_config()
    
#     # Initialize and run trading engine
#     engine = TradingEngine(config)
#     engine.run()

if __name__ == "__main__":
    main()