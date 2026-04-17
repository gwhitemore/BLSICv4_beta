import math

class LuckEngine:
    # Updated to reflect current real-world network difficulty
    def __init__(self, network_difficulty=101_000_000_000_000, bch_difficulty=500_000_000_000):
        self.net_diff = float(network_difficulty)
        self.bch_diff = float(bch_difficulty)
        
        # Expanded UK Game Odds Database
        self.games = {
            "lotto": {"name": "Lotto Jackpot", "odds": 45_057_474, "cost": 2.00},
            "scratchcard": {"name": "£250k Scratchcard", "odds": 4_000_000, "cost": 2.00},
            "thunderball": {"name": "Thunderball", "odds": 8_060_598, "cost": 1.00},
            "euromillions": {"name": "EuroMillions", "odds": 139_838_160, "cost": 2.50}
        }
        self.lotto_ticket_price = self.games["lotto"]["cost"]

    def format_diff_scaled(self, value):
        if not value or value == 0: return "0"
        units = ['', 'K', 'M', 'G', 'T', 'P']
        unit_idx = 0; val = float(value)
        while val >= 1000 and unit_idx < len(units) - 1:
            val /= 1000.0; unit_idx += 1
        label = units[unit_idx]
        if label == 'G': return f"[bold bright_green]{val:.2f}G[/]"
        if label == 'T': return f"[bold bright_magenta]{val:.2f}T[/]"
        if unit_idx == 0: return f"{int(val)}"
        return f"{val:.2f}{label}"

    def lottery_comparison(self, total_hashrate_ths, coin_type="BTC"):
        target_diff = self.net_diff if coin_type == "BTC" else self.bch_diff
        try:
            th = float(total_hashrate_ths)
        except (ValueError, TypeError):
            th = 0.0
            
        if th <= 0: return "Offline"
        
        # Calculates the probability of finding at least one block over 24 hours (86400 seconds)
        hashes_per_sec = th * 1_000_000_000_000 
        exponent = -(hashes_per_sec * 86400) / (target_diff * 4294967296)
        daily_prob = -math.expm1(exponent)
        
        if daily_prob <= 0: return "1 in Trillions"
        return int(1 / daily_prob)

    def get_lotto_analysis(self, daily_opex, btc_th, bch_th):
        # Calculate how many standard £2 Lotto tickets the daily OPEX buys
        daily_tickets = daily_opex / self.lotto_ticket_price if self.lotto_ticket_price > 0 else 0
        
        btc_odds = self.lottery_comparison(btc_th, "BTC")
        bch_odds = self.lottery_comparison(bch_th, "BCH")
        
        # Calculate luck multiples based on TRUE FINANCIAL PARITY
        luck_profiles = {}
        for key, data in self.games.items():
            # How many of THIS specific game's tickets can the OPEX buy?
            tickets_afforded = daily_opex / data["cost"] if data["cost"] > 0 else 0
            
            # Adjust the game's odds based on purchasing power
            if tickets_afforded > 0:
                effective_game_odds = data["odds"] / tickets_afforded
            else:
                effective_game_odds = data["odds"] # Fallback to base odds if OPEX is zero
            
            # Calculate the true financial multiplier
            b_luck = effective_game_odds / btc_odds if isinstance(btc_odds, int) and btc_odds > 0 else 0
            h_luck = effective_game_odds / bch_odds if isinstance(bch_odds, int) and bch_odds > 0 else 0
            
            luck_profiles[key] = {"btc_luck": b_luck, "bch_luck": h_luck}
        
        return {
            "equiv_tickets": daily_tickets,
            "btc_odds": btc_odds,
            "bch_odds": bch_odds,
            "profiles": luck_profiles
        }