import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from fastapi.staticfiles import StaticFiles


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CORS Setup ---
origins = [
    "https://tischkicker-admin.onrender.com", # deine Admin-Seite
    "https://tischkicker-leaderboard.onrender.com", # leaderboard-Seite
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DB-Verbindung ---
def get_db_connection():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL ist nicht gesetzt!")

    dsn = dsn.strip()

    # psql:// -> postgresql:// für psycopg2
    if dsn.startswith("psql://"):
        dsn = "postgresql://" + dsn[len("psql://"):]

    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)

# --- Pydantic Modelle ---
class PlayerCreate(BaseModel):
    name: str

class MatchCreate(BaseModel):
    team1_ids: list[UUID]
    team2_ids: list[UUID]
    score_team1: int
    score_team2: int
    mode: str = "solo"

# --- Spieler abrufen ---
@app.get("/api/players")
def get_players():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, elo, wins, losses FROM players ORDER BY name;")
            players = cur.fetchall()
        return players
    finally:
        conn.close()

# --- Rangliste abrufen ---
@app.get("/api/leaderboard")
def get_leaderboard():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.name, p.elo, p.wins, p.losses, r.name AS rank_name, r.icon_url
                FROM players p
                LEFT JOIN ranks r ON p.rank_id = r.id
                ORDER BY p.elo DESC
            """)
            leaderboard = cur.fetchall()
        return leaderboard
    finally:
        conn.close()

# --- Spieler hinzufügen ---
@app.post("/api/players")
def add_player(player: PlayerCreate):
    if not player.name.strip():
        raise HTTPException(status_code=400, detail="Spielername darf nicht leer sein!")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO players (name, elo, wins, losses) VALUES (%s, 1000, 0, 0) RETURNING id;",
                (player.name.strip(),)
            )
            player_id = cur.fetchone()['id']
            # Rang direkt nach Spielererstellung setzen
            cur.execute("""
                UPDATE players p
                SET rank_id = r.id
                FROM ranks r
                WHERE p.id = %s
                AND r.min_elo = (
                    SELECT MAX(min_elo) FROM ranks WHERE min_elo <= p.elo
                );
            """, (player_id,))
            conn.commit()
        return {"id": player_id, "message": f"Spieler '{player.name.strip()}' erfolgreich hinzugefügt!"}
    finally:
        conn.close()

# --- Match eintragen ---
from uuid import UUID
from fastapi import HTTPException

@app.post("/api/matches")
def add_match(match: MatchCreate):

    # --- Validierung ---
    if not match.team1_ids or not match.team2_ids:
        raise HTTPException(
            status_code=400,
            detail="Beide Teams brauchen mindestens einen Spieler"
        )

    if set(match.team1_ids) & set(match.team2_ids):
        raise HTTPException(
            status_code=400,
            detail="Ein Spieler kann nicht in beiden Teams sein"
        )

    # --- Match-Modus automatisch bestimmen ---
    mode = "solo"
    if len(match.team1_ids) > 1 or len(match.team2_ids) > 1:
        mode = "duo"

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            # --- Match anlegen ---
            cur.execute("""
                INSERT INTO matches (mode, score_team1, score_team2, processed)
                VALUES (%s, %s, %s, FALSE)
                RETURNING id;
            """, (
                mode,
                match.score_team1,
                match.score_team2
            ))

            match_id = cur.fetchone()["id"]

            # Team 1
            for pid in match.team1_ids:
                cur.execute("""
                    INSERT INTO match_players (match_id, player_id, team)
                    VALUES (%s, %s, 1);
                """, (str(match_id), str(pid)))
            
            # Team 2
            for pid in match.team2_ids:
                cur.execute("""
                    INSERT INTO match_players (match_id, player_id, team)
                    VALUES (%s, %s, 2);
                """, (str(match_id), str(pid)))
            
            # Match verarbeiten
            cur.execute("SELECT process_match(%s);", (str(match_id),))
            
            # Ränge aktualisieren
            cur.execute("""
                UPDATE players p
                SET rank_id = r.id
                FROM ranks r
                WHERE p.id IN (
                    SELECT player_id
                    FROM match_players
                    WHERE match_id = %s
                )
                AND r.min_elo = (
                    SELECT MAX(min_elo)
                    FROM ranks
                    WHERE min_elo <= p.elo
                );
            """, (str(match_id),))

            # --- Match als verarbeitet markieren ---
            cur.execute("""
                UPDATE matches
                SET processed = TRUE
                WHERE id = %s;
            """, (match_id,))

            conn.commit()

        return {
            "match_id": match_id,
            "message": "Match erfolgreich gespeichert und verarbeitet"
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        conn.close()

