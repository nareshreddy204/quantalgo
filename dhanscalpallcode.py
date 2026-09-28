import warnings
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")

"""
SUPER SCALPER — CONSOLIDATED FINAL VERSION
cache + multi-TF grid + IS/OOS validation + trade inspector
Fixes: exit-skip bug, EOD square-off, next-bar-open entry, correct ATR.
"""

import yaml
import os
from datetime import time as dtime

import numpy as np
import pandas as pd
import pandas_ta as ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher

console = Console()

# ==========================================================
# SETTINGS
# ==========================================================
DATA_DIR = r"C:\Users\bnare\Desktop\new way\data\options"

ENTRY_START = dtime(9, 30)
ENTRY_END   = dtime(15, 10)
EOD_EXIT    = dtime(15, 20)

# --- Realism ---
SLIPPAGE_PTS     = 0.75          # realistic for Nifty futures
BROKERAGE_RT     = 40.0          # round-turn brokerage in ₹ (both sides)
POINT_VALUE      = 25.0          # ₹ per point (Nifty lot size effect)
CAPITAL          = 500_000       # starting capital
RISK_PER_TRADE   = 0.0075        # 0.75% of capital risked per trade
MAX_LOTS         = 15            # hard safety cap

# --- Regime filter ---
USE_ADX_FILTER   = True
ADX_PERIOD       = 14
ADX_MIN          = 20            # only trade when ADX >= this

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config


# ==========================================================
# LOCAL CACHE
# ==========================================================
def get_local_filepath(symbol, tf):
    safe = symbol.replace(":", "_").replace("/", "_")
    return os.path.join(DATA_DIR, f"{safe}_{tf}m.csv")


def load_local_data(symbol, tf):
    fp = get_local_filepath(symbol, tf)
    if not os.path.exists(fp):
        return None
    try:
        df = pd.read_csv(fp)
        if df.empty:
            return None
        dtc = [c for c in df.columns if c.lower() in ("datetime", "date", "timestamp", "time")]
        if dtc:
            df[dtc[0]] = pd.to_datetime(df[dtc[0]])
            df.set_index(dtc[0], inplace=True)
        else:
            df.index = pd.to_datetime(df.index)
        df.columns = [c.lower() for c in df.columns]
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[keep].sort_index()
    except Exception as e:
        console.print(f"[red]Error reading {fp}: {e}[/red]")
        return None


def save_local_data(df, symbol, tf):
    fp = get_local_filepath(symbol, tf)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out.index.name = "datetime"
    out.to_csv(fp)
    console.print(f"[green]💾 Saved {len(out)} candles → {fp}[/green]")


def load_or_fetch_data(data_fetcher, symbol, tf, days, force_refresh=False):
    if not force_refresh:
        local_df = load_local_data(symbol, tf)
        if local_df is not None and len(local_df) > 0:
            console.print(f"[green]📂 {tf}m: {len(local_df)} candles from cache[/green]")
            return local_df
    raw = data_fetcher.get_historical_data(symbol=symbol, resolution=tf, days=days)
    if raw is not None and len(raw) > 0:
        save_local_data(raw, symbol, tf)
    return raw


