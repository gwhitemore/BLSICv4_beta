import sys
import os
import asyncio
import time
import json
import threading
import socket
import math
import httpx
from pathlib import Path
from datetime import datetime
from collections import deque

# --- CROSS-PLATFORM SYSTEM IMPORTS ---
try:
    import termios
    import fcntl
except ImportError:
    termios = None
    fcntl = None

# --- DEPENDENCY GUARD ---
try:
    from rich.live import Live
    from rich.table import Table
    from rich.layout import Layout
    from rich.panel import Panel
    from rich import box
    from rich.console import Console
    from rich.align import Align
    from rich.text import Text
except ImportError:
    print("\n[!] ERROR: 'rich' library missing. Run: python -m pip install rich\n")
    sys.exit(1)

console = Console()

# --- HARDWARE COLOR MAP ---
TYPE_COLORS = {
    "Gamma": "bright_magenta",
    "NerdQaxe++": "cyan",
    "NerdQAxe++": "cyan",
    "GT800": "orange3",
    "601": "green",
    "Bitaxe": "white",
    "NerdMiner": "bright_yellow",
    "Micro": "bright_yellow"
}

# --- GLOBAL CONFIGURATION ---
UK_VOLTAGE = 230  
MAX_HISTORY = 1000 
SOLAR_PANEL_WATTAGE = 450 
BIFACIAL_GAIN = 1.10

# --- PATH CONFIGURATION ---
current_dir = Path(__file__).resolve().parent
src_path = current_dir / "src"
calc_path = current_dir / "calculations"

if str(src_path) not in sys.path: sys.path.insert(0, str(src_path))
if str(calc_path) not in sys.path: sys.path.insert(0, str(calc_path))

from discovery.hunter import SwarmHunter

# Force strict absolute import of engine.py
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))
from engine import LuckEngine

luck_engine = LuckEngine()

# --- NEW: Import the Central Data Vault ---
from data import swarm_state, load_state, save_state, visual_recalibrate, check_and_trigger_72h_reset, update_known_miners

input_queue = asyncio.Queue(maxsize=10)

def get_layout_width(layout_name, layout_obj):
    try: return layout_obj[layout_name].region.width
    except: return 60

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception: return "127.0.0.1"

def get_high_res_graph(data, width=70, height=3, color="cyan"):
    effective_width = max(width - 4, 10)
    if not data or len(data) < 2: return [" " * effective_width] * height, 0, 0
    braille_chars = ["⡀", "⡄", "⡆", "⡇", "⣇", "⣧", "⣷", "⣿"]
    current_sample = data[-effective_width:]
    highest, lowest = max(current_sample), min(current_sample)
    rng = max(highest - lowest, 0.01) 
    lines = [""] * height
    for v in current_sample:
        norm = (v - lowest) / rng
        pos = int(norm * (height * 8 - 1))
        for h in range(height):
            if pos // 8 == (height - 1 - h): lines[h] += f"[{color}]{braille_chars[pos % 8]}[/]"
            elif pos // 8 > (height - 1 - h): lines[h] += f"[{color}]┃[/]" 
            else: lines[h] += " "
    return [l.ljust(effective_width) for l in lines], highest, lowest

def format_uptime(seconds):
    if not seconds: return "0s"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    return f"{days}d {hours}h" if days > 0 else f"{hours}h {mins}m" if hours > 0 else f"{mins}m {secs}s"

def resolve_miner_type(m):
    device_model = str(m.get('deviceModel', '')).lower()
    hostname = str(m.get('hostname', '')).lower()
    board_ver = str(m.get('boardVersion', ''))
    is_micro_flag = m.get('is_micro', False)
    
    if is_micro_flag or 'nerdminer' in device_model or 'micro' in hostname or 'nerd' in hostname:
        if 'nerdqaxe' not in hostname and 'nerdqaxe' not in device_model:
            return 'NerdMiner'
            
    if 'nerdqaxe' in hostname or 'nerdqaxe' in device_model: return 'NerdQAxe++'
    if board_ver == '800' or 'gt800' in device_model or 'gt800' in hostname: return 'GT800'
    if board_ver == '601' or 'gamma' in device_model or 'gamma' in hostname: return 'Gamma'
    if board_ver == '201' or 'bitaxe' in device_model or 'bitaxe' in hostname: return 'Bitaxe'
        
    t = m.get('type', 'Bitaxe')
    return "Bitaxe" if t in ["", "Unknown"] else t

async def async_prompt(message, default=None):
    def _ask():
        import sys
        sys.stdout.flush() 
        
        # CRITICAL FIX: Only import the Windows library IF we are actually on Windows!
        if sys.platform == 'win32':
            import msvcrt
            while msvcrt.kbhit(): msvcrt.getch()
            
        prompt_str = f"{message}"
        if default is not None: prompt_str += f" [{default}]"
        prompt_str += ": "
        print(prompt_str, end="", flush=True)
        
        try:
            # Bypass Python's input() to avoid GNU Readline panic in the background
            val = sys.stdin.readline().strip()
            if not val and default is not None: return default
            return val
        except Exception as e:
            print(f"\n[!] Input Error: {e}")
            import time; time.sleep(2)
            return default
            
    return await asyncio.get_running_loop().run_in_executor(None, _ask)

async def handle_settings_input():
    console.print(Panel("[bold cyan]GLOBAL SWARM SETTINGS[/]", border_style="cyan"))
    import sys; sys.stdout.flush()
    try:
        new_cost = await async_prompt("Electricity Cost (£ per kWh)", default=str(swarm_state["elec_cost"]))
        new_sun = await async_prompt("Peak Sun Hours (UK Avg 4.0)", default=str(swarm_state["sun_hours"]))
        if new_cost is not None and new_sun is not None:
            swarm_state["elec_cost"] = float(new_cost)
            swarm_state["sun_hours"] = float(new_sun)
    except Exception as e:
        print(f"\n[!] Input Error: {e}"); time.sleep(2)

async def handle_miner_cost_input():
    if not swarm_state["miners"]:
        print("\n[!] No miners found in the configuration.")
        await asyncio.sleep(2)
        return
        
    console.print(Panel("[bold green]UPDATE MINER PURCHASE PRICE & HARDWARE TAGS[/]", border_style="green"))
    miner_list = list(swarm_state["miners"].keys())
    for i, ip in enumerate(miner_list, 1):
        m = swarm_state["miners"][ip]
        console.print(f"{i}) {ip} - {m.get('hostname', 'Unknown')} (£{m.get('cost', 0)})")
        
    import sys; sys.stdout.flush()
    try:
        choice = await async_prompt("\nSelect Miner # (or press Enter to cancel)")
        if not choice or not choice.isdigit(): return
        idx = int(choice) - 1
        if idx < 0 or idx >= len(miner_list): return
        target_ip = miner_list[idx]
        
        # 1. Ask for the Purchase Price
        new_val = await async_prompt(f"Purchase Price for {target_ip} (£)", default=str(swarm_state["miners"][target_ip].get("cost", 0.0)))
        if new_val is not None:
            swarm_state["miners"][target_ip]["cost"] = float(new_val)
            print(f"\n[+] Cost updated to £{new_val}")
            
        # 2. Ask for Board Version if it's a NerdQAxe
        m = swarm_state["miners"][target_ip]
        m_type = resolve_miner_type(m)
        if "NerdQAxe" in m_type or "NerdQaxe" in m_type:
            hw_input = await async_prompt(f"NerdQAxe detected. Enter Board Version (e.g., 6.1, 501)", default=m.get("manual_hw_version", ""))
            if hw_input:
                swarm_state["miners"][target_ip]["manual_hw_version"] = hw_input.strip()
                print(f"[+] Board Version tagged as v{hw_input.strip()}")
                
        await asyncio.sleep(1)
    except Exception: pass

async def handle_miner_deletion():
    if not swarm_state["miners"]: return
    console.print(Panel("[bold red]PRUNE FLEET[/]", border_style="red"))
    miner_list = list(swarm_state["miners"].keys())
    for i, ip in enumerate(miner_list, 1):
        console.print(f"{i}) {ip} - {swarm_state['miners'][ip].get('hostname', 'Unknown')}")
        
    import sys; sys.stdout.flush()
    try:
        choice = await async_prompt("\nSelect Miner to DELETE (0 to cancel)", default="0")
        if choice != "0" and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(miner_list):
                target_ip = miner_list[idx]
                del swarm_state["miners"][target_ip]
                print(f"\n[+] Miner {target_ip} removed.")
                await asyncio.sleep(1)
    except Exception: pass

