from __future__ import annotations

from dataclasses import asdict, dataclass, field
import random


MAX_SQUAD_SIZE = 24
LINEUP_SIZE = 11

ORIGINAL_PLAYER_NAMES: list[str] = [
    # Defenders — indices 0-7 (BASIC p=1-8)
    "P.Parkes", "D.Watson", "P.Neal", "A.Martin",
    "K.Sansom", "M.Mills", "R.Osman", "S.Foster",
    # Midfielders — indices 8-15 (BASIC p=9-16)
    "B.Robson", "G.Hoddle", "G.Rix", "S.Hunt",
    "G.Owen", "R.Moses", "B.Talbot", "S.McCall",
    # Attackers — indices 16-23 (BASIC p=17-24)
    "C.Regis", "P.Withe", "T.Morley", "P.Barnes",
    "E.Gates", "K.Reeves", "K.Keegan", "G.Shaw",
]

RETRO_POSITIONS: list[str] = ["D"] * 8 + ["M"] * 8 + ["A"] * 8

ALL_TEAM_NAMES: list[str] = [
    # Division 1 (Python indices 0-15)
    "Arsenal", "Aston V.", "Brighton", "Coventry", "Everton", "Ipswich",
    "Liverpool", "Luton", "Man.City", "Man.Utd", "Norwich", "Notts.F.",
    "Swansea", "Spurs", "Watford", "West Ham",
    # Division 2 (indices 16-31)
    "Blackburn", "Bolton", "Cambridge", "Charlton", "Chelsea", "Crystal P.",
    "Derby Co.", "Fulham", "Grimsby", "Leeds", "Middlesbro", "Newcastle",
    "Oldham", "Q.P.R.", "Rotherham", "Sheff.Wed",
    # Division 3 (indices 32-47)
    "Bradford", "Brentford", "Bristol R.", "Cardiff", "Doncaster", "Exeter",
    "Lincoln", "Millwall", "Newport", "Orient", "Oxford", "Plymouth",
    "Preston", "Reading", "Southend", "Walsall",
    # Division 4 (indices 48-63)
    "Blackpool", "Bury", "Colchester", "Crewe", "Darlington", "Halifax",
    "Hartlepool", "Hereford", "Hull", "Mansfield", "Port Vale", "Rochdale",
    "Scunthorpe", "Stockport", "Torquay", "York City",
]

LEVEL_NAMES = [
    "Beginner", "Novice", "Average", "Good", "Expert", "Super Expert", "Genius"
]

POSITIONS = ("D", "M", "A")

# Modern-mode pools kept for modern season creation
AI_CLUB_POOL = [
    "Blackpool", "Bury", "Colchester", "Crewe", "Darlington", "Halifax",
    "Hartlepool", "Hereford", "Hull", "Mansfield", "Port Vale", "Rochdale",
    "Scunthorpe", "Stockport", "Torquay", "York City",
    "Railway Town", "Hillford", "Market Street", "Parkside",
    "Lakeside", "Ashfield", "Victoria Works", "Union City",
]

FIRST_NAMES = [
    "Alan", "Bobby", "Colin", "Dave", "Eddie", "Frank", "Gary", "Harry",
    "Ian", "Jimmy", "Kevin", "Les", "Mick", "Neil", "Owen", "Paul",
    "Ray", "Steve", "Terry", "Vic",
]
LAST_NAMES = [
    "Adams", "Baker", "Clarke", "Dawson", "Evans", "Foster", "Grant",
    "Hill", "Irwin", "Jones", "King", "Lewis", "Mason", "Noble",
    "Parker", "Quinn", "Reed", "Smith", "Taylor", "Walker",
]


def retro_div_multiplier(division: int) -> int:
    """d2 = 5 - d1 from BASIC line 8805."""
    return 5 - division


@dataclass
class Player:
    id: str
    name: str
    position: str
    skill: int
    energy: int
    morale: int
    value: int
    injured_weeks: int = 0
    skill_d: int = 0
    skill_m: int = 0
    skill_a: int = 0
    morale_delta: int = 0
    playing_as: str = ""  # overrides position for lineup role; "" = use natural position

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        if 'skill_d' not in self.__dict__:
            sd, sm, sa = _position_skills(self.position, self.skill)
            self.skill_d, self.skill_m, self.skill_a = sd, sm, sa
        if 'morale_delta' not in self.__dict__:
            self.morale_delta = 0
        if 'playing_as' not in self.__dict__:
            self.playing_as = ""

    def to_dict(self, active: bool = False) -> dict:
        data = asdict(self)
        data["active"] = active
        data["injured"] = self.injured_weeks > 0
        return data


