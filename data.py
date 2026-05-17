import json
import time
import math
import threading
import socket
import asyncio
from pydantic import BaseModel  
from src.discovery.hunter import SwarmHunter
from datetime import datetime
from pathlib import Path
from collections import deque
from fastapi import FastAPI, Request
import uvicorn
import httpx
from concurrent.futures import ThreadPoolExecutor

state_lock = threading.Lock()
save_executor = ThreadPoolExecutor(max_workers=1)

# --- CONFIG & PERSISTENCE ---
SAVE_FILE = Path("swarm_config.json")
MAX_HISTORY = 1000

# The Single Source of Truth for the Swarm
swarm_state = {
    "miners": {}, 
    "hashrate_history": deque(maxlen=MAX_HISTORY), 
    "power_history": deque(maxlen=MAX_HISTORY),
    "peak_th": 0.0,
    "elec_cost": 0.22,
    "sun_hours": 4.0,
    "solar_panel_watts": 475.0,
    "solar_panel_count": 19.0,
    "battery_kwh_per": 5.3,
    "battery_count": 2.0,
    "inverter_kw": 10.0,
    "wind_kw": 3.0,
    "is_hunting": False,
    "is_inputting": False, 
    "show_summary": False,
    "summary_timer": 0,
    "last_hunt_results": [],
    "current_scan_ip": "Idle", 
    "scan_progress": 0,    
    "new_miners_found": 0,
    "existing_miners_found": 0,
    "detected_local_ip": "Unknown",
    "total_btc_th": 0.0,
    "total_bch_th": 0.0,
    "total_power": 0.0,
    "total_amps": 0.0,
    "total_opex_daily": 0.0,
    "run_loop": True,
    "debug_log": deque(maxlen=6),
    "last_best_share": 0.0,
    "flash_timer": 0,
    "btc_epoch_progress": 0.0,
    "btc_epoch_change": 0.0,
    "btc_blocks_left": 0,
    "ladder_window_hours": 72,
    "last_3_winners": [], 
    "monthly_winner": {},
    "last_ladder_reset": time.time(),
    "telemetry_epoch": time.time(),
    "luck_archive": [],
    "fortnight_winner": {},
    "ping_net": 0,
    "ping_pool": 0,
    "bch_win_diff": 0.0,
    "bch_diff_history_list": [],
    "total_shares_acc": 0,
    "total_shares_rej": 0,
    "swarm_lifetime_hits": {"b": 0, "s": 0, "m": 0, "g": 0, "t": 0, "blocks": 0, "points": 0},
	"maintenance": {},
	"ambient_temp": 0.0,
	"system_booting": True  # <--- NEW: Engage the Boot Lock at startup
}

def save_state(shutdown=False):
    try:
        with state_lock:
            payload = {
                "miners": swarm_state["miners"],
                "hashrate_history": list(swarm_state["hashrate_history"]), 
                "power_history": list(swarm_state["power_history"]),
                "peak_th": swarm_state["peak_th"],
                "elec_cost": swarm_state["elec_cost"],
                "sun_hours": swarm_state["sun_hours"],
                "solar_panel_watts": swarm_state.get("solar_panel_watts", 475.0),
                "solar_panel_count": swarm_state.get("solar_panel_count", 19.0),
                "battery_kwh_per": swarm_state.get("battery_kwh_per", 5.3),
                "battery_count": swarm_state.get("battery_count", 2.0),
                "inverter_kw": swarm_state.get("inverter_kw", 10.0),
                "wind_kw": swarm_state.get("wind_kw", 3.0),
                "luck_archive": swarm_state.get("luck_archive", []),
                "monthly_winner": swarm_state.get("monthly_winner", {}),
                "fortnight_winner": swarm_state.get("fortnight_winner", {}),
                "last_ladder_reset": swarm_state.get("last_ladder_reset", time.time()),
                "telemetry_epoch": swarm_state.get("telemetry_epoch", time.time()),
                "total_shares_acc": swarm_state.get("total_shares_acc", 0),
                "total_shares_rej": swarm_state.get("total_shares_rej", 0),
                "swarm_lifetime_hits": swarm_state.get("swarm_lifetime_hits", {"b": 0, "s": 0, "m": 0, "g": 0, "t": 0, "blocks": 0, "points": 0}),
                "show_ips": swarm_state.get("show_ips", False),
                "maintenance": swarm_state.get("maintenance", {})
            }
            
            state_json = json.dumps(payload, indent=4)
        
        if shutdown:
            # --- CRITICAL FIX: Synchronous blocking write for system exit ---
            SAVE_FILE.write_text(state_json, encoding='utf-8')
        else:
            # ThreadPoolExecutor asynchronous write for UI fluidity
            save_executor.submit(lambda: SAVE_FILE.write_text(state_json, encoding='utf-8'))
            
    except Exception as e: 
        with state_lock:
            swarm_state["debug_log"].append(f"[bold red]Save Error:[/] {str(e)}")

