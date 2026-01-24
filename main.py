from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import os
import math
import traceback

app = FastAPI()

# -----------------------
# CORS
# -----------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# Datenbank
# -----------------------
DATABASE_URL = os.environ["DATABASE_URL"]

def get_db_connection():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL ist nicht gesetzt")
    # psycopg2.connect interpretiert DSN jetzt strikt, daher sicherstellen, dass es 'postgresql://' enthält
    if dsn.startswith("psql://"):
        dsn = "postgresql://" + dsn[6:]
    return psycopg2.connect(dsn)

# -----------------------
# RANG LOGIK
# -----------------------
def get_rank(elo: int) -> str:
    if elo < 1000:
        return "Bronze"
    if elo < 1500:
        return "Silber"
    if elo < 2200:
        return "Gold"
    if elo < 3800:
        return "Diamant"
    if elo < 5500:
        return "Elite"
    return "Champion"

# -----------------------
# ELO BERECHNUNG
# -----------------------
def calculate_elo(elo_a, elo_b, goals_a, goals_b):
    K = 80  # stark beschleunigt
    expected_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))

    if goals_a > goals_b:
        score_a = 1
    elif goals_a < goals_b:
        score_a = 0
    else:
        score_a = 0.5

    goal_diff = abs(goals_a - goals_b)
    goal_factor = min(2.5, 1 + goal_diff * 0.4)

    delta_a = K * (score_a - expected_a) * goal_factor
    delta_b = -delta_a

    new_elo_a = min(6000, max(0, round(elo_a + delta_a)))
    new_elo_b = min(6000, max(0, round(elo_b + delta_b)))

    return new_elo_a, new_elo_b

# -----------------------
# ROUTES
# -----------------------

@app.get("/")
def root():
    return {"status": "API läuft"}

@app.get("/api/players")
def get_players():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, elo, rank_name, wins, losses
                FROM players
                ORDER BY elo DESC
            """)
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "name": r[1],
                "elo": r[2],
                "rank": r[3],
                "wins": r[4],
                "losses": r[5]
            }
            for r in rows
        ]

    except Exception as e:
        # DEBUG: komplette Exception zurückgeben
        return {"error": str(e), "trace": traceback.format_exc()}

    finally:
        if conn:
            conn.close()

@app.get("/api/leaderboard")
def get_leaderboard():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name, elo, rank_name
                FROM players
                ORDER BY elo DESC
                LIMIT 10
            """)
            rows = cur.fetchall()

        return [
            {
                "name": r[0],
                "elo": r[1],
                "rank": r[2]
            }
            for r in rows
        ]
    finally:
        conn.close()

@app.post("/api/players")
def add_player(data: dict):
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO players (name, elo, rank_name, wins, losses)
                VALUES (%s, 1000, 'Silber', 0, 0)
                RETURNING id
            """, (name,))
            player_id = cur.fetchone()[0]
            conn.commit()

        return {"id": player_id, "name": name}
    finally:
        conn.close()

@app.post("/api/matches")
def add_match(data: dict):
    player_a = data.get("player_a")
    player_b = data.get("player_b")
    goals_a = data.get("goals_a")
    goals_b = data.get("goals_b")

    if None in [player_a, player_b, goals_a, goals_b]:
        raise HTTPException(status_code=400, detail="Matchdaten unvollständig")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, elo, wins, losses FROM players WHERE id = %s", (player_a,))
            a = cur.fetchone()

            cur.execute("SELECT id, elo, wins, losses FROM players WHERE id = %s", (player_b,))
            b = cur.fetchone()

            if not a or not b:
                raise HTTPException(status_code=404, detail="Spieler nicht gefunden")

            new_elo_a, new_elo_b = calculate_elo(a[1], b[1], goals_a, goals_b)

            rank_a = get_rank(new_elo_a)
            rank_b = get_rank(new_elo_b)

            win_a = 1 if goals_a > goals_b else 0
            win_b = 1 if goals_b > goals_a else 0

            cur.execute("""
                UPDATE players
                SET elo = %s, rank_name = %s,
                    wins = wins + %s,
                    losses = losses + %s
                WHERE id = %s
            """, (new_elo_a, rank_a, win_a, 1 - win_a, player_a))

            cur.execute("""
                UPDATE players
                SET elo = %s, rank_name = %s,
                    wins = wins + %s,
                    losses = losses + %s
                WHERE id = %s
            """, (new_elo_b, rank_b, win_b, 1 - win_b, player_b))

            cur.execute("""
                INSERT INTO matches (player_a, player_b, goals_a, goals_b)
                VALUES (%s, %s, %s, %s)
            """, (player_a, player_b, goals_a, goals_b))

            conn.commit()

        return {"status": "Match gespeichert"}

    finally:
        conn.close()
