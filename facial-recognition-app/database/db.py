import sqlite3
import numpy as np
import pickle
from pathlib import Path

DB_PATH = Path(__file__).parent / "faces.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables on first run."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT    NOT NULL,
                encoding  BLOB    NOT NULL,
                image     BLOB,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_face(name: str, encoding: np.ndarray, image_bytes: bytes | None = None) -> int:
    """Insert a face encoding and return its row id."""
    blob = pickle.dumps(encoding)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO faces (name, encoding, image) VALUES (?, ?, ?)",
            (name, blob, image_bytes),
        )
        conn.commit()
        return cur.lastrowid


def get_all_faces() -> list[dict]:
    """Return all faces with decoded encodings (no image blobs)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, encoding, created_at FROM faces ORDER BY id"
        ).fetchall()

    faces = []
    for row in rows:
        faces.append(
            {
                "id": row["id"],
                "name": row["name"],
                "encoding": pickle.loads(row["encoding"]),
                "created_at": row["created_at"],
            }
        )
    return faces


def get_face_thumbnail(face_id: int) -> bytes | None:
    """Return the raw image bytes for a face, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT image FROM faces WHERE id = ?", (face_id,)
        ).fetchone()
    if row and row["image"]:
        return row["image"]
    return None


def delete_face(face_id: int) -> bool:
    """Delete a face by id. Returns True if a row was deleted."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM faces WHERE id = ?", (face_id,))
        conn.commit()
        return cur.rowcount > 0


def get_face_count() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM faces").fetchone()
    return row["cnt"]