def load_state():
    if SAVE_FILE.exists():
        try:
            with open(SAVE_FILE, "r") as f:
                data = json.load(f)
                with state_lock:
                    if "hashrate_history" in data:
                        swarm_state["hashrate_history"] = deque(data["hashrate_history"], maxlen=MAX_HISTORY)
                    
                    for k in ["peak_th", "elec_cost", "sun_hours", "luck_archive", 
                              "monthly_winner", "fortnight_winner", "last_ladder_reset", "telemetry_epoch",
                              "total_shares_acc", "total_shares_rej", "swarm_lifetime_hits", "show_ips", "maintenance",
                              "solar_panel_watts", "solar_panel_count", "battery_kwh_per", "battery_count", "inverter_kw", "wind_kw"]:
                        if k in data:
                            swarm_state[k] = data[k]
                    
                    if "power_history" in data:
                        swarm_state["power_history"] = deque(data["power_history"], maxlen=MAX_HISTORY)
                    
                    if "miners" in data:
                        swarm_state["miners"] = data["miners"]
        except Exception as e: 
            with state_lock:
                swarm_state["debug_log"].append(f"[bold red]Load Error:[/] {str(e)}")
            
    with state_lock:
        if "last_ladder_reset" not in swarm_state or swarm_state["last_ladder_reset"] == 0:
            swarm_state["last_ladder_reset"] = time.time()

def safe_num(val, cast_type=float, default=0):
    try:
        if val is None or val == "": return default
        return cast_type(val)
    except Exception:
        return default

def visual_recalibrate():
    with state_lock:
        live_val = swarm_state.get('total_btc_th', 0.0) + swarm_state.get('total_bch_th', 0.0)
        if live_val > 1.0:
            swarm_state["hashrate_history"] = deque([v for v in swarm_state.get("hashrate_history", []) if v < (live_val * 1.5)], maxlen=MAX_HISTORY)
            swarm_state["power_history"] = deque([p for p in swarm_state.get("power_history", []) if p < (swarm_state.get('total_power', 0) * 1.5)], maxlen=MAX_HISTORY)
            swarm_state["peak_th"] = live_val  
            swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold cyan]INFO:[/] Trend chart and Peak recalibrated.")
        else:
            swarm_state["hashrate_history"] = deque(maxlen=MAX_HISTORY)
            swarm_state["power_history"] = deque(maxlen=MAX_HISTORY)
            swarm_state["peak_th"] = 0.0
    save_state()

def calculate_v4_metrics(miner, net_diff, round_start_epoch):
    now = time.time()
    
    # 1. RELIABILITY (The Bulletproof Downtime Method)
    join_epoch = safe_num(miner.get('round_join_epoch', now), float, now)
    effective_start = max(round_start_epoch, join_epoch)
    expected_seconds = max(now - effective_start, 1.0)
    
    downtime = safe_num(miner.get('accumulated_downtime', 0.0), float, 0.0)
    last_seen = safe_num(miner.get('last_seen_timestamp', now), float, now)
    
    # If the miner is currently disconnected, calculate the live penalty (with a 2-min grace period)
    live_downtime = max(0.0, now - last_seen - 120.0)
    total_downtime = downtime + live_downtime
    
    availability = max(0.0, (expected_seconds - total_downtime) / expected_seconds)
    rel_score = max(0.0, min(100.0, availability * 100.0))

    # 2. TRUE LUCK (Dynamic Hash Velocity)
    th_hashrate = safe_num(miner.get('th'), float, 0.0)
    raw_exp = safe_num(miner.get('expectedHashrate'), float, 0.0)
    
    if raw_exp > 1e12: exp_th = raw_exp / 1e12
    elif raw_exp > 100_000: exp_th = raw_exp / 1e6
    elif raw_exp > 50: exp_th = raw_exp / 1000.0
    elif raw_exp > 0: exp_th = raw_exp
    else:
        model = str(miner.get('deviceModel', '')).lower()
        host = str(miner.get('hostname', '')).lower()
        freq = safe_num(miner.get('frequency'), float, 0.0)
        
        if 'nerdqaxe' in model or 'nerdqaxe' in host:
            if freq > 0: exp_th = (freq / 600.0) * 4.9 
            else: exp_th = 4.9 
        elif 'gamma' in model or 'gamma' in host: exp_th = 1.2
        elif 'gt800' in model or 'gt800' in host: exp_th = 0.8
        else: exp_th = 0.5
        
    if exp_th > 0 and th_hashrate > 0:
        luck_score = (th_hashrate / exp_th) * 100.0
    else:
        luck_score = 100.0
        
    luck_score = max(0.0, min(999.9, luck_score))
    if math.isnan(luck_score): luck_score = 100.0

    # 3. PURE INFECTION (Strictly Round Best)
    round_best = safe_num(miner.get('round_best_diff'), float, 0.0)
    safe_net = max(safe_num(net_diff, float, 1_000_000.0), 1_000_000.0)
    
    min_log = math.log10(10_000_000.0)
    max_log = math.log10(safe_net)
    current_log = math.log10(max(round_best, 1.0))
    
    blocks = safe_num(miner.get('blocks'), int, 0)
    
    if blocks > 0 and round_best < 1_000_000_000.0:
        blocks = 0
        
    if blocks > 0:
        inf_score = 100.0
    elif current_log > min_log:
        if max_log > min_log:
            inf_score = 100 * ((current_log - min_log) / (max_log - min_log))
        else:
            inf_score = 0.0
    else:
        inf_score = 0.0
        
    inf_score = max(0.0, min(100.0, inf_score))
    
    # 4. OVERALL SCORES
    best_miner_score = (0.65 * rel_score) + (0.35 * luck_score)
    hottest_miner_score = (0.40 * luck_score) + (0.60 * inf_score)
    
    return rel_score, luck_score, inf_score, best_miner_score, hottest_miner_score

