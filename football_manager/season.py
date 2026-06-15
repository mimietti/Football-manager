from __future__ import annotations

from dataclasses import dataclass, field
import random

from .match_engine import MatchResult, Tactics, apply_result, simulate_match
from .teams import (
    ALL_TEAM_NAMES,
    LEVEL_NAMES,
    MAX_SQUAD_SIZE,
    Player,
    Team,
    create_retro_all_players,
    create_retro_human_team,
    create_transfer_market,
    retro_div_multiplier,
)

DIVISION_SIZE = 16
NUM_DIVISIONS = 4
TOTAL_TEAMS = DIVISION_SIZE * NUM_DIVISIONS  # 64
LEAGUE_MATCHES_PER_SEASON = 15              # BASIC ml goes 1-15


# ── Division helpers ─────────────────────────────────────────────────────────

def division_team_names(division: int) -> list[str]:
    """Return the 16 team names for the given division (1-4)."""
    start = (division - 1) * DIVISION_SIZE
    return ALL_TEAM_NAMES[start : start + DIVISION_SIZE]


def cup_opponent_index(cup_round: int) -> int:
    """
    BASIC line 4118: v1 = INT((8-c)*8 + FN r(8))  (1-indexed, range 1-64).
    Returns a Python 0-indexed team index in ALL_TEAM_NAMES.
    """
    # (8-c)*8 + random(1-8)  in BASIC 1-indexed → subtract 1 for Python
    base = (7 - cup_round) * 8   # e.g. round 1 → base=56 (last 8 of Div4)
    return base + random.randint(0, 7)


def simulate_other_result(team_pts: int, opp_pts: int, ml: int) -> tuple[int, int]:
    """
    BASIC lines 7610-7620: simulate a non-player match using team form.
      goals = INT(z(r)/ml + RND*4)
    """
    if ml == 0:
        ml = 1
    home_g = max(0, int(team_pts / ml + random.random() * 4))
    away_g = max(0, int(opp_pts / ml + random.random() * 4))
    return home_g, away_g


# ── Season state ─────────────────────────────────────────────────────────────

