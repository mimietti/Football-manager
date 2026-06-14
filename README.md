# Retro Football Manager

A standalone football management game inspired by early 1980s football manager games.

Credit line used in the UI:

> Inspired by the classic 1982 Football Manager by Kevin Toms.

This project uses its own code, club names, text, graphics, and match logic.

## Run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open `http://localhost:5100`.

## Current Design

- Flask + Socket.IO server
- HTML/CSS/JS frontend
- A new game starts in Division 4.
- Division 4 currently has 16 clubs, matching the classic league size.
- The league table starts at zero: no matches played, no goals, no points.
- 1-n human managers, with the rest of the league filled by AI clubs
- Each team has a squad of players.
- Maximum squad size is 16.
- 11 players are active in the lineup at once.
- Players have positions:
  - D = defence
  - M = midfield
  - A = attack
- Active players form the team's strengths and weaknesses.
- Players can be bought and sold through the transfer market.
- Each round offers the classic management choices in web form:
  - list, pick, buy, or sell players
  - print score and league table
  - obtain a loan
  - pay off loan
  - change skill level
  - change team or player names
  - play the next league match
- Weekly finances include wages, ground rent, and loan interest.
- Players in the active lineup lose energy after matches.
- Active players carry an injury risk after matches, and injured players miss upcoming rounds.
- Retro mode compares:
  - Energy vs Energy
  - Morale vs Morale
  - Defence vs Defence
  - Midfield vs Midfield
  - Attack vs Attack
- Retro mode uses a BASIC-style highlight check for each factor:
  - random 1-100 roll + strength difference * 5
  - 75 or higher creates two highlights
  - if the match has no goals after the main checks, each side gets one late chance
- In Retro mode, picked players lose energy before the match, resting players recover strongly, and injuries are rare one-round events.
- Modern mode:
  - uses the same player and team development as Retro mode for now
  - differs only in match resolution
  - midfield comparison controls the number of attacks
  - each attack compares attacking strength against defensive strength
- First match view is a short text report.
- Later: Spectrum-inspired canvas animation.

## Structure

- `football_manager/teams.py` - club and squad data
- `football_manager/season.py` - fixtures, league table, saveable season state
- `football_manager/match_engine.py` - Original 1982 mode and Modern mode simulations
- `app.py` - small Flask web app