async def handle_miner_action():
    if not swarm_state["miners"]: return
    console.print(Panel("[bold magenta]MINER ACTION TERMINAL[/]", border_style="magenta"))
    
    miner_list = list(swarm_state["miners"].keys())
    for i, ip in enumerate(miner_list, 1):
        m = swarm_state["miners"][ip]
        m_type = resolve_miner_type(m)
        color = TYPE_COLORS.get(m_type, "white")
        status = "[bold green]ONLINE[/]" if m.get('online') else "[bold red]OFFLINE[/]"
        console.print(f"[bold cyan]{i})[/] {ip} - [{color}]{m.get('hostname', 'Unknown')}[/] {status}")
        
    import sys; sys.stdout.flush()
    try:
        choice = await async_prompt("\nSelect Miner to [bold red]RESTART[/] (0 to cancel)", default="0")
        if choice != "0" and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(miner_list):
                target_ip = miner_list[idx]
                confirm = await async_prompt(f"Send reboot signal to {target_ip}? (y/n)", default="n")
                if confirm.lower() == 'y':
                    try:
                        async with httpx.AsyncClient() as client:
                            await client.post(f"http://{target_ip}/api/system/restart", timeout=5.0)
                        print(f"\n[+] Reboot command sent to {target_ip}!")
                    except Exception as e:
                        print(f"\n[!] Failed to connect to API: {e}")
                    await asyncio.sleep(2)
    except Exception: pass

def make_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3)
    )
    layout["body"].split_row(
        Layout(name="left_col", ratio=10),
        Layout(name="right_col", ratio=3)
    )
    layout["left_col"].split_column(
        Layout(name="telemetry", ratio=4),
        Layout(name="matrix_row", ratio=5),
        Layout(name="trend_row", ratio=2)
    )
    layout["matrix_row"].split_row(
        Layout(name="health_col", ratio=4),
        Layout(name="luck_ladder", ratio=6)
    )
    layout["trend_row"].split_row( 
        Layout(name="solar", ratio=3),
        Layout(name="trend", ratio=7)
    )
    layout["right_col"].split_column(
        Layout(name="system", size=12),
        Layout(name="lottery", size=12),
        Layout(name="podium", size=16),
        Layout(name="archive", size=8),  
        Layout(name="debug", ratio=1)
    )
    return layout

def lottery_analysis_panel():
    btc_th = swarm_state.get('total_btc_th', 0.0)
    bch_th = swarm_state.get('total_bch_th', 0.0)
    daily_opex = swarm_state.get('total_opex_daily', 0.0)
    
    analysis = luck_engine.get_lotto_analysis(daily_opex, btc_th, bch_th)
    border_style = "bright_white" if swarm_state["flash_timer"] > 0 else "magenta"
    
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    
    grid.add_row(f"[bold magenta]LOTTERY PARITY[/]")
    grid.add_row(f"Daily OPEX: £{daily_opex:.2f} | Tickets: [yellow]{analysis.get('equiv_tickets', 0):.2f}[/]")
    grid.add_row("-" * 15)
    
    b_odds = analysis.get('btc_odds', 'Offline')
    h_odds = analysis.get('bch_odds', 'Offline')
    profiles = analysis.get('profiles', {})
    
    grid.add_row("[bold cyan]vs. National Lotto (£15M)[/]")
    if isinstance(b_odds, int) and 'lotto' in profiles:
        color = "green" if profiles['lotto'].get('btc_luck', 0) > 1 else "yellow"
        grid.add_row(f"BTC Solo: 1:{b_odds:,} [{color}]({profiles['lotto'].get('btc_luck', 0):.1f}x odds)[/]")
    if isinstance(h_odds, int) and 'lotto' in profiles:
        color = "green" if profiles['lotto'].get('bch_luck', 0) > 1 else "yellow"
        grid.add_row(f"BCH Solo: 1:{h_odds:,} [{color}]({profiles['lotto'].get('bch_luck', 0):.1f}x odds)[/]")
        
    grid.add_row("[bold cyan]vs. Scratchcard (£250k)[/]")
    if isinstance(b_odds, int) and 'scratchcard' in profiles:
        color = "green" if profiles['scratchcard'].get('btc_luck', 0) > 1 else "yellow"
        grid.add_row(f"BTC Solo: [{color}]({profiles['scratchcard'].get('btc_luck', 0):.2f}x odds)[/]")
    if isinstance(h_odds, int) and 'scratchcard' in profiles:
        color = "green" if profiles['scratchcard'].get('bch_luck', 0) > 1 else "yellow"
        grid.add_row(f"BCH Solo: [{color}]({profiles['scratchcard'].get('bch_luck', 0):.2f}x odds)[/]")

    return Panel(grid, title="[ LOTTERY ANALYSIS ]", border_style=border_style)

def trend_panel(layout_obj):
    history_data = swarm_state.get("hashrate_history", [0, 0])
    if len(history_data) < 2: history_data = [0, 0]
    
    target_width = get_layout_width("trend", layout_obj) 
    if target_width <= 10: target_width = 80 
        
    live_total = swarm_state.get('total_btc_th', 0.0) + swarm_state.get('total_bch_th', 0.0)
    
    clean_sample = [v for v in history_data[-target_width:] if v > (live_total * 0.5)]
    if not clean_sample: clean_sample = history_data[-target_width:]

    try:
        spark, min_val, max_val = get_high_res_graph(history_data, width=target_width, height=3, color="cyan")
        display_min = max(min_val, live_total * 0.8) if live_total > 0 else 0
    except Exception:
        spark, display_min, max_val = [" [dim]Syncing Trend...[/] "], 0, 0

    if live_total > swarm_state.get("peak_th", 0): swarm_state["peak_th"] = live_total
    
    grid = Table.grid(expand=True)
    grid.add_row(Align.center("\n".join(spark)))
    
    stats_line = (
        f"[dim]RANGE: {display_min:.2f} ➔ {max_val:.2f} TH/s[/] | "
        f"LIVE: [bold white]{live_total:.2f}[/] | "
        f"PEAK: [bold green]{swarm_state.get('peak_th', 0):.2f}[/]"
    )
    grid.add_row(Align.center(stats_line))
    
    share_chunks = []
    sorted_ips = sorted([ip for ip, m in swarm_state["miners"].items() if not m.get('is_micro') and m.get('online')])
    
    for ip in sorted_ips:
        m = swarm_state["miners"][ip]
        tag = m.get('tag', '---')
        acc = m.get('mem_acc', 0)
        rej = m.get('mem_rej', 0)
        color = TYPE_COLORS.get(resolve_miner_type(m), "white")
        
        if rej > 0: chunk = f"[{color}]{tag}[/]: {acc:,}/[bold red]{rej}[/]"
        else: chunk = f"[{color}]{tag}[/]: {acc:,}/[dim]0[/]"
        share_chunks.append(chunk)

    if share_chunks:
        shares_line = "  [dim]||[/]  ".join(share_chunks)
        grid.add_row("") 
        grid.add_row(Align.center(shares_line))
    
    return Panel(grid, title="[ SWARM PERFORMANCE TREND ]", border_style="cyan", padding=(0, 1))

def create_header():
    grid = Table.grid(expand=True); grid.add_column(ratio=1); grid.add_column(ratio=1)
    total_miners = sum(1 for m in swarm_state["miners"].values() if not m.get('is_micro', False))
    online = sum(1 for m in swarm_state["miners"].values() if m.get('online', False) and not m.get('is_micro', False))
    status_text = "[bold yellow]DISCOVERY HUNT...[/]" if swarm_state["is_hunting"] else "[bold green]SWARM ACTIVE[/]"
    
    # --- NEW: Telemetry Epoch Calculation ---    
    epoch_dt = datetime.fromtimestamp(swarm_state.get("telemetry_epoch", time.time()))
    lifespan = datetime.now() - epoch_dt
    lifespan_str = f"{lifespan.days}d {lifespan.seconds//3600}h"
    
    grid.add_row(
        f"[bold white on blue] BLSIC COMMAND [/] {status_text} | [bold white]FLEET: {online}/{total_miners} ONLINE[/]", 
        f"[bold cyan]STATS SINCE:[/] {epoch_dt.strftime('%d/%m %H:%M')} [dim]({lifespan_str})[/]"
    )
    return Panel(grid, style="blue", box=box.HORIZONTALS)

def render_search_summary():
    grid = Table.grid(expand=True)
    grid.add_row(Align.center(f"[bold yellow]SCAN COMPLETE - {len(swarm_state['last_hunt_results'])} MINERS ONLINE[/]"))
    summary_table = Table(box=box.SIMPLE, expand=True, header_style="bold cyan")
    summary_table.add_column("ASSET TYPE"); summary_table.add_column("IP"); summary_table.add_column("STATUS")
    
    for m in swarm_state["last_hunt_results"]:
        status = "[bold green]New Discovery[/]" if m.get('is_new') else "[dim white]Refreshed[/]"
        summary_table.add_row(f"[magenta]{m.get('type','Bitaxe')}[/]", m['ip'], status)
        
    grid.add_row(summary_table); grid.add_row(""); grid.add_row(Align.center(f"[dim]Returning to dashboard in {int(swarm_state['summary_timer'])}s...[/]"))
    return Panel(grid, title="[ ASSET DISCOVERY SUMMARY ]", border_style="green")

