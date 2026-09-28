# auto_token.py
import webbrowser
import os
import yaml
from fyers_apiv3 import fyersModel

CONFIG_FILE = "config.yaml"
TOKEN_FILE = "token.txt"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return yaml.safe_load(f)
    return {}

if __name__ == "__main__":
    config = load_config()
    APP_ID = config.get("fyers", {}).get("app_id")
    SECRET_KEY = config.get("fyers", {}).get("secret_key")
    
    if not APP_ID or not SECRET_KEY:
        print("[ERROR] Missing app_id or secret_key in config.yaml")
        exit()
    
    # Use Google because Fyers UI rejects localhost
    REDIRECT_URL = "https://www.google.com"
    
    session = fyersModel.SessionModel(
        client_id=APP_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URL,
        response_type="code",
        grant_type="authorization_code"
    )
    
    auth_url = session.generate_authcode()
    
    print("="*50)
    print("  KRONOS TOKEN REFRESHER")
    print("="*50)
    print("[INFO] Opening browser for Fyers login...")
    
    webbrowser.open(auth_url)
    
    print("\n" + "="*50)
    print("[WAITING] After you login, Fyers will redirect you to Google.")
    print("[ACTION] COPY the entire URL from your browser's address bar")
    print("         and PASTE it below, then press ENTER:")
    print("="*50)
    
    # Wait for user to paste the URL
    redirected_url = input("\nPaste URL here: ").strip()
    
    if "auth_code=" not in redirected_url:
        print("[ERROR] Invalid URL. 'auth_code' not found.")
        exit()
        
    # Extract the auth_code from the pasted URL
    auth_code = redirected_url.split("auth_code=")[1].split("&")[0]
    
    print("\n[INFO] Generating Access Token...")
    
    # Initialize session
    token_session = fyersModel.SessionModel(
        client_id=APP_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URL,
        response_type="code",
        grant_type="authorization_code"
    )
    
    # 1. Bind the auth_code to the session (returns None)
    token_session.set_token(auth_code)
    
    # 2. ACTUALLY ask the Fyers server to turn that code into an access token
    response = token_session.generate_token()
    
    # 3. Process the response dictionary normally
    if response.get("s") == "ok":
        access_token = response["access_token"]
        
        # Create token.txt
        with open(TOKEN_FILE, "w") as f:
            f.write(access_token)
            
        print(f"\n[SUCCESS] Token saved to {TOKEN_FILE}")
    else:
        print(f"\n[ERROR] Failed to get token: {response}")