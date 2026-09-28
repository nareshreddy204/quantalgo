# dashboard.py
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
import datetime

console = Console()

class Dashboard:
    def __init__(self):
        self.console = Console()
    
    def clear_screen(self):
        self.console.clear()
    
    def render(self, symbols_data, positions, risk_data):
        self.clear_screen()
        
        # 1. Header
        header_text = Text(
            f"  NARESH TRADING ENGINE  |  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  MODE: PAPER TRADING",
            style="bold white on blue",
            justify="left"
        )
        self.console.print(Panel(header_text, box=box.HEAVY))
        
        # 2. Table (Only show if we actually have data)
        if symbols_data and len(symbols_data) > 0:
            table = Table(
                title="Market Watch & Signals",
                box=box.ROUNDED,
                show_lines=True,
                title_style="bold cyan",
                header_style="bold magenta",
                width=140  # Increased width to fit new Strategy Info column
            )
            
            table.add_column("Symbol", style="bold white", justify="center", min_width=22)
            table.add_column("Price", style="bold green", justify="right", min_width=10)
            table.add_column("Signal", style="bold yellow", justify="center", min_width=8)
            table.add_column("EMA 9", style="white", justify="right", min_width=10)
            table.add_column("EMA 21", style="white", justify="right", min_width=10)
            table.add_column("RSI (14)", style="white", justify="right", min_width=9)
            table.add_column("Strategy Info", style="bold yellow", justify="center", min_width=25) # NEW COLUMN
            table.add_column("Status / Live P&L", style="bold", justify="center", min_width=32)
            
            for data in symbols_data:
                signal = data.get("signal", 0)
                signal_str = "BUY" if signal == 1 else ("SELL" if signal == -1 else "---")
                signal_style = "bold green" if signal == 1 else ("bold red" if signal == -1 else "dim")
                
                rsi = data.get("rsi", 0)
                if rsi > 70:
                    rsi_style = "bold red"
                elif rsi < 30:
                    rsi_style = "bold green"
                else:
                    rsi_style = "white"
                
                # Live P&L Logic
                status_data = data.get("status_data")
                if status_data:
                    entry = status_data["entry"]
                    points = status_data["points"]
                    pnl = status_data["pnl"]
                    
                    pts_str = f"+{points:.2f}" if points >= 0 else f"{points:.2f}"
                    pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
                    color = "green" if pnl >= 0 else "red"
                    
                    status_str = f"Entry: {entry}\n[bold {color}]Pts: {pts_str} | P&L: {pnl_str}[/bold {color}]"
                else:
                    status_str = "WATCHING"
                
                # Get Strategy Info string
                strat_info = data.get("strategy_info", "N/A")
                
                table.add_row(
                    f"[cyan]{data.get('symbol', '')}[/cyan]",
                    f"{data.get('price', 0):.2f}",
                    f"[{signal_style}]{signal_str}[/{signal_style}]",
                    f"{data.get('ema_9', 0):.2f}",
                    f"{data.get('ema_21', 0):.2f}",
                    f"[{rsi_style}]{rsi:.1f}[/{rsi_style}]",
                    strat_info,  # NEW FIELD ADDED HERE
                    status_str
                )
            
            self.console.print(table)
        else:
            # Show loading message on first frame
            self.console.print(Panel("[dim]Fetching market data...[/dim]", box=box.ROUNDED, width=90))
        
        # 3. Footer
        pnl = risk_data.get("daily_pnl", 0)
        pnl_style = "bold green" if pnl >= 0 else "bold red"
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        
        footer_text = Text()
        footer_text.append(" Open Positions: ", style="bold white")
        footer_text.append(f"{len(positions)}", style="bold yellow")
        footer_text.append("  |  Daily P&L: ", style="bold white")
        footer_text.append(f"{pnl_str}", style=pnl_style)
        footer_text.append("  |  Trades Today: ", style="bold white")
        footer_text.append(f"{risk_data.get('total_trades', 0)}", style="bold yellow")
        
        self.console.print(Panel(footer_text, box=box.HEAVY, width=140))