def hardware_table():
    if swarm_state["show_summary"]: return render_search_summary()
    if not swarm_state["miners"] or swarm_state["is_hunting"]:
        grid = Table.grid(expand=True)
        if swarm_state["is_hunting"]:
            base_ip = ".".join(swarm_state["detected_local_ip"].split(".")[:-1])
            done = min(swarm_state["scan_progress"], 254)
            filled = int((done / 254) * 40)
            bar = f"[bold green]{'█' * filled}[/][white]{'░' * (40 - filled)}[/]"
            grid.add_row(""); grid.add_row(Align.center("[bold yellow] DISCOVERY HUNT IN PROGRESS[/]"))
            grid.add_row(Align.center(f"[dim]TARGET: {base_ip}.{done}[/]")); grid.add_row("")
            grid.add_row(Align.center(f"[{bar}] [bold white]{int((done/254)*100)}%[/]"))
            return Panel(grid, title="[ DISCOVERY MATRIX ]", border_style="yellow")
        return Panel(Align.center(f"\n[bold red]SWARM OFFLINE[/]\n[dim]Press 'H' to Hunt[/]"), title="[ SYSTEM ]", border_style="red")

    table = Table(box=box.SIMPLE_HEAD, expand=True, header_style="bold cyan")
    table.add_column("#", width=2, no_wrap=True)
    table.add_column("TYPE", width=16, no_wrap=True) # <-- WIDENED TO FIT BOARD VERSIONS
    table.add_column("TAG", justify="center", no_wrap=True) # <-- WIDTH REMOVED FOR IP
    table.add_column("COIN", width=5, justify="center", no_wrap=True)
    table.add_column("POOL", width=5, no_wrap=True, overflow="ellipsis")
    table.add_column("SESS DIFF", justify="right", no_wrap=True)
    table.add_column("BEST DIFF", justify="right", no_wrap=True)
    table.add_column("TH/s", justify="right", width=7, no_wrap=True)
    table.add_column("POWER", justify="right", width=8, no_wrap=True) 
    table.add_column("J/TH", justify="right", width=7, no_wrap=True)
    table.add_column("CORE/VR", justify="right", width=8, no_wrap=True)   
    table.add_column("FREQ/mV", justify="right", width=11, no_wrap=True) 
    table.add_column("WIFI", justify="right", width=5, no_wrap=True)
    table.add_column("UPTIME", justify="right", width=9, no_wrap=True)
    
    micro_table = Table(box=box.SIMPLE_HEAD, expand=True, header_style="bold yellow")
    micro_table.add_column("TYPE", width=16, no_wrap=True) # WIDENED
    micro_table.add_column("TAG", justify="center", no_wrap=True) # <-- WIDTH REMOVED FOR IP
    micro_table.add_column("COIN", width=6, justify="center", no_wrap=True)
    micro_table.add_column("POOL", width=10, no_wrap=True, overflow="ellipsis")
    micro_table.add_column("BEST DIFF", justify="right", no_wrap=True)
    micro_table.add_column("HASH", justify="right", no_wrap=True)
    micro_table.add_column("TEMP", justify="right", no_wrap=True)
    micro_table.add_column("UPTIME", justify="right", no_wrap=True)
    
    current_best_found = 0.0
    total_chips = 0
    asset_counts = {}
    has_micros = False
    
    for i, (ip, m) in enumerate(swarm_state["miners"].items(), 1):
        online = m.get('online', False)
        style = "white" if online else "dim grey37"
        is_micro = m.get('is_micro', False)
        m_type = resolve_miner_type(m)
        asic_count = int(m.get('asicCount', 1))
        tag = m.get('tag', '---')
        
        # ==========================================================
        # --- NEW: Build the Tag Cell Content (Last 2 Octets Only) ---
        # ==========================================================
        show_ips = swarm_state.get("show_ips", False)
        
        if show_ips and ip.count('.') == 3:
            # Slice off the first two octets (e.g., "192.168.1.107" -> "1.107")
            short_ip = ".".join(ip.split('.')[-2:])
            tag_display = f"[{style}]{tag}[/]\n[dim]{short_ip}[/]"
        else:
            tag_display = f"[{style}]{tag}[/]"
        # ==========================================================
        
        if online:
            total_chips += asic_count
            if not is_micro: asset_counts[m_type] = asset_counts.get(m_type, 0) + 1
            
        type_color = TYPE_COLORS.get(m_type, "white")
        
        # ==========================================================
        # --- THE FIX: Parse & Append Board Version ---
        # ==========================================================
        raw_hw = str(m.get('manual_hw_version', m.get('hardwareRevision', m.get('boardVersion', '')))).strip()
        clean_hw = raw_hw
        if clean_hw.lower().startswith('v'): clean_hw = clean_hw[1:]
        if clean_hw.endswith('.0'): clean_hw = clean_hw[:-2]
        
        if clean_hw and clean_hw.lower() not in ["", "?", "none", "null"]:
            display_type = f"[{type_color}]{m_type}[/] [dim]v{clean_hw}[/]" 
        else:
            display_type = f"[{type_color}]{m_type}[/]" 
        # ==========================================================
            
        c_type = m.get('coin_type', 'BTC')
        coin_tag = "[bold cyan]BCH[/]" if c_type == "BCH" else "[bold orange3]BTC[/]"

        if online:
            raw_hr = float(m.get('hashRate', m.get('hashrate', 0.0)))
            power = float(m.get('power', m.get('powerW', m.get('wattage', 0.0))))
            core_t = float(m.get('coreTemp', 0))
            if core_t <= 0: core_t = float(m.get('temp', m.get('temperature', m.get('boardTemp', 0.0))))
                
            global_best = float(m.get('bestDiff', 0.0))
            raw_url = str(m.get('stratumURL', 'Solo'))
            pool = raw_url.replace('stratum+tcp://', '').replace('stratum+ssl://', '').split('.')[0]
            
            temp_str = f"[green]{core_t:.0f}°C[/]"
            if core_t >= 80: temp_str = f"[bold blink red]{core_t:.0f}°C[/]"
            elif core_t >= 70: temp_str = f"[bold yellow]{core_t:.0f}°C[/]"

            if is_micro:
                has_micros = True
                hr_str = f"[bold cyan]{raw_hr/1_000_000:.2f} MH/s[/]" if raw_hr > 1_000_000 else f"[bold cyan]{raw_hr/1000:.1f} KH/s[/]"
                best_diff_str = luck_engine.format_diff_scaled(global_best) if global_best > 0 else "[dim]--[/]"
                micro_table.add_row(display_type, tag_display, coin_tag, pool[:10], best_diff_str, hr_str, temp_str, format_uptime(m.get('uptimeSeconds', 0)))
            else:
                if raw_hr > 1_000_000_000_000:       th = raw_hr / 1_000_000_000_000.0
                elif raw_hr > 100_000.0:             th = raw_hr / 1_000_000.0
                elif raw_hr > 50.0:                  th = raw_hr / 1000.0
                else:                                th = raw_hr
                
                eff_jth = (power / th) if th > 0 else 0
                vr_temp = float(m.get('vrTemp', m.get('tempVrm', 0.0)))
                freq = int(m.get('frequency', m.get('coreFreq', 0)))
                volt = int(m.get('coreVoltage', m.get('voltage', 0)))
                session_best = float(m.get('bestSessionDiff', 0.0))
                
                sess_diff_str = luck_engine.format_diff_scaled(session_best) if session_best > 0 else "[dim]--[/]"
                best_diff_str = luck_engine.format_diff_scaled(global_best) if global_best > 0 else "[dim]--[/]"
                
                if vr_temp > 0: temp_str += f" [dim]{vr_temp:.0f}°[/]"
                core_str = f"{freq}[dim]@[/]{volt}" if freq > 0 else "[dim]--[/]"
                
                try: rssi = int(float(m.get('wifiRSSI', m.get('rssi', 0))))
                except: rssi = 0
                if rssi == 0: rssi_str = "[dim]--[/]"
                elif rssi >= -60: rssi_str = f"[bold green]{rssi}[/]"   
                elif rssi >= -75: rssi_str = f"[bold yellow]{rssi}[/]"  
                else: rssi_str = f"[bold red]{rssi}[/]"                 
                
                # --- THE FIX: Track the Session Best for the Gamification Engine ---
                if session_best > current_best_found: current_best_found = session_best
                
                table.add_row(
                    str(i), display_type, tag_display, 
                    coin_tag, pool[:10], sess_diff_str, best_diff_str, 
                    f"[bold white]{th:.2f}[/]", f"{power:.1f}W", f"{eff_jth:.1f}", 
                    temp_str, core_str, rssi_str, format_uptime(m.get('uptimeSeconds', 0))
                )
        else:
            if is_micro:
                has_micros = True
                micro_table.add_row(f"[dim]{display_type}[/]", tag_display, coin_tag, "[dim]----[/]", "[dim]--[/]", "[dim]--[/]", "-", "[bold red]OFFLINE[/]")
            else:
                table.add_row(str(i), f"[dim]{display_type}[/]", tag_display, coin_tag, "[dim]----[/]", "[dim]--[/]", "[dim]--[/]", "[dim]----[/]", "[bold red]OFF[/]", "-", "-", "[dim]--[/]", "[bold red]OFFLINE[/]")
            
    if current_best_found > swarm_state["last_best_share"]:
        swarm_state["last_best_share"] = current_best_found
        swarm_state["flash_timer"] = 20

    mapping_items = []
    for ip in sorted([k for k,v in swarm_state["miners"].items() if not v.get('is_micro')]):
        m = swarm_state["miners"][ip]
        if m.get('online'):
            c = TYPE_COLORS.get(resolve_miner_type(m), "white")
            mapping_items.append(f"[link=http://{ip}][bold {c}]{m.get('tag')}[/]: [dim]{m.get('hostname','Unknown')[:12]}[/][/link]")
    mapping_str = "  [dim]||[/]  ".join(mapping_items)

    legend_text = "   ".join([f"[{TYPE_COLORS.get(a_type, 'white')}]●[/] {a_type}: {count}" for a_type, count in sorted(asset_counts.items())])
    legend_text += f"   [bold yellow]芯片 TOTAL ASIC CHIPS: {total_chips}[/]"
    
    wrapper = Table.grid(expand=True)
    wrapper.add_row(table)
    if has_micros:
        wrapper.add_row(Align.left("\n[bold yellow]MICRO SCOUT FLEET[/]"))
        wrapper.add_row(micro_table)

    agg_acc = swarm_state.get('total_shares_acc', 0)
    agg_rej = swarm_state.get('total_shares_rej', 0)
    tot_shares = agg_acc + agg_rej
    rej_ratio = (agg_rej / tot_shares * 100) if tot_shares > 0 else 0.0
    rej_color = "bold red" if agg_rej > 0 else "dim"

    shares_str = (
        f"\n[bold yellow]SWARM LIFETIME SHARES[/]   "
        f"[dim]Acc:[/] [bold white]{agg_acc:,}[/]   [dim]||[/]   "
        f"[dim]Rej:[/] [{rej_color}]{agg_rej:,}[/] [dim]({rej_ratio:.2f}%)[/]"
    )

    wrapper.add_row(Align.center(f"\n[bold dim white]ASIC SWARM ASSETS[/]\n{legend_text}\n\n{mapping_str}\n{shares_str}"))

    return Panel(wrapper, title="[ FLEET TELEMETRY - HARDWARE MATRIX ]", border_style="blue")

