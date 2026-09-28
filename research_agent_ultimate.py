# research_agent_ultimate.py
import os
import re
import pyautogui
import yaml
import glob
import shutil
import itertools
import pandas as pd
import logging
import traceback
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
# import imgkit
from rich.console import Console
import matplotlib.pyplot as plt



from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm, IntPrompt

from fyers_auth import FyersAuth
from data_fetcher import DataFetcher
from strategycopy3 import Strategy
from backtester_enhanced import EnhancedBacktester

# ==========================================
# CONFIGURATION & SETUP
# ==========================================
logging.basicConfig(
    filename=f"research_{datetime.now():%Y%m%d_%H%M%S}.log",
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# console = Console()

# To this:
console = Console(record=True)

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
    if os.path.exists("token.txt"):
        with open("token.txt", "r") as f:
            config["fyers"]["access_token"] = f.read().strip()
    return config

# ==========================================
# WORKER FUNCTION FOR MULTIPROCESSING
# ==========================================
# def run_strategy_batch(args):
#     """
#     Runs in a separate CPU core. 
#     Instantiates Backtester here to avoid Python pickling errors.
#     """
#     df_signals, symbol, strategy_name, combos, min_trades = args
    
#     backtester = EnhancedBacktester()
#     results = []
    
#     for params in combos:
#         try:
#             result = backtester.run_backtest(
#                 df=df_signals.copy(),
#                 symbol=symbol,
#                 strategy_name=strategy_name,  
#                 params=params,
#                 sl_multiplier=params['sl_mult'],
#                 tp_multiplier=params['tp_mult'],
#                 use_atr_sl=True,
#                 use_time_filter=True,
#                 use_trend_filter=True
#             )
            
#             if result and result.get("total_trades", 0) > min_trades:
#                 result["strategy"] = strategy_name
#                 result["params_tested"] = params
#                 results.append(result)
                
#         except Exception as e:
#             logging.error(f"Error in {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}")
            
#     return results
def run_strategy_batch(args):
    """
    Runs in a separate CPU core. 
    Instantiates Backtester here to avoid Python pickling errors.
    """
    df_signals, symbol, strategy_name, combos, min_trades = args
    
    backtester = EnhancedBacktester()
    results = []
    
    for params in combos:
        try:
            result = backtester.run_backtest(
                df=df_signals.copy(),
                symbol=symbol,
                strategy_name=strategy_name,  
                params=params,
                initial_capital=100000,    # <--- FIXED: ₹100,000 so it can afford lots
                sl_multiplier=params['sl_mult'],
                tp_multiplier=params['tp_mult'],
                use_atr_sl=True,
                use_time_filter=True,       # Turned back ON
                use_trend_filter=True       # Turned back ON
            )
            
            if result and result.get("total_trades", 0) > min_trades:
                result["strategy"] = strategy_name
                result["params_tested"] = params
                results.append(result)
                
        except Exception as e:
            logging.error(f"Error in {strategy_name} | Params: {params} | Error: {e}\n{traceback.format_exc()}")
            
    return results

# ==========================================
# MASTER RESEARCH AGENT CLASS
# ==========================================
class MasterResearchAgent:
    def __init__(self, config):
        self.config = config
        auth = FyersAuth(config["fyers"]["app_id"], config["fyers"]["secret_key"])
        auth.set_access_token(config["fyers"]["access_token"])
        
        self.data_fetcher = DataFetcher(auth.fyers)
        self.results = []
        
        # Synced Strategy List
        strategies_to_test = [
            "ema_crossover", "smart_ema", "ema_bounce", "liquidity_swings",
            "supertrend_macd", "rsi_reversal", "rsi_divergence", "bollinger_squeeze", 
            "vwap_bounce", "macd_crossover", "supertrend_only", "smc", 
            "bb_trap", "bb_blast", "3_step_bullish", "triveni_sangam", 
            "ema_convergence", "5_candle_reversal", "hm_rsi_confirm", "vwap_fake_break"
        ]
        
        self.strategies = {}
        for name in strategies_to_test:
            try:
                self.strategies[name] = Strategy(name)
            except Exception as e:
                console.print(f"[red]Failed to initialize {name}: {e}[/red]")
                
        self._archive_previous_results()

    def _archive_previous_results(self):
        """Moves old checkpoint files to a date-stamped folder to keep workspace clean."""
        partial_files = [f for f in os.listdir(".") if f.startswith("results_partial_") and f.endswith(".csv")]
        if not partial_files:
            return
            
        archive_folder = f"report_{datetime.now().strftime('%Y-%m-%d')}"
        os.makedirs(archive_folder, exist_ok=True)
        
        console.print(f"[yellow]Found {len(partial_files)} old checkpoint(s). Moving to ./{archive_folder}/[/yellow]")
        for file in partial_files:
            try:
                dest = os.path.join(archive_folder, file)
                if os.path.exists(dest): os.remove(dest)
                shutil.move(file, archive_folder)
            except Exception as e:
                console.print(f"[red]Failed to move {file}: {e}[/red]")

    def _load_data(self, symbol, timeframe, days):
        """Smart loader: Checks local cache recursively, prompts if multiple, else fetches API."""
        tf_int = int(timeframe)
        if tf_int <= 3: days = min(days, 60)
        elif tf_int <= 10: days = min(days, 120)
            
        safe_symbol = symbol.replace(":", "_")
        pattern = re.compile(rf"^{re.escape(safe_symbol)}_{timeframe}m_(\d+)d\.csv$")
        
        matching_files = []
        for root, _, files in os.walk("./data"):
            for file in files:
                if pattern.match(file):
                    matching_files.append(os.path.join(root, file))
        
        if matching_files:
            def get_days(filepath):
                m = pattern.search(os.path.basename(filepath))
                return int(m.group(1)) if m else 0
                
            matching_files.sort(key=get_days, reverse=True)
            
            console.print("[bold yellow]Found multiple cached files. Select one:[/bold yellow]")
            for i, f in enumerate(matching_files):
                console.print(f"  [{i+1}] {os.path.basename(f)} ({get_days(f)} days)")
            
            choice = IntPrompt.ask("Enter number", default=1, choices=[str(i+1) for i in range(len(matching_files))])
            cache_file = matching_files[choice - 1]
            
            console.print(f"[bold green]✓ Loaded: {cache_file}[/bold green]")
            try:
                return pd.read_csv(cache_file, index_col="datetime", parse_dates=["datetime"])
            except Exception as e:
                console.print(f"[red]Error reading cache: {e}. Fetching from API...[/red]")
        
        console.print(f"[yellow]No local cache. Fetching {days} days from API...[/yellow]")
        return self.data_fetcher.get_historical_data(symbol, timeframe, days)

    def run_feather_sweep(self, feather_path, symbol="SENSEX", timeframes=["1", "5", "15", "60"]):
        """Loads 1m .feather file, resamples it, and runs the sweep."""
        console.print(f"[cyan]Loading feather data from {feather_path}...[/cyan]")
        try:
            df = pd.read_feather(feather_path)
        except Exception as e:
            console.print(f"[red]Error reading feather: {e}[/red]")
            return
            
        if 'datetime' not in df.columns:
            for col in ['date', 'time', 'timestamp']:
                if col in df.columns: df.rename(columns={col: 'datetime'}, inplace=True)
                    
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        df.columns = [c.lower() for c in df.columns]
        if 'volume' not in df.columns: df['volume'] = 0
        
        for tf in timeframes:
            console.print(f"\n[bold blue]{'='*50}\nSWEEP FOR {symbol} {tf}m\n{'='*50}[/bold blue]")
            
            df_tf = df if str(tf) == "1" else df.resample(f'{tf}min').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna()
            
            self._execute_sweep(df_tf, symbol, tf)

    def run_full_sweep(self, symbol, timeframe, days=100):
        """Standard sweep using CSV/API data."""
        console.print(f"\n[bold blue]{'='*50}\nSWEEP FOR {symbol} {timeframe}m\n{'='*50}[/bold blue]")
        df = self._load_data(symbol, timeframe, days)
        if df is None or df.empty:
            console.print(f"[red]No data available for {symbol} {timeframe}m. Skipping.[/red]")
            return
        self._execute_sweep(df, symbol, timeframe)

    def _execute_sweep(self, df, symbol, timeframe):
        """Core engine that pre-calculates signals and distributes work to CPU cores."""
        start_time = datetime.now()
        console.print(f"[cyan]Loaded {len(df)} candles. Pre-calculating signals...[/cyan]")
        
        param_grid = {'sl_mult': [1.0, 1.5, 2.0], 'tp_mult': [1.0, 2.0, 3.0, 5.0, 10.0]}
        combinations = [dict(zip(param_grid.keys(), combo)) for combo in itertools.product(*param_grid.values())]
        
        min_trades = max(5, len(df) // 1000)        
        console.print(f"[dim]Minimum trades required to qualify: {min_trades}[/dim]")
        
        tasks = []
        for name, strat in self.strategies.items():
            console.print(f"[dim]  -> Pre-calculating [bold]{name}[/bold]...[/dim]", end=" ")
            try:
                df_signals = strat.generate_signals(df.copy())
                if 'atr' not in df_signals.columns:
                    df_signals['atr'] = strat.calculate_atr(df_signals)
                
                df_signals.dropna(subset=['close', 'atr'], inplace=True)
                
                # ❌ DELETE OR COMMENT OUT THESE TWO LINES:
                # essential_cols = ['open', 'high', 'low', 'close', 'volume', 'signal', 'atr']
                # df_signals = df_signals[[c for c in essential_cols if c in df_signals.columns]]
                
                tasks.append((df_signals, symbol, name, combinations, min_trades))
                console.print("[green]Done[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        console.print(f"\n[yellow]Running {len(tasks)} strategy batches via Multicore CPU...[/yellow]\n")
        
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(), console=console,
        ) as progress:
            task_id = progress.add_task("[cyan]Backtesting...", total=len(tasks))
            
            max_workers = max(1, (os.cpu_count() or 2) - 1)
            try:
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(run_strategy_batch, task) for task in tasks]
                    for future in as_completed(futures):
                        try:
                            batch_results = future.result(timeout=900)
                            if batch_results:
                                for res in batch_results:
                                    res["timeframe"] = timeframe
                                    self.results.append(res)
                        except Exception as e:
                            console.print(f"[red]Worker failed: {e}[/red]")
                        progress.advance(task_id)
            except BrokenProcessPool:
                console.print("[bold red]Process pool crashed. Reduce memory or worker count.[/bold red]")

        # Checkpoint save
        if self.results:
            safe_sym = symbol.replace(":", "_")
            pd.DataFrame(self.results).to_csv(f"results_partial_{safe_sym}_{timeframe}m.csv", index=False)
            console.print(f"[dim]Saved checkpoint: results_partial_{safe_sym}_{timeframe}m.csv[/dim]")

        elapsed = datetime.now() - start_time
        console.print(f"[bold green]Sweep Complete in {int(elapsed.total_seconds()//60)}m {int(elapsed.total_seconds()%60)}s![/bold green]\n")

    def generate_report(self):
        if not self.results:
            console.print("[bold red]No trades were taken by ANY strategy.[/bold red]")
            return

        valid_results = [r for r in self.results if 'profit_factor' in r]
        if not valid_results:
            console.print("[bold red]No valid backtest results found.[/bold red]")
            return
            
        df = pd.DataFrame(valid_results)
        
        # Deduplicate
        df['params_str'] = df['params_tested'].astype(str)
        df = df.drop_duplicates(subset=['strategy', 'timeframe', 'params_str'])
        df = df.drop(columns=['params_str'])
        
        profitable = df[df['profit_factor'] > 1.1].copy()
        
        if profitable.empty:
            console.print("[bold red]No profitable setups found across all strategies![/bold red]")
            df = df.sort_values(by='profit_factor', ascending=False).head(15)
        else:
            df = profitable.sort_values(by=['profit_factor', 'total_pnl'], ascending=[False, False]).head(15)
        
        console.clear()
        console.print(Panel(
            "[bold white]MASTER RESEARCH AGENT REPORT\nTop Performing Strategies[/bold white]",
            title="🤖 KRONOS QUANT RESEARCH", box=box.DOUBLE
        ))
        
        table = Table(box=box.HEAVY, show_lines=True, title="Best Setups Found", title_style="bold cyan")
        table.add_column("Rank", style="bold", justify="center", width=5)
        table.add_column("Strategy", style="bold cyan", min_width=20)
        table.add_column("TF", justify="center", width=4)
        table.add_column("Params (SL / TP)", style="dim white", min_width=20)
        table.add_column("Trades", justify="center", width=7)
        table.add_column("Win %", justify="right", width=8)
        table.add_column("PF", justify="right", width=6)
        table.add_column("Max DD", justify="right", width=8)
        table.add_column("Net P&L", justify="right", width=11)

        for rank, (_, row) in enumerate(df.iterrows(), 1):
            pf = min(row['profit_factor'], 999.0) if pd.notna(row['profit_factor']) else 0.0
            pf_str = f"[bold green]{pf:.2f}[/bold green]" if pf > 1.2 else f"{pf:.2f}"
            pnl = row.get('total_pnl', 0)
            pnl_str = f"[green]+{pnl:,.0f}[/green]" if pnl > 0 else f"[red]{pnl:,.0f}[/red]"
            wr = row.get('win_rate', 0)
            wr_str = f"{wr:.2f}%" if pd.notna(wr) else "N/A"
            
            params = row.get('params_tested', {})
            if isinstance(params, str):
                import ast
                try: params = ast.literal_eval(params)
                except: params = {}
            
            table.add_row(
                f"{rank}", str(row.get('strategy', 'Unknown')), str(row.get('timeframe', '')),
                f"SL: {params.get('sl_mult', 'N/A')}x / TP: {params.get('tp_mult', 'N/A')}x",
                f"{row.get('total_trades', 0)}", wr_str, pf_str,
                f"{row.get('max_drawdown_pct', 0):.2f}%", pnl_str
            )
            
        console.print(table)
        
        best = df.iloc[0]
        best_params = best.get('params_tested', {})
        if isinstance(best_params, str):
            import ast
            try: best_params = ast.literal_eval(best_params)
            except: best_params = {}
            
            console.print(Panel(
            f"[bold green]ACTION ITEM:[/bold green] Update your config.yaml with the following:\n\n"
            f"  strategy: \"{best.get('strategy')}\"\n"
            f"  resolution: \"{best.get('timeframe')}\"\n"
            f"  sl_atr_multiplier: {best_params.get('sl_mult')}\n"
            f"  tp_atr_multiplier: {best_params.get('tp_mult')}",
            title="🚀 BEST SETUP FOUND", box=box.ROUNDED
        ))
        
        # ==========================================
        # GENERATE THE PNG IMAGE
        # ==========================================
        try:
            self._export_report_image(df)
        except Exception as e:
            console.print(f"[yellow]Could not generate report image: {e}[/yellow]")

    def _export_report_image(self, df_results):
        """Exports the top results dataframe to a styled PNG image using matplotlib."""
        # 1. Build a formatted dataframe specifically for the image
        img_data = []
        for i, (_, row) in enumerate(df_results.iterrows(), 1):
            params = row.get('params_tested', {})
            if isinstance(params, str):
                import ast
                try: params = ast.literal_eval(params)
                except: params = {}

            pnl = row.get('total_pnl', 0)
            pnl_str = f"+{pnl:,.0f}" if pnl > 0 else f"{pnl:,.0f}"

            img_data.append({
                "Rank": i,
                "Strategy": str(row.get('strategy', 'Unknown')),
                "TF": str(row.get('timeframe', '')),
                "Params (SL / TP)": f"SL: {params.get('sl_mult', 'N/A')}x / TP: {params.get('tp_mult', 'N/A')}x",
                "Trades": int(row.get('total_trades', 0)),
                "Win %": f"{row.get('win_rate', 0):.2f}%",
                "PF": round(row.get('profit_factor', 0), 2),
                "Max DD": f"{row.get('max_drawdown_pct', 0):.2f}%",
                "Net P&L": pnl_str
            })

        df_img = pd.DataFrame(img_data)

        # 2. Matplotlib Rendering
        row_count = len(df_img)
        fig, ax = plt.subplots(figsize=(14, row_count * 0.45 + 1.8))
        
        dark_bg = '#121212'
        header_bg = '#1f2937'
        row_bg_1 = '#1a1d24'
        row_bg_2 = '#111318'
        
        fig.patch.set_facecolor(dark_bg)
        ax.set_facecolor(dark_bg)
        ax.axis('off')

        plt.title("KRONOS QUANT RESEARCH - BEST SETUPS", color='#38bdf8', fontsize=14, fontweight='bold', pad=20, loc='center')

        table = ax.table(
            cellText=df_img.values,
            colLabels=df_img.columns,
            cellLoc='center',
            loc='center'
        )

        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.1, 1.8)

        # 3. Style Header & Alternating Rows
        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor('#374151')
            cell.set_linewidth(0.8)
            
            if r == 0:  # Headers
                cell.set_facecolor(header_bg)
                cell.get_text().set_color('#f3f4f6')
                cell.get_text().set_weight('bold')
            else:  # Data rows
                bg = row_bg_1 if r % 2 == 0 else row_bg_2
                cell.set_facecolor(bg)
                
                col_name = df_img.columns[c]
                if col_name == "Net P&L":
                    # Get the actual numeric value to determine color
                    val = df_img.iloc[r-1][col_name]
                    cell.get_text().set_color('#4ade80' if val.startswith('+') else '#f87171')
                    cell.get_text().set_weight('bold')
                elif col_name == "PF":
                    cell.get_text().set_color('#facc15')  # Gold for Profit Factor
                    cell.get_text().set_weight('bold')
                else:
                    cell.get_text().set_color('#e5e7eb')

        # 4. Save PNG Image
        img_filename = f"kronos_report_{datetime.now():%Y%m%d_%H%M%S}.png"
        plt.savefig(img_filename, bbox_inches='tight', facecolor=fig.get_facecolor(), dpi=300)
        plt.close(fig)
        console.print(f"[bold green]🖼️ Report image saved perfectly as {img_filename}[/bold green]")
    
