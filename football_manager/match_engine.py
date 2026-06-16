from __future__ import annotations

from dataclasses import dataclass, field
import random

from .teams import Team


RETRO_FACTORS = ("energy", "morale", "defence", "midfield", "attack")


@dataclass
class Tactics:
    engine_mode: str = "retro"
    formation: str = "balanced"
    pressing: str = "normal"
    skill_level: int = 1


@dataclass
class MatchEvent:
    """A named moment in a modern-mode match involving a human team player."""
    event_type: str    # "goal" | "save" | "wide"
    player_name: str
    team_name: str
    home_score: int
    away_score: int

    def to_dict(self) -> dict:
        return {
            "type": self.event_type,
            "player": self.player_name,
            "team": self.team_name,
            "home_score": self.home_score,
            "away_score": self.away_score,
        }


@dataclass
class MatchResult:
    home: str
    away: str
    home_goals: int
    away_goals: int
    report: list[str]
    factor_scores: dict[str, int]
    events: list[MatchEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "home": self.home,
            "away": self.away,
            "home_goals": self.home_goals,
            "away_goals": self.away_goals,
            "report": self.report,
            "factor_scores": self.factor_scores,
            "events": [e.to_dict() for e in self.events],
        }


def simulate_match(
    home: Team,
    away: Team,
    tactics: Tactics | None = None,
    *,
    ai_league_pts: int = 0,
    league_match_num: int = 1,
    cup_round: int = 0,
    human_is_home: bool = True,
) -> MatchResult:
    tactics = tactics or Tactics()
    kwargs = dict(
        ai_league_pts=ai_league_pts,
        league_match_num=league_match_num,
        cup_round=cup_round,
        human_is_home=human_is_home,
    )
    if tactics.engine_mode == "modern":
        return _simulate_modern_match(home, away, tactics, **kwargs)
    return _simulate_retro_match(home, away, tactics, **kwargs)


# ── GK helpers ───────────────────────────────────────────────────────────────

def _active_gk(team: Team):
    """Return the first active GK in the lineup, or None."""
    return next((p for p in team.active_players if p.position == "G"), None)


def _gk_save_prob(gk_skill_g: int, att_skill_a: int) -> float:
    """
    Probability that a GK (skill_g) saves a shot from an attacker (skill_a).
    Both are 1–5. Neutral at equal skills (~20%), up to ~40% for elite GK
    vs weak attacker, down to ~5% for weak GK vs elite attacker.
    """
    if gk_skill_g <= 0:
        return 0.0
    diff = gk_skill_g - att_skill_a        # −4 to +4
    return max(0.05, min(0.40, 0.20 + diff * 0.05))


# ── Shared attribute helpers ─────────────────────────────────────────────────

def _retro_player_attrs(team: Team) -> list[int]:
    """
    BASIC lines 6500-6560.
      a[0] = avg energy of picked players   (1-20)
      a[1] = team retro_morale              (1-20)
      a[2] = sum of skill of picked DEF
      a[3] = sum of skill of picked MID
      a[4] = sum of skill of picked ATT
    """
    active = team.active_players
    if not active:
        return [1, team.retro_morale, 1, 1, 1]
    energy_avg = sum(p.energy for p in active) // len(active)
    return [
        max(1, energy_avg),
        max(1, team.retro_morale),
        max(1, sum(p.skill for p in active if p.position == "D")),
        max(1, sum(p.skill for p in active if p.position == "M")),
        max(1, sum(p.skill for p in active if p.position == "A")),
    ]


def _retro_ai_attrs(
    skill_level: int,
    ai_league_pts: int,
    league_match_num: int,
    cup_round: int,
    player_div_index: int,
    opp_div_index: int,
) -> list[int]:
    """
    BASIC lines 4150-4155.
    League: u(i) = INT(FN r(14) + l1 + z/ml)    clamped 1-20
    Cup:    u(i) = INT(FN r(16) + l1 + pdiv - (odiv+1))  clamped 1-20
    """
    result: list[int] = []
    for _ in range(5):
        if cup_round > 0:
            raw = random.randint(1, 16) + skill_level + player_div_index - (opp_div_index + 1)
        else:
            form = ai_league_pts / max(1, league_match_num)
            raw = random.randint(1, 14) + skill_level + int(form)
        result.append(max(1, min(20, int(raw))))
    return result