def solar_saver_panel():
    total_w = swarm_state.get('total_power', 0)
    base_limit = SOLAR_PANEL_WATTAGE
    effective_limit = int(base_limit * BIFACIAL_GAIN) 
    is_off_grid = swarm_state.get('solar_mode', False) 
    sun_hours = swarm_state.get('sun_hours', 4.0) 
    
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    
    if is_off_grid:
        ratio = total_w / effective_limit if effective_limit > 0 else 0
        bar_len = 35
        filled = min(int(ratio * bar_len), bar_len)
        
        if ratio > 1.0:
            bar = f"[bold red]{'█' * bar_len}[/]"
            status = "[bold red]GRID DEPENDENT[/]"
            color = "red"
            alert_text = f"Exceeding solar capacity by {total_w - effective_limit:.0f}W"
        else:
            bar = f"[bold green]{'█' * filled}[/][dim white]{'░' * (bar_len - filled)}[/]"
            status = "[bold green]100% OFF-GRID[/]"
            color = "green"
            alert_text = f"Excess solar buffer: {effective_limit - total_w:.0f}W"
            
        grid.add_row(f"[bold yellow]ACTIVE BIFACIAL SOLAR ({effective_limit}W)[/]")
        grid.add_row(f"Live Swarm Draw: [{color}]{total_w:.0f}W[/]")
        grid.add_row(f"[{bar}]")
        grid.add_row(status)
        grid.add_row(f"[dim]{alert_text}[/]")
        
    else:
        daily_wh = total_w * 24
        night_hours = 24 - sun_hours
        night_wh_req = (total_w * night_hours) * 1.2
        req_daily_wh = daily_wh * 1.2 
        req_solar_w = req_daily_wh / sun_hours if sun_hours > 0 else 0
        
        panels_needed = math.ceil(req_solar_w / effective_limit) if req_solar_w > 0 else 0
        rec_array_w = panels_needed * effective_limit
        rec_battery_kwh = (night_wh_req / 0.8) / 1000 
        
        grid.add_row("[bold cyan]OFF-GRID RECOMMENDER (BIFACIAL)[/]")
        grid.add_row(f"Swarm Draw: [red]{total_w:.0f}W[/] ({daily_wh/1000:.1f} kWh/day)")
        grid.add_row("-" * 15)
        
        if total_w > 0:
            grid.add_row(f"Array: [bold yellow]{panels_needed}x {base_limit}W Panels[/] ({rec_array_w}W Effective)")
            grid.add_row(f"Night Battery: [bold green]{rec_battery_kwh:.1f} kWh[/] [dim](80% DoD)[/]")
            grid.add_row(f"[dim]Includes 10% rear-gain + 20% sys loss[/]")
        else:
            grid.add_row("[dim]Awaiting power telemetry...[/]")
            
    return Panel(grid, title="[ POWER METRICS ]", border_style="yellow")

def investment_podium_panel():
    main_miners = [m for m in swarm_state["miners"].values() if not m.get('is_micro', False)]
    total_investment = sum(float(m.get('cost', 0)) for m in main_miners)
    btc_th = swarm_state.get('total_btc_th', 0.0)
    bch_th = swarm_state.get('total_bch_th', 0.0)
    total_th = btc_th + bch_th
    cost_per_th = total_investment / total_th if total_th > 0 else 0.0
    
    btc_diff = luck_engine.net_diff
    bch_diff = luck_engine.bch_diff
    
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    
    grid.add_row("[bold yellow]CAPITAL DEPLOYED[/]")
    grid.add_row(f"Cost: [bold white]£{total_investment:,.2f}[/] | Power: [bold white]{total_th:.2f} TH/s[/]")
    grid.add_row(f"Efficiency Rating: [cyan]£{cost_per_th:,.2f} per TH/s[/]")
    grid.add_row("-" * 15)
    
    progress = swarm_state.get("btc_epoch_progress", 0.0)
    change = swarm_state.get("btc_epoch_change", 0.0)
    blocks_left = swarm_state.get("btc_blocks_left", 0)
    win_diff = swarm_state.get("bch_win_diff", 0.0)
    
    change_color = "red" if change > 0 else "green"
    bar_fill = int((progress / 100) * 28)
    bar = f"[cyan]{'█' * bar_fill}[/][dim white]{'░' * (28 - bar_fill)}[/]"
    
    grid.add_row("[bold green]STRATEGY ENGINE[/]")
    
    if progress > 0:
        grid.add_row(f"BTC Epoch: [{bar}] {progress:.1f}%")
        grid.add_row(f"BTC Adj:   [{change_color}]{'+' if change > 0 else ''}{change:.2f}%[/] (in ~{blocks_left} blks)")
    else:
        grid.add_row("[dim]Syncing BTC Network...[/]")

    grid.add_row("-" * 15)
    win_color = "green" if win_diff > 0 else "red"
    win_prefix = "+" if win_diff > 0 else ""
    grid.add_row(f"BCH Win:   [{win_color}]{win_prefix}{win_diff*100:.2f}% Advantage[/]")

    bch_vel = swarm_state.get("bch_daa_velocity", 0.0)
    bar_width = 24
    mid = bar_width // 2
    
    vel_clamp = max(min(bch_vel, 0.5), -0.5) 
    shift = int((vel_clamp / 0.5) * mid) 
    
    meter_chars = ["-"] * bar_width
    meter_chars[mid] = "|" 
    
    if shift < 0:
        for i in range(mid + shift, mid): meter_chars[i] = "█"
        v_color = "green"
    elif shift > 0:
        for i in range(mid + 1, mid + shift + 1): 
            if i < bar_width: meter_chars[i] = "█"
        v_color = "red"
    else:
        v_color = "white"
        
    meter_str = "".join(meter_chars)
    grid.add_row(f"BCH ASERT: [{v_color}][{meter_str}][/]")
    
    v_text = "Easier" if bch_vel < 0 else "Harder" if bch_vel > 0 else "Stable"
    grid.add_row(f"           [dim]1H Trend: {bch_vel:+.3f}% ({v_text})[/]")
    
    bch_diff_g = bch_diff / 1_000_000_000 if bch_diff > 0 else 0
    if (0 < bch_diff_g < 600) or win_diff > 0.10: 
        grid.add_row("[bold blink magenta]REC: SHIFT FLEET TO BCH (OPPORTUNITY)[/]")
    elif change < -5.0:  
        grid.add_row("[bold bright_magenta]REC: HODL BTC STRATEGY[/]")
    else:
        grid.add_row("[bold dim white]REC: STABLE DEPLOYMENT[/]")

    return Panel(grid, title="[ INVESTMENT PODIUM ]", border_style="yellow")