# ==========================================================
# STRATEGY — ATR = WMA(True Range)  (correct Dhan implementation)
# ==========================================================
def calculate_dhan_super_scalper(df, atr_period=14, atr_multiplier=1.0,
                                 fast_ema_len=21, slow_ema_len=65,
                                 fast_rsi_len=25, slow_rsi_len=100,
                                 adx_period=14):
    df = df.copy()
    df.columns = df.columns.str.capitalize()

    df['hl2'] = (df['High'] + df['Low']) / 2.0
    prev_close = df['Close'].shift(1)
    tr = pd.concat([(df['High'] - df['Low']).abs(),
                    (df['High'] - prev_close).abs(),
                    (df['Low'] - prev_close).abs()], axis=1).max(axis=1)

    df['atr'] = ta.wma(tr, length=atr_period)
    df['fast_ema'] = ta.ema(df['Close'], length=fast_ema_len)
    df['slow_ema'] = ta.ema(df['Close'], length=slow_ema_len)
    df['fast_rsi'] = ta.rsi(df['Close'], length=fast_rsi_len)
    df['slow_rsi'] = ta.rsi(df['Close'], length=slow_rsi_len).fillna(50)

    # ADX for regime filter
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=adx_period)
    df['adx'] = adx_df[f'ADX_{adx_period}']

    df['up_raw'] = df['hl2'] + atr_multiplier * df['atr']
    df['lo_raw'] = df['hl2'] - atr_multiplier * df['atr']

    n = len(df)
    fin_up = np.full(n, np.nan)
    fin_lo = np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    close = df['Close'].to_numpy()
    up_raw = df['up_raw'].to_numpy()
    lo_raw = df['lo_raw'].to_numpy()

    for i in range(n):
        if i == 0 or np.isnan(up_raw[i]):
            fin_up[i], fin_lo[i], trend[i] = up_raw[i], lo_raw[i], 1
            continue
        pc, pu, pl = close[i - 1], fin_up[i - 1], fin_lo[i - 1]
        fin_up[i] = up_raw[i] if (np.isnan(pu) or up_raw[i] < pu or pc > pu) else pu
        fin_lo[i] = lo_raw[i] if (np.isnan(pl) or lo_raw[i] > pl or pc < pl) else pl
        if not np.isnan(pu) and pc > pu:
            trend[i] = 1
        elif not np.isnan(pl) and pc < pl:
            trend[i] = -1
        elif trend[i - 1] == 1 and close[i] < fin_lo[i]:
            trend[i] = -1
        elif trend[i - 1] == -1 and close[i] > fin_up[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i - 1]

    df['trend'] = trend
    df['trend_prev'] = df['trend'].shift(1)

    # Base signals
    buy_base  = ((df['trend'] == 1) & (df['trend_prev'] == -1) &
                 (df['fast_ema'] > df['slow_ema']) & (df['fast_rsi'] > df['slow_rsi']))
    sell_base = ((df['trend'] == -1) & (df['trend_prev'] == 1) &
                 (df['fast_ema'] < df['slow_ema']) & (df['fast_rsi'] < df['slow_rsi']))

    # Regime filter
    if USE_ADX_FILTER:
        regime_ok = df['adx'] >= ADX_MIN
        df['buy_signal']  = buy_base  & regime_ok
        df['sell_signal'] = sell_base & regime_ok
    else:
        df['buy_signal']  = buy_base
        df['sell_signal'] = sell_base

    return df


