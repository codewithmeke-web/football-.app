from flask import Flask, request, render_template_string
app = Flask(__name__)
# store the teams & scores
teams=[]

# website homepage
@app.route("/",methods=["GET","POST"])
def home():
    if request.method=="POST":
        team1 = request.form.get("team1", "").strip()
        team2 = request.form.get("team2", "").strip()
        score1 = request.form.get("score1", "").strip()
        score2 = request.form.get("score2", "").strip()

        if team1 and team2 and score1.isdigit() and score2.isdigit():
            teams.append({
                "team1": team1,
                "team2": team2,
                "score1": score1,
                "score2": score2,
            })

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>My Football</title>
        <style>
            body {
                font-family: Arial; 
                background: #101820;
                color: white;
                text-align: center;
                padding: 20px;
            }

            h1 {
                color: #00ff88;
            }

            input {
                padding: 10px;
                margin: 5px;
                width: 200px;
            }

            button {
                padding: 10px 20px;
                background: #00ff88;
                border: none;
                cursor: pointer;
            }
            
            .match {
            background: #243447;
            padding: 15px;
            margin: 15px auto;
            max-width: 400px;
            border-radius: 10px;
            }
        </style>
    </head>

    <body>
        <h1>My Football Website</h1>
        <h2>Add a Match</h2>

        <form method="POST">
            <input type="text" name="team1" placeholder="Team 1" required><br>
            <input type="text" name="team2"
              placeholder="Team 2" required>
              <br>
            <input type="number" name="score1" placeholder="Score 1" required><br>
            <input type="number" name="score2"
              placeholder="Score 2" required>
              <br>

            <button type="submit">Add Match</button>
        </form>

        <h2>Matches</h2>
        {% for match in teams %}
            <div class="match">
                {{ match.team1 }} {{ match.score1 }} - {{ match.score2 }} {{ match.team2 }}
            </div>
        {% endfor %}
    </body> 
    </html>    
    """, teams=teams)

@app.route('/register', methods=['GET', 'POST'])
def register():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Register</title>
    </head>
    <body>
        <h1>Register</h1>
        <form method="POST">
            <input type="text" name="username" placeholder="Username" required><br>
            <input type="email" name="email" placeholder="Email" required><br>
            <input type="password" name="password" placeholder="Password" required><br>
            <button type="submit">Register</button>
        </form>
    """)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
    
