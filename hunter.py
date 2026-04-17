import asyncio
import httpx
import socket

class SwarmHunter:
    def __init__(self, subnet=None):
        self.subnet = subnet or self._get_local_subnet()
        if self.subnet == "127.0.0" or not self.subnet:
            self.subnet = "192.168.1"
        
        # INCREASED TIMEOUTS for ESP32 stability
        self.timeout = httpx.Timeout(8.0, connect=4.0)
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Connection": "close", 
            "Accept": "application/json"
        }
        
        self.abort_event = asyncio.Event()

    def _get_local_subnet(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 1))
            local_ip = s.getsockname()[0]
            s.close()
            return ".".join(local_ip.split(".")[:-1])
        except Exception:
            return "192.168.1"

    def _detect_miner_type(self, data):
        """
        Intelligence table for strictly determining hardware type.
        """
        hostname = str(data.get("hostname", "")).lower()
        board_ver = str(data.get("boardversion", data.get("boardVersion", ""))).upper()
        device_model = str(data.get("deviceModel", "")).lower()
        
        if "nerdminer" in hostname or "nerdminer" in device_model:
            return "NerdMiner"
        if "micro" in hostname:
            return "Micro"
        if board_ver == "800" or "gt800" in device_model or "gt800" in hostname:
            return "GT800"
        if "nerdqaxe" in hostname or "nerdqaxe" in device_model:
            return "NerdQAxe++"
        if board_ver == "601" or "gamma" in device_model or "gamma" in hostname:
            return "Gamma"
        if board_ver == "201" or "bitaxe" in device_model or "bitaxe" in hostname:
            return "Bitaxe"
        return "Bitaxe"

    async def get_miner_data(self, ip):
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"http://{ip}/api/system/info", headers=self.headers)
                if resp.status_code == 200:
                    data = resp.json()
                    
                    m_type = self._detect_miner_type(data)
                    
                    # --- FIX: Support nested stratum objects for NerdQAxe ---
                    stratum_data = data.get("stratum", {})
                    stratum_url = data.get("stratumURL", stratum_data.get("url", "Solo"))
                    
                    # --- NEW: Capture Dedicated Port for Gamma/NerdQAxe ---
                    # This is the key piece needed for your BCH correlation
                    stratum_port = data.get("stratumPort", stratum_data.get("port", 0))
                    
                    coin = data.get("coin", stratum_data.get("coin", "BTC"))
                    session_diff = data.get("bestSessionDiff", stratum_data.get("bestSessionDiff", 0.0))
                    
					
					# --- RESILIENT ASIC CHIP DETECTION ---
                    hm = data.get('hashrateMonitor', {})
                    asics_array = hm.get('asics', [])
                    
                    # Primary: Count objects in the asics array (Best for GT800/NerdQAxe)
                    # Secondary: Fallback to top-level asicCount key
                    # Tertiary: Default to 1 for standard Bitaxes
                    if isinstance(asics_array, list) and len(asics_array) > 0:
                        asic_count = len(asics_array)
                    else:
                        # Ensure we handle the case where asicCount might be None or missing
                        val = data.get("asicCount")
                        asic_count = int(val) if val is not None else 1
					                  
                    return {
                        "ip": ip,
                        "hostname": data.get("hostname", f"Miner-{ip.split('.')[-1]}"),
                        "type": m_type,
                        "asicCount": asic_count,
                        "hashRate": float(data.get("hashRate", data.get("hashrate", 0))),
                        "power": float(data.get("power", data.get("powerW", data.get("wattage", 0)))),
                        "temp": float(data.get("temp", data.get("temperature", data.get("boardTemp", 0)))),
                        "coreTemp": float(data.get("coreTemp", 0)),
                        "vrTemp": float(data.get("vrTemp", 0)),
                        
                        "fanrpm": int(data.get("fanrpm", 0)),
                        "fanspeed": int(data.get("fanspeed", 0)),
                        "fanSpeed": int(data.get("fanSpeed", data.get("fan", 0))),
                        
                        "frequency": int(data.get("frequency", data.get("coreFreq", data.get("freq", 0)))),
                        "coreVoltage": int(data.get("coreVoltage", data.get("voltage", data.get("volts", 0)))),
                        "uptimeSeconds": data.get("uptimeSeconds", 0),
                        "coin": coin,
                        
                        "stratumUser": str(data.get("stratumUser", stratum_data.get("user", ""))),
                        "stratumURL": stratum_url,
                        "stratumPort": stratum_port, # NEW: Explicitly passing port to main_ui
                        
                        "bestDiff": float(data.get("bestDiff", 0)),
                        "bestSessionDiff": float(session_diff),
                        "online": True
                    }, "Found!"
                return None, f"HTTP {resp.status_code}"
        except Exception: 
            return None, "Timeout"

    async def scan_network(self, logger=None):
        self.abort_event.clear()
        semaphore = asyncio.Semaphore(10) 
        
        async def throttled_check(ip_suffix):
            if self.abort_event.is_set():
                return None
            full_ip = f"{self.subnet}.{ip_suffix}"
            async with semaphore:
                await asyncio.sleep(0.05 * (ip_suffix % 10)) 
                if self.abort_event.is_set():
                    return None
                if logger: logger(full_ip, "probing")
                data, status = await self.get_miner_data(full_ip)
                if logger: logger(full_ip, status)
                return data

        tasks = [throttled_check(i) for i in range(1, 255)]
        results = []
        for coro in asyncio.as_completed(tasks):
            if self.abort_event.is_set():
                break
            res = await coro
            if res:
                results.append(res)
        return results