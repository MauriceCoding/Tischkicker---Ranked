import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

origins = [
    "https://tischkicker-admin.onrender.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # <- explizit
    allow_credentials=True,  # <- ok
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Funktion: DB-Verbindung öffnen ---
def get_db_connection():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL ist nicht gesetzt!")

    # Zeilenumbrüche/Leerzeichen entfernen
    dsn = dsn.strip()

    # psql:// zu postgresql:// konvertieren, falls Render das liefert
    if dsn.startswith("psql://"):
        dsn = "postgresql://" + dsn[len("psql://"):]

    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)


class PlayerCreate(BaseModel):
    name: str

class MatchCreate(BaseModel):
    team1_ids: List[int]
    team2_ids: List[int]
    score_team1: int
    score_team2: int
    mode: Optional[str] = "solo"

# --- Endpoint: Spieler abrufen ---
@app.get("/api/players")
def get_players():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, elo, wins, losses FROM players ORDER BY name;")
        players = cur.fetchall()
    conn.close()
    return players

# --- Endpoint: Spieler hinzufügen ---
@app.post("/api/players")
def add_player(player: PlayerCreate):
    conn = get_db_connection()
    with conn.cursor() as cur:
        # Spieler einfügen
        cur.execute(
            "INSERT INTO players (name, elo, wins, losses) VALUES (%s, 1000, 0, 0) RETURNING id;",
            (player.name,)
        )
        player_id = cur.fetchone()['id']

        # Rang für neuen Spieler setzen
        cur.execute("""
            UPDATE players p
            SET rank_id = r.id
            FROM ranks r
            WHERE p.id = %s
            AND r.min_elo = (
                SELECT MAX(min_elo)
                FROM ranks
                WHERE min_elo <= p.elo
            );
        """, (player_id,))

        # Änderungen speichern
        conn.commit()

    conn.close()
    return {"id": player_id, "message": f"Spieler '{player.name}' erfolgreich hinzugefügt!"}


# --- Endpoint: Rangliste ---
@app.get("/api/leaderboard")
def get_leaderboard():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.name, p.elo, p.wins, p.losses, r.name AS rank_name, r.icon_url
            FROM players p
            LEFT JOIN ranks r ON p.rank_id = r.id
            ORDER BY p.elo DESC
        """)
        leaderboard = cur.fetchall()
    conn.close()
    return leaderboard

# --- Endpoint: Match eintragen ---
@app.post("/api/matches")
def add_match(match: MatchCreate):
    conn = get_db_connection()
    with conn.cursor() as cur:
        # Neues Match einfügen
        cur.execute(
            "INSERT INTO matches (mode, score_team1, score_team2, processed) VALUES (%s,%s,%s,FALSE) RETURNING id;",
            (match.mode, match.score_team1, match.score_team2)
        )
        match_id = cur.fetchone()['id']

        # Spieler zuordnen (Team 1)
        for pid in match.team1_ids:
            if pid:
                cur.execute(
                    "INSERT INTO match_players (match_id, player_id, team) VALUES (%s,%s,1);",
                    (match_id, pid)
                )
        # Spieler zuordnen (Team 2)
        for pid in match.team2_ids:
            if pid:
                cur.execute(
                    "INSERT INTO match_players (match_id, player_id, team) VALUES (%s,%s,2);",
                    (match_id, pid)
                )

        # Elo & Ränge über DB-Funktion aktualisieren
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
    conn.close()
    return {"match_id": match_id, "message": "Match erfolgreich eingetragen!"}