# ── Retro match engine — faithful to BASIC lines 5000-5105 ──────────────────

def _scoring_attempt() -> int:
    return 1 if random.random() < 0.28 else 0


def _retro_pressure_line(team_name: str, factor: str) -> str:
    lines = {
        "energy": f"{team_name} start the move at a high tempo.",
        "morale": f"{team_name} look confident and push players forward.",
        "defence": f"{team_name} win it back and break quickly.",
        "midfield": f"{team_name} string passes together through midfield.",
        "attack": f"{team_name} carve out space around the box.",
    }
    return lines.get(factor, f"{team_name} build another attack.")


def _retro_missed_line(team_name: str, factor: str) -> str:
    lines = {
        "energy": f"{team_name} force the pace, but the final ball runs through.",
        "morale": f"{team_name} keep the pressure on, but the shot is blocked.",
        "defence": f"{team_name} counter from deep, but the chance is cleared.",
        "midfield": f"{team_name} work it into a good area, but the effort goes wide.",
        "attack": f"{team_name} get a sight of goal, but the keeper is equal to it.",
    }
    return lines.get(factor, f"{team_name} threaten, but cannot finish the move.")


def _simulate_retro_match(
    home: Team,
    away: Team,
    tactics: Tactics,
    *,
    ai_league_pts: int = 0,
    league_match_num: int = 1,
    cup_round: int = 0,
    human_is_home: bool = True,
) -> MatchResult:
    human_team = home if human_is_home else away
    ai_team = away if human_is_home else home

    player_div_index = max(0, human_team.division - 1)
    opp_div_index = max(0, getattr(ai_team, "division", human_team.division) - 1)

    a = _retro_player_attrs(human_team)
    u = _retro_ai_attrs(
        tactics.skill_level, ai_league_pts, league_match_num,
        cup_round, player_div_index, opp_div_index,
    )

    human_gk = _active_gk(human_team)
    # Estimate AI attacker skill from their attack factor (1-20 → 1-5 per player, ~3 attackers)
    ai_att_est = max(1, min(5, u[4] // 3))
    # Best human attacker's skill_a (for human attack, AI has no GK model)
    human_att_est = max(
        (p.skill_a for p in human_team.active_players if p.position == "A"),
        default=3,
    )

    player_goals = 0
    ai_goals = 0
    home_score = 0
    away_score = 0
    had_chance = False
    factor_scores: dict[str, int] = {}
    report: list[str] = [f"{human_team.name} v {ai_team.name}"]

    def record_goal(team_name: str) -> None:
        nonlocal home_score, away_score
        if team_name == home.name:
            home_score += 1
        else:
            away_score += 1
        report.append(f"GOAL - {team_name}! ({home_score}-{away_score})")

    for idx, factor in enumerate(RETRO_FACTORS):
        pa, ua = a[idx], u[idx]
        factor_scores[factor] = pa - ua

        if random.randint(1, 100) + (pa - ua) * 5 >= 75:
            had_chance = True
            g = _scoring_attempt() + _scoring_attempt()
            player_goals += g
            report.append(_retro_pressure_line(human_team.name, factor))
            if g:
                for _ in range(g):
                    record_goal(human_team.name)
            else:
                report.append(_retro_missed_line(human_team.name, factor))

        if random.randint(1, 100) + (ua - pa) * 5 >= 75:
            had_chance = True
            g_raw = _scoring_attempt() + _scoring_attempt()
            report.append(_retro_pressure_line(ai_team.name, factor))
            if g_raw > 0 and human_gk:
                sp = _gk_save_prob(0 if human_gk.injured_weeks > 0 else human_gk.skill_g, ai_att_est)
                g = sum(1 for _ in range(g_raw) if random.random() > sp)
                saved = g_raw - g
                if saved:
                    report.append(f"{human_gk.name} pulls off {'a save' if saved == 1 else str(saved) + ' saves'}!")
            else:
                g = g_raw
            ai_goals += g
            if g:
                for _ in range(g):
                    record_goal(ai_team.name)
            else:
                report.append(_retro_missed_line(ai_team.name, factor))

    if not had_chance:
        report.append("A tight first half gives both sides little room to play.")
        late_player_goals = _scoring_attempt()
        late_ai_raw = _scoring_attempt()
        if late_player_goals or late_ai_raw:
            report.append("The game finally opens up late on.")
        for _ in range(late_player_goals):
            player_goals += 1
            record_goal(human_team.name)
        late_ai_goals = 0
        if late_ai_raw and human_gk and random.random() < _gk_save_prob(0 if human_gk.injured_weeks > 0 else human_gk.skill_g, ai_att_est):
            report.append(f"{human_gk.name} denies them at the death!")
        elif late_ai_raw:
            late_ai_goals = 1
            ai_goals += 1
            record_goal(ai_team.name)
        if not late_player_goals and not late_ai_goals:
            report.append("Neither side can turn possession into a clear chance.")

    home_goals = home_score
    away_goals = away_score
    report.extend(_score_report(home, away, home_goals, away_goals))

    return MatchResult(home.name, away.name, home_goals, away_goals, report, factor_scores)


# ── Modern match engine — per-player named events ───────────────────────────

def _pick_player_weighted(players, role: str):
    """
    Weighted player draw for modern events.
    role="attacker": attackers most likely, then midfielders, then defenders.
    role="defender": defenders most likely, then midfielders, then attackers.
    Skill and energy add to the weight.
    """
    if not players:
        return None
    pos_weights = (
        {"A": 6, "M": 3, "D": 1} if role == "attacker"
        else {"D": 6, "M": 3, "A": 1}
    )
    weights = [
        max(0.1, pos_weights.get(p.position, 1) + p.skill + p.energy // 4)
        for p in players
    ]
    return random.choices(players, weights=weights, k=1)[0]


def _on_target_prob(player) -> float:
    """Higher skill → shot more likely on target. Retro skill 1-5 → 0.40-0.80."""
    if player is None:
        return 0.50
    return max(0.20, min(0.90, 0.30 + player.skill * 0.10))


def _goal_prob(att: int, def_: int, att_en: int, def_en: int, att_mo: int, def_mo: int) -> float:
    """
    Weighted formula per the spec:
      attack vs defence (largest weight 0.5)
      energy difference (0.3)
      morale difference (0.2)
    """
    diff = (att - def_) * 0.5 + (att_en - def_en) * 0.3 + (att_mo - def_mo) * 0.2
    return max(0.08, min(0.72, 0.35 + diff * 0.015))


def _simulate_modern_match(
    home: Team,
    away: Team,
    tactics: Tactics,
    *,
    ai_league_pts: int = 0,
    league_match_num: int = 1,
    cup_round: int = 0,
    human_is_home: bool = True,
) -> MatchResult:
    human_team = home if human_is_home else away
    ai_team = away if human_is_home else home

    player_div_index = max(0, human_team.division - 1)
    opp_div_index = max(0, getattr(ai_team, "division", human_team.division) - 1)

    # Use the same retro attribute calculation for both sides — modern mode only
    # changes HOW attacks are resolved (named events, three outcomes, weights)
    a = _retro_player_attrs(human_team)
    u = _retro_ai_attrs(
        tactics.skill_level, ai_league_pts, league_match_num,
        cup_round, player_div_index, opp_div_index,
    )

    # Attack count driven by midfield (a[3] vs u[3])
    mid_diff = a[3] - u[3]
    human_attacks = max(1, 5 + round(mid_diff / 5) + random.randint(-1, 1))
    ai_attacks = max(1, 5 - round(mid_diff / 5) + random.randint(-1, 1))

    home_score = 0
    away_score = 0
    events: list[MatchEvent] = []
    report: list[str] = [f"{human_team.name} v {ai_team.name}"]
    report.append(
        f"Midfield battle creates {human_attacks} attacks for "
        f"{human_team.name} and {ai_attacks} for {ai_team.name}."
    )

    factor_scores = {
        "energy":   a[0] - u[0],
        "morale":   a[1] - u[1],
        "defence":  a[2] - u[2],
        "midfield": mid_diff,
        "attack":   a[4] - u[4],
    }

    active = human_team.active_players
    human_gk = _active_gk(human_team)
    ai_att_est = max(1, min(5, u[4] // 3))

    # ── Human team attacks ───────────────────────────────────────────────────
    for _ in range(human_attacks):
        shooter = _pick_player_weighted(
            [p for p in active if p.position != "G"], "attacker"
        )
        if random.random() > _on_target_prob(shooter):
            name = shooter.name if shooter else human_team.name
            report.append(f"{name} shoots wide.")
            if shooter:
                events.append(MatchEvent("wide", shooter.name, human_team.name, home_score, away_score))
        else:
            gp = _goal_prob(a[4], u[2], a[0], u[0], a[1], u[1])
            if random.random() < gp:
                if human_is_home:
                    home_score += 1
                else:
                    away_score += 1
                name = shooter.name if shooter else human_team.name
                report.append(f"GOAL — {name}! ({home_score}–{away_score})")
                if shooter:
                    events.append(MatchEvent("goal", shooter.name, human_team.name, home_score, away_score))
            else:
                name = shooter.name if shooter else "Shot"
                report.append(f"{name} is saved by {ai_team.name}.")

    # ── AI team attacks ──────────────────────────────────────────────────────
    for _ in range(ai_attacks):
        if random.random() > 0.55:   # AI has a fixed on-target rate (no named AI player)
            report.append(f"{ai_team.name} shoot wide.")
        else:
            gp = _goal_prob(u[4], a[2], u[0], a[0], u[1], a[1])
            if random.random() < gp:
                # Check GK save: shooter att_est vs GK skill_g
                if human_gk and random.random() < _gk_save_prob(0 if human_gk.injured_weeks > 0 else human_gk.skill_g, ai_att_est):
                    report.append(f"{human_gk.name} saves it!")
                    events.append(MatchEvent("save", human_gk.name, human_team.name, home_score, away_score))
                else:
                    if human_is_home:
                        away_score += 1
                    else:
                        home_score += 1
                    report.append(f"GOAL — {ai_team.name}! ({home_score}–{away_score})")
            else:
                interceptor = _pick_player_weighted(
                    [p for p in active if p.position != "G"], "defender"
                )
                if interceptor:
                    report.append(
                        f"{interceptor.name} intercepts {ai_team.name}'s attack."
                    )
                    events.append(
                        MatchEvent("save", interceptor.name, human_team.name, home_score, away_score)
                    )
                else:
                    report.append(f"{human_team.name} defend.")

    report.extend(_score_report(home, away, home_score, away_score))
    return MatchResult(
        home.name, away.name, home_score, away_score, report, factor_scores, events
    )


# ── Shared result application ────────────────────────────────────────────────

def apply_result(home: Team, away: Team, result: MatchResult) -> None:
    home.played += 1
    away.played += 1
    home.goals_for += result.home_goals
    home.goals_against += result.away_goals
    away.goals_for += result.away_goals
    away.goals_against += result.home_goals

    if result.home_goals > result.away_goals:
        home.won += 1
        away.lost += 1
        home.points += 3
    elif result.away_goals > result.home_goals:
        away.won += 1
        home.lost += 1
        away.points += 3
    else:
        home.drawn += 1
        away.drawn += 1
        home.points += 1
        away.points += 1


def _score_report(home: Team, away: Team, hg: int, ag: int) -> list[str]:
    if hg > ag:
        verdict = f"{home.name} take the points."
    elif ag > hg:
        verdict = f"{away.name} win away from home."
    else:
        verdict = "The match ends level."
    return [f"Final score: {home.name} {hg} – {ag} {away.name}.", verdict]
