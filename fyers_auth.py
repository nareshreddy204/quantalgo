# fyers_auth.py
from fyers_apiv3 import fyersModel
import json

class FyersAuth:
    def __init__(self, app_id, secret_key):
        self.app_id = app_id
        self.secret_key = secret_key
        self.access_token = None
        self.fyers = None
    
    def generate_auth_code(self, redirect_uri):
        """Generate authorization URL"""
        session = fyersModel.SessionModel(
            client_id=self.app_id,
            secret_key=self.secret_key,
            redirect_uri=redirect_uri,
            response_type="code",
            grant_type="authorization_code"
        )
        return session.generate_authcode()
    
    def generate_access_token(self, auth_code, redirect_uri):
        """Exchange auth code for access token"""
        session = fyersModel.SessionModel(
            client_id=self.app_id,
            secret_key=self.secret_key,
            redirect_uri=redirect_uri,
            response_type="code",
            grant_type="authorization_code",
            auth_code=auth_code
        )
        response = session.generate_token()
        
        if response.get("s") == "ok":
            self.access_token = response["access_token"]
            self._initialize_fyers()
            return True
        return False
    
    def set_access_token(self, access_token):
        """Set existing access token"""
        self.access_token = access_token
        self._initialize_fyers()
    
    def _initialize_fyers(self):
        """Initialize FyersModel with access token"""
        self.fyers = fyersModel.FyersModel(
            token=self.access_token,
            log_path="./logs"
        )