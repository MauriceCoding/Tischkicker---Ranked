import os
from fastapi import FastAPI, HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor

# App starten
app = FastAPI()

# DB-Verbindung aus Environment Variable
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL nicht gesetzt!")

# Verbindung öffnen
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --- Endpoint: Spieler abrufen ---
@app.get("/api/players")
def get_players():
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, elo, wins, losses FROM players ORDER BY name;")
        players = cur.fetchall()
    return players

# --- Endpoint: Rangliste ---
@app.get("/api/leaderboard")
def get_leaderboard():
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.name, p.elo, p.wins, p.losses, r.name AS rank_name, r.icon_url
            FROM players p
            LEFT JOIN ranks r ON p.rank_id = r.id
            ORDER BY p.elo DESC
        """)
        leaderboard = cur.fetchall()
    return leaderboard

# --- Endpoint: Spieler hinzufügen ---
@app.post("/api/players")
def add_player(name: str):
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")
    with conn.cursor() as cur:
        # Spieler mit Start-Elo 1000 und Standard-Rank (z.B. Bronze) anlegen
        cur.execute("""
            INSERT INTO players (name, elo, wins, losses)
            VALUES (%s, 1000, 0, 0)
            RETURNING id, name, elo;
        """, (name,))
        new_player = cur.fetchone()
        conn.commit()
    return {"id": new_player["id"], "name": new_player["name"], "elo": new_player["elo"]}

# --- Endpoint: Match eintragen ---
@app.post("/api/matches")
def add_match(team1_ids: list[int], team2_ids: list[int], score_team1: int, score_team2: int, mode: str = "solo"):
    with conn.cursor() as cur:
        # Neues Match einfügen
        cur.execute("INSERT INTO matches (mode, score_team1, score_team2, processed) VALUES (%s,%s,%s,FALSE) RETURNING id;",
                    (mode, score_team1, score_team2))
        match_id = cur.fetchone()['id']

        # Spieler zuordnen
        for pid in team1_ids:
            cur.execute("INSERT INTO match_players (match_id, player_id, team) VALUES (%s,%s,1)", (match_id, pid))
        for pid in team2_ids:
            cur.execute("INSERT INTO match_players (match_id, player_id, team) VALUES (%s,%s,2)", (match_id, pid))

        # Elo & Ränge aktualisieren über die DB Function
        cur.execute("SELECT process_match(%s);", (match_id,))
        # Spieler-Ränge aktualisieren
        cur.execute("""
            UPDATE players p
            SET rank_id = r.id
            FROM ranks r
            WHERE p.elo >= r.min_elo
            AND r.min_elo = (
                SELECT MAX(min_elo)
                FROM ranks
                WHERE min_elo <= p.elo
            );
        """)
        conn.commit()
    return {"match_id": match_id, "message": "Match erfolgreich eingetragen"}
