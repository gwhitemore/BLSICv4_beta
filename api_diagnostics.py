import httpx
import asyncio
import json
import sys
from pathlib import Path

# Use the rich library from your main project for clean terminal output
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
except ImportError:
    print("[!] Missing 'rich' library. Run: pip install rich")
    sys.exit(1)

console = Console()

def resolve_miner_type(data):
    """Simulates the hardware resolver from the main dashboard."""
    device_model = str(data.get('deviceModel', '')).lower()
    hostname = str(data.get('hostname', '')).lower()
    board_ver = str(data.get('boardVersion', ''))
    
    if 'nerdminer' in device_model or 'micro' in hostname or 'nerd' in hostname:
        if 'nerdqaxe' not in hostname and 'nerdqaxe' not in device_model:
            return 'NerdMiner'
    if 'nerdqaxe' in hostname or 'nerdqaxe' in device_model: return 'NerdQAxe++'
    if board_ver == '800' or 'gt800' in device_model or 'gt800' in hostname: return 'GT800'
    if board_ver == '601' or 'gamma' in device_model or 'gamma' in hostname: return 'Gamma'
    if board_ver == '201' or 'bitaxe' in device_model or 'bitaxe' in hostname: return 'Bitaxe'
    return data.get('type', 'Unknown')

def safe_num(val, cast_type=float, default=0):
    try:
        if val is None or val == "": return default
        return cast_type(val)
    except Exception: return default

def get_sh(obj, key):
    if not isinstance(obj, dict): return 0
    try:
        v = obj.get(key)
        if v is not None and str(v).strip() != "":
            return int(float(v))
    except: pass
    return 0

async def run_diagnostics(ip):
    console.print(Panel(f"[bold cyan]Initiating API Diagnostics for {ip}[/]", border_style="cyan"))
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://{ip}/api/system/info", timeout=10.0)
            if resp.status_code != 200:
                console.print(f"[bold red]HTTP Error {resp.status_code}[/]")
                return
            data = resp.json()
            
        # 1. Save Raw Dump
        dump_file = Path(f"debug_dump_{ip.replace('.', '_')}.json")
        with open(dump_file, "w") as f:
            json.dump(data, f, indent=4)
        console.print(f"[bold green]✔ Raw payload saved to {dump_file.name}[/]")

        # 2. Hardware Profiling
        hw_type = resolve_miner_type(data)
        asic_count = data.get('asicCount', 'Unknown')
        fw_ver = data.get('version', data.get('axeOSVersion', 'Unknown'))
        
        info_table = Table(show_header=False, box=None)
        info_table.add_row("[bold white]Detected Hardware:[/]", f"[magenta]{hw_type}[/]")
        info_table.add_row("[bold white]ASIC Count:[/]", str(asic_count))
        info_table.add_row("[bold white]Firmware Version:[/]", str(fw_ver))
        console.print(Panel(info_table, title="[ SYSTEM PROFILE ]", border_style="magenta"))

        # 3. Dashboard Extraction Test
        console.print("\n[bold yellow]Running Dashboard Parser Simulation...[/]")
        test_table = Table(box=None, expand=True)
        test_table.add_column("METRIC", style="cyan", width=20)
        test_table.add_column("EXTRACTED VALUE", style="white")
        test_table.add_column("STATUS", justify="right")

        def add_result(metric, value, is_valid):
            status = "[bold green]✔ PASS[/]" if is_valid else "[bold red]✖ FAIL[/]"
            test_table.add_row(metric, str(value), status)

        # Temp Check
        core_t = safe_num(data.get('coreTemp'), float)
        if core_t <= 0: core_t = safe_num(data.get('temp'), float)
        if core_t <= 0: core_t = safe_num(data.get('boardTemp'), float)
        add_result("Core Temp", f"{core_t}°C", core_t > 0)

        # Hashrate Check
        raw_hr = safe_num(data.get('hashRate'), float)
        if raw_hr <= 0: raw_hr = safe_num(data.get('hashrate'), float)
        add_result("Raw Hashrate", f"{raw_hr} H/s", raw_hr > 0)

        # Power Check
        power = safe_num(data.get('power'), float)
        if power <= 0: power = safe_num(data.get('powerW'), float)
        if power <= 0: power = safe_num(data.get('wattage'), float)
        add_result("Power Draw", f"{power} W", power > 0)

        # Session Diff Check
        st = data.get('stratum', {})
        current_sess = safe_num(data.get('bestSessionDiff'), float)
        if current_sess == 0.0 and isinstance(st, dict):
            current_sess = safe_num(st.get('bestSessionDiff'), float)
        add_result("Best Session Diff", current_sess, current_sess > 0)

        # Shares Check (Smart Delta Sim)
        c_acc = max(get_sh(data, 'sharesAccepted'), get_sh(data, 'accepted'))
        if isinstance(st, dict):
            c_acc = max(c_acc, get_sh(st, 'sharesAccepted'), get_sh(st, 'accepted'))
            if isinstance(st.get('pools'), list):
                for p in st['pools']:
                    c_acc = max(c_acc, get_sh(p, 'sharesAccepted'), get_sh(p, 'accepted'))
        
        pl = data.get('pool')
        if isinstance(pl, dict):
            c_acc = max(c_acc, get_sh(pl, 'sharesAccepted'), get_sh(pl, 'accepted'))
            
        add_result("Shares Accepted", c_acc, c_acc >= 0)

        # Stratum Check
        url = str(data.get('stratumURL', st.get('url', ''))).lower()
        add_result("Stratum URL", url if url else "Not Found", len(url) > 0)

        console.print(test_table)

    except Exception as e:
        import traceback
        console.print(f"\n[bold red]CRITICAL EXTRACTION CRASH:[/]")
        traceback.print_exc()

if __name__ == "__main__":
    console.clear()
    target = console.input("\n[bold yellow]Enter Target Miner IP:[/] ").strip()
    if target:
        asyncio.run(run_diagnostics(target))