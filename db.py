"""
SQLite persistence for users, login sessions, profiles and analysis history.

Profile updates are made reliable by:
  - validating every write against profile_schema.Profile before it is stored,
  - a CHECK(json_valid(...)) constraint so the column can never hold bad JSON,
  - optimistic concurrency: each profile has a version number, and a write
    only succeeds if the caller's version matches the stored one (otherwise
    ProfileConflict is raised and nothing is changed),
  - doing each write in a single transaction.

A resume upload merges into the profile (see profile_merge.py) and keeps the
previous profile in profile_upload_undo, so the latest upload can be undone.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from profile_schema import drop_legacy_keys, empty_profile, normalize_profile

DB_PATH = os.getenv("RESUME_DESK_DB", os.path.join(os.path.dirname(__file__), "data", "resume_desk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data               TEXT NOT NULL CHECK (json_valid(data)),
    version            INTEGER NOT NULL,
    updated_at         TEXT NOT NULL,
    resume_filename    TEXT,
    resume_pdf         BLOB,
    resume_text        TEXT,
    resume_uploaded_at TEXT,
    -- What the LLM parsed from the stored resume, before any merge or edit.
    -- Lets the next upload tell hand-edited fields from resume-derived ones.
    resume_baseline    TEXT CHECK (resume_baseline IS NULL OR json_valid(resume_baseline))
);

-- The profile as it was just before the latest resume upload, so that upload
-- can be undone. One row per user; restore_version is the profile version the
-- upload produced, and undo is only allowed while the profile is still at it.
CREATE TABLE IF NOT EXISTS profile_upload_undo (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data               TEXT NOT NULL CHECK (json_valid(data)),
    resume_filename    TEXT,
    resume_pdf         BLOB,
    resume_text        TEXT,
    resume_uploaded_at TEXT,
    resume_baseline    TEXT,
    restore_version    INTEGER NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    job_url          TEXT,
    job_description  TEXT NOT NULL,
    improvements     TEXT NOT NULL,
    profile_snapshot TEXT NOT NULL CHECK (json_valid(profile_snapshot)),
    profile_version  INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    insights         TEXT CHECK (insights IS NULL OR json_valid(insights))
);

CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
"""


class ProfileConflict(Exception):
    """The profile changed since the caller loaded it."""

    def __init__(self, current_version, message="Profile was updated elsewhere. Reload and try again."):
        super().__init__(message)
        self.current_version = current_version


class NothingToUndo(Exception):
    """There is no resume upload that can still be undone."""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open a connection; commit on success, roll back on any error."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # Databases created before job insights existed lack the column.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(analyses)")}
        if "insights" not in columns:
            conn.execute(
                "ALTER TABLE analyses ADD COLUMN insights TEXT "
                "CHECK (insights IS NULL OR json_valid(insights))"
            )
        # Databases created before resume merging lack the baseline column.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
        if "resume_baseline" not in columns:
            conn.execute(
                "ALTER TABLE profiles ADD COLUMN resume_baseline TEXT "
                "CHECK (resume_baseline IS NULL OR json_valid(resume_baseline))"
            )


# ============================================================
# Users & sessions
# ============================================================

def create_user(email, password_hash, initial_profile=None):
    """Create a user and their starting profile atomically. Returns the user
    id, or None if the email is already registered."""
    profile = normalize_profile(initial_profile) if initial_profile else empty_profile()
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, now_iso()),
            )
        except sqlite3.IntegrityError:
            return None
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO profiles (user_id, data, version, updated_at) VALUES (?, ?, 1, ?)",
            (user_id, json.dumps(profile, ensure_ascii=False), now_iso()),
        )
        return user_id


