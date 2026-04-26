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


# --- CONFIG & PERSISTENCE ---
SAVE_FILE = Path("swarm_config.json")
MAX_HISTORY = 1000

# The Single Source of Truth for the Swarm
swarm_state = {
    "miners": {}, 
    "hashrate_history": [], 
    "power_history": [],
    "peak_th": 0.0,
    "elec_cost": 0.28,
    "sun_hours": 4.0,
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
        swarm_state["hashrate_history"] = swarm_state.get("hashrate_history", [])[-MAX_HISTORY:]
        swarm_state["power_history"] = swarm_state.get("power_history", [])[-MAX_HISTORY:]
        
        payload = {
            "miners": swarm_state["miners"],
            "hashrate_history": swarm_state["hashrate_history"], 
            "power_history": swarm_state["power_history"],
            "peak_th": swarm_state["peak_th"],
            "elec_cost": swarm_state["elec_cost"],
            "sun_hours": swarm_state["sun_hours"],
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
            # Detached asynchronous write for UI fluidity
            threading.Thread(target=lambda: SAVE_FILE.write_text(state_json, encoding='utf-8'), daemon=True).start()
            
    except Exception: 
        pass

def load_state():
    if SAVE_FILE.exists():
        try:
            with open(SAVE_FILE, "r") as f:
                data = json.load(f)
                if "hashrate_history" in data:
                    swarm_state["hashrate_history"] = data["hashrate_history"]
                
                for k in ["power_history", "peak_th", "elec_cost", "sun_hours", "luck_archive", 
                          "monthly_winner", "fortnight_winner", "last_ladder_reset", "telemetry_epoch",
                          "total_shares_acc", "total_shares_rej", "swarm_lifetime_hits", "show_ips", "maintenance"]:
                    if k in data:
                        swarm_state[k] = data[k]
                
                if "miners" in data:
                    swarm_state["miners"] = data["miners"]
        except Exception: 
            pass
            
    if "last_ladder_reset" not in swarm_state or swarm_state["last_ladder_reset"] == 0:
        swarm_state["last_ladder_reset"] = time.time()

def safe_num(val, cast_type=float, default=0):
    try:
        if val is None or val == "": return default
        return cast_type(val)
    except Exception:
        return default

def visual_recalibrate():
    live_val = swarm_state.get('total_btc_th', 0.0) + swarm_state.get('total_bch_th', 0.0)
    if live_val > 1.0:
        swarm_state["hashrate_history"] = [v for v in swarm_state.get("hashrate_history", []) if v < (live_val * 1.5)]
        swarm_state["power_history"] = [p for p in swarm_state.get("power_history", []) if p < (swarm_state.get('total_power', 0) * 1.5)]
        swarm_state["peak_th"] = live_val  
        swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold cyan]INFO:[/] Trend chart and Peak recalibrated.")
    else:
        swarm_state["hashrate_history"] = []
        swarm_state["power_history"] = []
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
                "color": TYPE_COLORS.get(resolve_miner_type(best_miner), "white")
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

async def update_known_miners():
    swarm_state["detected_local_ip"] = get_local_ip()
    
    async def fetch_api(ip):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://{ip}/api/system/info", timeout=8.0)
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None

    while swarm_state["run_loop"]:
        try:
            if not swarm_state["is_hunting"] and swarm_state["miners"] and not swarm_state["show_summary"]:
                miner_ips = list(swarm_state["miners"].keys())
                
                tasks = [fetch_api(ip) for ip in miner_ips]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                btc_th = bch_th = total_pw = 0
                state_changed = False
                now = time.time()
                
                for ip, data in zip(miner_ips, results):
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
                            
                            total_pw += power
                            miner['th'] = th
                            miner['power'] = power
                            miner['jth'] = power / th if th > 0 else 0
                            miner['frequency'] = safe_num(data.get('frequency'), float, 1.0)
                            miner['coreVoltage'] = safe_num(data.get('coreVoltage'), float, 1200.0)

                            url = str(data.get('stratumURL', '')).lower()
                            user = str(data.get('stratumUser', '')).lower()
                            port = safe_num(data.get('stratumPort'), int)

                            # Identify BCH by standard ports or wallet address prefixes
                            is_bch_port = (port == 3337 or port == 3338 or ":3337" in url or ":3338" in url)
                            is_bch_user = any(user.startswith(pre) for pre in ['q', 'p', 'bitcoincash:'])
                            
                            # CRITICAL FIX: Trust the port detection, even on local 192.168.x.x networks!
                            is_bch = is_bch_port or is_bch_user
                            miner['coin_type'] = "BCH" if is_bch else "BTC"

                            if is_bch: bch_th += th
                            else: btc_th += th

                    except Exception as parse_error:
                        swarm_state["debug_log"].append(f"[bold red]CRASH {ip}:[/] {str(parse_error)}")
                        # ... rest of the code ...
                
                if state_changed: save_state() 
                
                swarm_state.update({
                    "total_btc_th": btc_th, 
                    "total_bch_th": bch_th, 
                    "total_power": total_pw, 
                    "total_opex_daily": ((total_pw * 24) / 1000) * swarm_state.get('elec_cost', 0.28), 
                    "total_amps": total_pw / 230
                })
                
                if "hashrate_history" not in swarm_state: swarm_state["hashrate_history"] = []
                swarm_state["hashrate_history"].append(btc_th + bch_th)
                swarm_state["power_history"].append(total_pw)

                check_and_trigger_72h_reset()

            # ==========================================
            # --- NEW: THE SYSTEM UNLOCK TRIGGER ---
            # Once the backend has completed its initial 
            # telemetry sweep, drop the boot screen.
            # ==========================================
            if swarm_state.get("system_booting"):
                swarm_state["system_booting"] = False
                swarm_state["debug_log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] [bold green]SYSTEM ONLINE:[/] Boot sequence complete.")

        except Exception as loop_error:
            swarm_state["debug_log"].append(f"[bold red]MAIN LOOP DEAD:[/] {str(loop_error)}") 
                                    
        await asyncio.sleep(10)

# --- COMPANION API (The Mobile Bridge) ---
companion_app = FastAPI(title="BLSIC Companion API")

@companion_app.get("/swarm")
async def get_swarm_broadcast():
    tot_b = tot_s = tot_m = tot_g = tot_t = tot_blocks = 0
    for m in swarm_state["miners"].values():
        if not m.get('is_micro'):
            tot_b += m.get('b_hits', 0)
            tot_s += m.get('s_hits', 0)
            tot_m += m.get('m_hits', 0)
            tot_g += m.get('g_hits', 0)
            tot_t += m.get('t_hits', 0)
            tot_blocks += m.get('blocks', 0)
            
    tot_pts = (tot_b*1) + (tot_s*5) + (tot_m*20) + (tot_g*100) + (tot_t*500) + (tot_blocks*5000)
    
    swarm_state["swarm_current_hits"] = {
        "b": tot_b, "s": tot_s, "m": tot_m, "g": tot_g, "t": tot_t, "blocks": tot_blocks, "points": tot_pts
    }
    return swarm_state

@companion_app.api_route("/hunt", methods=["GET", "POST"])
async def trigger_hunt_api():
    swarm_state["trigger_hunt"] = True
    return {"status": "Hunt initiated"}

@companion_app.api_route("/reset", methods=["GET", "POST"])
async def reset_swarm_views():
    visual_recalibrate()
    return {"status": "Views reset"}

@companion_app.api_route("/update_config", methods=["GET", "POST"])
async def update_config_api(power: float = None, cost: float = None):
    if cost is not None:
        swarm_state["elec_cost"] = cost
    if power is not None:
        swarm_state["power_override"] = power
        
    save_state() 
    return {"status": "Config updated"}

# --- ADD THIS NEW ENDPOINT FOR THE APP TO CALL ---
@companion_app.post("/maintenance/repaste")
async def log_repaste(tag: str):
    if not tag: 
        return {"error": "Missing tag"}
    
    if "maintenance" not in swarm_state:
        swarm_state["maintenance"] = {}
        
    if tag not in swarm_state["maintenance"]:
        swarm_state["maintenance"][tag] = {}
        
    # Log the exact UNIX timestamp of the click
    swarm_state["maintenance"][tag]["last_repaste"] = time.time()
    save_state()
    
    return {"status": "success", "tag": tag, "timestamp": swarm_state["maintenance"][tag]["last_repaste"]}
	
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
    
    while swarm_state["run_loop"]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10.0)
                if resp.status_code == 200:
                    weather_data = resp.json()
                    if "current_weather" in weather_data:
                        swarm_state["ambient_temp"] = weather_data["current_weather"].get("temperature", 0.0)
        except Exception:
            pass
        await asyncio.sleep(1800) # Refresh every 30 mins
		