# ==========================================================
# BACKTEST — exits NEVER skipped, EOD square-off, next-bar-open entry
# ==========================================================
def backtest_strategy(df, sl_multiplier=1.0, tp_multiplier=1.5,
                      slippage=SLIPPAGE_PTS,
                      capital=CAPITAL,
                      risk_pct=RISK_PER_TRADE,
                      brokerage_rt=BROKERAGE_RT,
                      point_value=POINT_VALUE,
                      max_lots=MAX_LOTS):
    """
    Fully realistic backtest:
    - Next-bar open entry
    - SL / TP / EOD never skipped
    - Slippage
    - Round-turn brokerage
    - Risk-based position sizing (lots)
    Returns trades in ₹ (not points)
    """
    in_position = False
    ptype = None
    entry_price = stop_loss = take_profit = entry_atr = 0.0
    entry_time = None
    qty = 0                    # number of lots
    pending_type = None
    pending_atr = 0.0
    trades = []
    equity = capital

    def close_trade(index, exit_price, reason):
        nonlocal in_position, equity
        # Gross points PnL
        points = (exit_price - entry_price) if ptype == 'LONG' else (entry_price - exit_price)
        gross_pnl = points * point_value * qty
        net_pnl = gross_pnl - brokerage_rt * qty          # round-turn already
        equity += net_pnl

        trades.append({
            "Entry Time": entry_time,
            "Exit Time": index,
            "Type": ptype,
            "Entry": round(entry_price, 2),
            "Exit": round(exit_price, 2),
            "ATR": round(entry_atr, 2),
            "Lots": qty,
            "Gross PnL": round(gross_pnl, 1),
            "Net PnL": round(net_pnl, 1),
            "Reason": reason,
            "Equity": round(equity, 1)
        })
        in_position = False

    for index, row in df.iterrows():
        if pd.isna(row['atr']) or pd.isna(row['fast_ema']) or pd.isna(row['slow_ema']):
            continue
        if USE_ADX_FILTER and pd.isna(row.get('adx', np.nan)):
            continue

        t = index.time()

        # 0) drop stale pending outside entry window
        if pending_type is not None and not (ENTRY_START <= t <= ENTRY_END):
            pending_type = None

        # 1) EOD square-off
        if in_position and t >= EOD_EXIT:
            px = row['Close'] - slippage if ptype == 'LONG' else row['Close'] + slippage
            close_trade(index, px, "EOD Square-Off")

        # 2) SL / TP (SL checked first)
        if in_position:
            if ptype == 'LONG':
                if row['Low'] <= stop_loss:
                    close_trade(index, stop_loss - slippage, "SL Hit")
                elif row['High'] >= take_profit:
                    close_trade(index, take_profit - slippage, "TP Hit")
            else:
                if row['High'] >= stop_loss:
                    close_trade(index, stop_loss + slippage, "SL Hit")
                elif row['Low'] <= take_profit:
                    close_trade(index, take_profit + slippage, "TP Hit")

        # 3) Enter at this bar's OPEN from previous signal
        if (not in_position) and pending_type is not None and (ENTRY_START <= t <= ENTRY_END):
            in_position = True
            ptype = pending_type
            entry_price = row['Open'] + slippage if ptype == 'LONG' else row['Open'] - slippage
            entry_atr = pending_atr
            entry_time = index

            # Risk-based position sizing
            sl_distance = entry_atr * sl_multiplier
            if sl_distance <= 0:
                in_position = False
                pending_type = None
                continue

            risk_amount = equity * risk_pct
            raw_lots = risk_amount / (sl_distance * point_value)
            qty = max(1, min(int(raw_lots), max_lots))

            if ptype == 'LONG':
                stop_loss   = entry_price - sl_distance
                take_profit = entry_price + entry_atr * tp_multiplier
                # Intra-bar SL/TP check on entry bar
                if row['Low'] <= stop_loss:
                    close_trade(index, stop_loss - slippage, "SL Hit")
                elif row['High'] >= take_profit:
                    close_trade(index, take_profit - slippage, "TP Hit")
            else:
                stop_loss   = entry_price + sl_distance
                take_profit = entry_price - entry_atr * tp_multiplier
                if row['High'] >= stop_loss:
                    close_trade(index, stop_loss + slippage, "SL Hit")
                elif row['Low'] <= take_profit:
                    close_trade(index, take_profit + slippage, "TP Hit")

            pending_type = None
            continue

        # 4) New signal → pending for next bar
        if (not in_position) and pending_type is None and (ENTRY_START <= t <= ENTRY_END):
            if row['buy_signal']:
                pending_type, pending_atr = 'LONG', row['atr']
            elif row['sell_signal']:
                pending_type, pending_atr = 'SHORT', row['atr']

    return pd.DataFrame(trades)