# ==========================================
# MAIN EXECUTION BLOCK
# ==========================================
# if __name__ == "__main__":
#     config = load_config()
#     agent = MasterResearchAgent(config)
    
#     # ==========================================
#     # CHOOSE YOUR MODE HERE
#     # ==========================================
    
#     MODE = "CSV_API"  # Options: "CSV_API" or "FEATHER"
    
#     if MODE == "FEATHER":
#         # Use this for testing 1-minute options/index feather files
#         feather_file = r"data\otpion\SENSEX_2026-07-23_expiry_1min.feather"
#         timeframes_to_test = ["1", "5", "15", "60"]
        
#         agent.run_feather_sweep(
#             feather_path=feather_file,
#             symbol="SENSEX",
#             timeframes=timeframes_to_test
#         )
#     else:
#         # Use this for standard CSV cache or Fyers API fetching
#         # symbol_to_test = "NSE:NIFTYBANK-INDEX"
#         symbol_to_test = "NSE:BANKNIFTY26JUL55900CE"
#         timeframes_to_test = ["1", "5", "15", "60", "240"]
#         # timeframes_to_test = ["15"]  # Reduced for quicker testing
        
#         for tf in timeframes_to_test:
#             agent.run_full_sweep(
#                 symbol=symbol_to_test,
#                 timeframe=tf,
#                 days=365
#             )
            