@dataclass
class SeasonState:
    manager_names: list[str]
    # The active 16 teams in the human team's division (Team objects)
    teams: list[Team]
    # All 64 team names, ordered by division
    all_team_names: list[str]
    # League standing arrays for the division (indices 0-15)
    div_pts: list[int]         # z(i) — league points
    div_gf: list[int]          # b(i) — goals for
    div_ga: list[int]          # c(i) — goals against
    # Retro season counters (mirror BASIC variables)
    retro_ma: int = 1          # ma: 1,2 → league; 0 → cup
    retro_ml: int = 0          # ml: 0-15 (league match counter)
    retro_fa: int = 0          # fa: 0=in cup, 1=eliminated
    retro_cup_round: int = 0   # c: 0=not started, 1-8
    division: int = 4          # d1: human team's current division
    skill_level: int = 1
    tactics: Tactics = field(default_factory=Tactics)
    last_human_matches: list[MatchResult] = field(default_factory=list)
    last_other_matches: list[dict] = field(default_factory=list)
    transfer_market: list[Player] = field(default_factory=list)
    transfer_offer_id: str | None = None
    transfer_bought_this_window: bool = False
    transfer_sold_this_window: bool = False
    season_number: int = 0
    transfer_window: bool = False   # opens after each match, closes when next match starts
    # cup replay flag (set when a cup match ends level)
    _cup_replay: bool = False

    # ── Accessors ────────────────────────────────────────────────────────────

    @property
    def season_over(self) -> bool:
        """BASIC line 8100: IF ml<15 OR fa=0 THEN continue."""
        return self.retro_ml >= LEAGUE_MATCHES_PER_SEASON and self.retro_fa == 1

    def human_teams(self) -> list[Team]:
        return [t for t in self.teams if t.is_human]

    def find_team(self, name: str) -> Team:
        return next(t for t in self.teams if t.name == name)

    def _division_names(self) -> list[str]:
        start = (self.division - 1) * DIVISION_SIZE
        return self.all_team_names[start : start + DIVISION_SIZE]

    def table(self) -> list[Team]:
        return sorted(
            self.teams,
            key=lambda t: (t.points, t.goal_difference, t.goals_for),
            reverse=True,
        )

    def next_fixture_description(self) -> dict:
        """Return description of the upcoming match."""
        if self.season_over:
            return {"type": "season_over"}
        lc, c_next, ml_next = self._next_lc()
        human = self.human_teams()[0] if self.human_teams() else None
        if not human:
            return {"type": "no_human"}
        if lc == 1:
            # Cup: determine opponent name
            opp_idx = self._cup_opp_index(c_next)
            opp_name = self.all_team_names[opp_idx]
            return {"type": "cup", "cup_round": c_next, "opponent": opp_name}
        else:
            opp_name = self._league_opp_name(ml_next)
            return {"type": "league", "league_match": ml_next, "opponent": opp_name}

    def _next_lc(self) -> tuple[int, int, int]:
        """Determine next match type (BASIC lines 4000-4010)."""
        ma = self.retro_ma + 1
        lc = 2  # league
        if ma == 3:
            ma = 0
            lc = 1  # cup
        if self.retro_fa == 1:
            lc = 2  # out of cup → league
        c_next = self.retro_cup_round + 1 if lc == 1 else self.retro_cup_round
        ml_next = self.retro_ml + 1 if lc == 2 else self.retro_ml
        return lc, c_next, ml_next

    def _cup_opp_index(self, cup_round: int) -> int:
        """BASIC line 4118-4119: pick cup opponent, reroll if same as player."""
        human = self.human_teams()[0]
        human_idx = self.all_team_names.index(human.name)
        for _ in range(50):
            idx = cup_opponent_index(cup_round)
            if idx != human_idx:
                return idx
        return (human_idx + 1) % TOTAL_TEAMS

    def _league_opp_name(self, ml: int) -> str:
        """
        BASIC line 4125: v1 = ml + lt (sequential opponents).
        The human team is at division slot index 15 (last); opponents are 0-14.
        """
        div_names = self._division_names()
        human = self.human_teams()[0]
        opponent_names = [n for n in div_names if n != human.name]
        idx = (ml - 1) % len(opponent_names)
        return opponent_names[idx]

    # ── play_round ───────────────────────────────────────────────────────────

    def play_round(self) -> list[MatchResult]:
        if self.season_over:
            return self.last_human_matches

        self.transfer_window = False   # close window while match is being played
        self.transfer_offer_id = None
        self.transfer_bought_this_window = False
        self.transfer_sold_this_window = False
        self.last_other_matches = []   # cleared each round; only populated for league
        lc, cup_round_next, ml_next = self._next_lc()

        # Advance counters
        self.retro_ma = 0 if (self.retro_ma + 1) == 3 else (self.retro_ma + 1)
        if lc == 2:
            self.retro_ml = ml_next

        human_results: list[MatchResult] = []

        for human in self.human_teams():
            # Injuries / energy update between matches (BASIC lines 6000-6100)
            messages = human.advance_retro_round()

            if lc == 1:
                # ── FA Cup match ─────────────────────────────────────────────
                result = self._play_cup_match(human, cup_round_next)
                human_results.append(result)
                for msg in messages:
                    result.report.append(f"{human.name}: {msg}")
                human_won = result.home_goals > result.away_goals if human.name == result.home else result.away_goals > result.home_goals
                drew = result.home_goals == result.away_goals

                if drew:
                    # Replay (BASIC lines 4330-4338): play again immediately
                    replay = self._play_cup_match(human, cup_round_next)
                    replay.report.insert(0, "REPLAY:")
                    human_results.append(replay)
                    human_won = replay.home_goals > replay.away_goals if human.name == replay.home else replay.away_goals > replay.home_goals
                    drew = replay.home_goals == replay.away_goals
                    if drew:
                        # Second replay draw → toss (BASIC doesn't model this, give 50/50)
                        human_won = random.random() > 0.5
                        drew = False
                        replay.report.append("After replay: match decided by toss." +
                                             (" You go through!" if human_won else " You're eliminated."))

                # BASIC line 5200-5237: process cup result
                if human_won:
                    human.retro_morale = human.retro_morale + (20 - human.retro_morale) // 2
                    human.retro_morale = min(20, human.retro_morale)
                    human.cash += self._cup_prize(cup_round_next)
                    if cup_round_next == 8:
                        result.report.append("FA CUP WINNERS!")
                        self.retro_fa = 1  # fa=1 after cup complete
                    else:
                        result.report.append(f"Through to cup round {cup_round_next + 1}!")
                        self.retro_cup_round = cup_round_next
                else:
                    human.retro_morale = human.retro_morale // 2
                    human.retro_morale = max(1, human.retro_morale)
                    self.retro_fa = 1
                    result.report.append("Eliminated from the FA Cup.")

            else:
                # ── League match ─────────────────────────────────────────────
                opp_name = self._league_opp_name(ml_next)

                # Find or proxy opponent
                try:
                    ai_team = self.find_team(opp_name)
                    opp_in_teams = True
                except StopIteration:
                    # AI team not in teams list (shouldn't happen for same division)
                    ai_team = _make_proxy_team(opp_name, self.division)
                    opp_in_teams = False

                # AI league form for attribute calculation
                opp_slot = self._div_slot(opp_name)
                ai_pts = self.div_pts[opp_slot] if opp_slot >= 0 else 0

                human_is_home = (ml_next % 2 == 1)  # alternate home/away
                home = human if human_is_home else ai_team
                away = ai_team if human_is_home else human

                self.tactics.skill_level = self.skill_level
                result = simulate_match(
                    home, away, self.tactics,
                    ai_league_pts=ai_pts,
                    league_match_num=max(1, ml_next),
                    cup_round=0,
                    human_is_home=human_is_home,
                )
                human_results.append(result)
                for msg in messages:
                    result.report.append(f"{human.name}: {msg}")

                # Apply result to Team objects
                apply_result(home, away, result)
                if not opp_in_teams:
                    # Update division arrays only
                    pass

                # Update division stats arrays
                human_slot = self._div_slot(human.name)
                if human_slot >= 0:
                    self.div_pts[human_slot] = human.points
                    self.div_gf[human_slot] = human.goals_for
                    self.div_ga[human_slot] = human.goals_against

                if opp_in_teams:
                    if opp_slot >= 0:
                        self.div_pts[opp_slot] = ai_team.points
                        self.div_gf[opp_slot] = ai_team.goals_for
                        self.div_ga[opp_slot] = ai_team.goals_against

                # Gate money and morale for human
                human_won = result.home_goals > result.away_goals if human_is_home else result.away_goals > result.home_goals
                drew = result.home_goals == result.away_goals
                human.apply_gate_income(human_won, drew)
                if self.tactics.engine_mode == "modern":
                    human.apply_modern_match_events(result.events)
                else:
                    human.apply_match_result(human_won, drew)

                # Simulate other 7 matches in division (BASIC lines 7500-7700)
                self._simulate_other_league_matches(human.name, opp_name, ml_next)

                result.report.append(
                    f"Gate receipts: £{human.gate_money:,}. "
                    f"Cash: £{human.cash:,}."
                )

            # Weekly finances (BASIC line 8000)
            fin = human.apply_weekly_finance()
            human_results[-1].report.append(
                f"Weekly costs — wages £{fin['wages']:,}, "
                f"rent £{fin['rent']:,}, interest £{fin['interest']:,}."
            )

        self.last_human_matches = human_results
        self.transfer_window = True    # open window after match
        self._pick_transfer_offer()
        return human_results

    def _pick_transfer_offer(self) -> None:
        self.transfer_offer_id = random.choice(self.transfer_market).id if self.transfer_market else None

    def _current_transfer_offer(self) -> Player | None:
        offer_id = getattr(self, "transfer_offer_id", None)
        if not offer_id:
            return None
        return next((p for p in self.transfer_market if p.id == offer_id), None)

    # ── Cup prize money ───────────────────────────────────────────────────────

    def _cup_prize(self, cup_round: int) -> int:
        """BASIC line 4620/4622: x9 = gate or special amounts."""
        if cup_round == 7:
            return 50000
        if cup_round == 8:
            return 100000
        human = self.human_teams()[0]
        return human.gate_money  # BASIC: x9=g

    # ── Division slot lookup ─────────────────────────────────────────────────

    def _div_slot(self, team_name: str) -> int:
        """Return 0-15 index of team within its division, or -1 if not found."""
        div_names = self._division_names()
        try:
            return div_names.index(team_name)
        except ValueError:
            return -1

    # ── Cup match helper ─────────────────────────────────────────────────────

    def _play_cup_match(self, human: Team, cup_round: int) -> MatchResult:
        opp_idx = self._cup_opp_index(cup_round)
        opp_name = self.all_team_names[opp_idx]
        opp_div = opp_idx // DIVISION_SIZE + 1

        # Proxy AI team for the cup opponent
        opp = _make_proxy_team(opp_name, opp_div)

        human_is_home = random.random() > 0.5
        home = human if human_is_home else opp
        away = opp if human_is_home else human

        self.tactics.skill_level = self.skill_level
        return simulate_match(
            home, away, self.tactics,
            ai_league_pts=0,
            league_match_num=1,
            cup_round=cup_round,
            human_is_home=human_is_home,
        )

    # ── Simulate other division matches ──────────────────────────────────────

    def _simulate_other_league_matches(
        self, human_name: str, human_opp_name: str, ml: int
    ) -> None:
        """
        BASIC lines 7500-7700: simulate the other 7 matches in the round.
        Uses simple form-based goal generation.
        """
        div_names = self._division_names()
        taken: set[str] = {human_name, human_opp_name}
        available = [n for n in div_names if n not in taken]
        random.shuffle(available)
        pairs = [(available[i], available[i + 1]) for i in range(0, len(available) - 1, 2)]

        other_results: list[dict] = []
        for home_name, away_name in pairs:
            home_slot = self._div_slot(home_name)
            away_slot = self._div_slot(away_name)
            home_pts = self.div_pts[home_slot] if home_slot >= 0 else 0
            away_pts = self.div_pts[away_slot] if away_slot >= 0 else 0
            hg, ag = simulate_other_result(home_pts, away_pts, ml)

            other_results.append({"home": home_name, "home_goals": hg, "away": away_name, "away_goals": ag})

            if home_slot >= 0:
                self.div_pts[home_slot] += 3 if hg > ag else (1 if hg == ag else 0)
                self.div_gf[home_slot] += hg
                self.div_ga[home_slot] += ag
            if away_slot >= 0:
                self.div_pts[away_slot] += 3 if ag > hg else (1 if hg == ag else 0)
                self.div_gf[away_slot] += ag
                self.div_ga[away_slot] += hg

            # Sync to Team objects if in teams list
            for slot, name in ((home_slot, home_name), (away_slot, away_name)):
                if slot < 0:
                    continue
                try:
                    team = self.find_team(name)
                    team.points = self.div_pts[slot]
                    team.goals_for = self.div_gf[slot]
                    team.goals_against = self.div_ga[slot]
                except StopIteration:
                    pass

        self.last_other_matches = other_results

    # ── Management actions ───────────────────────────────────────────────────

    def buy_player(self, team_name: str, player_id: str) -> None:
        if not self.transfer_window:
            raise ValueError("Transfer window is closed — play a match first.")
        if getattr(self, "transfer_bought_this_window", False):
            raise ValueError("You can buy only one player this round.")
        team = self.find_team(team_name)
        player = self._current_transfer_offer()
        if not player or player.id != player_id:
            raise ValueError("That player is not available this round.")
        if team.cash < player.value:
            raise ValueError(
                f"Not enough cash — need £{player.value:,}, have £{team.cash:,}."
            )
        if len(team.squad) >= MAX_SQUAD_SIZE:
            raise ValueError(f"Squad full (max {MAX_SQUAD_SIZE} players).")
        team.buy(player)
        self.transfer_market = [p for p in self.transfer_market if p.id != player_id]
        self.transfer_offer_id = None
        self.transfer_bought_this_window = True

    def sell_player(self, team_name: str, player_id: str) -> None:
        if not self.transfer_window:
            raise ValueError("Transfer window is closed — play a match first.")
        if getattr(self, "transfer_sold_this_window", False):
            raise ValueError("You can sell only one player this round.")
        team = self.find_team(team_name)
        sold = team.sell(player_id)
        self.transfer_market.append(sold)
        self.transfer_sold_this_window = True

    def toggle_lineup(self, team_name: str, player_id: str) -> None:
        self.find_team(team_name).toggle_lineup(player_id)

    def swap_lineup(self, team_name: str, in_player_id: str, out_player_id: str) -> None:
        self.find_team(team_name).swap_lineup(in_player_id, out_player_id)

    def set_player_role(self, team_name: str, player_id: str, role: str) -> None:
        self.find_team(team_name).set_player_role(player_id, role)

    def change_player_position(self, team_name: str, player_id: str, position: str) -> None:
        self.find_team(team_name).change_player_position(player_id, position)

    def borrow(self, team_name: str, amount: int) -> None:
        self.find_team(team_name).borrow(amount)

    def repay(self, team_name: str, amount: int) -> None:
        self.find_team(team_name).repay(amount)

    def set_skill_level(self, level: int) -> None:
        if level < 1 or level > 7:
            raise ValueError("Skill level must be 1-7")
        self.skill_level = level
        self.tactics.skill_level = level

    def rename_team(self, team_name: str, new_name: str) -> None:
        new_name = new_name.strip()[:18]
        if not new_name:
            raise ValueError("Team name cannot be empty")
        if any(t.name == new_name for t in self.teams):
            raise ValueError("Name already taken")
        team = self.find_team(team_name)
        old_name = team.name
        team.name = new_name
        # Update in all_team_names
        try:
            idx = self.all_team_names.index(old_name)
            self.all_team_names[idx] = new_name
        except ValueError:
            pass

    def rename_player(self, team_name: str, player_id: str, new_name: str) -> None:
        new_name = new_name.strip()[:10]
        if not new_name:
            raise ValueError("Player name cannot be empty")
        team = self.find_team(team_name)
        player = next((p for p in team.squad if p.id == player_id), None)
        if not player:
            raise ValueError("Player not found")
        player.name = new_name

    # ── End of season ────────────────────────────────────────────────────────

    def end_season(self) -> dict:
        """
        BASIC lines 8100-8699: promotion, relegation, and reset for new season.
        Returns a summary of what happened.
        """
        sorted_teams = self.table()
        human = self.human_teams()[0] if self.human_teams() else None
        summary: dict = {"promotions": [], "relegations": [], "champions": None}

        if human:
            pos = next(i for i, t in enumerate(sorted_teams) if t.name == human.name) + 1
            summary["position"] = pos
            summary["division"] = self.division

            if self.division > 1 and pos <= 3:
                self.division -= 1
                human.division = self.division
                summary["promotions"].append(human.name)
                if pos == 1:
                    summary["champions"] = human.name
            elif self.division < 4 and pos >= 14:
                self.division += 1
                human.division = self.division
                summary["relegations"].append(human.name)

        # Reset season counters (BASIC lines 8800-8819)
        self.retro_ma = 1
        self.retro_ml = 0
        self.retro_fa = 0
        self.retro_cup_round = 0
        self.season_number += 1
        self.transfer_window = False
        self.transfer_offer_id = None
        self.transfer_bought_this_window = False
        self.transfer_sold_this_window = False

        # Reset league stats for all teams
        self.div_pts = [0] * DIVISION_SIZE
        self.div_gf = [0] * DIVISION_SIZE
        self.div_ga = [0] * DIVISION_SIZE
        for team in self.teams:
            team.played = team.won = team.drawn = team.lost = 0
            team.goals_for = team.goals_against = team.points = 0

        # Rebuild division team list for human's (possibly new) division
        self.teams = _build_division_teams(
            human.name if human else None,
            self.division,
            human,
        )

        return summary

    # ── Pre-match comparison ─────────────────────────────────────────────────

    def _pre_match_attrs(self) -> dict | None:
        """Human team actual attrs vs estimated AI attrs for the next fixture."""
        human = self.human_teams()[0] if self.human_teams() else None
        if not human or self.season_over:
            return None
        nf = self.next_fixture_description()
        opponent = nf.get("opponent", "")
        if not opponent:
            return None
        lc, _, ml_next = self._next_lc()
        player_div_index = max(0, human.division - 1)
        if lc == 1:  # cup
            opp_indices = [i for i, n in enumerate(self.all_team_names) if n == opponent]
            opp_div_index = (opp_indices[0] // DIVISION_SIZE) if opp_indices else 3
            base = self.skill_level + player_div_index - (opp_div_index + 1)
            ai_val = max(1, min(20, 8 + base))   # 8 = midpoint of randint(1,16)
        else:  # league
            opp_slot = self._div_slot(opponent)
            ai_pts = self.div_pts[opp_slot] if opp_slot >= 0 else 0
            form = int(ai_pts / max(1, ml_next))
            ai_val = max(1, min(20, 7 + self.skill_level + form))  # 7 = midpoint of randint(1,14)
        from .match_engine import _retro_player_attrs
        a = _retro_player_attrs(human)
        return {
            "opponent": opponent,
            "human": {
                "energy": a[0], "morale": a[1],
                "defence": a[2], "midfield": a[3], "attack": a[4],
                "lineup_size": len(human.lineup_ids),
            },
            "ai": {
                "energy": ai_val, "morale": ai_val,
                "defence": ai_val, "midfield": ai_val, "attack": ai_val,
            },
        }

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_public_dict(self) -> dict:
        div_names = self._division_names()
        league_table = []
        for i, name in enumerate(div_names):
            try:
                team = self.find_team(name)
                league_table.append({
                    "name": team.name,
                    "pts": team.points,
                    "gf": team.goals_for,
                    "ga": team.goals_against,
                    "gd": team.goal_difference,
                    "is_human": team.is_human,
                })
            except StopIteration:
                league_table.append({
                    "name": name,
                    "pts": self.div_pts[i],
                    "gf": self.div_gf[i],
                    "ga": self.div_ga[i],
                    "gd": self.div_gf[i] - self.div_ga[i],
                    "is_human": False,
                })

        league_table.sort(key=lambda x: (x["pts"], x["gd"], x["gf"]), reverse=True)

        next_fixture = self.next_fixture_description()
        if self.transfer_window and not getattr(self, "transfer_bought_this_window", False):
            if not self._current_transfer_offer() and self.transfer_market:
                self._pick_transfer_offer()

        level_name = LEVEL_NAMES[self.skill_level - 1] if 1 <= self.skill_level <= 7 else ""
        return {
            "managers": self.manager_names,
            "division": self.division,
            "div_multiplier": retro_div_multiplier(self.division),
            "skill_level": self.skill_level,
            "skill_name": level_name,
            "season_number": self.season_number,
            "league_match": self.retro_ml,
            "total_league_matches": LEAGUE_MATCHES_PER_SEASON,
            "cup_round": self.retro_cup_round,
            "cup_eliminated": self.retro_fa == 1,
            "season_over": self.season_over,
            "mode": self.tactics.engine_mode,
            "formation": self.tactics.formation,
            "pressing": self.tactics.pressing,
            "next_fixture": next_fixture,
            "pre_match": self._pre_match_attrs(),
            "human_teams": [t.to_dict() for t in self.human_teams()],
            "table": league_table,
            "last_matches": [m.to_dict() for m in self.last_human_matches],
            "other_matches": self.last_other_matches,
            "transfer_market": (
                [self._current_transfer_offer().to_dict(False)]
                if self.transfer_window and self._current_transfer_offer()
                else []
            ),
            "transfer_window": self.transfer_window,
            "transfer_bought_this_window": getattr(self, "transfer_bought_this_window", False),
            "transfer_sold_this_window": getattr(self, "transfer_sold_this_window", False),
            "inspiration": "Faithful port of the 1982 Football Manager by Kevin & John Toms.",
        }


# ── Factory ──────────────────────────────────────────────────────────────────

def create_new_season(
    manager_names: list[str] | None = None,
    league_size: int = 16,          # ignored in retro — always 4×16
    team_index: int = 63,           # 0-63 across all 64 teams; default York City
) -> SeasonState:
    managers = [n.strip() for n in (manager_names or ["Manager"]) if n.strip()] or ["Manager"]
    manager_name = managers[0]
    team_index = max(0, min(63, team_index))
    division = (team_index // 16) + 1
    team_name = ALL_TEAM_NAMES[team_index]

    human_team, unsigned_players = create_retro_human_team(manager_name, team_name, division)

    teams = _build_division_teams(team_name, division, human_team)

    return SeasonState(
        manager_names=managers,
        teams=teams,
        all_team_names=list(ALL_TEAM_NAMES),
        div_pts=[0] * DIVISION_SIZE,
        div_gf=[0] * DIVISION_SIZE,
        div_ga=[0] * DIVISION_SIZE,
        retro_ma=1,
        retro_ml=0,
        retro_fa=0,
        retro_cup_round=0,
        division=division,
        skill_level=1,
        tactics=Tactics(engine_mode="modern"),
        transfer_market=unsigned_players,
        season_number=1,
    )


def _build_division_teams(
    human_name: str | None,
    division: int,
    human_team: Team | None,
) -> list[Team]:
    """Build the 16-team list for the given division, placing human team last."""
    div_names = division_team_names(division)
    teams: list[Team] = []
    for name in div_names:
        if name == human_name and human_team:
            teams.append(human_team)
        else:
            d2 = retro_div_multiplier(division)
            # AI team: minimal placeholder (stats computed by match engine formula)
            from .teams import Team as T
            ai = T(
                name=name,
                manager="AI",
                is_human=False,
                squad=[],
                lineup_ids=[],
                cash=0,
                division=division,
                ground_rent=500 * d2,
                gate_money=5000 * d2,
                retro_morale=10,
            )
            teams.append(ai)
    return teams


def _make_proxy_team(name: str, division: int) -> Team:
    """Create a minimal AI proxy team for use as a match opponent."""
    d2 = retro_div_multiplier(division)
    from .teams import Team as T
    return T(
        name=name,
        manager="AI",
        is_human=False,
        squad=[],
        lineup_ids=[],
        cash=0,
        division=division,
        ground_rent=500 * d2,
        gate_money=5000 * d2,
        retro_morale=10,
    )