def get_user_by_email(email):
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def create_session(token_hash, user_id, expires_at):
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def get_session_user(token_hash):
    with connect() as conn:
        return conn.execute(
            """SELECT u.id, u.email FROM auth_sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (token_hash, now_iso()),
        ).fetchone()


def delete_session(token_hash):
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))


# ============================================================
# Profiles
# ============================================================

def _profile_row_to_dict(row):
    return {
        "profile": drop_legacy_keys(json.loads(row["data"])),
        "version": row["version"],
        "updated_at": row["updated_at"],
        "resume": (
            {"filename": row["resume_filename"], "uploaded_at": row["resume_uploaded_at"]}
            if row["resume_filename"] else None
        ),
        "can_undo_upload": bool(row["can_undo_upload"]),
    }


def get_profile(user_id):
    with connect() as conn:
        row = conn.execute(
            """SELECT p.data, p.version, p.updated_at, p.resume_filename, p.resume_uploaded_at,
                      u.restore_version IS NOT NULL AND u.restore_version = p.version AS can_undo_upload
               FROM profiles p LEFT JOIN profile_upload_undo u ON u.user_id = p.user_id
               WHERE p.user_id = ?""",
            (user_id,),
        ).fetchone()
    return _profile_row_to_dict(row)


def get_merge_inputs(user_id):
    """What a resume upload needs to merge: the current profile and version,
    the baseline parsed from the stored resume (or None), and that resume's text."""
    with connect() as conn:
        row = conn.execute(
            "SELECT data, version, resume_baseline, resume_text FROM profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return {
        "profile": drop_legacy_keys(json.loads(row["data"])),
        "version": row["version"],
        "baseline": drop_legacy_keys(json.loads(row["resume_baseline"])) if row["resume_baseline"] else None,
        "resume_text": row["resume_text"],
    }


def save_profile(user_id, data, expected_version, resume=None):
    """Validate and store a profile, bumping its version.

    `resume` optionally carries {"filename", "pdf", "text", "baseline"} to
    store alongside the profile in the same transaction. A resume save first
    copies the current profile into profile_upload_undo so it can be undone.
    Raises pydantic.ValidationError for bad data and ProfileConflict if
    `expected_version` is stale.
    """
    normalized = json.dumps(normalize_profile(data), ensure_ascii=False)
    timestamp = now_iso()

    with connect() as conn:
        if resume:
            baseline = json.dumps(normalize_profile(resume["baseline"]), ensure_ascii=False)
            conn.execute(
                """INSERT OR REPLACE INTO profile_upload_undo
                       (user_id, data, resume_filename, resume_pdf, resume_text,
                        resume_uploaded_at, resume_baseline, restore_version, created_at)
                   SELECT user_id, data, resume_filename, resume_pdf, resume_text,
                          resume_uploaded_at, resume_baseline, version + 1, ?
                   FROM profiles WHERE user_id = ? AND version = ?""",
                (timestamp, user_id, expected_version),
            )
            cur = conn.execute(
                """UPDATE profiles
                   SET data = ?, version = version + 1, updated_at = ?,
                       resume_filename = ?, resume_pdf = ?, resume_text = ?, resume_uploaded_at = ?,
                       resume_baseline = ?
                   WHERE user_id = ? AND version = ?""",
                (normalized, timestamp, resume["filename"], resume["pdf"], resume["text"],
                 timestamp, baseline, user_id, expected_version),
            )
        else:
            cur = conn.execute(
                """UPDATE profiles SET data = ?, version = version + 1, updated_at = ?
                   WHERE user_id = ? AND version = ?""",
                (normalized, timestamp, user_id, expected_version),
            )

        if cur.rowcount != 1:
            row = conn.execute(
                "SELECT version FROM profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            raise ProfileConflict(row["version"] if row else None)

    return get_profile(user_id)


def undo_resume_upload(user_id, expected_version):
    """Restore the profile and resume from just before the latest upload.

    Only allowed while the profile is still exactly what that upload produced:
    undoing after later edits would silently throw those edits away. Raises
    NothingToUndo if there is no such upload, ProfileConflict if the profile
    changed since.
    """
    with connect() as conn:
        snap = conn.execute(
            "SELECT * FROM profile_upload_undo WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not snap:
            raise NothingToUndo()
        row = conn.execute(
            "SELECT version FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row["version"] != expected_version:
            raise ProfileConflict(row["version"])
        if snap["restore_version"] != expected_version:
            raise ProfileConflict(
                row["version"],
                "Your profile changed after the upload, so it can no longer be undone.",
            )

        conn.execute(
            """UPDATE profiles
               SET data = ?, version = version + 1, updated_at = ?,
                   resume_filename = ?, resume_pdf = ?, resume_text = ?,
                   resume_uploaded_at = ?, resume_baseline = ?
               WHERE user_id = ? AND version = ?""",
            (snap["data"], now_iso(), snap["resume_filename"], snap["resume_pdf"],
             snap["resume_text"], snap["resume_uploaded_at"], snap["resume_baseline"],
             user_id, expected_version),
        )
        conn.execute("DELETE FROM profile_upload_undo WHERE user_id = ?", (user_id,))

    return get_profile(user_id)


def get_resume_file(user_id):
    with connect() as conn:
        return conn.execute(
            "SELECT resume_filename, resume_pdf FROM profiles WHERE user_id = ? AND resume_pdf IS NOT NULL",
            (user_id,),
        ).fetchone()


# ============================================================
# Analyses
# ============================================================

def _analysis_row_to_dict(row, full=True):
    item = {
        "id": row["id"],
        "title": row["title"],
        "job_url": row["job_url"],
        "profile_version": row["profile_version"],
        "created_at": row["created_at"],
    }
    if full:
        item["job_description"] = row["job_description"]
        item["improvements"] = row["improvements"]
        item["profile_snapshot"] = json.loads(row["profile_snapshot"])
        item["insights"] = json.loads(row["insights"]) if row["insights"] else None
    return item


def create_analysis(user_id, title, job_url, job_description, improvements, profile,
                    profile_version, insights=None):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO analyses (user_id, title, job_url, job_description, improvements,
                                     profile_snapshot, profile_version, created_at, insights)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, title, job_url, job_description, improvements,
             json.dumps(profile, ensure_ascii=False), profile_version, now_iso(),
             json.dumps(insights, ensure_ascii=False) if insights else None),
        )
        analysis_id = cur.lastrowid
    return get_analysis(user_id, analysis_id)


def list_analyses(user_id):
    """History summaries, newest first, with a few insight fields for cards."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, title, job_url, profile_version, created_at,
                      json_extract(insights, '$.at_a_glance.location') AS location,
                      json_array_length(insights, '$.skills_matched') AS matched_count,
                      json_array_length(insights, '$.skills_missing') AS missing_count
               FROM analyses WHERE user_id = ? ORDER BY id DESC""",
            (user_id,),
        ).fetchall()
    summaries = []
    for row in rows:
        item = _analysis_row_to_dict(row, full=False)
        item["location"] = row["location"]
        item["matched_count"] = row["matched_count"]
        item["missing_count"] = row["missing_count"]
        summaries.append(item)
    return summaries


def get_analysis(user_id, analysis_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE user_id = ? AND id = ?", (user_id, analysis_id)
        ).fetchone()
    return _analysis_row_to_dict(row) if row else None


def delete_analysis(user_id, analysis_id):
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM analyses WHERE user_id = ? AND id = ?", (user_id, analysis_id)
        )
        return cur.rowcount == 1
