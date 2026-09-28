# train_ml_filter.py
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
import joblib

# Load your labeled dataset (you’d create this from backtest results)
# Columns: rsi, macd_hist, supertrend_dir, volume_ratio, vwap_dist, label
df = pd.read_csv("labeled_trades.csv")

X = df[["rsi", "macd_hist", "supertrend_dir", "volume_ratio", "vwap_dist"]]
y = df["label"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = LogisticRegression()
model.fit(X_train, y_train)

print(f"Train accuracy: {model.score(X_train, y_train):.3f}")
print(f"Test accuracy: {model.score(X_test, y_test):.3f}")

# Save model
joblib.dump(model, "smc_ml_filter.pkl")