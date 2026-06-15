from __future__ import annotations

from gevent import monkey
monkey.patch_all()

import os
import pickle
import base64
import hashlib
import hmac

from flask import Flask, render_template, request, redirect, url_for, session
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

def _encode_save(season: SeasonState) -> str:
    return base64.b64encode(pickle.dumps(season)).decode()


def _sign_save(blob: str) -> str:
    secret = app.config["SECRET_KEY"].encode()
    sig = hmac.new(secret, blob.encode(), hashlib.sha256).hexdigest()
    return f"{blob}.{sig}"


def _verify_save(signed_blob: str) -> str | None:
    blob, sep, sig = signed_blob.rpartition(".")
    if not sep or not blob or not sig:
        return None
    expected = hmac.new(app.config["SECRET_KEY"].encode(), blob.encode(), hashlib.sha256).hexdigest()
    return blob if hmac.compare_digest(sig, expected) else None


def _decode_save(blob: str) -> SeasonState | None:
    try:
        season = pickle.loads(base64.b64decode(blob.encode()))
    except Exception:
        return None
    return season if isinstance(season, SeasonState) else None


def _public_payload(season: SeasonState) -> dict:
    payload = season.to_public_dict()
    payload["save_blob"] = _sign_save(_encode_save(season))
    return payload


def _save(user_id: int, season: SeasonState) -> None:
    blob = _encode_save(season)
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
    return _decode_save(gs.state_blob)


def _broadcast(uid: int, season: SeasonState) -> None:
    _save(uid, season)
    socketio.emit("state", _public_payload(season), room=f"u{uid}")


def _load_or_restore(uid: int, data: dict | None) -> SeasonState | None:
    season = _load(uid)
    if season:
        return season
    blob = _verify_save(str((data or {}).get("save_blob") or ""))
    if not blob:
        return None
    season = _decode_save(blob)
    if season:
        _save(uid, season)
    return season


def _session_uid() -> int | None:
    # Flask-Login stores user_id in session['_user_id']; more reliable than
    # current_user proxy inside SocketIO handlers under gevent.
    uid = session.get("_user_id")
    if uid is None:
        return None
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


# ── HTTP routes ───────────────────────────────────────────────────────────────

def _uid(data: dict | None = None) -> int | None:
    session_uid = _session_uid()
    if session_uid is not None:
        user = User.query.get(session_uid)
        if user and not user.username.startswith("guest_"):
            return session_uid

    client_id = str((data or {}).get("client_id") or "").strip()
    if client_id:
        digest = hashlib.sha256(client_id.encode()).hexdigest()[:16]
        username = f"guest_{digest}"
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username)
            user.set_password(os.urandom(16).hex())
            db.session.add(user)
            db.session.commit()
        session["guest_uid"] = user.id
        return user.id

    return session_uid


@app.get("/")
def index():
    if not current_user.is_authenticated:
        # Auto-create or restore an anonymous guest session — no registration needed
        import secrets as _secrets
        guest_uid = session.get("guest_uid")
        user = User.query.get(guest_uid) if guest_uid else None
        if not user:
            user = User(username=f"guest_{_secrets.token_hex(6)}")
            user.set_password(_secrets.token_hex(16))
            db.session.add(user)
            db.session.commit()
            session["guest_uid"] = user.id
        login_user(user, remember=True)
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
    uid = _uid(data)
    if uid is None:
        return
    join_room(f"u{uid}")
    season = _load(uid)
    if season:
        emit("state", _public_payload(season))
    else:
        emit("state_missing", {})


@socketio.on("restore_save")
def handle_restore_save(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    blob = _verify_save(str(data.get("save_blob") or ""))
    if not blob:
        emit("action_error", {"message": "Local save could not be verified."})
        return
    season = _decode_save(blob)
    if not season:
        emit("action_error", {"message": "Local save could not be restored."})
        return
    _broadcast(uid, season)


@socketio.on("new_season")
def handle_new_season(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    raw = data.get("managers") or data.get("manager") or current_user.username
    if isinstance(raw, str):
        managers = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
    else:
        managers = [str(n).strip() for n in raw if str(n).strip()]
    team_index = int(data.get("team_index", 63))
    season = create_new_season(managers[:1] or [current_user.username], team_index=team_index)
    _broadcast(uid, season)


@socketio.on("set_tactics")
def handle_set_tactics(data=None):
    uid = _uid(data)
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
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    season = _load_or_restore(uid, data)
    if not season:
        emit("state_missing", {})
        emit("action_error", {"message": "No saved season found. Choose a team first."})
        return
    if season.season_over:
        emit("action_error", {"message": "Season is over — start a new season or advance to the next."})
        return
    season.play_round()
    _broadcast(uid, season)


@socketio.on("end_season")
def handle_end_season(data=None):
    uid = _uid(data)
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
    state = _public_payload(season)
    state["season_summary"] = summary
    socketio.emit("state", state, room=f"u{uid}")


@socketio.on("buy_player")
def handle_buy_player(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.buy_player(str(data.get("team") or ""), str(data.get("player_id") or ""))
    except ValueError as e:
        emit("action_error", {"message": str(e), "action": "buy_player"})
        return
    _broadcast(uid, season)


@socketio.on("sell_player")
def handle_sell_player(data=None):
    uid = _uid(data)
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
    uid = _uid(data)
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


@socketio.on("swap_lineup")
def handle_swap_lineup(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.swap_lineup(
            str(data.get("team") or ""),
            str(data.get("in_player_id") or ""),
            str(data.get("out_player_id") or ""),
        )
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("change_player_position")
def handle_change_player_position(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.change_player_position(
            str(data.get("team") or ""),
            str(data.get("player_id") or ""),
            str(data.get("position") or ""),
        )
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("set_player_role")
def handle_set_player_role(data=None):
    uid = _uid(data)
    if uid is None:
        return
    data = data or {}
    season = _load(uid)
    if not season:
        return
    try:
        season.set_player_role(
            str(data.get("team") or ""),
            str(data.get("player_id") or ""),
            str(data.get("role") or ""),
        )
    except ValueError as e:
        emit("action_error", {"message": str(e)})
        return
    _broadcast(uid, season)


@socketio.on("borrow")
def handle_borrow(data=None):
    uid = _uid(data)
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
    uid = _uid(data)
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
    uid = _uid(data)
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
    uid = _uid(data)
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
    uid = _uid(data)
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