@dataclass
class Team:
    name: str
    manager: str
    is_human: bool
    squad: list[Player]
    lineup_ids: list[str]
    cash: int = 0
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0
    division: int = 4
    loan: int = 0
    ground_rent: int = 500
    # Retro-specific
    gate_money: int = 5000
    retro_morale: int = 10

    @property
    def active_players(self) -> list[Player]:
        ids = set(self.lineup_ids)
        return [p for p in self.squad if p.id in ids]

    @property
    def available_players(self) -> list[Player]:
        return [p for p in self.squad if p.injured_weeks <= 0]

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def energy(self) -> int:
        return _avg([p.energy for p in self.active_players])

    @property
    def morale(self) -> int:
        return _avg([p.morale for p in self.active_players])

    @property
    def defence(self) -> int:
        return _unit_strength(self.active_players, "D")

    @property
    def midfield(self) -> int:
        return _unit_strength(self.active_players, "M")

    @property
    def attack(self) -> int:
        return _unit_strength(self.active_players, "A")

    @property
    def strength(self) -> int:
        return round((self.energy + self.morale + self.defence + self.midfield + self.attack) / 5)

    @property
    def wage_bill(self) -> int:
        """BASIC: sum(r(i)*100*d2) for all squad members."""
        d2 = retro_div_multiplier(self.division)
        return sum(p.skill * 100 * d2 for p in self.squad)

    @property
    def loan_interest(self) -> int:
        """BASIC: INT(l/100)."""
        return self.loan // 100

    @property
    def loan_limit(self) -> int:
        """BASIC: 250000*d2."""
        return 250000 * retro_div_multiplier(self.division)

    @property
    def squad_size(self) -> int:
        return len(self.squad)

    def can_buy(self, player: Player) -> bool:
        return len(self.squad) < MAX_SQUAD_SIZE and self.cash >= player.value

    def buy(self, player: Player) -> None:
        if not self.can_buy(player):
            raise ValueError("Cannot buy player")
        self.cash -= player.value
        player.morale = self.retro_morale
        self.squad.append(player)
        if len(self.lineup_ids) < LINEUP_SIZE and player.injured_weeks == 0:
            self.lineup_ids.append(player.id)

    def sell(self, player_id: str) -> Player:
        if len(self.squad) <= LINEUP_SIZE:
            raise ValueError("Cannot sell: squad too small")
        player = next((p for p in self.squad if p.id == player_id), None)
        if not player:
            raise ValueError("Player not found")
        if player.injured_weeks > 0:
            raise ValueError("Nobody wants an injured player!")
        sell_price = int(((random.random() * 5 + 8) * player.value) / 10)
        self.cash += sell_price
        was_in_lineup = player_id in self.lineup_ids
        self.squad = [p for p in self.squad if p.id != player_id]
        self.lineup_ids = [pid for pid in self.lineup_ids if pid != player_id]
        if was_in_lineup:
            # Fill only the one vacated slot from available bench players
            already_in = set(self.lineup_ids)
            candidates = sorted(
                [p for p in self.squad if p.injured_weeks == 0 and p.id not in already_in],
                key=lambda p: p.skill + p.energy * 0.1,
                reverse=True,
            )
            if candidates:
                self.lineup_ids.append(candidates[0].id)
        return player

    def borrow(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("Loan amount must be positive")
        if self.loan + amount > self.loan_limit:
            raise ValueError(f"Loan limit is £{self.loan_limit:,}")
        self.loan += amount
        self.cash += amount

    def repay(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if amount > self.loan:
            raise ValueError("Cannot repay more than owed")
        if amount > self.cash:
            raise ValueError("Not enough cash")
        self.loan -= amount
        self.cash -= amount

    def apply_weekly_finance(self) -> dict:
        wages = self.wage_bill
        rent = self.ground_rent
        interest = self.loan_interest
        total = wages + rent + interest
        self.cash -= total
        if self.cash < 0:
            overdraft = -self.cash
            self.loan += overdraft
            self.cash = 0
        return {"wages": wages, "rent": rent, "interest": interest, "total": total}

    def force_sell_cheapest(self) -> dict | None:
        """Sell lowest-skill non-injured player when loan is maxed out."""
        if len(self.squad) <= LINEUP_SIZE:
            return None
        candidates = sorted(
            [p for p in self.squad if p.injured_weeks == 0],
            key=lambda p: (p.skill, p.value),
        )
        if not candidates:
            return None
        player = candidates[0]
        sell_price = int(((random.random() * 5 + 8) * player.value) / 10)
        self.cash += sell_price
        was_in_lineup = player.id in self.lineup_ids
        self.squad = [p for p in self.squad if p.id != player.id]
        self.lineup_ids = [pid for pid in self.lineup_ids if pid != player.id]
        if was_in_lineup:
            already_in = set(self.lineup_ids)
            bench = sorted(
                [p for p in self.squad if p.injured_weeks == 0 and p.id not in already_in],
                key=lambda p: p.skill + p.energy * 0.1,
                reverse=True,
            )
            if bench:
                self.lineup_ids.append(bench[0].id)
        return {"name": player.name, "price": sell_price}

    def toggle_lineup(self, player_id: str) -> None:
        if player_id in self.lineup_ids:
            if len(self.lineup_ids) <= 1:
                raise ValueError("Must have at least 1 player in lineup")
            self.lineup_ids.remove(player_id)
        else:
            player = next((p for p in self.squad if p.id == player_id), None)
            if not player:
                raise ValueError("Player not found")
            if player.injured_weeks > 0:
                raise ValueError("Player is injured")
            self.lineup_ids.append(player_id)

    def swap_lineup(self, in_player_id: str, out_player_id: str) -> None:
        if in_player_id == out_player_id:
            raise ValueError("Choose two different players")
        if out_player_id not in self.lineup_ids:
            raise ValueError("Choose a player who is currently playing")
        if in_player_id in self.lineup_ids:
            raise ValueError("Incoming player is already playing")
        incoming = next((p for p in self.squad if p.id == in_player_id), None)
        if not incoming:
            raise ValueError("Incoming player not found")
        if incoming.injured_weeks > 0:
            raise ValueError("Player is injured")
        self.lineup_ids = [
            in_player_id if pid == out_player_id else pid
            for pid in self.lineup_ids
        ]

    def set_player_role(self, player_id: str, role: str) -> None:
        player = next((p for p in self.squad if p.id == player_id), None)
        if not player:
            raise ValueError("Player not found")
        if role in ("D", "M", "A"):
            player.playing_as = role
            if player.id not in self.lineup_ids:
                if player.injured_weeks > 0:
                    raise ValueError("Player is injured")
                self.lineup_ids.append(player.id)
        else:  # bench
            player.playing_as = ""
            if player.id in self.lineup_ids:
                if len(self.lineup_ids) <= 1:
                    raise ValueError("Must have at least 1 player in lineup")
                self.lineup_ids.remove(player.id)

    def change_player_position(self, player_id: str, position: str) -> None:
        position = position.upper()
        if position not in {"D", "M", "A"}:
            raise ValueError("Position must be D, M or A")
        player = next((p for p in self.squad if p.id == player_id), None)
        if not player:
            raise ValueError("Player not found")
        player.position = position

    def auto_pick_lineup(self) -> None:
        available = sorted(
            [p for p in self.squad if p.injured_weeks == 0],
            key=lambda p: p.skill + p.energy * 0.1,
            reverse=True,
        )
        self.lineup_ids = [p.id for p in available[:LINEUP_SIZE]]

    def advance_retro_round(self) -> list[str]:
        """BASIC lines 6000-6100: energy/injury updates between matches."""
        messages: list[str] = []
        active_ids = set(self.lineup_ids)
        for player in self.squad:
            if player.injured_weeks > 0:
                # Injured player recovers: p(i)=3→1, energy +10
                player.injured_weeks -= 1
                player.energy = min(20, player.energy + 10)
            else:
                # Active players tire (-1/round); bench players recover quickly (+10/round)
                if player.id in active_ids:
                    player.energy = max(1, player.energy - 1)
                else:
                    player.energy = min(20, player.energy + 10)
                # Low energy increases injury risk; fresh players are less fragile.
                if player.energy >= 15:
                    injury_roll = 36
                elif player.energy >= 8:
                    injury_roll = 20
                else:
                    injury_roll = 10
                if player.id in active_ids and random.randint(1, injury_roll) == injury_roll:
                    player.injured_weeks = 1
                    messages.append(f"{player.name} is injured.")
        # Remove injured players from lineup, then fill only those vacated spots
        original_size = len(self.lineup_ids)
        self.lineup_ids = [pid for pid in self.lineup_ids
                           if self._player_by_id(pid).injured_weeks == 0]
        injured_count = original_size - len(self.lineup_ids)
        if injured_count > 0:
            already_in = set(self.lineup_ids)
            candidates = sorted(
                [p for p in self.squad if p.injured_weeks == 0 and p.id not in already_in],
                key=lambda p: p.skill + p.energy * 0.1,
                reverse=True,
            )
            for p in candidates[:injured_count]:
                self.lineup_ids.append(p.id)
        return messages

    def apply_match_result(self, won: bool, drew: bool) -> None:
        """Update retro morale after a match (BASIC lines 4350-4374)."""
        if won:
            self.retro_morale = self.retro_morale + (20 - self.retro_morale) // 2
        elif not drew:
            self.retro_morale = self.retro_morale // 2
        self.retro_morale = max(1, min(20, self.retro_morale))
        # Sync all active players' morale to team morale
        for player in self.active_players:
            old = player.morale
            player.morale = self.retro_morale
            player.morale_delta = player.morale - old

    def apply_modern_match_events(self, events: list) -> None:
        """
        Per-player morale updates after a modern-mode match.

        Rules (from spec):
          Playing: morale +1
          Not playing: morale -1
          Goal scorer or interceptor: +3 total (i.e. +2 extra on top of the +1)
          Wide shooter: -2 total (i.e. -3 extra on top of the +1)

        The relation "failure = half of success magnitude" maps to:
          success extra = +2, failure extra = -3  (so final: +3 vs -2)

        Team retro_morale is then updated to the rounded average of active players.
        """
        active_ids = set(self.lineup_ids)

        # Base adjustment: +1 for playing, -1 for bench
        deltas: dict[str, int] = {
            p.id: (1 if p.id in active_ids else -1)
            for p in self.squad
        }

        # Named event adjustments
        for event in events:
            if event.team_name != self.name:
                continue
            player = next((p for p in self.squad if p.name == event.player_name), None)
            if not player:
                continue
            if event.event_type in ("goal", "save"):
                deltas[player.id] = deltas.get(player.id, 0) + 2   # → +3 total
            elif event.event_type == "wide":
                deltas[player.id] = deltas.get(player.id, 0) - 3   # → -2 total

        # Apply and clamp
        for player in self.squad:
            old = player.morale
            player.morale = max(1, min(20, player.morale + deltas.get(player.id, 0)))
            player.morale_delta = player.morale - old

        # Sync team morale = rounded average of active players' morale
        if self.active_players:
            avg = round(sum(p.morale for p in self.active_players) / len(self.active_players))
            self.retro_morale = max(1, min(20, avg))

    def apply_gate_income(self, won: bool, drew: bool) -> None:
        """BASIC lines 7020-7070: gate money adjusts based on result."""
        d2 = retro_div_multiplier(self.division)
        if won:
            # g = g + INT(((10000*d2)-g)/10) — approaches 10000*d2
            self.gate_money += (10000 * d2 - self.gate_money) // 10
        elif not drew:
            # g = g - INT(g/10), minimum 1000
            self.gate_money -= self.gate_money // 10
            self.gate_money = max(1000, self.gate_money)
        self.cash += self.gate_money

    def _player_by_id(self, player_id: str) -> Player:
        return next(p for p in self.squad if p.id == player_id)

    def to_dict(self) -> dict:
        active = set(self.lineup_ids)
        d2 = retro_div_multiplier(self.division)
        return {
            "name": self.name,
            "manager": self.manager,
            "is_human": self.is_human,
            "cash": self.cash,
            "loan": self.loan,
            "ground_rent": self.ground_rent,
            "wage_bill": self.wage_bill,
            "loan_interest": self.loan_interest,
            "loan_limit": self.loan_limit,
            "gate_money": self.gate_money,
            "retro_morale": self.retro_morale,
            "division": self.division,
            "div_multiplier": d2,
            "squad_size": len(self.squad),
            "lineup_size": len(self.lineup_ids),
            "max_squad_size": MAX_SQUAD_SIZE,
            "energy": self.energy,
            "morale": self.morale,
            "defence": self.defence,
            "midfield": self.midfield,
            "attack": self.attack,
            "strength": self.strength,
            "played": self.played,
            "won": self.won,
            "drawn": self.drawn,
            "lost": self.lost,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "goal_difference": self.goal_difference,
            "points": self.points,
            "squad": [p.to_dict(p.id in active) for p in self.squad],
        }


# ── Retro team/player creation ──────────────────────────────────────────────

def create_retro_player(index: int, d2: int) -> Player:
    """BASIC lines 8824-8830: randomise player stats for a new season."""
    name = ORIGINAL_PLAYER_NAMES[index]
    position = RETRO_POSITIONS[index]
    skill = random.randint(1, 5)          # r(i) = INT(v(i)/(5000*d2))
    value = 5000 * d2 * skill             # v(i) = INT(5000*d2*FN r(5))
    energy = random.randint(1, 20)        # y(i) = FN r(20)
    # Morale starts random around 10 so modern mode has per-player variation
    morale = random.randint(7, 13)
    sd, sm, sa = _position_skills(position, skill)
    return Player(
        id=f"p{index}",
        name=name,
        position=position,
        skill=skill,
        energy=energy,
        morale=morale,
        value=value,
        skill_d=sd,
        skill_m=sm,
        skill_a=sa,
    )


def create_retro_all_players(d2: int) -> list[Player]:
    return [create_retro_player(i, d2) for i in range(24)]


def create_retro_human_team(manager_name: str, team_name: str, division: int = 4) -> Team:
    """
    BASIC lines 8970-8987: pick 12 random players from the 24 to start with.
    11 are picked (p=2), 1 is available (p=1), remaining 12 stay unsigned (p=0).
    """
    d2 = retro_div_multiplier(division)
    all_players = create_retro_all_players(d2)

    indices = random.sample(range(24), 12)
    squad = [all_players[i] for i in indices]       # 12 in squad
    lineup_ids = [all_players[i].id for i in indices[:11]]  # 11 picked

    # Transfer market: the other 12 unsigned players
    transfer = [all_players[i] for i in range(24) if i not in indices]

    return (
        Team(
            name=team_name,
            manager=manager_name,
            is_human=True,
            squad=squad,
            lineup_ids=lineup_ids,
            cash=0,
            division=division,
            loan=0,
            ground_rent=500 * d2,
            gate_money=5000 * d2,
            retro_morale=10,
        ),
        transfer,
    )


# ── Modern team/player creation (kept for modern mode) ─────────────────────

def create_player(position: str, prefix: str = "p") -> Player:
    skill = random.randint(35, 88)
    value = max(5000, round(skill * 2200 + random.randint(-12000, 18000)))
    pos = position if position in POSITIONS else random.choice(POSITIONS)
    sd, sm, sa = _position_skills(pos, skill)
    return Player(
        id=f"{prefix}-{random.randint(100000, 999999)}",
        name=f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
        position=pos,
        skill=skill,
        energy=random.randint(55, 95),
        morale=random.randint(40, 80),
        value=value,
        skill_d=sd,
        skill_m=sm,
        skill_a=sa,
    )


def create_squad(prefix: str) -> list[Player]:
    shape = ["D"] * 5 + ["M"] * 5 + ["A"] * 4
    return [create_player(pos, prefix=prefix) for pos in shape]


def create_transfer_market(count: int = 12) -> list[Player]:
    return [create_player(random.choice(POSITIONS), prefix="market") for _ in range(count)]


def create_human_team(manager_name: str, index: int) -> Team:
    squad = create_squad(f"h{index}")
    team = Team(
        name=f"{(manager_name or f'Manager {index}')[:18]} FC",
        manager=manager_name or f"Manager {index}",
        is_human=True,
        squad=squad,
        lineup_ids=[],
        cash=280000,
        division=4,
        ground_rent=500,
        gate_money=5000,
        retro_morale=10,
    )
    team.auto_pick_lineup()
    return team


def create_ai_team(name: str, index: int, division: int = 4) -> Team:
    d2 = retro_div_multiplier(division)
    squad = [create_player(random.choice(POSITIONS), prefix=f"ai{index}") for _ in range(14)]
    team = Team(
        name=name,
        manager="AI",
        is_human=False,
        squad=squad,
        lineup_ids=[],
        cash=180000,
        division=division,
        ground_rent=500 * d2,
        gate_money=5000 * d2,
        retro_morale=10,
    )
    team.auto_pick_lineup()
    return team


def create_league(manager_names: list[str], league_size: int = 20) -> list[Team]:
    humans = [create_human_team(name, idx + 1) for idx, name in enumerate(manager_names)]
    ai_needed = max(0, league_size - len(humans))
    ai_names = [n for n in AI_CLUB_POOL if n not in {t.name for t in humans}]
    return humans + [create_ai_team(name, idx + 1) for idx, name in enumerate(ai_names[:ai_needed])]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _avg(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 1


def _position_skills(position: str, skill: int) -> tuple[int, int, int]:
    """Returns (skill_d, skill_m, skill_a). -1 per adjacent position, -2 for far."""
    adj = max(1, skill - 1)
    far = max(1, skill - 2)
    if position == "D":
        return skill, adj, far
    elif position == "M":
        return adj, skill, adj
    else:  # A
        return far, adj, skill


def _unit_strength(players: list[Player], position: str) -> int:
    unit = [p for p in players if (p.playing_as or p.position) == position]
    if not unit:
        return 1
    return sum(p.skill for p in unit)