def check_and_trigger_72h_reset():
    from main_ui import resolve_miner_type, TYPE_COLORS 
    
    now = time.time()
    round_start_epoch = swarm_state.get("last_ladder_reset", now)
    window_seconds = swarm_state.get("ladder_window_hours", 72) * 3600
    elapsed_round = now - round_start_epoch
    
    btc_diff = swarm_state.get("btc_net_diff", 100_000_000_000_000.0)
    bch_diff = swarm_state.get("bch_net_diff", 500_000_000_000.0)
    
    for ip, m in swarm_state["miners"].items():
        if not m.get('is_micro'):
            if 'round_join_epoch' not in m:
                m['round_join_epoch'] = now
                
            target_diff = bch_diff if m.get('coin_type') == 'BCH' else btc_diff
            
            try:
                rel, luck, inf, best_m, hot_m = calculate_v4_metrics(m, target_diff, round_start_epoch)
                m['v4_rel'] = round(rel, 1)
                m['v4_luck'] = round(luck, 1)
                m['v4_inf'] = round(inf, 1)
                m['v4_best_score'] = round(best_m, 1)
                m['v4_hot_score'] = round(hot_m, 1)
            except Exception:
                pass
    
    if elapsed_round > window_seconds:
        best_miner = None
        best_points = -1
        
        for ip, m in swarm_state["miners"].items():
            if m.get('online') and not m.get('is_micro'):
                pts = m.get('v4_hot_score', 0)
                if pts > best_points:
                    best_points = pts
                    best_miner = m
                    
        if best_miner and best_points > 0:
            new_win = {
                "tag": best_miner.get("tag", "???"),
                "points": best_points,
                "date": datetime.now().strftime("%d/%m"),
                "coin": best_miner.get("coin_type", "BTC"),
                "color": TYPE_COLORS.get(resolve_miner_type(best_miner), "white"),
                "best_diff": float(best_miner.get("round_best_diff", 0.0))
            }
            archive = swarm_state.get("luck_archive", [])
            archive.insert(0, new_win)
            swarm_state["luck_archive"] = archive[:10] 
            
            f_archive = swarm_state["luck_archive"][:5]
            f_counts = {}
            for w in f_archive: f_counts[w['tag']] = f_counts.get(w['tag'], 0) + 1
            if f_counts: swarm_state["fortnight_winner"] = {"tag": max(f_counts, key=f_counts.get), "wins": f_counts[max(f_counts, key=f_counts.get)]}
            
            m_counts = {}
            for w in swarm_state["luck_archive"]: m_counts[w['tag']] = m_counts.get(w['tag'], 0) + 1
            if m_counts: swarm_state["monthly_winner"] = {"tag": max(m_counts, key=m_counts.get), "wins": m_counts[max(m_counts, key=m_counts.get)]}

        cycle_b = cycle_s = cycle_m = cycle_g = cycle_t = cycle_blocks = 0
        for m in swarm_state["miners"].values():
            if not m.get('is_micro'):
                cycle_b += m.get('b_hits', 0)
                cycle_s += m.get('s_hits', 0)
                cycle_m += m.get('m_hits', 0)
                cycle_g += m.get('g_hits', 0)
                cycle_t += m.get('t_hits', 0)
                cycle_blocks += m.get('blocks', 0)
        
        cycle_pts = (cycle_b*1) + (cycle_s*5) + (cycle_m*20) + (cycle_g*100) + (cycle_t*500) + (cycle_blocks*5000)
        
        lt = swarm_state.get("swarm_lifetime_hits", {"b": 0, "s": 0, "m": 0, "g": 0, "t": 0, "blocks": 0, "points": 0})
        lt["b"] += cycle_b
        lt["s"] += cycle_s
        lt["m"] += cycle_m
        lt["g"] += cycle_g
        lt["t"] += cycle_t
        lt["blocks"] += cycle_blocks
        lt["points"] += cycle_pts
        swarm_state["swarm_lifetime_hits"] = lt

        for m in swarm_state["miners"].values():
            for k in ['b_hits', 's_hits', 'm_hits', 'g_hits', 't_hits', 'round_acc', 'round_uptime', 'accumulated_downtime', 'round_best_diff', 'blocks']:
                m[k] = 0
            m['round_join_epoch'] = now
            m['last_seen_timestamp'] = now 

        # --- THE FIX: Wipe the global gamification tracker for the new session ---
        swarm_state["last_best_share"] = 0.0

        swarm_state["last_ladder_reset"] = now
        swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold yellow]META:[/] V4 72H Cycle Complete. Round variables reset.")
        save_state()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception: return "127.0.0.1"

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

