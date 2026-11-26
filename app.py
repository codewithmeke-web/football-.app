
                import os
import sqlite3
from functools import wraps

from flask import (
    Flask, request, redirect, url_for, session, flash,
    render_template_string, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

DATABASE = os.environ.get("DATABASE_PATH", "football.db")


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

        # The admin account is created from Render environment variables.
        admin_username = os.environ.get("ADMIN_USERNAME")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_username and admin_password:
            existing = db.execute(
                "SELECT id FROM users WHERE username = ?",
                (admin_username,)
            ).fetchone()
            if not existing:
                db.execute(
                    """INSERT INTO users
                       (username, email, password_hash, role)
                       VALUES (?, ?, ?, 'admin')""",
                    (
                        admin_username,
                        f"{admin_username}@football.local",
                        generate_password_hash(admin_password)
                    )
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


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | Football Hub</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#071522;color:#f4f7fb;font-family:Arial,sans-serif}
nav{display:flex;justify-content:space-between;align-items:center;gap:12px;
    padding:16px 6%;background:#0c2031;border-bottom:1px solid #20394d}
.brand{font-size:21px;font-weight:800;color:#fff;text-decoration:none}
.brand span{color:#37d98a}
nav a{color:#dce8f1;text-decoration:none;margin-left:14px;font-size:14px}
.wrap{width:min(1100px,92%);margin:28px auto}
.hero{min-height:245px;border-radius:18px;padding:30px;display:flex;align-items:end;
 background:linear-gradient(90deg,rgba(3,14,25,.94),rgba(3,14,25,.25)),
 url('https://images.unsplash.com/photo-1522778119026-d647f0596c20?auto=format&fit=crop&w=1600&q=85') center/cover}
.hero h1{font-size:clamp(30px,5vw,48px);margin:0 0 8px}
.hero p{color:#d2e0e9;margin:0}
.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:18px;margin-top:20px}
.panel{background:#10283a;border:1px solid #203e53;border-radius:15px;padding:20px;margin-top:18px}
h2{font-size:20px;margin:0 0 14px}
input{width:100%;padding:12px;margin:6px 0 12px;border:1px solid #35536a;
 border-radius:9px;background:#071a29;color:white}
label{font-size:13px;color:#b8cad7}
button,.btn{display:inline-block;border:0;border-radius:9px;padding:12px 16px;
 background:#20b978;color:#041b13;font-weight:800;text-decoration:none;cursor:pointer}
.btn-secondary{background:#29465b;color:#fff}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #294457}
th{color:#9fb5c5;font-size:12px;text-transform:uppercase}
td strong{color:#43df98}
.result{display:flex;justify-content:space-between;align-items:center;
 padding:12px 0;border-bottom:1px solid #294457}
.score{font-size:20px;font-weight:800;color:#42df98}
.flash{padding:12px;border-radius:9px;margin:12px 0;background:#23445a}
.flash.success{background:#164c39}.flash.warning{background:#604a1e}
.flash.error{background:#612d35}
.small{font-size:12px;color:#9fb5c5}
.auth{max-width:460px;margin:40px auto}
@media(max-width:750px){.grid{grid-template-columns:1fr}.hero{min-height:210px;padding:22px}
 nav{padding:14px 4%;flex-wrap:wrap}.wrap{width:94%}nav a{margin-left:8px}}
</style>
</head>
<body>
<nav>
 <a class="brand" href="{{ url_for('home') }}">⚽ FOOTBALL <span>HUB</span></a>
 <div>
  <a href="{{ url_for('home') }}">Home</a>
  {% if session.get('user_id') %}
   <a href="{{ url_for('logout') }}">Logout</a>
  {% else %}
   <a href="{{ url_for('login') }}">Login</a>
   <a href="{{ url_for('register') }}">Sign up</a>
  {% endif %}
 </div>
</nav>
<div class="wrap">
 {% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}
   <div class="flash {{ category }}">{{ message }}</div>
  {% endfor %}
 {% endwith %}
 {{ content|safe }}
</div>
</body></html>
"""


def page(title, content, **context):
    body = render_template_string(content, **context)
    return render_template_string(PAGE, title=title, content=body)


@app.route("/")
def home():
    with get_db() as db:
        matches = db.execute(
            "SELECT * FROM matches ORDER BY id DESC"
        ).fetchall()
        rows = db.execute("""
            SELECT team,
                   COUNT(*) AS played,
                   SUM(won) AS won,
                   SUM(drawn) AS drawn,
                   SUM(lost) AS lost,
                   SUM(gf) AS gf,
                   SUM(ga) AS ga,
                   SUM(gf-ga) AS gd,
                   SUM(points) AS points
            FROM (
                SELECT home_team AS team, 1 AS played,
                       CASE WHEN home_score > away_score THEN 1 ELSE 0 END AS won,
                       CASE WHEN home_score = away_score THEN 1 ELSE 0 END AS drawn,
                       CASE WHEN home_score < away_score THEN 1 ELSE 0 END AS lost,
                       home_score AS gf, away_score AS ga,
                       CASE WHEN home_score > away_score THEN 3
                            WHEN home_score = away_score THEN 1 ELSE 0 END AS points
                FROM matches
                UNION ALL
                SELECT away_team AS team, 1 AS played,
                       CASE WHEN away_score > home_score THEN 1 ELSE 0 END,
                       CASE WHEN away_score = home_score THEN 1 ELSE 0 END,
                       CASE WHEN away_score < home_score THEN 1 ELSE 0 END,
                       away_score, home_score,
                       CASE WHEN away_score > home_score THEN 3
                            WHEN away_score = home_score THEN 1 ELSE 0 END
                FROM matches
            )
            GROUP BY team
            ORDER BY points DESC, gd DESC, gf DESC, team ASC
        """).fetchall()

    content = """
    <section class="hero">
      <div><h1>THE BEAUTIFUL GAME.</h1>
      <p>Match results, league standings and football updates in one place.</p></div>
    </section>
    <div class="grid">
      <section class="panel">
       <h2>🏆 League standings</h2>
       <div style="overflow-x:auto"><table>
        <tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr>
        {% for t in table %}
        <tr><td>{{ loop.index }}</td><td><b>{{ t.team }}</b></td><td>{{ t.played }}</td>
        <td>{{ t.won }}</td><td>{{ t.drawn }}</td><td>{{ t.lost }}</td>
        <td>{{ '%+d'|format(t.gd) }}</td><td><strong>{{ t.points }}</strong></td></tr>
        {% else %}
        <tr><td colspan="8" class="small">No results yet. Match standings will appear here.</td></tr>
        {% endfor %}
       </table></div>
      </section>
      <section class="panel">
       <h2>⚽ Recent results</h2>
       {% for m in matches[:8] %}
        <div class="result"><span>{{ m.home_team }}</span>
         <span class="score">{{ m.home_score }} - {{ m.away_score }}</span>
         <span>{{ m.away_team }}</span></div>
       {% else %}
        <p class="small">No matches have been added yet.</p>
       {% endfor %}
      </section>
    </div>
    {% if session.get('role') == 'admin' %}
    <section class="panel">
      <h2>➕ Add a match result (Admin)</h2>
      <form method="post" action="{{ url_for('add_match') }}">
       <div class="grid" style="margin:0">
        <div><label>Home team</label><input name="home_team" required maxlength="40" placeholder="e.g. Arsenal"></div>
        <div><label>Away team</label><input name="away_team" required maxlength="40" placeholder="e.g. Chelsea"></div>
        <div><label>Home score</label><input type="number" min="0" name="home_score" required></div>
        <div><label>Away score</label><input type="number" min="0" name="away_score" required></div>
       </div><button type="submit">Save match result</button>
      </form>
    </section>
    <section class="panel">
      <h2>Manage results</h2>
      {% for m in matches %}
       <div class="result"><span>{{ m.home_team }} {{ m.home_score }} - {{ m.away_score }} {{ m.away_team }}</span>
        <form method="post" action="{{ url_for('delete_match', match_id=m.id) }}" onsubmit="return confirm('Delete this result?')">
         <button class="btn-secondary" type="submit">Delete</button>
        </form></div>
      {% endfor %}
    </section>
    {% endif %}
    """
    return page("Home", content, table=rows, matches=matches)


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
    <form method="post">
     <label>Username</label><input name="username" required minlength="3" maxlength="30">
     <label>Email address</label><input type="email" name="email" required>
     <label>Password (at least 8 characters)</label><input type="password" name="password" required minlength="8">
     <button type="submit">Sign up</button>
    </form><p class="small">New accounts can view results and standings.</p>
    <p>Already registered? <a href="{{ url_for('login') }}">Log in</a></p></section>
    """
    return page("Sign up", content)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("home"))
        flash("Incorrect username or password.", "error")

    content = """
    <section class="panel auth"><h2>Welcome back</h2>
    <form method="post">
     <label>Username</label><input name="username" required>
     <label>Password</label><input type="password" name="password" required>
     <button type="submit">Log in</button>
    </form><p>New here? <a href="{{ url_for('register') }}">Create an account</a></p></section>
    """
    return page("Login", content)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "success")
    return redirect(url_for("home"))


@app.route("/add-match", methods=["POST"])
@admin_required
def add_match():
    home_team = request.form.get("home_team", "").strip()
    away_team = request.form.get("away_team", "").strip()
    try:
        home_score = int(request.form.get("home_score", ""))
        away_score = int(request.form.get("away_score", ""))
    except ValueError:
        flash("Please enter valid scores.", "error")
        return redirect(url_for("home"))

    if not home_team or not away_team or home_team.lower() == away_team.lower():
        flash("Enter two different team names.", "error")
    elif len(home_team) > 40 or len(away_team) > 40 or home_score < 0 or away_score < 0:
        flash("Team names or scores are not valid.", "error")
    else:
        with get_db() as db:
            db.execute(
                "INSERT INTO matches (home_team,away_team,home_score,away_score) VALUES (?,?,?,?)",
                (home_team, away_team, home_score, away_score)
            )
        flash("Match saved. League standings updated!", "success")
    return redirect(url_for("home"))


@app.route("/delete/<int:match_id>", methods=["POST"])
@admin_required
def delete_match(match_id):
    with get_db() as db:
        db.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    flash("Match result deleted.", "success")
    return redirect(url_for("home"))


@app.errorhandler(403)
def forbidden(_error):
    return page("Access denied", """
    <section class="panel"><h2>403 — Admin only</h2>
    <p>Your account can view the football app, but only the administrator can manage match results.</p>
    <a class="btn" href="/">Back to home</a></section>
    """), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