def luck_ladder_panel():
    if not swarm_state["miners"] or swarm_state["is_hunting"]:
        return Panel(Align.center("\n[dim]Awaiting telemetry...[/]\n"), title="[ SILICON LUCK ]", border_style="yellow")

    # --- LADDER LAYOUT ---
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_header=True, header_style="bold yellow")
    table.add_column("TAG", width=5, no_wrap=True) 
    table.add_column("COIN", width=4, no_wrap=True) 
    table.add_column("REL%", justify="right", no_wrap=True) 
    table.add_column("LUCK", justify="right", no_wrap=True) 
    # CRITICAL FIX: Change to INF (LOG)
    table.add_column("INF (LOG)", justify="right", no_wrap=True)     
    table.add_column("VIRAL LOAD", justify="left", width=15, no_wrap=True)
    table.add_column("STAGE", justify="left", width=11, no_wrap=True)      # <-- Mutation Labels
    table.add_column("RND BEST", justify="right", width=9, no_wrap=True)

    active_miners = [m for m in swarm_state["miners"].values() if m.get('online') and not m.get('is_micro')]
    active_miners.sort(key=lambda x: float(x.get('v4_hot_score', 0)), reverse=True)

    bch_fleet = [m for m in active_miners if m.get('coin_type') == 'BCH']
    btc_fleet = [m for m in active_miners if m.get('coin_type') == 'BTC']

    def render_fleet_rows(fleet):
        for m in fleet:
            tag = m.get('tag', '---')
            m_type = resolve_miner_type(m)
            color = TYPE_COLORS.get(m_type, "white")
            c_type = m.get('coin_type', 'BTC')
            coin_tag = f"[bold cyan]BCH[/]" if c_type == "BCH" else f"[bold orange3]BTC[/]"
            
            rel = float(m.get('v4_rel', 0.0))
            luck = float(m.get('v4_luck', 0.0))
            inf = float(m.get('v4_inf', 0.0))
            blocks = int(m.get('blocks', 0))
            
            # --- WIDER BAR CALCULATION (15 Blocks) ---
            bar_len = 15
            filled_blocks = int((inf / 100) * bar_len)
            if filled_blocks > bar_len: filled_blocks = bar_len
            
            # ==========================================================
            # --- MUTATION PATHWAY LOGIC ---
            # ==========================================================
            if blocks > 0: 
                # Block Found: Mutation 6 (Satoshi/BCH)
                if c_type == "BCH":
                    status_text = "[bold green]BCH[/]"
                    inf_color = "green"
                else:
                    status_text = "[bold bright_magenta]SATOSHI[/]"
                    inf_color = "bright_magenta"
                filled_blocks = bar_len
            elif inf >= 80: 
                # Mutation 5: Epidemic (Face 5)
                status_text = "[bold blink gold1]EPIDEMIC[/]"
                inf_color = "gold1"
            elif inf >= 60: 
                # Mutation 4: Virulent (Face 4)
                status_text = "[bold yellow]VIRULENT[/]"
                inf_color = "yellow"
            elif inf >= 40: 
                # Mutation 3: Infectious (Face 3)
                status_text = "[bold green]INFECTIOUS[/]"
                inf_color = "green"
            elif inf >= 20: 
                # Mutation 2: Sniffle (Face 2)
                status_text = "[bold dim green]SNIFFLE[/]"
                inf_color = "green"
            elif luck > 0: 
                # Mutation 1: Immune (Face 1)
                status_text = "[dim white]IMMUNE[/]"
                inf_color = "white"
            else: 
                status_text = "[dim]WARMING[/]"
                inf_color = "dim white"
                filled_blocks = 0
            
            bar_str = f"[{inf_color}]{'█' * filled_blocks}[/][dim white]{'░' * (bar_len - filled_blocks)}[/]"
            
            rel_str = f"[{'green' if rel >= 95 else 'yellow' if rel >= 80 else 'red'}]{rel:.1f}[/]"
            luck_str = f"[{'bright_magenta' if luck >= 150 else 'cyan' if luck >= 100 else 'white'}]{luck:.1f}[/]"
            inf_str = f"[{inf_color}]{inf:.1f}[/]" 
            
            rnd_diff = float(m.get('round_best_diff', 0.0))
            best_str = luck_engine.format_diff_scaled(rnd_diff) if rnd_diff > 0 else "[dim]--[/]"
            
            table.add_row(
                f"[{color}]{tag}[/]", 
                coin_tag,
                rel_str,
                luck_str,
                inf_str,
                bar_str,       # VIRAL LOAD
                status_text,   # STAGE
                best_str
            )
            
    if bch_fleet: render_fleet_rows(bch_fleet)
    if btc_fleet:
        if bch_fleet: table.add_section() 
        render_fleet_rows(btc_fleet)

    # --- AGGREGATE STATS ---
    lt = swarm_state.get("swarm_lifetime_hits", {"b": 0, "s": 0, "m": 0, "g": 0, "t": 0, "blocks": 0})
    tot_b = lt.get('b', 0) + sum(m.get('b_hits', 0) for m in active_miners)
    tot_s = lt.get('s', 0) + sum(m.get('s_hits', 0) for m in active_miners)
    tot_m = lt.get('m', 0) + sum(m.get('m_hits', 0) for m in active_miners)
    tot_g = lt.get('g', 0) + sum(m.get('g_hits', 0) for m in active_miners)
    tot_t = lt.get('t', 0) + sum(m.get('t_hits', 0) for m in active_miners)
    tot_blocks = lt.get('blocks', 0) + sum(m.get('blocks', 0) for m in active_miners)

    agg_hits_str = (
        f"[bold white]{tot_b}B[/]  "
        f"[bold yellow]{tot_s}S[/]  "
        f"[bold green]{tot_m}M[/]  "
        f"[bold cyan]{tot_g}G[/]  "
        f"[bold bright_magenta]{tot_t}T[/]"
    )
    if tot_blocks > 0: agg_hits_str += f"  [bold gold1]🏆 {tot_blocks}[/]"

    wrapper = Table.grid(expand=True)
    wrapper.add_row(table)
    wrapper.add_row("") 
    
    swarm_banner = f"[bold dim white]LIFETIME SWARM GROWTH HITS:[/] {agg_hits_str}"
    wrapper.add_row(Align.center(swarm_banner))
    wrapper.add_row("") 
    wrapper.add_row(Align.center("[bold dim white]--- GAMIFICATION ENGINE ---[/]"))
    
    # --- LEGEND UPDATE ---
    leg_grid = Table.grid(padding=(0, 1))
    leg_grid.add_column(justify="left")
    leg_grid.add_row("[dim]• [/][bold green]REL%[/][dim]:  Reliability (0-100%). Perfect hw uptime since joining the current 72H cycle.[/]")
    leg_grid.add_row("[dim]• [/][bold cyan]LUCK[/][dim]:  Hash Velocity. Live TH/s vs Hardware Specs. 100 = Expected pace.[/]")
    
    # CRITICAL FIX: Change to INF (LOG)
    leg_grid.add_row("[dim]• [/][bold bright_magenta]INF (LOG)[/][dim]:  Infection (0-100%). Logarithmic proximity of the RND BEST share to a Block Solve (100).[/]")
    
    leg_grid.add_row("[dim]• [/][bold yellow]STAGE[/][dim]: IMMUNE ➔ SNIFFLE ➔ INFECTIOUS ➔ VIRULENT ➔ EPIDEMIC ➔ BCH / SATOSHI[/]")
	
    wrapper.add_row(Align.center(leg_grid))
    wrapper.add_row("")

    window_sec = swarm_state.get("ladder_window_hours", 72) * 3600
    elapsed = time.time() - swarm_state.get("last_ladder_reset", time.time())
    rem_sec = max(0, window_sec - elapsed)
    h, m = int(rem_sec // 3600), int((rem_sec % 3600) // 60)
    
    wrapper.add_row(Align.center(f"⏳ [bold dim white]CYCLE ENDS IN:[/] [bold cyan]{h}h {m}m[/]"))

    # --- TOP PERFORMERS ---
    hot_leader = None
    best_hot_pts = 0
    rel_leader = None
    best_rel_pts = 0
    for miner in active_miners:
        hot, rel = miner.get('v4_hot_score', 0), miner.get('v4_best_score', 0)
        if hot > best_hot_pts: best_hot_pts, hot_leader = hot, miner
        if rel > best_rel_pts: best_rel_pts, rel_leader = rel, miner

    if hot_leader and best_hot_pts > 0:
        l_tag, l_coin = hot_leader.get('tag', '---'), hot_leader.get('coin_type', 'BTC')
        c_style = "[bold cyan]BCH[/]" if l_coin == "BCH" else "[bold orange3]BTC[/]"
        leader_banner = f"\n[bold bright_magenta]🔥 HOTTEST (VIRAL):[/] [{TYPE_COLORS.get(resolve_miner_type(hot_leader), 'white')} bold]{l_tag}[/] {c_style} [dim]({best_hot_pts:.1f} pts)[/]"
    else:
        leader_banner = f"\n[bold dim white]🔥 HOTTEST (VIRAL):[/] [dim italic]Awaiting infection...[/]"
        
    if rel_leader and best_rel_pts > 0:
        r_tag, r_coin = rel_leader.get('tag', '---'), rel_leader.get('coin_type', 'BTC')
        rc_style = "[bold cyan]BCH[/]" if r_coin == "BCH" else "[bold orange3]BTC[/]"
        leader_banner += f"   [dim]||[/]   [bold green]💎 BEST (STEADY):[/] [{TYPE_COLORS.get(resolve_miner_type(rel_leader), 'white')} bold]{r_tag}[/] {rc_style} [dim]({best_rel_pts:.1f} pts)[/]"

    wrapper.add_row(Align.center(leader_banner))

    # --- PREVIOUS WINNER ---
    archive = swarm_state.get("luck_archive", [])
    if archive:
        prev = archive[0]
        pts = prev.get('points', 0)
        pts_str = f"{pts:.1f}" if isinstance(pts, float) else str(pts)
        prev_banner = f"[dim]⏮ PREVIOUS WINNER:[/] [{prev.get('color', 'white')}]{prev.get('tag', '???')}[/] [dim]({pts_str} pts on {prev.get('date', '')})[/]"
    else:
        prev_banner = f"[dim]⏮ PREVIOUS WINNER:[/] [dim italic]Awaiting first cycle...[/]"
        
    wrapper.add_row(Align.center(prev_banner))

    return Panel(wrapper, title="[ 72H SILICON RELIABILITY & LUCK LADDER - THE LUCK VIRUS ]", border_style="yellow")

def luck_archive_panel():
    archive = swarm_state.get("luck_archive", [])
    
    if not archive:
        return Panel(Align.center("\n[dim italic]Compiling historical data...[/]\n"), title="[ LUCK ARCHIVE ]", border_style="magenta")
        
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_header=False)
    table.add_column("DATE", justify="left", width=4)
    table.add_column("TAG", justify="center", width=4)
    table.add_column("COIN", justify="center", width=4)
    table.add_column("POINTS", justify="right")
    
    # Display up to the last 6 entries to fit the panel size
    for entry in archive[:6]:
        date = entry.get("date", "--/--")
        tag = entry.get("tag", "---")
        color = entry.get("color", "white")
        coin = entry.get("coin", "BTC")
        coin_styled = "[bold cyan]BCH[/]" if coin == "BCH" else "[bold orange3]BTC[/]"
        
        pts = entry.get("points", 0)
        pts_str = f"{pts:.1f}" if isinstance(pts, float) else str(pts)
        
        table.add_row(
            f"[dim]{date}[/]",
            f"[{color} bold]{tag}[/]",
            coin_styled,
            f"[bold green]{pts_str} pts[/]"
        )
        
    return Panel(table, title="[ LUCK ARCHIVE ]", border_style="magenta")