def auto_detect_coin(url, user, port):
    url = url.lower()
    user = user.lower()
    
    # Tier 1: URL Ticker Match (for public pools)
    if "bch" in url: return "BCH"
    if "btc" in url: return "BTC"
    if "fnc" in url or "fractal" in url: return "FNC"
    if "xec" in url or "ecash" in url: return "XEC"
    
    # Tier 2: Port Match (crucial for local 192.168.x.x nodes)
    # BCH standard ports on local proxies / solo pools
    if port in [3337, 3338] or ":3337" in url or ":3338" in url: 
        return "BCH"
    # BTC standard ports
    if port in [3333, 8332, 8333]: 
        return "BTC"
        
    # Tier 3: Wallet Address Pattern Match (ONLY triggered if len > 25)
    # This prevents pool usernames like 'paul.worker' from matching BCH 'p' rule!
    if len(user) > 25:
        if user.startswith("bitcoincash:") or user.startswith("q") or user.startswith("p"):
            return "BCH"
        if user.startswith("bc1") or user.startswith("1") or user.startswith("3"):
            return "BTC"
        if user.startswith("ecash:") or user.startswith("e"):
            return "XEC"
            
    # Default Fallback
    return "BTC"


async def update_known_miners():
    swarm_state["detected_local_ip"] = get_local_ip()
    
    async def fetch_api(ip, client):
        try:
            resp = await client.get(f"http://{ip}/api/system/info")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    async with httpx.AsyncClient(headers={"Connection": "close"}, timeout=8.0) as client:
        while swarm_state["run_loop"]:
            try:
                with state_lock:
                    is_hunting = swarm_state["is_hunting"]
                    show_summary = swarm_state["show_summary"]
                    miner_ips = list(swarm_state["miners"].keys()) if swarm_state["miners"] else []
                    
                if not is_hunting and miner_ips and not show_summary:
                    tasks = [fetch_api(ip, client) for ip in miner_ips]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    btc_th = bch_th = other_th = total_pw = 0
                    state_changed = False
                    now = time.time()
                    
                    with state_lock:
                        for ip, data in zip(miner_ips, results):
                            if ip not in swarm_state["miners"]: continue
                            miner = swarm_state["miners"][ip]
                    
                            if not isinstance(data, dict):
                                if miner.get('online', False):
                                    swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold yellow]WARN:[/] {miner.get('hostname', ip)} connection dropped.")
                                miner['online'] = False
                                continue
                        
                            try:
                                if not miner.get('online', False):
                                    swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold green]RECOV:[/] {miner.get('hostname', ip)} back online.")
                        
                                miner.update(data)
                        
                                # --- THE BULLETPROOF REL% FIX: Track Downtime, Not Uptime ---
                                last_seen = miner.get('last_seen_timestamp', now)
                                time_since_last_seen = now - last_seen
                        
                                # If it took more than 2 minutes to hear back, it was offline! Penalize it.
                                if time_since_last_seen > 120 and miner.get('round_join_epoch'):
                                    miner['accumulated_downtime'] = miner.get('accumulated_downtime', 0.0) + (time_since_last_seen - 120)
                            
                                miner['last_seen_timestamp'] = now
                                miner['online'] = True
                        
                                m_type = resolve_miner_type(miner)
                                miner['is_micro'] = True if m_type == "NerdMiner" else False

                                expected_raw = safe_num(data.get('expectedHashrate'), float)
                                if expected_raw > 1_000_000_000_000: exp_th = expected_raw / 1e12
                                elif expected_raw > 100_000.0:       exp_th = expected_raw / 1e6
                                elif expected_raw > 50.0:            exp_th = expected_raw / 1000.0
                                else:                                exp_th = expected_raw
                                miner['expected_th'] = exp_th
                        
                                p_diff = safe_num(data.get('difficulty'), float)
                                if p_diff == 0: p_diff = safe_num(data.get('stratumDifficulty'), float)
                                if p_diff == 0 and isinstance(data.get('stratum'), dict):
                                    p_diff = safe_num(data.get('stratum').get('difficulty'), float)
                                if p_diff == 0: p_diff = 4096.0
                                miner['pool_diff'] = p_diff

                                chips = 1
                                root_asics = data.get('asics')
                                hm = data.get('hashrateMonitor', {})
                        
                                if safe_num(data.get('asicCount'), int) > 0:
                                    chips = int(data.get('asicCount'))
                                elif isinstance(root_asics, list) and len(root_asics) > 0:
                                    chips = len(root_asics)
                                elif isinstance(hm, dict) and isinstance(hm.get('asics'), list) and len(hm.get('asics')) > 0:
                                    chips = len(hm.get('asics'))
                                else:
                                    if m_type in ["NerdQAxe++", "NerdQaxe++"]:
                                        chips = 6
                                
                                miner['asicCount'] = chips

                                def get_sh(obj, key):
                                    if not isinstance(obj, dict): return 0
                                    try:
                                        v = obj.get(key)
                                        if v is not None and str(v).strip() != "":
                                            return int(float(v))
                                    except: pass
                                    return 0

                                c_acc = max(get_sh(data, 'sharesAccepted'), get_sh(data, 'accepted'))
                                c_rej = max(get_sh(data, 'sharesRejected'), get_sh(data, 'rejected'))
                        
                                st = data.get('stratum')
                                if isinstance(st, dict):
                                    c_acc = max(c_acc, get_sh(st, 'sharesAccepted'), get_sh(st, 'accepted'))
                                    c_rej = max(c_rej, get_sh(st, 'sharesRejected'), get_sh(st, 'rejected'))
                                    pools = st.get('pools')
                                    if isinstance(pools, list):
                                        for p in pools:
                                            c_acc = max(c_acc, get_sh(p, 'sharesAccepted'), get_sh(p, 'accepted'))
                                            c_rej = max(c_rej, get_sh(p, 'sharesRejected'), get_sh(p, 'rejected'))

                                pl = data.get('pool')
                                if isinstance(pl, dict):
                                    c_acc = max(c_acc, get_sh(pl, 'sharesAccepted'), get_sh(pl, 'accepted'))
                                    c_rej = max(c_rej, get_sh(pl, 'sharesRejected'), get_sh(pl, 'rejected'))

                                # ==========================================================
                                # --- THE VAULT: One-Way Lifetime Share Accumulator ---
                                # ==========================================================
                                if 'last_raw_acc' not in miner:
                                    miner['last_raw_acc'] = c_acc
                                    miner['last_raw_rej'] = c_rej
                                    if 'mem_acc' not in miner: miner['mem_acc'] = c_acc
                                    if 'mem_rej' not in miner: miner['mem_rej'] = c_rej
                                else:
                                    last_raw_acc = miner['last_raw_acc']
                                    last_raw_rej = miner['last_raw_rej']
                            
                                    if c_acc > last_raw_acc:
                                        acc_diff = c_acc - last_raw_acc
                                        miner['mem_acc'] = miner.get('mem_acc', 0) + acc_diff
                                        miner['round_acc'] = miner.get('round_acc', 0) + acc_diff 
                                        # Push new shares directly to the one-way Global Vault
                                        swarm_state['total_shares_acc'] = swarm_state.get('total_shares_acc', 0) + acc_diff
                                        state_changed = True
                                    elif c_acc < last_raw_acc: 
                                        miner['mem_acc'] = miner.get('mem_acc', 0) + c_acc
                                        miner['round_acc'] = miner.get('round_acc', 0) + c_acc 
                                        swarm_state['total_shares_acc'] = swarm_state.get('total_shares_acc', 0) + c_acc
                                        state_changed = True
                                
                                    if c_rej > last_raw_rej:
                                        rej_diff = c_rej - last_raw_rej
                                        miner['mem_rej'] = miner.get('mem_rej', 0) + rej_diff
                                        swarm_state['total_shares_rej'] = swarm_state.get('total_shares_rej', 0) + rej_diff
                                        state_changed = True
                                    elif c_rej < last_raw_rej:
                                        miner['mem_rej'] = miner.get('mem_rej', 0) + c_rej
                                        swarm_state['total_shares_rej'] = swarm_state.get('total_shares_rej', 0) + c_rej
                                        state_changed = True
                                
                                    miner['last_raw_acc'] = c_acc
                                    miner['last_raw_rej'] = c_rej

                                current_sess = safe_num(data.get('bestSessionDiff'), float)
                                if current_sess == 0.0 and isinstance(st, dict):
                                    current_sess = safe_num(st.get('bestSessionDiff'), float)
                            
                                last_sess = safe_num(miner.get('last_session_best'), float)
                        
                                for k in ['b_hits', 's_hits', 'm_hits', 'g_hits', 't_hits']:
                                    if k not in miner: miner[k] = 0
                            
                                if current_sess > last_sess:
                                    if current_sess >= 1_000_000_000_000:
                                        miner['t_hits'] += 1; state_changed = True
                                    elif current_sess >= 1_000_000_000:
                                        miner['g_hits'] += 1; state_changed = True
                                    elif current_sess >= 100_000_000:
                                        miner['m_hits'] += 1; state_changed = True
                                    elif current_sess >= 10_000_000:
                                        miner['s_hits'] += 1; state_changed = True
                                    elif current_sess >= 1_000_000:
                                        miner['b_hits'] += 1; state_changed = True
                                
                                    miner['last_session_best'] = current_sess
                            
                                    if current_sess > miner.get('round_best_diff', 0.0):
                                        miner['round_best_diff'] = current_sess
                                
                                elif 0 < current_sess < last_sess:
                                    miner['last_session_best'] = current_sess
                                    miner['round_best_diff'] = current_sess

                                blocks = safe_num(data.get('blockFound'), int)
                                blocks = max(blocks, safe_num(data.get('foundBlocks'), int))
                        
                                if miner.get('bestDiff', 0.0) < 1_000_000_000.0:
                                    blocks = 0
                            
                                miner['blocks'] = max(miner.get('blocks', 0), blocks)

                                core_t = safe_num(data.get('coreTemp'), float)
                                if core_t <= 0: 
                                    core_t = safe_num(data.get('temp'), float)
                                    if core_t <= 0: core_t = safe_num(data.get('boardTemp'), float)
                                miner['temp'] = core_t
                            
                                if core_t > 75 and miner.get('last_temp_alert', 0) < time.time() - 3600:
                                    swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold red]ALERT:[/] {miner['hostname']} high temp ({core_t}°C)")
                                    miner['last_temp_alert'] = time.time()

                                if not miner.get('is_micro'):
                                    raw_hr = safe_num(data.get('hashRate'), float)
                                    if raw_hr <= 0: raw_hr = safe_num(data.get('hashrate'), float)
                            
                                    power = safe_num(data.get('power'), float)
                                    if power <= 0: power = safe_num(data.get('powerW'), float)
                                    if power <= 0: power = safe_num(data.get('wattage'), float)
                            
                                    if raw_hr > 1_000_000_000_000:       th = raw_hr / 1_000_000_000_000.0
                                    elif raw_hr > 100_000.0:             th = raw_hr / 1_000_000.0
                                    elif raw_hr > 50.0:                  th = raw_hr / 1000.0
                                    else:                                th = raw_hr
                            
                                    # Note: total_pw is now calculated at the end of the loop
                                    miner['th'] = th
                                    miner['power'] = power
                                    miner['jth'] = power / th if th > 0 else 0
                                    miner['frequency'] = safe_num(data.get('frequency'), float, 1.0)
                                    miner['coreVoltage'] = safe_num(data.get('coreVoltage'), float, 1200.0)

                                    url = str(data.get('stratumURL', '')).lower()
                                    user = str(data.get('stratumUser', '')).lower()
                                    port = safe_num(data.get('stratumPort'), int)
 
                                    # Determine the coin type (prioritize manual override, fallback to auto-detection)
                                    forced_coin = miner.get('coin_override')
                                    if forced_coin and forced_coin.strip().upper() in ["BTC", "BCH", "FNC", "XEC", "BSV"]:
                                        coin = forced_coin.strip().upper()
                                    else:
                                        coin = auto_detect_coin(url, user, port)
                                
                                    miner['coin_type'] = coin

                                    if coin == "BCH": bch_th += th
                                    elif coin == "BTC": btc_th += th
                                    else: other_th += th

                            except Exception as parse_error:
                                swarm_state["debug_log"].append(f"[bold red]CRASH {ip}:[/] {str(parse_error)}")
                                # ... rest of the code ...
                        
                        total_pw = sum(m.get('power', 0) for m in swarm_state["miners"].values() if not m.get('is_micro'))
                        
                        swarm_state.update({
                            "total_btc_th": btc_th, 
                            "total_bch_th": bch_th, 
                            "total_other_th": other_th, 
                            "total_power": total_pw, 
                            "total_opex_daily": ((total_pw * 24) / 1000) * swarm_state.get('elec_cost', 0.28), 
                            "total_amps": total_pw / 230
                        })
                        
                        if "hashrate_history" not in swarm_state: swarm_state["hashrate_history"] = deque(maxlen=MAX_HISTORY)
                        swarm_state["hashrate_history"].append(btc_th + bch_th + other_th)
                        if "power_history" not in swarm_state: swarm_state["power_history"] = deque(maxlen=MAX_HISTORY)
                        swarm_state["power_history"].append(total_pw)
                        
                    if state_changed: save_state() 
                    
                    check_and_trigger_72h_reset()

                # ==========================================
                # --- NEW: THE SYSTEM UNLOCK TRIGGER ---
                # Once the backend has completed its initial 
                # telemetry sweep, drop the boot screen.
                # ==========================================
                with state_lock:
                    if swarm_state.get("system_booting"):
                        swarm_state["system_booting"] = False
                        swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold green]SYSTEM ONLINE:[/] Boot sequence complete.")

            except Exception as loop_error:
                with state_lock:
                    swarm_state["debug_log"].append(f"[bold red]MAIN LOOP DEAD:[/] {str(loop_error)}") 
                                        
            await asyncio.sleep(10)

