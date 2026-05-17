import math

class LuckEngine:
    # Updated to reflect current real-world network difficulty
    def __init__(self, network_difficulty=101_000_000_000_000, bch_difficulty=500_000_000_000):
        self.net_diff = float(network_difficulty)
        self.bch_diff = float(bch_difficulty)
        
        # Expanded UK Game Odds Database
        self.games = {
            "lotto": {"name": "Lotto Jackpot", "odds": 45_057_474, "cost": 2.00},
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

    def lottery_comparison(self, total_hashrate_ths, coin_type="BTC", live_btc_diff=None, live_bch_diff=None):
        btc_diff = live_btc_diff if live_btc_diff is not None else self.net_diff
        bch_diff = live_bch_diff if live_bch_diff is not None else self.bch_diff
        target_diff = btc_diff if coin_type == "BTC" else bch_diff
        
        try:
            th = float(total_hashrate_ths)
        except (ValueError, TypeError):
            th = 0.0
            
        if th <= 0: return "Offline"
        
        # Calculates the probability of finding at least one block over 72 hours (259200 seconds)
        hashes_per_sec = th * 1_000_000_000_000 
        exponent = -(hashes_per_sec * 259200) / (target_diff * 4294967296)
        prob_72h = -math.expm1(exponent)
        
        if prob_72h <= 0: return "Offline"
        return int(1 / prob_72h)

    def get_best_share_probability(self, max_round_best, coin_type="BTC", live_btc_diff=None, live_bch_diff=None):
        btc_diff = live_btc_diff if live_btc_diff is not None else self.net_diff
        bch_diff = live_bch_diff if live_bch_diff is not None else self.bch_diff
        target_diff = btc_diff if coin_type == "BTC" else bch_diff
        
        if max_round_best <= 0:
            return None
            
        odds = target_diff / max_round_best
        prob_percent = (max_round_best / target_diff) * 100.0
        
        # Dynamically map block odds to UK Lotto equivalents
        if odds >= 45_057_474:
            eq = "Match 6 Jackpot (Odds 1:45M)"
        elif odds >= 7_509_579:
            eq = "Match 5 + Bonus (Odds 1:7.5M)"
        elif odds >= 144_415:
            eq = "Match 5 Main Numbers (Odds 1:144k)"
        elif odds >= 2_180:
            eq = "Match 4 Main Numbers (Odds 1:2.1k)"
        elif odds >= 97:
            eq = "Match 3 Main Numbers (Odds 1:97)"
        elif odds >= 10.3:
            eq = "Match 2 Main Numbers (Odds 1:10)"
        else:
            eq = "Match 1 Main Number (Odds 1:2)"
            
        return {
            "odds": int(odds),
            "prob_percent": prob_percent,
            "equivalence": eq
        }

    def get_lotto_analysis(self, daily_opex, btc_th, bch_th, live_btc_diff=None, live_bch_diff=None):
        # Calculate 72-hour OPEX
        opex_72h = daily_opex * 3
        tickets_72h = opex_72h / self.lotto_ticket_price if self.lotto_ticket_price > 0 else 0
        
        btc_odds = self.lottery_comparison(btc_th, "BTC", live_btc_diff, live_bch_diff)
        bch_odds = self.lottery_comparison(bch_th, "BCH", live_btc_diff, live_bch_diff)
        
        luck_profiles = {}
        for key, data in self.games.items():
            game_odds = data["odds"]
            cost = data["cost"]
            
            # How many tickets needed to match mining odds?
            btc_match_tickets = game_odds / btc_odds if isinstance(btc_odds, int) and btc_odds > 0 else 0
            btc_match_cost = btc_match_tickets * cost
            
            bch_match_tickets = game_odds / bch_odds if isinstance(bch_odds, int) and bch_odds > 0 else 0
            bch_match_cost = bch_match_tickets * cost
            
            luck_profiles[key] = {
                "btc_match_tickets": btc_match_tickets,
                "btc_match_cost": btc_match_cost,
                "bch_match_tickets": bch_match_tickets,
                "bch_match_cost": bch_match_cost
            }
        
        return {
            "equiv_tickets": tickets_72h,
            "opex_72h": opex_72h,
            "btc_odds": btc_odds,
            "bch_odds": bch_odds,
            "profiles": luck_profiles
        }