#             # Interactive prompt between timeframes
#             if tf != timeframes_to_test[-1]:
#                 if not Confirm.ask(f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"):
#                     console.print("[bold red]Sweep stopped by user.[/bold red]")
#                     break
    
#     console.print(f"\n[bold magenta]Generating Consolidated Report...[/bold magenta]")
#     agent.generate_report()

# ==========================================
# MAIN EXECUTION BLOCK
# ==========================================
if __name__ == "__main__":
    config = load_config()
    agent = MasterResearchAgent(config)
    
    # ==========================================
    # CHOOSE YOUR MODE HERE
    # ==========================================
    
    MODE = "OPTIONS_1M"  # Options: "OPTIONS_1M", "FEATHER", or "CSV_API"
    
    if MODE == "OPTIONS_1M":
        # Load 1m CSV from the option folder and resample it to 5m, 15m, 60m
        csv_file = r"data\otpion\NSE_BANKNIFTY26JUL57000PE_1m_100d.csv"
        symbol_to_test = "NSE:BANKNIFTY26JUL57000PE"
        timeframes_to_test = [ "15", "60"]
        
        console.print(f"[cyan]Loading 1m CSV data from {csv_file}...[/cyan]")
        try:
            df = pd.read_csv(csv_file)
            
            # Standardize datetime column
            if 'datetime' not in df.columns:
                for col in ['date', 'time', 'timestamp']:
                    if col in df.columns: df.rename(columns={col: 'datetime'}, inplace=True)
                    
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
            df.columns = [c.lower() for c in df.columns]
            if 'volume' not in df.columns: df['volume'] = 0
            
            # Loop through and resample
            for tf in timeframes_to_test:
                console.print(f"\n[bold blue]{'='*50}\nSWEEP FOR {symbol_to_test} {tf}m\n{'='*50}[/bold blue]")
                
                # Resample 1m data into the target timeframe
                df_tf = df.resample(f'{tf}min').agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
                }).dropna()
                
                # Run the sweep on the resampled data
                agent._execute_sweep(df_tf, symbol_to_test, tf)
                
                # Interactive prompt
                if tf != timeframes_to_test[-1]:
                    if not Confirm.ask(f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"):
                        console.print("[bold red]Sweep stopped by user.[/bold red]")
                        break
                        
        except Exception as e:
            console.print(f"[red]Error reading CSV: {e}[/red]")
        
    elif MODE == "FEATHER":
        # Use this for testing 1-minute options/index feather files
        feather_file = r"data\otpion\SENSEX_2026-07-23_expiry_1min.feather"
        timeframes_to_test = ["1", "5", "15", "60"]
        
        agent.run_feather_sweep(
            feather_path=feather_file,
            symbol="SENSEX",
            timeframes=timeframes_to_test
        )
        
    else:
        # Use this for standard CSV cache or Fyers API fetching
        symbol_to_test = "NSE:NIFTYBANK-INDEX"
        timeframes_to_test = ["5", "15", "60", "240"]
        
        for tf in timeframes_to_test:
            agent.run_full_sweep(
                symbol=symbol_to_test,
                timeframe=tf,
                days=365
            )
            
            # Interactive prompt between timeframes
            if tf != timeframes_to_test[-1]:
                if not Confirm.ask(f"[bold yellow]Finished {tf}m sweep. Continue to next timeframe?[/bold yellow]"):
                    console.print("[bold red]Sweep stopped by user.[/bold red]")
                    break
    
    console.print(f"\n[bold magenta]Generating Consolidated Report...[/bold magenta]")
    agent.generate_report()