# --- COMPANION API (The Mobile Bridge) ---
companion_app = FastAPI(title="BLSIC Companion API")

@companion_app.get("/swarm")
async def get_swarm_broadcast():
    tot_b = tot_s = tot_m = tot_g = tot_t = tot_blocks = 0
    with state_lock:
        ambient = swarm_state.get('ambient_temp', 22.0)
        if ambient <= 0: ambient = 22.0
        
        miners_data = {}
        for ip, m in swarm_state["miners"].items():
            miner_copy = dict(m)
            if not m.get('is_micro'):
                tot_b += m.get('b_hits', 0)
                tot_s += m.get('s_hits', 0)
                tot_m += m.get('m_hits', 0)
                tot_g += m.get('g_hits', 0)
                tot_t += m.get('t_hits', 0)
                tot_blocks += m.get('blocks', 0)
                
                # Calibrated paste health calculation for Companion App
                temp = float(m.get('temp', 0))
                fan = float(m.get('fanrpm', m.get('fanspeed', m.get('fan', 0))))
                
                if temp <= 62.0:
                    paste_health = 98.0 - (temp - 40.0) * 0.2 - (fan / 2500.0)
                else:
                    degrade = max(0.0, (temp - 60.0) * 2.0 + (fan / 150.0) - 15.0)
                    paste_health = max(15.0, 95.0 - degrade)
                paste_health = min(100.0, max(5.0, paste_health))
                
                if paste_health > 75:
                    status = "OPTIMAL"
                elif paste_health > 45:
                    status = "DEGRADED"
                else:
                    status = "CRITICAL"
                    
                miner_copy["paste_health"] = round(paste_health, 1)
                miner_copy["paste_status"] = status
            miners_data[ip] = miner_copy
                
        tot_pts = (tot_b*1) + (tot_s*5) + (tot_m*20) + (tot_g*100) + (tot_t*500) + (tot_blocks*5000)
        
        swarm_state["swarm_current_hits"] = {
            "b": tot_b, "s": tot_s, "m": tot_m, "g": tot_g, "t": tot_t, "blocks": tot_blocks, "points": tot_pts
        }
        
        response_data = dict(swarm_state)
        response_data["miners"] = miners_data
        return response_data

