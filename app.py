import os
import sqlite3
from functools import wraps
from datetime import datetime, timedelta, timezone, date
from threading import Lock

import requests
from flask import (
    Flask, request, redirect, url_for, session, flash,
    render_template_string, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

# Football Hub - Flask application
# Render start command remains: gunicorn app:app

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
DATABASE = os.environ.get("DATABASE_PATH", "football.db")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip()
API_BASE = "https://api.football-data.org/v4"
COMPETITION = "PL"

_api_cache = {}
_api_lock = Lock()
API_CACHE_SECONDS = 900


def get_db():
    folder = os.path.dirname(DATABASE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer'
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                home_score INTEGER NOT NULL,
                away_score INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS follows (
                user_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                team_name TEXT NOT NULL,
                crest TEXT,
                PRIMARY KEY (user_id, team_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                match_id INTEGER NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                kickoff TEXT,
                pick TEXT NOT NULL CHECK(pick IN ('HOME', 'DRAW', 'AWAY')),
                points INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, match_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        admin_username = os.environ.get("ADMIN_USERNAME")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_username and admin_password:
            existing = db.execute(
                "SELECT id FROM users WHERE username = ?", (admin_username,)
            ).fetchone()
            if not existing:
                db.execute(
                    """INSERT INTO users
                       (username, email, password_hash, role)
                       VALUES (?, ?, ?, 'admin')""",
                    (admin_username, f"{admin_username}@football.local",
                     generate_password_hash(admin_password))
                )


init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def football_api(path, params=None):
    """Cached football-data.org v4 request. Returns (data, error)."""
    if not FOOTBALL_API_KEY:
        return None, "Football API key is not configured in Render."
    params = params or {}
    cache_key = (path, tuple(sorted(params.items())))
    now = datetime.now(timezone.utc).timestamp()
    with _api_lock:
        cached = _api_cache.get(cache_key)
        if cached and now - cached["time"] < API_CACHE_SECONDS:
            return cached["data"], None
    try:
        response = requests.get(
            f"{API_BASE}{path}",
            headers={"X-Auth-Token": FOOTBALL_API_KEY},
            params=params,
            timeout=12
        )
        if response.status_code == 403:
            return None, "Your football-data.org plan may not include this data."
        if response.status_code == 429:
            return None, "The football data request limit was reached. Please try again later."
        response.raise_for_status()
        data = response.json()
        with _api_lock:
            _api_cache[cache_key] = {"time": now, "data": data}
        return data, None
    except requests.RequestException as exc:
        print(f"Football API request failed:{exc}")
        print(f"Provider response: {exc.response.text if exc.response is not None else 'No reponse body'}")                                
        return None, "Football data is temporarily unavailable. Please try again shortly."
    except ValueError:
        return None, "The football data provider returned an unreadable response."


def get_fixtures():
    today = datetime.now(timezone.utc).date()
    params = {
        "dateFrom": (today - timedelta(days=7)).isoformat(),
        "dateTo": (today + timedelta(days=35)).isoformat()
    }
    data, error = football_api(f"/competitions/{COMPETITION}/matches", params)
    matches = (data or {}).get("matches", [])
    if matches:
        sync_prediction_scores(matches)
    return matches, error


def sync_prediction_scores(matches):
    """Set prediction points once the provider marks a match as finished."""
    with get_db() as db:
        for match in matches:
            if match.get("status") != "FINISHED":
                continue
            score = (match.get("score") or {}).get("fullTime") or {}
            home_score, away_score = score.get("home"), score.get("away")
            if home_score is None or away_score is None:
                continue
            actual = "DRAW" if home_score == away_score else (
                "HOME" if home_score > away_score else "AWAY"
            )
            db.execute("""
                UPDATE predictions
                   SET points = CASE WHEN pick = ? AND ? = 'DRAW' THEN 1
                                     WHEN pick = ? AND ? != 'DRAW' THEN 3
                                     ELSE 0 END
                 WHERE match_id = ? AND points IS NULL
            """, (actual, actual, actual, actual, match["id"]))


def page(title, content, **context):
    body = render_template_string(content, **context)
    return render_template_string(PAGE, title=title, content=body)


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | Football Hub</title>
<style>
:root{--bg:#071522;--panel:#10283a;--line:#203e53;--green:#20b978;--muted:#a9bdcb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#f4f7fb;font-family:Arial,sans-serif}
nav{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
padding:15px 5%;background:#0c2031;border-bottom:1px solid var(--line)}
.brand{font-size:21px;font-weight:800;color:#fff;text-decoration:none}
.brand span{color:#37d98a}
nav a{color:#dce8f1;text-decoration:none;margin:5px 8px;font-size:14px}
.wrap{width:min(1150px,94%);margin:26px auto}
.hero{min-height:240px;border-radius:18px;padding:28px;display:flex;align-items:end;
background:linear-gradient(90deg,rgba(3,14,25,.94),rgba(3,14,25,.2)),
url('https://images.unsplash.com/photo-1522778119026-d647f0596c20?auto=format&fit=crop&w=1600&q=85') center/cover}
.hero h1{font-size:clamp(30px,5vw,48px);margin:0 0 8px}
.hero p{color:#d2e0e9;margin:0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:20px;margin-top:18px}
h2{font-size:20px;margin:0 0 14px}
h3{margin:5px 0 10px}
input,select,textarea{width:100%;padding:12px;margin:6px 0 12px;border:1px solid #35536a;
border-radius:9px;background:#071a29;color:white}
textarea{min-height:120px;resize:vertical}
label{font-size:13px;color:#b8cad7}
button,.btn{display:inline-block;border:0;border-radius:9px;padding:11px 15px;
background:var(--green);color:#041b13;font-weight:800;text-decoration:none;cursor:pointer}
.secondary{background:#29465b;color:#fff}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 7px;border-bottom:1px solid #294457}
th{color:#9fb5c5;font-size:12px;text-transform:uppercase}
td strong,.score{color:#43df98}
.result,.fixture{display:flex;justify-content:space-between;align-items:center;gap:10px;
padding:12px 0;border-bottom:1px solid #294457}
.flash{padding:12px;border-radius:9px;margin:12px 0;background:#23445a}
.flash.success{background:#164c39}.flash.warning{background:#604a1e}.flash.error{background:#612d35}
.small{font-size:12px;color:var(--muted)}
.auth{max-width:460px;margin:40px auto}
.team{display:flex;align-items:center;gap:10px}
.crest{width:28px;height:28px;object-fit:contain}
.tag{display:inline-block;padding:5px 8px;border-radius:20px;background:#193d50;color:#bcebd4;font-size:11px}
.news{white-space:pre-wrap;line-height:1.55;color:#d5e2eb}
@media(max-width:760px){.grid{grid-template-columns:1fr}.hero{min-height:205px;padding:20px}
nav{padding:13px 4%}.wrap{width:94%}nav a{margin:4px}}
</style>
</head>
<body>
<nav>
 <a class="brand" href="{{ url_for('home') }}">⚽ FOOTBALL <span>HUB</span></a>
 <div>
  <a href="{{ url_for('home') }}">Home</a>
  <a href="{{ url_for('fixtures') }}">Fixtures</a>
  <a href="{{ url_for('predictions') }}">Fantasy</a>
  <a href="{{ url_for('clubs') }}">Clubs</a>
  <a href="{{ url_for('news_page') }}">News</a>
  {% if session.get('user_id') %}
   <a href="{{ url_for('profile') }}">My profile</a>
   {% if session.get('role') == 'admin' %}<a href="{{ url_for('admin') }}">Admin</a>{% endif %}
   <a href="{{ url_for('logout') }}">Logout</a>
  {% else %}
   <a href="{{ url_for('login') }}">Login</a><a href="{{ url_for('register') }}">Sign up</a>
  {% endif %}
 </div>
</nav>
<div class="wrap">
 {% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}
 {% endwith %}
 {{ content|safe }}
</div>
</body></html>
"""


@app.route("/")
def home():
    standings, standing_error = football_api(f"/competitions/{COMPETITION}/standings")
    table = []
    if standings:
        for group in standings.get("standings", []):
            if group.get("type") == "TOTAL":
                table = group.get("table", [])
                break
    matches, match_error = get_fixtures()
    recent = sorted(
        [m for m in matches if m.get("status") == "FINISHED"],
        key=lambda m: m.get("utcDate", ""), reverse=True
    )[:6]
    upcoming = sorted(
        [m for m in matches if m.get("status") not in ("FINISHED", "CANCELLED", "POSTPONED")],
        key=lambda m: m.get("utcDate", "")
    )[:6]
    with get_db() as db:
        manual = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 6").fetchall()
        news = db.execute("SELECT * FROM news ORDER BY id DESC LIMIT 3").fetchall()
    content = """
    <section class="hero"><div><h1>THE BEAUTIFUL GAME.</h1>
    <p>Premier League fixtures, results, standings, fantasy predictions and club updates.</p></div></section>
    {% if match_error %}<div class="flash warning">{{ match_error }}</div>{% endif %}
    {% if standing_error %}<p class="small">{{ standing_error }}</p>{% endif %}
    <div class="grid">
      <section class="panel"><h2>🏆 Premier League table</h2>
       <div style="overflow-x:auto"><table>
        <tr><th>#</th><th>Club</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr>
        {% for t in table %}
        <tr><td>{{ t.position }}</td><td>{{ t.team.name }}</td><td>{{ t.playedGames }}</td>
        <td>{{ t.won }}</td><td>{{ t.draw }}</td><td>{{ t.lost }}</td><td>{{ t.goalDifference }}</td>
        <td><strong>{{ t.points }}</strong></td></tr>
        {% else %}<tr><td colspan="8" class="small">Standings will appear when the API provides them.</td></tr>{% endfor %}
       </table></div>
      </section>
      <section class="panel"><h2>📅 Upcoming matches</h2>
       {% for m in upcoming %}
        <div class="result"><span>{{ m.homeTeam.shortName or m.homeTeam.name }}</span>
        <span class="tag">{{ m.utcDate[:10] }} · {{ m.status }}</span>
        <span>{{ m.awayTeam.shortName or m.awayTeam.name }}</span></div>
       {% else %}<p class="small">No upcoming matches returned for this date range.</p>{% endfor %}
       <p><a class="btn" href="{{ url_for('fixtures') }}">All fixtures</a></p>
      </section>
    </div>
    <section class="panel"><h2>🔥 Latest results</h2>
     {% for m in recent %}<div class="result"><span>{{ m.homeTeam.name }}</span>
      <b class="score">{{ (m.score.fullTime or {}).get('home', '-') }} - {{ (m.score.fullTime or {}).get('away', '-') }}</b>
      <span>{{ m.awayTeam.name }}</span></div>
     {% else %}<p class="small">No completed Premier League matches in the current feed.</p>{% endfor %}
    </section>
    <section class="panel"><h2>📰 Latest Football Hub news</h2>
     {% for n in news %}<h3>{{ n.title }}</h3><p class="news">{{ n.body }}</p>{% else %}
     <p class="small">News updates will appear here when the admin publishes them.</p>{% endfor %}
     <a href="{{ url_for('news_page') }}">Read all news →</a>
    </section>
    {% if manual %}<section class="panel"><h2>Admin-entered results</h2>
     {% for m in manual %}<div class="result"><span>{{ m.home_team }}</span>
     <b class="score">{{ m.home_score }} - {{ m.away_score }}</b><span>{{ m.away_team }}</span></div>{% endfor %}
    </section>{% endif %}
    """
    return page("Home", content, table=table, upcoming=upcoming,
                recent=recent, match_error=match_error,
                standing_error=standing_error, news=news, manual=manual)


@app.route("/fixtures")
def fixtures():
    matches, error = get_fixtures()
    matches = sorted(matches, key=lambda m: m.get("utcDate", ""))
    content = """
    <section class="panel"><h2>Premier League fixtures & results</h2>
    <p class="small">Fixtures and scores are supplied by football-data.org. Live updates depend on your API plan.</p>
    {% if error %}<div class="flash warning">{{ error }}</div>{% endif %}
    {% for m in matches %}
     <div class="fixture">
      <div style="flex:1"><b>{{ m.homeTeam.name }}</b><br><span class="small">{{ m.utcDate[:16].replace('T',' ') }} UTC</span></div>
      <div style="text-align:center;min-width:90px"><b class="score">
       {% if m.status == 'FINISHED' or m.status in ('IN_PLAY','PAUSED','EXTRA_TIME','PENALTY_SHOOTOUT') %}
        {{ (m.score.fullTime or {}).get('home', '-') }} - {{ (m.score.fullTime or {}).get('away', '-') }}
       {% else %}{{ m.status }}{% endif %}
      </b><br><span class="small">Matchday {{ m.matchday or '-' }}</span></div>
      <div style="flex:1;text-align:right"><b>{{ m.awayTeam.name }}</b></div>
     </div>
    {% else %}<p class="small">No fixtures were returned. Check the API key and plan access.</p>{% endfor %}
    </section>
    """
    return page("Fixtures", content, matches=matches, error=error)


@app.route("/predictions", methods=["GET", "POST"])
@login_required
def predictions():
    matches, error = get_fixtures()
    future = [
        m for m in matches
        if m.get("status") in ("SCHEDULED", "TIMED")
        and (m.get("utcDate") or "") > datetime.now(timezone.utc).isoformat()
    ]
    if request.method == "POST":
        try:
            match_id = int(request.form.get("match_id", ""))
        except ValueError:
            match_id = 0
        pick = request.form.get("pick", "")
        match = next((m for m in future if m.get("id") == match_id), None)
        if not match or pick not in ("HOME", "DRAW", "AWAY"):
            flash("That prediction is not valid or the match has already started.", "error")
        else:
            with get_db() as db:
                db.execute("""
                    INSERT INTO predictions
                    (user_id, match_id, home_team, away_team, kickoff, pick, points)
                    VALUES (?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(user_id, match_id) DO UPDATE SET
                    pick=excluded.pick, home_team=excluded.home_team,
                    away_team=excluded.away_team, kickoff=excluded.kickoff,
                    points=NULL
                """, (session["user_id"], match_id, match["homeTeam"]["name"],
                      match["awayTeam"]["name"], match.get("utcDate"), pick))
            flash("Your prediction has been saved!", "success")
        return redirect(url_for("predictions"))

    with get_db() as db:
        mine = db.execute(
            "SELECT * FROM predictions WHERE user_id=? ORDER BY kickoff",
            (session["user_id"],)
        ).fetchall()
        leaderboard = db.execute("""
            SELECT u.username, COALESCE(SUM(p.points),0) AS points,
                   COUNT(p.id) AS predictions
            FROM users u LEFT JOIN predictions p ON p.user_id=u.id
            GROUP BY u.id ORDER BY points DESC, predictions DESC, u.username
            LIMIT 20
        """).fetchall()
    my_picks = {p["match_id"]: p for p in mine}
    content = """
    <section class="panel"><h2>🎯 Fantasy match predictions</h2>
    <p>Choose the home win, draw or away win before kick-off. Correct win/loss outcome = 3 points.
    Correct draw = 1 point. Incorrect = 0 points.</p>
    {% if error %}<div class="flash warning">{{ error }}</div>{% endif %}
    {% if not session.get('user_id') %}<p>Please log in to predict.</p>{% endif %}
    {% for m in future %}
     <form method="post" class="fixture">
      <input type="hidden" name="match_id" value="{{ m.id }}">
      <div style="flex:1"><b>{{ m.homeTeam.name }}</b><br><span class="small">{{ m.utcDate[:16].replace('T',' ') }} UTC</span></div>
      <div style="min-width:170px">
       <select name="pick" required>
        <option value="">Choose result</option>
        <option value="HOME" {% if my_picks.get(m.id) and my_picks[m.id].pick=='HOME' %}selected{% endif %}>Home win</option>
        <option value="DRAW" {% if my_picks.get(m.id) and my_picks[m.id].pick=='DRAW' %}selected{% endif %}>Draw</option>
        <option value="AWAY" {% if my_picks.get(m.id) and my_picks[m.id].pick=='AWAY' %}selected{% endif %}>Away win</option>
       </select><button type="submit">Save pick</button>
      </div>
      <div style="flex:1;text-align:right"><b>{{ m.awayTeam.name }}</b></div>
     </form>
    {% else %}<p class="small">There are no upcoming fixtures available for predictions right now.</p>{% endfor %}
    </section>
    <div class="grid">
     <section class="panel"><h2>My predictions</h2>
      {% for p in mine %}<div class="result"><span>{{ p.home_team }} v {{ p.away_team }}<br><span class="small">{{ p.kickoff or '' }}</span></span>
      <span class="tag">{{ p.pick }} · {{ p.points if p.points is not none else 'Pending' }}</span></div>
      {% else %}<p class="small">You have not made any predictions yet.</p>{% endfor %}
     </section>
     <section class="panel"><h2>🏅 Leaderboard</h2>
      <table><tr><th>#</th><th>Player</th><th>Predictions</th><th>Points</th></tr>
      {% for p in leaderboard %}<tr><td>{{ loop.index }}</td><td>{{ p.username }}</td><td>{{ p.predictions }}</td><td><strong>{{ p.points }}</strong></td></tr>{% endfor %}</table>
     </section>
    </div>
    """
    return page("Fantasy", content, future=future, mine=mine,
                my_picks=my_picks, leaderboard=leaderboard, error=error)


@app.route("/clubs", methods=["GET", "POST"])
@login_required
def clubs():
    data, error = football_api(f"/competitions/{COMPETITION}/teams")
    teams = (data or {}).get("teams", [])
    if request.method == "POST":
        try:
            team_id = int(request.form.get("team_id", ""))
        except ValueError:
            team_id = 0
        team = next((t for t in teams if t.get("id") == team_id), None)
        if team:
            with get_db() as db:
                db.execute("""
                    INSERT OR REPLACE INTO follows(user_id,team_id,team_name,crest)
                    VALUES(?,?,?,?)
                """, (session["user_id"], team["id"], team["name"], team.get("crest")))
            flash(f"You are now following {team['name']}.", "success")
        else:
            flash("Please choose a valid Premier League club.", "error")
        return redirect(url_for("clubs"))
    with get_db() as db:
        followed = db.execute(
            "SELECT * FROM follows WHERE user_id=? ORDER BY team_name",
            (session["user_id"],)
        ).fetchall()
    followed_ids = {t["team_id"] for t in followed}
    followed_names = {t["team_name"] for t in followed}
    all_matches, fixture_error = get_fixtures()
    club_updates = [m for m in all_matches if
                    m.get("homeTeam", {}).get("name") in followed_names or
                    m.get("awayTeam", {}).get("name") in followed_names]
    content = """
    <section class="panel"><h2>💚 Follow your club</h2>
    <p>Choose a Premier League club to add it to your followed teams.</p>
    {% if error %}<div class="flash warning">{{ error }}</div>{% endif %}
    <form method="post"><label>Premier League club</label><select name="team_id" required>
     <option value="">Select a club</option>
     {% for t in teams %}<option value="{{ t.id }}">{{ t.name }}</option>{% endfor %}
    </select><button type="submit">Follow club</button></form>
    </section>
    <section class="panel"><h2>My followed clubs</h2>
    {% for t in followed %}
     <div class="result"><div class="team">
      {% if t.crest %}<img class="crest" src="{{ t.crest }}" alt="">{% endif %}
      <b>{{ t.team_name }}</b></div>
      <form method="post" action="{{ url_for('unfollow', team_id=t.team_id) }}"><button class="secondary">Unfollow</button></form>
     </div>
    {% else %}<p class="small">You are not following any clubs yet.</p>{% endfor %}
    </section>
    <section class="panel"><h2>📣 Updates for my clubs</h2>
    {% if fixture_error %}<p class="small">{{ fixture_error }}</p>{% endif %}
    {% for m in club_updates %}
     <div class="result"><span>{{ m.homeTeam.name }} v {{ m.awayTeam.name }}<br>
      <span class="small">{{ m.utcDate[:16].replace('T',' ') }} UTC</span></span>
      <span class="tag">{% if m.status == 'FINISHED' %}
       {{ (m.score.fullTime or {}).get('home', '-') }} - {{ (m.score.fullTime or {}).get('away', '-') }}
      {% else %}{{ m.status }}{% endif %}</span></div>
    {% else %}<p class="small">Follow a club to see its fixtures and results here.</p>{% endfor %}
    </section>
    """
    return page("Clubs", content, teams=teams, followed=followed,
                followed_ids=followed_ids, error=error,
                club_updates=club_updates, fixture_error=fixture_error)


@app.route("/unfollow/<int:team_id>", methods=["POST"])
@login_required
def unfollow(team_id):
    with get_db() as db:
        db.execute("DELETE FROM follows WHERE user_id=? AND team_id=?",
                   (session["user_id"], team_id))
    flash("Club removed from your followed teams.", "success")
    return redirect(url_for("clubs"))


@app.route("/news")
def news_page():
    with get_db() as db:
        news = db.execute("SELECT * FROM news ORDER BY id DESC").fetchall()
    content = """
    <section class="panel"><h2>📰 Football Hub news</h2>
    {% for n in news %}<article style="padding:12px 0;border-bottom:1px solid #294457">
     <h3>{{ n.title }}</h3><p class="news">{{ n.body }}</p>
     <span class="small">{{ n.created_at }}</span></article>
    {% else %}<p class="small">No news has been published yet. Check back soon.</p>{% endfor %}
    </section>
    """
    return page("News", content, news=news)


@app.route("/profile")
@login_required
def profile():
    with get_db() as db:
        user = db.execute("SELECT username,email,role FROM users WHERE id=?",
                          (session["user_id"],)).fetchone()
        followed = db.execute("SELECT team_name FROM follows WHERE user_id=? ORDER BY team_name",
                              (session["user_id"],)).fetchall()
        score = db.execute("SELECT COALESCE(SUM(points),0) FROM predictions WHERE user_id=?",
                           (session["user_id"],)).fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM predictions WHERE user_id=?",
                           (session["user_id"],)).fetchone()[0]
    content = """
    <section class="panel"><h2>My Football Hub profile</h2>
     <h3>{{ user.username }}</h3><p>{{ user.email }}</p><span class="tag">{{ user.role }}</span>
     <div class="grid"><div><h3>{{ score }}</h3><p class="small">Fantasy points</p></div>
     <div><h3>{{ count }}</h3><p class="small">Predictions made</p></div></div>
     <h3>Followed clubs</h3>{% for t in followed %}<span class="tag">{{ t.team_name }}</span> {% else %}
     <p class="small">No clubs followed yet.</p>{% endfor %}
    </section>
    """
    return page("My profile", content, user=user, followed=followed,
                score=score, count=count)


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if title and body:
            with get_db() as db:
                db.execute("INSERT INTO news(title,body) VALUES(?,?)", (title[:140], body[:5000]))
            flash("News article published.", "success")
        else:
            flash("Enter both a title and news text.", "error")
        return redirect(url_for("admin"))
    with get_db() as db:
        news = db.execute("SELECT * FROM news ORDER BY id DESC").fetchall()
        users = db.execute("SELECT username,email,role FROM users ORDER BY id DESC").fetchall()
        matches = db.execute("SELECT * FROM matches ORDER BY id DESC").fetchall()
    content = """
    <section class="panel"><h2>⚙️ Admin dashboard</h2>
    <p>Manage Football Hub news and review registered users.</p>
    <form method="post"><label>News headline</label><input name="title" maxlength="140" required>
     <label>News article</label><textarea name="body" required></textarea><button type="submit">Publish news</button>
    </form></section>
    <div class="grid">
     <section class="panel"><h2>Registered users</h2>
      {% for u in users %}<div class="result"><span>{{ u.username }}<br><span class="small">{{ u.email }}</span></span><span class="tag">{{ u.role }}</span></div>
      {% else %}<p class="small">No users yet.</p>{% endfor %}
     </section>
     <section class="panel"><h2>Published news</h2>
      {% for n in news %}<div class="result"><span>{{ n.title }}</span>
       <form method="post" action="{{ url_for('delete_news', news_id=n.id) }}"><button class="secondary">Delete</button></form></div>
      {% else %}<p class="small">No articles yet.</p>{% endfor %}
     </section>
    </div>
    <section class="panel"><h2>Legacy manual match results</h2>
     {% for m in matches %}<div class="result"><span>{{ m.home_team }} {{ m.home_score }} - {{ m.away_score }} {{ m.away_team }}</span>
      <form method="post" action="{{ url_for('delete_match', match_id=m.id) }}"><button class="secondary">Delete</button></form></div>
     {% else %}<p class="small">No manual results saved.</p>{% endfor %}
    </section>
    """
    return page("Admin", content, news=news, users=users, matches=matches)


@app.route("/admin/news/delete/<int:news_id>", methods=["POST"])
@admin_required
def delete_news(news_id):
    with get_db() as db:
        db.execute("DELETE FROM news WHERE id=?", (news_id,))
    flash("News article deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 8 or "@" not in email:
            flash("Use a username of 3+ characters, a valid email and password of 8+ characters.", "error")
        else:
            try:
                with get_db() as db:
                    db.execute(
                        "INSERT INTO users (username,email,password_hash,role) VALUES (?,?,?,'viewer')",
                        (username, email, generate_password_hash(password))
                    )
                flash("Account created. You can now log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.", "error")
    content = """
    <section class="panel auth"><h2>Create your account</h2>
    <form method="post"><label>Username</label><input name="username" required minlength="3" maxlength="30">
    <label>Email address</label><input type="email" name="email" required>
    <label>Password (at least 8 characters)</label><input type="password" name="password" required minlength="8">
    <button type="submit">Sign up</button></form>
    <p class="small">Create an account to make predictions and follow clubs.</p>
    <p>Already registered? <a href="{{ url_for('login') }}">Log in</a></p></section>
    """
    return page("Sign up", content)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("home"))
        flash("Incorrect username or password.", "error")
    content = """
    <section class="panel auth"><h2>Welcome back</h2>
    <form method="post"><label>Username</label><input name="username" required>
    <label>Password</label><input type="password" name="password" required>
    <button type="submit">Log in</button></form>
    <p>New here? <a href="{{ url_for('register') }}">Create an account</a></p></section>
    """
    return page("Login", content)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "success")
    return redirect(url_for("home"))


@app.route("/delete/<int:match_id>", methods=["POST"])
@admin_required
def delete_match(match_id):
    with get_db() as db:
        db.execute("DELETE FROM matches WHERE id=?", (match_id,))
    flash("Manual match result deleted.", "success")
    return redirect(url_for("admin"))


@app.errorhandler(403)
def forbidden(_error):
    return page("Access denied", """
    <section class="panel"><h2>403 — Admin only</h2>
    <p>Your account can use Football Hub, but only the administrator can manage news and users.</p>
    <a class="btn" href="/">Back to home</a></section>
    """), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
