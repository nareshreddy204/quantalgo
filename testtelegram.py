import requests
import yaml

# Load your config
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

tg = config.get("telegram", {})
token = tg.get("bot_token")
chat_id = tg.get("chat_id")

print(f"Token: {token[:10]}..." if token else "Token: MISSING")
print(f"Chat ID: {chat_id}" if chat_id else "Chat ID: MISSING")

if token and chat_id:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": "✅ Test message from Kronos",
        "parse_mode": "HTML"
    }, timeout=5)
    
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
else:
    print("ERROR: Missing telegram config!")