@companion_app.api_route("/hunt", methods=["GET", "POST"])
async def trigger_hunt_api():
    with state_lock:
        swarm_state["trigger_hunt"] = True
    return {"status": "Hunt initiated"}

@companion_app.api_route("/reset", methods=["GET", "POST"])
async def reset_swarm_views():
    visual_recalibrate()
    return {"status": "Views reset"}

@companion_app.api_route("/update_config", methods=["GET", "POST"])
async def update_config_api(power: float = None, cost: float = None):
    with state_lock:
        if cost is not None:
            swarm_state["elec_cost"] = cost
        if power is not None:
            swarm_state["power_override"] = power
        
    save_state() 
    return {"status": "Config updated"}

# --- ADD THIS NEW ENDPOINT FOR THE APP TO CALL ---
@companion_app.api_route("/maintenance/repaste", methods=["GET", "POST"])
async def log_repaste(tag: str):
    if not tag: 
        return {"error": "Missing tag"}
    
    with state_lock:
        if "maintenance" not in swarm_state:
            swarm_state["maintenance"] = {}
            
        if tag not in swarm_state["maintenance"]:
            swarm_state["maintenance"][tag] = {}
            
        # Log the exact UNIX timestamp of the click
        swarm_state["maintenance"][tag]["last_repaste"] = time.time()
        
    save_state()
    
    with state_lock:
        return {"status": "success", "tag": tag, "timestamp": swarm_state["maintenance"][tag]["last_repaste"]}

