from __future__ import annotations

from gevent import monkey
monkey.patch_all()

import os
import pickle
import base64

from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user,
)

from football_manager.match_engine import Tactics
from football_manager.season import SeasonState, create_new_season
from football_manager.models import db, User, GameSession


# ── App factory ───────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-retro-football-manager-change-me")

_db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = ""

with app.app_context():
    db.create_all()

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


# ── State persistence ─────────────────────────────────────────────────────────

def _save(user_id: int, season: SeasonState) -> None:
    blob = base64.b64encode(pickle.dumps(season)).decode()
    gs = GameSession.query.filter_by(user_id=user_id).first()
    if gs:
        gs.state_blob = blob
    else:
        gs = GameSession(user_id=user_id, state_blob=blob)
        db.session.add(gs)
    db.session.commit()


def _load(user_id: int) -> SeasonState | None:
    gs = GameSession.query.filter_by(user_id=user_id).first()
    if not gs or not gs.state_blob:
        return None
    try:
        return pickle.loads(base64.b64decode(gs.state_blob.encode()))
    except Exception:
        return None


def _broadcast(uid: int, season: SeasonState) -> None:
    _save(uid, season)
    socketio.emit("state", season.to_public_dict(), room=f"u{uid}")


def _uid() -> int | None:
    return current_user.id if current_user.is_authenticated else None


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.get("/")
@login_required
def index():
    return render_template("index.html", username=current_user.username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        action = request.form.get("action", "login")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if action == "register":
            if len(username) < 2:
                error = "Username must be at least 2 characters."
            elif len(password) < 4:
                error = "Password must be at least 4 characters."
            elif User.query.filter_by(username=username).first():
                error = "Username already taken."
            else:
                user = User(username=username)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                login_user(user, remember=True)
                return redirect(url_for("index"))
        else:
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user, remember=True)
                return redirect(url_for("index"))
            error = "Invalid username or password."
    return render_template("auth.html", error=error)


@app.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Socket handlers ───────────────────────────────────────────────────────────

@socketio.on("join")
def handle_join(data=None):
    uid = _uid()
    if uid is None:
        return
    join_room(f"u{uid}")
    season = _load(uid)
    if season:
        emit("state", season.to_public_dict())


@socketio.on("new_season")
def handle_new_season(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    raw = data.get("managers") or data.get("manager") or current_user.username
    if isinstance(raw, str):
        managers = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
    else:
        managers = [str(n).strip() for n in raw if str(n).strip()]
    season = create_new_season(managers[:1] or [current_user.username])
    _broadcast(uid, season)


@socketio.on("set_tactics")
def handle_set_tactics(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    season.tactics = Tactics(
        engine_mode=str(data.get("mode") or season.tactics.engine_mode),
        formation=str(data.get("formation") or season.tactics.formation),
        pressing=str(data.get("pressing") or season.tactics.pressing),
        skill_level=season.skill_level,
    )
    _broadcast(uid, season)


@socketio.on("play_round")
def handle_play_round(data=None):
    uid = _uid()
    if uid is None:
        return
    season = _load(uid)
    if not season:
        return
    if season.season_over:
        emit("action_error", {"message": "Season is over — start a new season or advance to the next."})
        return
    season.play_round()
    _broadcast(uid, season)


@socketio.on("end_season")
def handle_end_season(data=None):
    uid = _uid()
    if uid is None:
        return
    season = _load(uid)
    if not season:
        return
    if not season.season_over:
        emit("action_error", {"message": "Season is not over yet."})
        return
    summary = season.end_season()
    _save(uid, season)
    state = season.to_public_dict()
    state["season_summary"] = summary
    socketio.emit("state", state, room=f"u{uid}")


@socketio.on("buy_player")
def handle_buy_player(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.buy_player(str(data.get("team") or ""), str(data.get("player_id") or ""))
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("sell_player")
def handle_sell_player(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.sell_player(str(data.get("team") or ""), str(data.get("player_id") or ""))
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("toggle_lineup")
def handle_toggle_lineup(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.toggle_lineup(str(data.get("team") or ""), str(data.get("player_id") or ""))
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("borrow")
def handle_borrow(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.borrow(str(data.get("team") or ""), int(data.get("amount") or 0))
    except (TypeError, ValueError) as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("repay")
def handle_repay(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.repay(str(data.get("team") or ""), int(data.get("amount") or 0))
    except (TypeError, ValueError) as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("set_skill_level")
def handle_set_skill_level(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.set_skill_level(int(data.get("level") or 1))
    except (TypeError, ValueError) as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("rename_team")
def handle_rename_team(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.rename_team(str(data.get("team") or ""), str(data.get("name") or ""))
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("rename_player")
def handle_rename_player(data=None):
    uid = _uid()
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.rename_player(
            str(data.get("team") or ""),
            str(data.get("player_id") or ""),
            str(data.get("name") or ""),
        )
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5100, debug=True, allow_unsafe_werkzeug=True)