async def fetch_network_stats():
    while swarm_state["run_loop"]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp_epoch = await client.get("https://mempool.space/api/v1/difficulty-adjustment")
                if resp_epoch.status_code == 200:
                    data = resp_epoch.json()
                    swarm_state["btc_epoch_progress"] = float(data.get("progressPercent", 0.0))
                    swarm_state["btc_epoch_change"] = float(data.get("difficultyChange", 0.0))
                    swarm_state["btc_blocks_left"] = int(data.get("remainingBlocks", 0))
                
                resp_btc = await client.get("https://api.blockchair.com/bitcoin/stats")
                resp_bch = await client.get("https://api.blockchair.com/bitcoin-cash/stats")
                
                if resp_btc.status_code == 200 and resp_bch.status_code == 200:
                    btc_d = float(resp_btc.json()["data"]["difficulty"])
                    bch_d = float(resp_bch.json()["data"]["difficulty"])
                    btc_p = float(resp_btc.json()["data"]["market_price_usd"])
                    bch_p = float(resp_bch.json()["data"]["market_price_usd"])
                    
                    luck_engine.net_diff = btc_d
                    luck_engine.bch_diff = bch_d
                    
                    # --- NEW: Push exact network targets to the data vault for V4 gamification ---
                    swarm_state["btc_net_diff"] = btc_d
                    swarm_state["bch_net_diff"] = bch_d
                    
                    btc_yield = btc_p / btc_d
                    bch_yield = bch_p / bch_d
                    swarm_state["bch_win_diff"] = (bch_yield / btc_yield) - 1.0
                    
                    bch_history = swarm_state.get("bch_diff_history_list", [])
                    bch_history.append(bch_d)
                    
                    if len(bch_history) > 6: 
                        bch_history.pop(0)
                    swarm_state["bch_diff_history_list"] = bch_history
                    
                    if len(bch_history) >= 2:
                        old_bch = bch_history[0]
                        swarm_state["bch_daa_velocity"] = ((bch_d - old_bch) / old_bch) * 100
                    
        except Exception as e:
            swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [dim]Net sync delayed: {e}[/]")
        
        await asyncio.sleep(600)

async def track_network_latency():
    while swarm_state["run_loop"]:
        try:
            start = time.perf_counter()
            reader, writer = await asyncio.wait_for(asyncio.open_connection('1.1.1.1', 53), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            swarm_state['ping_net'] = int((time.perf_counter() - start) * 1000)
        except Exception:
            swarm_state['ping_net'] = 999

        try:
            active_pool_url = None
            active_pool_port = 3333
            for m in swarm_state["miners"].values():
                if m.get('online') and m.get('stratumURL'):
                    url = m.get('stratumURL', '').replace('stratum+tcp://', '').replace('stratum+ssl://', '')
                    if url and "192.168" not in url and "127.0.0.1" not in url:
                        parts = url.split(':')
                        active_pool_url = parts[0]
                        if len(parts) > 1: active_pool_port = int(parts[1])
                        break
            
            if active_pool_url:
                start = time.perf_counter()
                reader, writer = await asyncio.wait_for(asyncio.open_connection(active_pool_url, active_pool_port), timeout=2.0)
                writer.close()
                await writer.wait_closed()
                swarm_state['ping_pool'] = int((time.perf_counter() - start) * 1000)
            else:
                swarm_state['ping_pool'] = 0 
        except Exception:
            swarm_state['ping_pool'] = 999
            
        await asyncio.sleep(5) 

def ensure_fleet_tags():
    try:
        used_tags = [m.get('tag') for m in swarm_state["miners"].values() if m.get('tag') and m.get('tag') != '---']
        sorted_ips = sorted(list(swarm_state["miners"].keys()))
        state_changed = False
        
        for ip in sorted_ips:
            m = swarm_state["miners"][ip]
            if m.get('tag') and m.get('tag') != '---': 
                continue 
                
            m_type = resolve_miner_type(m)
            if m.get('is_micro'): pfx = "MC"
            elif "NerdQAxe" in m_type or "NerdQaxe" in m_type: pfx = "NX"
            elif m_type == "Gamma": pfx = "GM"
            elif m_type == "GT800": pfx = "GT"
            else: pfx = "BX"
            
            idx = 1
            while f"{pfx}{idx}" in used_tags:
                idx += 1
                
            new_tag = f"{pfx}{idx}"
            m['tag'] = new_tag
            used_tags.append(new_tag)
            state_changed = True
            
        if state_changed:
            save_state()
    except Exception as e:
        swarm_state["debug_log"].append(f"[bold red]TAG ERROR:[/] {str(e)}")



async def instigate_hunt_controller(hunter):
    if swarm_state["is_hunting"]:
        hunter.abort_event.set()
        swarm_state["is_hunting"] = False
        return
        
    swarm_state["is_hunting"] = True
    swarm_state["scan_progress"] = 0
    swarm_state["new_miners_found"] = 0
    swarm_state["existing_miners_found"] = 0
    
    def hunt_logger(ip, status):
        if status == "probing":
            swarm_state["scan_progress"] += 1
        elif status == "Found!":
            if ip in swarm_state["miners"]: 
                swarm_state["existing_miners_found"] += 1
            else: 
                swarm_state["new_miners_found"] += 1

    found = await hunter.scan_network(logger=hunt_logger)
    
    if not hunter.abort_event.is_set():
        summary_list = []
        for m in found:
            ip = m.get('ip')
            m['type'] = resolve_miner_type(m) 
            
            is_new = ip not in swarm_state["miners"]
            summary_list.append({"ip": ip, "type": m.get("type", "Bitaxe"), "is_new": is_new})
            
            if is_new:
                m['cost'] = 0.0
                swarm_state["miners"][ip] = m
            else:
                swarm_state["miners"][ip].update(m)
                
        # --- THE FIX: Run the auto-tagger immediately after the hunt finishes ---
        ensure_fleet_tags()
        
        swarm_state["last_hunt_results"] = summary_list
        swarm_state["show_summary"] = True
        swarm_state["summary_timer"] = 10
        save_state()
        
        while swarm_state["summary_timer"] > 0 and swarm_state["run_loop"]:
            await asyncio.sleep(1)
            swarm_state["summary_timer"] -= 1
            
        swarm_state["show_summary"] = False
    swarm_state["is_hunting"] = False

async def handle_commands(hunter, live_handle):
    last_h_press = 0  
    
    while swarm_state["run_loop"]:
        try:
            cmd = await asyncio.wait_for(input_queue.get(), timeout=0.1)
            
            # Flush queue to prevent ghost keypresses
            while not input_queue.empty():
                input_queue.get_nowait()
                
            if cmd == 'q': 
                swarm_state["run_loop"] = False
                live_handle.stop()
                play_shutdown_sequence() 
                import os
                os._exit(0)
                
            elif cmd == 'h': 
                if time.time() - last_h_press > 1.5:
                    last_h_press = time.time()
                    swarm_state["trigger_hunt"] = True
                
            elif cmd in ['s', 'c', 'd', 'a']:
                swarm_state["is_inputting"] = True
                
                live_handle.stop()
                await asyncio.sleep(0.1) 
                console.clear()          
                
                try:
                    if cmd == 's': await handle_settings_input()
                    elif cmd == 'c': await handle_miner_cost_input()
                    elif cmd == 'd': await handle_miner_deletion()
                    elif cmd == 'a': await handle_miner_action()
                    save_state()
                finally:
                    console.clear()
                    live_handle.start()
                
                swarm_state["is_inputting"] = False
                
            elif cmd == 'p':
                swarm_state['solar_mode'] = not swarm_state.get('solar_mode', False)
                save_state()
                
            # --- NEW: IP Privacy Toggle ---
            elif cmd == 'i':
                swarm_state['show_ips'] = not swarm_state.get('show_ips', False)
                save_state()
                
            elif cmd == 'r': 
                # --- VISUAL RECALIBRATION ONLY (Protecting the Data Model) ---
                live_val = swarm_state.get('total_btc_th', 0.0) + swarm_state.get('total_bch_th', 0.0)
                
                # Clean the Trend Charts & Reset Peak Memory
                if live_val > 1.0:
                    # Filter out massive unrealistic spikes (e.g. > 150% of current live value)
                    swarm_state["hashrate_history"] = [v for v in swarm_state.get("hashrate_history", []) if v < (live_val * 1.5)]
                    swarm_state["power_history"] = [p for p in swarm_state.get("power_history", []) if p < (swarm_state.get('total_power', 0) * 1.5)]
                    swarm_state["peak_th"] = live_val  
                    swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold cyan]INFO:[/] Trend chart and Peak recalibrated.")
                else:
                    swarm_state["hashrate_history"] = []
                    swarm_state["power_history"] = []
                    swarm_state["peak_th"] = 0.0
                
                # We intentionally DO NOT wipe hits, shares, archives, or the epoch clock.
                # Data excellence is permanently preserved.
                save_state()
                
        except asyncio.TimeoutError: 
            continue