@companion_app.get("/lottery")
async def get_lottery_broadcast():
    # Dynamic path configuration to prevent circular imports during startup
    import sys
    from pathlib import Path
    current_dir = Path(__file__).resolve().parent
    calc_path = current_dir / "calculations"
    if str(calc_path) not in sys.path:
        sys.path.insert(0, str(calc_path))
        
    from engine import LuckEngine
    
    luck = LuckEngine()
    btc_th = swarm_state.get('total_btc_th', 0.0)
    bch_th = swarm_state.get('total_bch_th', 0.0)
    daily_opex = swarm_state.get('total_opex_daily', 0.0)
    
    live_btc_diff = swarm_state.get("btc_net_diff", 101_000_000_000_000.0)
    live_bch_diff = swarm_state.get("bch_net_diff", 500_000_000_000.0)
    
    analysis = luck.get_lotto_analysis(daily_opex, btc_th, bch_th, live_btc_diff, live_bch_diff)
    
    # Calculate best round candidate from online miners
    max_round_best = 0.0
    best_miner_tag = "None"
    best_miner_coin = "BTC"
    
    # Calculate gamification top performers
    hottest_val = 0.0
    hottest_tag = "None"
    best_val = 0.0
    best_tag = "None"
    
    with state_lock:
        for ip, m in swarm_state.get("miners", {}).items():
            if m.get('online'):
                rb = float(m.get('round_best_diff', 0.0))
                if rb > max_round_best:
                    max_round_best = rb
                    best_miner_tag = m.get('tag', m.get('hostname', ip))
                    best_miner_coin = m.get('coin_type', 'BTC')
                
                # Gamification: Hottest (Viral) inf_score
                inf = float(m.get('inf_score', 0.0))
                if inf > hottest_val:
                    hottest_val = inf
                    hottest_tag = m.get('tag', m.get('hostname', ip))
                
                # Gamification: Best (Steady) luck_score
                luck_sc = float(m.get('luck_score', 0.0))
                if luck_sc > best_val:
                    best_val = luck_sc
                    best_tag = m.get('tag', m.get('hostname', ip))
                
    luck_engine_data = None
    if max_round_best > 0:
        res = luck.get_best_share_probability(max_round_best, best_miner_coin, live_btc_diff, live_bch_diff)
        if res:
            formatted_best = luck.format_diff_scaled(max_round_best)
            luck_engine_data = {
                "best_share_raw": max_round_best,
                "best_share_formatted": formatted_best,
                "miner": best_miner_tag,
                "coin": best_miner_coin,
                "odds": res['odds'],
                "prob_percent": res['prob_percent'],
                "equivalence": res['equivalence']
            }
            
    # Calculate 72h cycle countdown ends
    start_time = swarm_state.get("cycle_start_time", time.time())
    elapsed = time.time() - start_time
    remaining_sec = max(0.0, (72 * 3600) - elapsed)
    rem_h = int(remaining_sec // 3600)
    rem_m = int((remaining_sec % 3600) // 60)
    cycle_ends_str = f"{rem_h}h {rem_m}m"
            
    return {
        "analysis": analysis,
        "luck_engine": luck_engine_data,
        "gamification": {
            "hottest_viral_miner": hottest_tag,
            "hottest_viral_score": round(hottest_val, 1),
            "best_steady_miner": best_tag,
            "best_steady_score": round(best_val, 1),
            "cycle_ends_countdown": cycle_ends_str,
            "cycle_ends_seconds": int(remaining_sec),
            "previous_winner": swarm_state.get("previous_winner", "None"),
            "swarm_hits": swarm_state.get("swarm_current_hits", {})
        }
    }
    
@companion_app.get("/power")
async def get_power_broadcast():
    with state_lock:
        solar_w = swarm_state.get("solar_panel_watts", 475.0)
        solar_count = swarm_state.get("solar_panel_count", 19.0)
        battery_kwh = swarm_state.get("battery_kwh_per", 5.3)
        battery_count = swarm_state.get("battery_count", 2.0)
        inverter_kw = swarm_state.get("inverter_kw", 10.0)
        wind_kw = swarm_state.get("wind_kw", 3.0)
        
        sun_hours = swarm_state.get("sun_hours", 4.0)
        elec_cost = swarm_state.get("elec_cost", 0.22)
        total_w = swarm_state.get("total_power", 0.0)
        
        # Renewable specs
        p_solar_installed = solar_w * solar_count
        p_solar_effective = p_solar_installed * 1.10 # 10% rear-gain
        p_wind_installed = wind_kw * 1000.0
        p_renewable_max = p_solar_effective + p_wind_installed
        p_inverter_limit = inverter_kw * 1000.0
        
        # Cap AC generation to inverter limit
        p_gen_capped = min(p_renewable_max, p_inverter_limit)
        
        # Battery capacity
        e_battery = battery_kwh * battery_count
        e_usable = e_battery * 0.90 # 90% usable capacity
        
        # Runtime calculation
        runtime_hours = 0.0
        if total_w > 0:
            runtime_hours = e_usable / (total_w / 1000.0)
            
        # 24H energy balances
        e_swarm_daily = (total_w / 1000.0) * 24.0
        e_solar_daily = (p_solar_effective / 1000.0) * sun_hours
        e_wind_daily = wind_kw * 24.0 * 0.20 # 20% capacity factor
        e_gen_daily = e_solar_daily + e_wind_daily
        
        coverage_pct = 0.0
        if e_swarm_daily > 0:
            coverage_pct = (e_gen_daily / e_swarm_daily) * 100.0
            
        # Determine day/night auto astronomical weather state
        from datetime import datetime
        local_hour = datetime.now().hour
        is_sunny = 6 <= local_hour < 18
            
        return {
            "solar_watts": solar_w,
            "solar_count": solar_count,
            "battery_kwh": battery_kwh,
            "battery_count": battery_count,
            "inverter_kw": inverter_kw,
            "wind_kw": wind_kw,
            "sun_hours": sun_hours,
            "total_power_draw_w": total_w,
            "installed_solar_w": p_solar_installed,
            "effective_solar_w": p_solar_effective,
            "total_battery_kwh": e_battery,
            "usable_battery_kwh": e_usable,
            "daily_swarm_consumption_kwh": e_swarm_daily,
            "daily_solar_generation_kwh": e_solar_daily,
            "daily_wind_generation_kwh": e_wind_daily,
            "daily_renewable_generation_total_kwh": e_gen_daily,
            "self_sufficiency_ratio_pct": coverage_pct,
            "battery_runtime_hours": runtime_hours,
            "sunsynk_enabled": swarm_state.get("sunsynk_enabled", False),
            "is_sunny": is_sunny,
            "ambient_temp": swarm_state.get("ambient_temp", 22.0)
        }

@companion_app.api_route("/power/config", methods=["GET", "POST"])
async def update_power_config(
    solar_w: float = None,
    solar_count: float = None,
    battery_kwh: float = None,
    battery_count: float = None,
    inverter_kw: float = None,
    wind_kw: float = None,
    sunsynk_enabled: bool = None
):
    with state_lock:
        if solar_w is not None: swarm_state["solar_panel_watts"] = solar_w
        if solar_count is not None: swarm_state["solar_panel_count"] = solar_count
        if battery_kwh is not None: swarm_state["battery_kwh_per"] = battery_kwh
        if battery_count is not None: swarm_state["battery_count"] = battery_count
        if inverter_kw is not None: swarm_state["inverter_kw"] = inverter_kw
        if wind_kw is not None: swarm_state["wind_kw"] = wind_kw
        if sunsynk_enabled is not None: swarm_state["sunsynk_enabled"] = sunsynk_enabled
        
    save_state()
    return {"status": "Power configuration updated successfully"}
	
#---------------------------------------------

def start_broadcaster():
    try:
        uvicorn.run(companion_app, host="0.0.0.0", port=8000, log_level="error")
    except Exception:
        pass

threading.Thread(target=start_broadcaster, daemon=True).start()

async def fetch_ambient_weather():
    # Coordinates for the console's physical deployment zone
    lat, lon = 51.208, -1.480
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while swarm_state["run_loop"]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    weather_data = resp.json()
                    if "current_weather" in weather_data:
                        with state_lock:
                            swarm_state["ambient_temp"] = weather_data["current_weather"].get("temperature", 0.0)
            except Exception:
                pass
            await asyncio.sleep(1800) # Refresh every 30 mins
		