# ==========================================================
# STATS
# ==========================================================
def compute_stats(trades_df):
    if trades_df is None or len(trades_df) == 0:
        return None
    total = len(trades_df)
    wins = int((trades_df['Net PnL'] > 0).sum())
    gw = trades_df.loc[trades_df['Net PnL'] > 0, 'Net PnL'].sum()
    gl = abs(trades_df.loc[trades_df['Net PnL'] <= 0, 'Net PnL'].sum())
    eq = trades_df['Net PnL'].cumsum()
    return {
        "Trades": total,
        "Win%": wins / total * 100,
        "Total PnL": trades_df['Net PnL'].sum(),
        "Avg PnL": trades_df['Net PnL'].mean(),
        "PF": gw / gl if gl > 0 else float('inf'),
        "MaxDD": (eq - eq.cummax()).min()
    }

# ==========================================================
# MULTI-FOLD WALK-FORWARD + ROBUSTNESS RANKING
# ==========================================================

def walk_forward_backtest(df_full, sl, tp, n_folds=5, min_is_days=25, min_oos_days=8):
    """
    Anchored expanding-window walk-forward.
    Returns list of dicts: one per fold with IS + OOS stats.
    """
    if df_full is None or len(df_full) < 100:
        return []

    days = sorted(set(df_full.index.date))
    total_days = len(days)
    if total_days < min_is_days + min_oos_days * 2:
        return []

    # Calculate fold boundaries (expanding IS, fixed-size OOS)
    oos_size = max(min_oos_days, total_days // (n_folds + 2))
    results = []

    for fold in range(n_folds):
        # OOS starts after expanding IS
        oos_start_idx = min_is_days + fold * oos_size
        oos_end_idx   = oos_start_idx + oos_size

        if oos_end_idx > total_days:
            break

        is_end_date   = days[oos_start_idx - 1]
        oos_start_date = days[oos_start_idx]
        oos_end_date   = days[min(oos_end_idx, total_days) - 1]

        df_is  = df_full[df_full.index.date <= is_end_date]
        df_oos = df_full[(df_full.index.date >= oos_start_date) &
                         (df_full.index.date <= oos_end_date)]

        if len(df_is) < 50 or len(df_oos) < 20:
            continue

        is_stats  = compute_stats(backtest_strategy(df_is,  sl, tp))
        oos_stats = compute_stats(backtest_strategy(df_oos, sl, tp))

        if is_stats is None:
            continue

        results.append({
            "fold": fold + 1,
            "is_end": is_end_date,
            "oos_start": oos_start_date,
            "oos_end": oos_end_date,
            "is": is_stats,
            "oos": oos_stats if oos_stats is not None else {
                "Trades": 0, "Win%": 0, "Total PnL": 0, "Avg PnL": 0,
                "PF": 0, "MaxDD": 0
            }
        })

    return results


def robustness_score(fold_results, min_oos_trades=6):
    """
    Composite robustness score.
    Higher = better.
    """
    if not fold_results:
        return -9999.0

    oos_pnls = []
    oos_pfs  = []
    oos_trades = []
    profitable_folds = 0

    for fr in fold_results:
        oos = fr["oos"]
        oos_pnls.append(oos["Total PnL"])
        oos_pfs.append(oos["PF"] if np.isfinite(oos["PF"]) else 0)
        oos_trades.append(oos["Trades"])
        if oos["Total PnL"] > 0:
            profitable_folds += 1

    if sum(oos_trades) < min_oos_trades:
        return -9999.0

    avg_oos_pnl   = np.mean(oos_pnls)
    avg_oos_pf    = np.mean(oos_pfs)
    consistency   = profitable_folds / len(fold_results)          # 0–1
    total_oos_pnl = sum(oos_pnls)

    # Heavy penalty for negative average OOS
    if avg_oos_pnl <= 0:
        return avg_oos_pnl * 2 - 50   # push negatives further down

    # Score formula (tuneable)
    score = (
        avg_oos_pnl * 1.0 +
        total_oos_pnl * 0.3 +
        avg_oos_pf * 40 +
        consistency * 80 -
        abs(np.std(oos_pnls)) * 0.4
    )
    return score


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    config = load_config()
    auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
    auth.set_access_token(config["fyers"]["access_token"])
    data_fetcher = DataFetcher(auth.fyers)

    SYMBOL = "NSE:NIFTY26SEPFUT"
    DAYS = 90
    FORCE_REFRESH = False
    N_FOLDS = 5
    MIN_IS_DAYS = 28
    MIN_OOS_DAYS = 8
    TOP_K = 12

    # Parameter grid (slightly tightened + more robust values)
    tf_range        = ["5", "15"]
    atr_mult_range  = [0.7, 0.9, 1.1, 1.3]
    fast_ema_range  = [13, 21, 34, 55]
    sl_range        = [1.0, 1.3, 1.6]
    tp_range        = [1.5, 2.0, 2.5, 3.0]

    console.print(Panel(
        f"Symbol: {SYMBOL} | {DAYS} days | {N_FOLDS}-Fold Walk-Forward\n"
        f"Entry: {ENTRY_START}-{ENTRY_END} | EOD: {EOD_EXIT} | Slippage: {SLIPPAGE_PTS} pts",
        title="SUPER SCALPER — MULTI-FOLD WALK-FORWARD OPTIMIZER",
        box=box.DOUBLE
    ))

    all_results = []
    df_cache = {}

    for tf in tf_range:
        raw_df = load_or_fetch_data(data_fetcher, SYMBOL, tf, DAYS, FORCE_REFRESH)
        if raw_df is None or len(raw_df) == 0:
            console.print(f"[red]No {tf}m data. Skipping.[/red]")
            continue

        console.print(f"\n[cyan]=== {tf}m | {len(raw_df)} candles ===[/cyan]")

        for atr_mult in atr_mult_range:
            for ema_len in fast_ema_range:
                key = (tf, atr_mult, ema_len)
                if key not in df_cache:
                    df_cache[key] = calculate_dhan_super_scalper(
                        raw_df.copy(),
                        atr_multiplier=atr_mult,
                        fast_ema_len=ema_len
                    )

                df_full = df_cache[key]

                for sl in sl_range:
                    for tp in tp_range:
                        folds = walk_forward_backtest(
                            df_full, sl, tp,
                            n_folds=N_FOLDS,
                            min_is_days=MIN_IS_DAYS,
                            min_oos_days=MIN_OOS_DAYS
                        )
                        if not folds:
                            continue

                        score = robustness_score(folds)
                        if score <= -9000:
                            continue

                        # Aggregate metrics
                        oos_pnls = [f["oos"]["Total PnL"] for f in folds]
                        oos_trades = sum(f["oos"]["Trades"] for f in folds)
                        profitable = sum(1 for p in oos_pnls if p > 0)

                        all_results.append({
                            "TF": tf,
                            "ATR": atr_mult,
                            "EMA": ema_len,
                            "SL": sl,
                            "TP": tp,
                            "Score": score,
                            "Avg OOS PnL": np.mean(oos_pnls),
                            "Total OOS PnL": sum(oos_pnls),
                            "OOS Trades": oos_trades,
                            "Profitable Folds": f"{profitable}/{len(folds)}",
                            "Consistency": profitable / len(folds),
                            "folds": folds          # keep for later inspection
                        })

    if not all_results:
        console.print("[red]No robust configurations found. Try loosening filters or more data.[/red]")
        raise SystemExit

    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values("Score", ascending=False).reset_index(drop=True)

    # ---------- ROBUSTNESS LEADERBOARD ----------
    t = Table(title=f"🏆 ROBUSTNESS LEADERBOARD — Top {TOP_K} (multi-fold walk-forward)",
              box=box.HEAVY)
    for c, j in [("Rank","center"),("TF","center"),("ATR","center"),("EMA","center"),
                 ("SL","center"),("TP","center"),("Score","right"),
                 ("Avg OOS","right"),("Total OOS","right"),
                 ("OOS Trades","center"),("Folds +","center")]:
        t.add_column(c, justify=j)

    for i, r in results_df.head(TOP_K).iterrows():
        pc = "green" if r["Avg OOS PnL"] > 0 else "red"
        t.add_row(
            str(i+1),
            f"{r['TF']}m",
            f"{r['ATR']:.1f}",
            str(int(r['EMA'])),
            f"{r['SL']:.1f}",
            f"{r['TP']:.1f}",
            f"{r['Score']:.1f}",
            f"[{pc}]{r['Avg OOS PnL']:.1f}[/{pc}]",
            f"[{pc}]{r['Total OOS PnL']:.1f}[/{pc}]",
            str(int(r['OOS Trades'])),
            r['Profitable Folds']
        )
    console.print(t)

    # ---------- FOLD-BY-FOLD DETAIL FOR TOP CONFIGS ----------
    console.print("\n[bold yellow]📊 Fold-by-Fold Breakdown (Top 5)[/bold yellow]")
    for rank in range(min(5, len(results_df))):
        r = results_df.iloc[rank]
        console.print(f"\n[bold]Rank {rank+1} → {r['TF']}m | ATR {r['ATR']} | EMA {int(r['EMA'])} | "
                      f"SL {r['SL']}x | TP {r['TP']}x | Score {r['Score']:.1f}[/bold]")

        ft = Table(box=box.SIMPLE)
        ft.add_column("Fold", justify="center")
        ft.add_column("IS End", justify="center")
        ft.add_column("OOS Period", justify="center")
        ft.add_column("OOS Trades", justify="center")
        ft.add_column("OOS Win%", justify="center")
        ft.add_column("OOS PnL", justify="right")
        ft.add_column("OOS PF", justify="right")

        for fr in r["folds"]:
            oos = fr["oos"]
            pc = "green" if oos["Total PnL"] > 0 else "red"
            period = f"{fr['oos_start']} → {fr['oos_end']}"
            ft.add_row(
                str(fr["fold"]),
                str(fr["is_end"]),
                period,
                str(int(oos["Trades"])),
                f"{oos['Win%']:.0f}%",
                f"[{pc}]{oos['Total PnL']:.1f}[/{pc}]",
                f"{oos['PF']:.2f}" if np.isfinite(oos["PF"]) else "inf"
            )
        console.print(ft)

    # ---------- TRADE INSPECTOR ----------
    console.print(f"\n[bold blue]🔍 Inspector[/bold blue] — enter rank (1-{TOP_K}) to see all trades of that config, 'q' to quit")
    while True:
        u = input("Rank: ").strip().lower()
        if u in ('q', '0', 'exit', 'quit'):
            break
        try:
            rank = int(u)
        except ValueError:
            continue
        if not (1 <= rank <= min(TOP_K, len(results_df))):
            continue

        r = results_df.iloc[rank - 1]
        key = (r['TF'], r['ATR'], r['EMA'])
        dff = df_cache[key]

        # Show full-period trades for this parameter set
        all_tr = backtest_strategy(dff, r['SL'], r['TP'])

        dt = Table(title=f"Rank {rank} | {r['TF']}m | ATR {r['ATR']} | EMA {int(r['EMA'])} | "
                         f"SL {r['SL']}x | TP {r['TP']}x | Score {r['Score']:.1f}",
                   box=box.SIMPLE_HEAVY)
        for c, j in [("Entry Time","left"),("Exit Time","left"),("Type","center"),
             ("Entry","right"),("Exit","right"),("Lots","center"),
             ("Net PnL","right"),("Reason","center")]:
            dt.add_column(c, justify=j)

        for _, tr in all_tr.iterrows():
            col = "green" if tr['Net PnL'] > 0 else "red"
            dt.add_row(
                str(tr['Entry Time']), str(tr['Exit Time']), tr['Type'],
                f"{tr['Entry']:.2f}", f"{tr['Exit']:.2f}",
                str(int(tr['Lots'])),
                f"[{col}]{tr['Net PnL']:.0f}[/{col}]", tr['Reason']
            )
        console.print(dt)