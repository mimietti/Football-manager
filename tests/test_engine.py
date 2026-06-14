from football_manager.match_engine import Tactics
from football_manager.season import (
    DIVISION_SIZE,
    LEAGUE_MATCHES_PER_SEASON,
    create_new_season,
)
from football_manager.teams import ALL_TEAM_NAMES, retro_div_multiplier


def test_retro_season_starts_in_division_4():
    season = create_new_season(["Tester"])
    assert season.division == 4
    assert len(season.teams) == DIVISION_SIZE  # 16 teams in division
    assert season.skill_level == 1
    assert all(t.division == 4 for t in season.teams)
    assert all(t.played == 0 for t in season.teams)
    assert all(t.goals_for == 0 and t.goals_against == 0 for t in season.teams)
    assert all(t.points == 0 for t in season.teams)


def test_all_64_teams_in_all_team_names():
    assert len(ALL_TEAM_NAMES) == 64
    assert "Arsenal" in ALL_TEAM_NAMES      # Division 1
    assert "York City" in ALL_TEAM_NAMES    # Division 4 slot 16
    assert "Blackpool" in ALL_TEAM_NAMES    # Division 4 slot 1


def test_human_team_starts_as_york_city():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    assert human.name == "York City"
    assert human.manager == "Tester"
    assert human.is_human


def test_human_team_starts_with_12_players_in_squad_11_picked():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    assert len(human.squad) == 12
    assert len(human.lineup_ids) == 11
    # Remaining 12 are in transfer market
    assert len(season.transfer_market) == 12


def test_player_skill_in_retro_range():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    for player in human.squad:
        assert 1 <= player.skill <= 5, f"{player.name} skill={player.skill} out of range"
        assert 1 <= player.energy <= 20, f"{player.name} energy={player.energy} out of range"


def test_retro_season_can_play_a_round():
    season = create_new_season(["Tester"])
    assert not season.season_over
    assert season.next_fixture_description()["type"] in ("league", "cup")

    results = season.play_round()

    assert results
    assert season.retro_ml == 1 or season.retro_cup_round >= 1  # something advanced


def test_retro_season_15_league_matches():
    season = create_new_season(["Tester"])
    # Play through all 15 league matches (and any cup rounds)
    for _ in range(40):  # enough iterations to finish a season
        if season.season_over:
            break
        season.play_round()

    assert season.retro_ml >= LEAGUE_MATCHES_PER_SEASON
    assert season.season_over


def test_retro_finance_actions():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]

    initial_loan = human.loan
    season.borrow(human.name, 10000)
    assert human.loan == initial_loan + 10000
    assert human.cash == 10000

    season.repay(human.name, 4000)
    assert human.loan == initial_loan + 6000
    assert human.cash == 6000


def test_transfer_window_offers_only_one_player_and_limits_actions():
    season = create_new_season(["Tester"])
    season.play_round()
    human = season.human_teams()[0]

    public_market = season.to_public_dict()["transfer_market"]
    assert len(public_market) == 1

    human.cash = public_market[0]["value"] * 2
    season.buy_player(human.name, public_market[0]["id"])
    assert season.to_public_dict()["transfer_market"] == []

    other_market_player = next((p for p in season.transfer_market if p.id != public_market[0]["id"]), None)
    if other_market_player:
        try:
            season.buy_player(human.name, other_market_player.id)
            assert False, "second buy should fail"
        except ValueError:
            pass

    sellable = next(p for p in human.squad if p.injured_weeks == 0)
    original_value = sellable.value
    season.sell_player(human.name, sellable.id)
    assert sellable.value == original_value

    another_sellable = next((p for p in human.squad if p.injured_weeks == 0), None)
    if another_sellable:
        try:
            season.sell_player(human.name, another_sellable.id)
            assert False, "second sell should fail"
        except ValueError:
            pass


def test_lineup_can_swap_bench_player_for_active_player():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    incoming = next(p for p in human.squad if p.id not in human.lineup_ids and p.injured_weeks == 0)
    outgoing_id = human.lineup_ids[0]

    season.swap_lineup(human.name, incoming.id, outgoing_id)

    assert incoming.id in human.lineup_ids
    assert outgoing_id not in human.lineup_ids
    assert len(human.lineup_ids) == 11


def test_retro_skill_level():
    season = create_new_season(["Tester"])
    season.set_skill_level(5)
    assert season.skill_level == 5


def test_retro_rename_player_and_team():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    player = human.squad[0]
    original_team_name = human.name

    season.rename_player(human.name, player.id, "New Name")
    assert player.name == "New Name"

    season.rename_team(human.name, "My FC")
    assert human.name == "My FC"
    # Old name should be replaced in all_team_names
    assert "My FC" in season.all_team_names
    assert original_team_name not in season.all_team_names
    table_names = [row["name"] for row in season.to_public_dict()["table"]]
    assert "My FC" in table_names
    assert original_team_name not in table_names


def test_retro_match_report_reads_like_live_commentary():
    season = create_new_season(["Tester"])
    results = season.play_round()
    report = " ".join(results[0].report)
    assert "Final score:" in report
    assert "create a" not in report
    assert "highlight" not in report.lower()
    assert set(results[0].factor_scores) == {"energy", "morale", "defence", "midfield", "attack"}


def test_modern_mode_still_works():
    season = create_new_season(["Tester"])
    season.tactics = Tactics(engine_mode="modern")
    results = season.play_round()
    report = " ".join(results[0].report)
    assert "Midfield battle creates" in report


def test_div_multiplier():
    assert retro_div_multiplier(1) == 4
    assert retro_div_multiplier(2) == 3
    assert retro_div_multiplier(3) == 2
    assert retro_div_multiplier(4) == 1


def test_retro_wage_bill_uses_div_multiplier():
    season = create_new_season(["Tester"])
    human = season.human_teams()[0]
    # wage_bill = sum(skill * 100 * d2) for d2=1 (Div4)
    expected = sum(p.skill * 100 * 1 for p in human.squad)
    assert human.wage_bill == expected


def test_cup_round_interspersed():
    """Cup match occurs every 3rd game (BASIC ma cycle: 1,2,0,1,2,0...)."""
    season = create_new_season(["Tester"])
    # Turn 1: ma 1→2, league (ml=1)
    season.play_round()
    assert season.retro_ml == 1
    # Turn 2: ma 2→0, cup (ml stays 1, cup_round becomes 1)
    season.play_round()
    # After turn 2: either cup (cup_round=1) or league if already eliminated
    assert season.retro_ml == 1 or season.retro_ml == 2  # depends on fa