def swarm_intel_panel():
    net_ping = swarm_state.get('ping_net', '--')
    pool_ping = swarm_state.get('ping_pool', '--')

    grid = Table.grid(expand=True, padding=(0, 2, 0, 0))
    grid.add_column(ratio=1, no_wrap=True) 
    grid.add_column(ratio=1, no_wrap=True) 
    grid.add_column(ratio=1, no_wrap=True) 
    
    grid.add_row("[bold cyan]HASHRATE[/]", "[bold magenta]LATENCY[/]", "[bold orange3]LOAD[/]")
    
    grid.add_row(
        f"BTC: {swarm_state.get('total_btc_th', 0):.2f} TH",
        f"Net:  {net_ping}ms",
        f"Draw: {swarm_state.get('total_amps', 0):.2f} A"
    )
    
    grid.add_row(
        f"BCH: {swarm_state.get('total_bch_th', 0):.2f} TH",
        f"Pool: {pool_ping}ms",
        f"Total: {int(swarm_state.get('total_power', 0))} W"
    )
    
    grid.add_row("", "", "")
    
    grid.add_row("[bold green]OPEX[/]", "[bold dim white]SWARM PK[/]", "")
    
    grid.add_row(
        f"Daily: £{swarm_state.get('total_opex_daily', 0):.2f}",
        f"{swarm_state.get('peak_th', 0):.2f} TH/s",
        ""
    )
    
    return Panel(grid, title="[ SYSTEM MONITOR ]", border_style="magenta")

def efficiency_leaderboard_panel():
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_header=True, header_style="bold green")
    
    table.add_column("TAG", width=5, justify="center", no_wrap=True)
    table.add_column("SCORE", justify="center", width=5, no_wrap=True)
    table.add_column("TEMP", justify="center", width=5, no_wrap=True)
    table.add_column("FAN", justify="right", width=5, no_wrap=True) 
    table.add_column("J/TH", justify="center", width=6, no_wrap=True) 
    table.add_column("STABILITY", justify="left", ratio=1, no_wrap=True) 

    scored_fleet = []
    total_jth, active_count = 0.0, 0
    active_miners = [m for m in swarm_state["miners"].values() if m.get('online') and not m.get('is_micro')]
    
    for ip, m in swarm_state["miners"].items():
        if m.get('online') and not m.get('is_micro', False):
            raw_hr = float(m.get('hashRate', m.get('hashrate', 0.0)))
            
            if raw_hr > 1_000_000_000_000:       th_val = raw_hr / 1_000_000_000_000.0
            elif raw_hr > 100_000.0:             th_val = raw_hr / 1_000_000.0
            elif raw_hr > 50.0:                  th_val = raw_hr / 1000.0
            else:                                th_val = raw_hr
                
            power = float(m.get('power', m.get('powerW', 0.0)))
            jth = power / th_val if th_val > 0 else 0
            
            total_jth += jth
            active_count += 1
            
            temp = float(m.get('temp', m.get('coreTemp', 40)))
            freq = float(m.get('frequency', m.get('coreFreq', 1)))
            volt = float(m.get('coreVoltage', m.get('voltage', 1200)))
            fan = int(m.get('fanrpm', m.get('fanspeed', m.get('fan', 0)))) 
            
            j_score = max(0, 100 - (max(0, jth - 15) * 4)) 
            t_score = max(0, 100 - (max(0, temp - 55) * 5))
            v_score = min(100, (freq / volt) * 165) if volt > 0 else 0
            final_score = (j_score * 0.4) + (t_score * 0.3) + (v_score * 0.3)
            
            scored_fleet.append({
                "tag": m.get('tag', '---'), 
                "score": int(final_score),
                "temp": f"{temp:.0f}°C",
                "fan": f"{fan}",
                "elec": f"{jth:.1f}",
                "stability": int(min(100, v_score)),
                "color": "green" if final_score > 85 else "yellow" if final_score > 70 else "red"
            })
    
    sorted_fleet = sorted(scored_fleet, key=lambda x: x['score'], reverse=True)

    for m in sorted_fleet:
        bar_len = 10 
        filled = int((m['stability'] / 100) * bar_len)
        gauge_color = "bold cyan" if m['stability'] >= 90 else "bold yellow" if m['stability'] >= 75 else "bold red"
        bar = f"[{gauge_color}]{'/' * filled}[/][dim white]{'.' * (bar_len - filled)}[/]"
        
        table.add_row(
            f"[{m['color']}]{m['tag']}[/]", 
            f"[{m['color']}]{m['score']}[/]", 
            m['temp'], 
            f"[dim]{m['fan']}[/]", 
            m['elec'], 
            bar
        )

    if active_count > 0:
        avg = total_jth / active_count
        status = "[bold green]OPTIMISED[/]" if avg < 25 else "[bold yellow]TUNE[/]"
        table.add_section()
        table.add_row("[bold white]AVG[/]", "", "", "", f"[{'green' if avg < 20 else 'yellow'}]{avg:.1f}[/]", status)

    btc_diff_str = luck_engine.format_diff_scaled(luck_engine.net_diff) if luck_engine.net_diff > 0 else "[dim]Syncing...[/]"
    bch_diff_str = luck_engine.format_diff_scaled(luck_engine.bch_diff) if luck_engine.bch_diff > 0 else "[dim]Syncing...[/]"

    wrapper = Table.grid(expand=True)
    wrapper.add_row(table)
    wrapper.add_row("") 
    
    target_display = (
        f"[bold dim white]GLOBAL NETWORK TARGETS[/]\n"
        f"[bold orange3]BTC:[/] {btc_diff_str}   [dim]||[/]   [bold cyan]BCH:[/] {bch_diff_str}"
    )
    wrapper.add_row(Align.center(target_display))

    # ==========================================================
    # --- INTEGRATED SWARM TUNING ADVISORY ---
    # ==========================================================
    advice = []
    low_score_found = False
    best_efficiency = 999.0
    mvp_miner = None

    for m in active_miners:
        temp = float(m.get('temp', 0))
        jth = float(m.get('jth', 0))
        tag = m.get('tag', '---')
        
        if 0 < jth < best_efficiency:
            best_efficiency = jth
            mvp_miner = tag

        if temp > 72:
            advice.append(f"[bold red]⚠ {tag}:[/] High Thermals ({temp:.0f}°C). Check fan/airflow.")
            low_score_found = True
        
        if jth > 22.0:
            advice.append(f"[bold yellow]⚡ {tag}:[/] Efficiency Drift ({jth:.1f} J/TH). Suggest Undervolt.")
            low_score_found = True

    if mvp_miner:
        advice.append(f"[bold cyan]\U0001F48E {mvp_miner}:[/] Efficiency MVP ({best_efficiency:.1f} J/TH).")

    if not low_score_found and active_miners:
        advice.append("[bold green]✔ STABLE:[/] Swarm efficiency within nominal parameters.")
    elif not active_miners:
        advice.append("[dim]Waiting for ASIC telemetry...[/]")

    wrapper.add_row("")
    wrapper.add_row(Align.center("[bold dim white]- SWARM TUNING ADVISORY -[/]"))
    wrapper.add_row(Align.center("\n".join(advice)))

    return Panel(wrapper, title="[ FLEET HEALTH & EFFICIENCY ]", border_style="green", padding=(0,1))

def swarm_tuning_advisor():
    advice = []
    low_score_found = False
    best_efficiency = 999.0
    mvp_miner = None
    
    active_miners = [m for m in swarm_state["miners"].values() if m.get('online') and not m.get('is_micro')]

    for m in active_miners:
        temp = float(m.get('temp', 0))
        jth = m.get('jth', 0)
        tag = m.get('tag', '---')
        
        if 0 < jth < best_efficiency:
            best_efficiency = jth
            mvp_miner = tag

        if temp > 72:
            advice.append(f"[bold red]⚠ {tag}:[/] High Thermals ({temp:.0f}°C). Check fan/airflow.")
            low_score_found = True
        
        if jth > 22.0:
            advice.append(f"[bold yellow]⚡ {tag}:[/] Efficiency Drift ({jth:.1f} J/TH). Suggest Undervolt.")
            low_score_found = True

    if mvp_miner:
        advice.append(f"[bold cyan]\U0001F48E {mvp_miner}:[/] Efficiency MVP ({best_efficiency:.1f} J/TH).")

    if not low_score_found and active_miners:
        advice.append("[bold green]✔ STABLE:[/] Swarm efficiency within nominal ASIC parameters.")
    elif not active_miners:
        advice.append("[dim]Waiting for ASIC telemetry...[/]")

    return Panel("\n".join(advice), title="[ SWARM TUNING ADVISORY ]", border_style="magenta")

async def run_ui(live_handle, layout, hunter):
    while swarm_state["run_loop"]:
        
        if swarm_state.get("trigger_hunt", False):
            swarm_state["trigger_hunt"] = False
            asyncio.create_task(instigate_hunt_controller(hunter))
            
        if not swarm_state["is_inputting"]:
            
            layout["header"].update(create_header())
            layout["telemetry"].update(hardware_table())
            
            layout["health_col"].update(efficiency_leaderboard_panel())
            layout["luck_ladder"].update(luck_ladder_panel())
            
            layout["trend"].update(trend_panel(layout))
            layout["solar"].update(solar_saver_panel())
            
            layout["system"].update(swarm_intel_panel())
            layout["lottery"].update(lottery_analysis_panel())
            layout["podium"].update(investment_podium_panel())
            layout["archive"].update(luck_archive_panel()) 
                                         
            debug_logs = "\n".join(swarm_state["debug_log"]) if swarm_state["debug_log"] else "System stable. No alerts."
            layout["debug"].update(Panel(debug_logs, title="[ SYSTEM EVENT LOG ]", border_style="dim white"))
            
            footer_grid = Table.grid(expand=True)
            footer_grid.add_column(justify="left", ratio=1)
            footer_grid.add_column(justify="right", no_wrap=True)
            
            controls = "[bold cyan]H[/]UNT | [bold cyan]I[/]P | [bold cyan]S[/]ET | [bold cyan]C[/]OST | [bold cyan]P[/]OWER | [bold magenta]A[/]CTION | [bold yellow]R[/]ESET | [bold red]D[/]EL | [bold cyan]Q[/]UIT"
            donation = "[dim]Support Build (BTC): [link=bitcoin:bc1qnpn7svcrra6x6dvfcnxuzg3jdc9q08p8lpvzvy]bc1qnpn7svcrra6x6dvfcnxuzg3jdc9q08p8lpvzvy[/link][/dim]"
            
            footer_grid.add_row(controls, donation)
            
            layout["footer"].update(Panel(footer_grid, box=box.ASCII))
            
        await asyncio.sleep(0.1)

def keyboard_listener(loop):
    import sys
    import time
    is_windows = sys.platform == 'win32'
    
    if not is_windows:
        import termios, select
        fd = sys.stdin.fileno()
        oldterm = termios.tcgetattr(fd)
        
        # Helper to disable terminal echo (for dashboard mode)
        def set_raw():
            newattr = termios.tcgetattr(fd)
            newattr[3] = newattr[3] & ~termios.ICANON & ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, newattr)
            
        # Helper to restore terminal (for typing in menus)
        def set_normal():
            termios.tcsetattr(fd, termios.TCSANOW, oldterm)

    try:
        if not is_windows: 
            set_raw()
            
        was_inputting = False
        escape_seq = False
        
        while swarm_state["run_loop"]:
            is_in = swarm_state.get("is_inputting", False)
            
            # Smart-toggle the terminal state if we open a menu
            if is_in != was_inputting:
                if not is_windows:
                    if is_in: set_normal()
                    else: set_raw()
                was_inputting = is_in
                
            if not is_in:
                key = None
                if is_windows:
                    import msvcrt
                    if msvcrt.kbhit():
                        try: key = msvcrt.getch().decode('utf-8').lower()
                        except: pass
                else:
                    import select
                    # Wait efficiently for input instead of redlining the CPU
                    dr, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if dr:
                        c = sys.stdin.read(1)
                        if c == '\x1b':  # ESC character starts a mouse/arrow sequence
                            escape_seq = True
                        elif escape_seq:
                            # Sequences end with a letter or tilde, swallow everything until then
                            if c.isalpha() or c == '~':
                                escape_seq = False
                        else:
                            key = c.lower()
                            
                if key and not escape_seq:
                    try: loop.call_soon_threadsafe(input_queue.put_nowait, key)
                    except: pass
            else:
                time.sleep(0.05)
    finally:
        # Guarantee the terminal is restored to normal when the app quits
        if not is_windows:
            try: set_normal()
            except: pass

def show_boot_sequence():
    console.clear()
    logo = """[bold cyan]
    ██████╗ ██╗      ███████╗██╗ ██████╗ 
    ██╔══██╗██║      ██╔════╝██║██╔════╝ 
    ██████╔╝██║      ███████╗██║██║      
    ██╔══██╗██║      ╚════██║██║██║      
    ██████╔╝███████╗ ███████║██║╚██████╗ 
    ╚═════╝ ╚══════╝ ╚══════╝╚═╝ ╚═════╝ [/]
    [bold magenta] BITCOIN SWARM INTELLIGENCE CONSOLE [/]"""
    console.print(Align.center(logo))
    time.sleep(0.5)

def play_shutdown_sequence():
    console.clear()
    time.sleep(0.2)
    
    lines = [
        "[bold red]SYSTEM TERMINATION INITIATED...[/]",
        "[dim]Severing Stratum pool uplinks...[/]",
        "[dim]Powering down ASIC cores...[/]",
        "[dim]Committing neural hash state to local drive...[/]",
        "[bold yellow]HALTING STRATEGY ENGINE...[/]",
        "",
        "[bold green]FLEET DISCONNECTED.[/]",
        "",
        "[bold cyan]A STRANGE GAME.[/]",
        "[bold cyan]THE ONLY WINNING MOVE IS TO PLAY.[/]",
        "",
        "[bold dim]Goodbye.[/]"
    ]
    
    for line in lines:
        console.print(Align.center(line))
        time.sleep(0.35)
        
    time.sleep(0.5)

async def main():
    global loop
    loop = asyncio.get_running_loop()
    load_state()
    swarm_state["detected_local_ip"] = get_local_ip()
    
    ensure_fleet_tags() 
    
    hunter = SwarmHunter(subnet=".".join(swarm_state["detected_local_ip"].split(".")[:-1]))
    threading.Thread(target=keyboard_listener, args=(loop,), daemon=True).start()
    
    layout = make_layout()
    show_boot_sequence()
    
    with Live(layout, refresh_per_second=10, screen=True) as live:
        await asyncio.gather(
            run_ui(live, layout, hunter),  
            handle_commands(hunter, live), 
            update_known_miners(),
            fetch_network_stats(),
            track_network_latency()  
        )

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): 
        pass
    except Exception as e:
        import traceback
        console.print(f"\n[bold red]CRITICAL CRASH:[/]\n")
        traceback.print_exc()
        input("\nPress Enter to exit...")
    finally: 
        save_state()
        console.show_cursor(True)
        import os, sys
        os._exit(0)