"""Persistent student profiles — SQLite, survives closing the app.

Mastery is Bayesian Knowledge Tracing (BKT) tracked PER CONCEPT, not per topic.
Each (topic, concept) pair carries P(L), the probability the student has learned
it, updated by Bayes' rule after every graded answer:

    posterior = P(L)*P(obs|L) / (P(L)*P(obs|L) + (1-P(L))*P(obs|~L))
    P(L)_new  = posterior + (1 - posterior) * p_transit

with obs likelihoods set by slip (wrong despite knowing) and guess (right despite
not knowing). Concepts come from the hand-authored concept map (concepts.py);
a graded question updates exactly the (topic, concept) it is tagged with.

Questions that are not concept-tagged yet (concept=None) still land in history
but do NOT move BKT — an untagged answer carries no concept evidence.

When a concept has no row yet its prior is seeded from topic_seed (the student's
old per-topic mastery, kept purely as a migration prior) or p_init.

Multiple accounts live in `students`; every profile table carries a student_id.
Each account is gated by an email + password (see "Accounts: sign-in credentials"
below) — nothing here hands out another student's id.
The active account is set per request by the Flask app (set_current); background
jobs pass an explicit sid.
"""

import json
import os
import re
import sqlite3
import threading
import time
import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = Path(__file__).parent / "profile.db"

# The deployment (Vercel) sets DATABASE_URL and gets Postgres; a local run with no
# such variable keeps the SQLite file so the app still works offline.
DATABASE_URL = (os.environ.get("DATABASE_URL", "") or "").strip()
PG = bool(DATABASE_URL)

# Standard BKT starting values.
P_INIT = 0.3
P_TRANSIT = 0.15
P_SLIP = 0.1
P_GUESS = 0.2

ERROR_TYPES = ("conceptual_gap", "computational", "transfer", "time_pressure")

ERROR_LABELS = {
    "conceptual_gap": "core idea not yet clear",
    "computational": "method right, calculation or units slipped",
    "transfer": "right knowledge, wrong situation",
    "time_pressure": "answer rushed or left incomplete",
}

CONFIDENCE_LEVELS = ("just_starting", "getting_there", "pretty_strong")
STUDY_STYLES = ("lessons", "tests", "papers")

# Active account for this request/thread; the app sets it in before_request.
# Thread-local because Flask runs requests on several threads at once.
_LOCAL = threading.local()


def set_current(sid):
    _LOCAL.sid = sid


def current_sid():
    return getattr(_LOCAL, "sid", None)


def _sid(sid):
    return sid if sid is not None else current_sid()


def _key(subject, topic):
    return f"{subject}:{topic}"


def _split(key):
    subject, _, topic = (key or "").partition(":")
    return subject, topic


class _Sqlite:
    def __init__(self):
        self._c = sqlite3.connect(DB_PATH)
        self._c.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._c.execute(sql, params)

    def insert(self, sql, params=()):
        """Run an INSERT into a table with a surrogate id column, return that id."""
        return self._c.execute(sql, params).lastrowid

    def script(self, ddl):
        self._c.executescript(ddl)

    def tables(self):
        return {r["name"] for r in
                self._c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def columns(self, table):
        return {r["name"] for r in self._c.execute(f"PRAGMA table_info({table})")}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        self._c.close()
        return False


class _Postgres:
    """Same small API as _Sqlite so the queries below stay dialect-agnostic: '?'
    markers, dict rows, last-insert-id helper, and schema introspection."""

    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        # One connection per operation — these instances are short-lived and Neon's
        # pooler is what keeps the real backend connections warm.
        self._c = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("?", "%s"), params)

    def insert(self, sql, params=()):
        return self.execute(sql + " RETURNING id", params).fetchone()["id"]

    def script(self, ddl):
        for stmt in (s.strip() for s in ddl.split(";")):
            if stmt:
                self._c.execute(stmt)

    def tables(self):
        return {r["table_name"] for r in self._c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema").fetchall()}

    def columns(self, table):
        return {r["column_name"] for r in self._c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema AND table_name = %s",
            (table,)).fetchall()}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        self._c.close()
        return False


def _conn():
    return _Postgres() if PG else _Sqlite()


# One schema, two renderings: {IDCOL} and {NUM} expand to the dialect's auto-increment
# id column and 8-byte float (Postgres REAL is only 4 bytes, which would quietly round
# off timestamps and BKT probabilities).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    {IDCOL},
    name TEXT NOT NULL,
    grade TEXT NOT NULL,
    struggles TEXT NOT NULL DEFAULT '[]',
    confidence TEXT,
    style TEXT,
    curriculum TEXT NOT NULL DEFAULT 'kcse',
    email TEXT,
    password_hash TEXT,
    birth_date TEXT,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until {NUM} NOT NULL DEFAULT 0,
    created_at {NUM} NOT NULL
);
CREATE TABLE IF NOT EXISTS concept_mastery (
    student_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    concept TEXT NOT NULL,
    p {NUM} NOT NULL,
    answers INTEGER NOT NULL DEFAULT 0,
    updated_at {NUM} NOT NULL,
    PRIMARY KEY (student_id, topic, concept)
);
CREATE TABLE IF NOT EXISTS topic_seed (
    student_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    score {NUM} NOT NULL,
    PRIMARY KEY (student_id, topic)
);
CREATE TABLE IF NOT EXISTS history (
    {IDCOL},
    student_id INTEGER NOT NULL,
    ts {NUM} NOT NULL,
    topic TEXT NOT NULL,
    concept TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    correct INTEGER NOT NULL,
    error_type TEXT,
    feedback TEXT
);
CREATE TABLE IF NOT EXISTS error_tally (
    student_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    concept TEXT NOT NULL,
    error_type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (student_id, topic, concept, error_type)
);
CREATE TABLE IF NOT EXISTS lessons (
    student_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    lesson_json TEXT NOT NULL,
    created_at {NUM} NOT NULL,
    profile_sig TEXT,
    PRIMARY KEY (student_id, topic)
);
CREATE TABLE IF NOT EXISTS documents (
    {IDCOL},
    student_id INTEGER NOT NULL,
    ts {NUM} NOT NULL,
    subject TEXT NOT NULL,
    filename TEXT NOT NULL,
    pages INTEGER,
    analysis_json TEXT,
    status TEXT NOT NULL DEFAULT 'analyzing'
);
CREATE TABLE IF NOT EXISTS walkthroughs (
    {IDCOL},
    student_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    filename TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    idx INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at {NUM} NOT NULL
);
CREATE TABLE IF NOT EXISTS grading_jobs (
    {IDCOL},
    student_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'grading',
    result_json TEXT,
    created_at {NUM} NOT NULL,
    finished_at {NUM}
);
CREATE TABLE IF NOT EXISTS gen_jobs (
    key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    error TEXT,
    updated_at {NUM} NOT NULL
);
CREATE TABLE IF NOT EXISTS walkthrough_steps (
    walkthrough_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    lesson_json TEXT NOT NULL,
    created_at {NUM} NOT NULL,
    PRIMARY KEY (walkthrough_id, idx, attempt)
);
"""

_IDCOL = {"sqlite": "id INTEGER PRIMARY KEY AUTOINCREMENT", "pg": "id SERIAL PRIMARY KEY"}
# Postgres REAL is only 4 bytes, which would quietly round off timestamps and BKT
# probabilities, so the 8-byte type is named once and shared.
_NUM = "DOUBLE PRECISION" if PG else "REAL"


def _render(ddl):
    idcol = _IDCOL["pg" if PG else "sqlite"]
    return [s.strip().replace("{IDCOL}", idcol).replace("{NUM}", _NUM)
            for s in ddl.split(";") if s.strip()]


def init_db():
    with _conn() as c:
        for stmt in _render(_SCHEMA):
            c.script(stmt)
        if "curriculum" not in c.columns("students"):
            c.execute("ALTER TABLE students ADD COLUMN curriculum TEXT NOT NULL DEFAULT 'kcse'")
        for col, ddl in (("email", "TEXT"), ("password_hash", "TEXT"),
                         ("birth_date", "TEXT"),
                         ("failed", "INTEGER NOT NULL DEFAULT 0"),
                         ("locked_until", _NUM + " NOT NULL DEFAULT 0")):
            if col not in c.columns("students"):
                c.execute(f"ALTER TABLE students ADD COLUMN {col} {ddl}")
        c.script("CREATE UNIQUE INDEX IF NOT EXISTS students_email_key "
                 "ON students (lower(email)) WHERE email IS NOT NULL")
        if PG:
            return  # every legacy migration below upgrades an old local SQLite file
        _migrate_multi_student(c)
        _migrate_legacy_keys(c)
        _migrate_concept_schema(c)


def _tables(c):
    return c.tables()


def _migrate_multi_student(c):
    """Single-student schema -> per-student rows. Old data all belongs to account 1."""
    tables = _tables(c)
    if "mastery" in tables and "student_id" not in c.columns("mastery"):
        c.script(
            """
            CREATE TABLE mastery_new (
                student_id INTEGER NOT NULL, topic TEXT NOT NULL,
                score REAL NOT NULL, answers INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (student_id, topic)
            );
            INSERT INTO mastery_new (student_id, topic, score, answers)
                SELECT 1, topic, score, answers FROM mastery;
            DROP TABLE mastery;
            ALTER TABLE mastery_new RENAME TO mastery;
            """
        )
    if "history" in tables and "student_id" not in c.columns("history"):
        c.script(
            """
            CREATE TABLE history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
                ts REAL NOT NULL, topic TEXT NOT NULL, question TEXT NOT NULL,
                answer TEXT NOT NULL, correct INTEGER NOT NULL, error_type TEXT, feedback TEXT
            );
            INSERT INTO history_new (student_id, ts, topic, question, answer, correct, error_type, feedback)
                SELECT 1, ts, topic, question, answer, correct, error_type, feedback FROM history;
            DROP TABLE history;
            ALTER TABLE history_new RENAME TO history;
            """
        )
    if "error_tally" in tables and "student_id" not in c.columns("error_tally"):
        c.script(
            """
            CREATE TABLE error_tally_new (
                student_id INTEGER NOT NULL, topic TEXT NOT NULL, error_type TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (student_id, topic, error_type)
            );
            INSERT INTO error_tally_new (student_id, topic, error_type, count)
                SELECT 1, topic, error_type, count FROM error_tally;
            DROP TABLE error_tally;
            ALTER TABLE error_tally_new RENAME TO error_tally;
            """
        )
    if "lessons" in tables and "student_id" not in c.columns("lessons"):
        c.script(
            """
            CREATE TABLE lessons_new (
                student_id INTEGER NOT NULL, topic TEXT NOT NULL, lesson_json TEXT NOT NULL,
                created_at REAL NOT NULL, profile_sig TEXT, PRIMARY KEY (student_id, topic)
            );
            INSERT INTO lessons_new (student_id, topic, lesson_json, created_at, profile_sig)
                SELECT 1, topic, lesson_json, created_at, profile_sig FROM lessons;
            DROP TABLE lessons;
            ALTER TABLE lessons_new RENAME TO lessons;
            """
        )
    if "documents" in tables and "student_id" not in c.columns("documents"):
        c.execute("ALTER TABLE documents ADD COLUMN student_id INTEGER NOT NULL DEFAULT 1")

    if "student" in tables:
        for row in c.execute("SELECT name, grade, struggles, created_at FROM student").fetchall():
            c.execute(
                "INSERT INTO students (id, name, grade, struggles, created_at) VALUES (1, ?, ?, ?, ?)",
                (row["name"], row["grade"], row["struggles"], row["created_at"]),
            )
        c.execute("DROP TABLE student")


def _migrate_legacy_keys(c):
    """Pre-change rows keyed by bare topic were all Chemistry; scope them."""
    tables = _tables(c)
    for table in ("mastery", "lessons"):
        if table not in tables:
            continue
        for row in c.execute(f"SELECT topic FROM {table}").fetchall():
            if ":" not in row["topic"]:
                c.execute(
                    f"UPDATE {table} SET topic = ? WHERE topic = ?",
                    (_key("chemistry", row["topic"]), row["topic"]),
                )
    if "error_tally" in tables:
        for row in c.execute("SELECT DISTINCT topic FROM error_tally").fetchall():
            if ":" not in row["topic"]:
                c.execute(
                    "UPDATE error_tally SET topic = ? WHERE topic = ?",
                    (_key("chemistry", row["topic"]), row["topic"]),
                )
    if "history" in tables:
        for row in c.execute("SELECT DISTINCT topic FROM history").fetchall():
            if ":" not in row["topic"]:
                c.execute(
                    "UPDATE history SET topic = ? WHERE topic = ?",
                    (_key("chemistry", row["topic"]), row["topic"]),
                )
    # Struggles picked in the old Chemistry-only onboarding are bare slugs too.
    for row in c.execute("SELECT id, struggles FROM students").fetchall():
        items = json.loads(row["struggles"] or "[]")
        if any(":" not in s for s in items):
            scoped = [s if ":" in s else _key("chemistry", s) for s in items]
            c.execute("UPDATE students SET struggles = ? WHERE id = ?",
                      (json.dumps(scoped), row["id"]))


def _migrate_concept_schema(c):
    """Topic-level mastery/error-tally/history -> per-concept schema.

    The old per-topic mastery score cannot be re-attributed to concepts (answers
    were never concept-tagged), so it is preserved as topic_seed: a prior used
    only to initialise concepts the student has not answered yet.
    """
    tables = _tables(c)
    if "history" in tables and "concept" not in c.columns("history"):
        c.script(
            """
            CREATE TABLE history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
                ts REAL NOT NULL, topic TEXT NOT NULL, concept TEXT,
                question TEXT NOT NULL, answer TEXT NOT NULL, correct INTEGER NOT NULL,
                error_type TEXT, feedback TEXT
            );
            INSERT INTO history_new (student_id, ts, topic, concept, question, answer, correct, error_type, feedback)
                SELECT student_id, ts, topic, NULL, question, answer, correct, error_type, feedback FROM history;
            DROP TABLE history;
            ALTER TABLE history_new RENAME TO history;
            """
        )
    if "error_tally" in tables and "concept" not in c.columns("error_tally"):
        c.script(
            """
            CREATE TABLE error_tally_new (
                student_id INTEGER NOT NULL, topic TEXT NOT NULL, concept TEXT NOT NULL,
                error_type TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (student_id, topic, concept, error_type)
            );
            DROP TABLE error_tally;
            ALTER TABLE error_tally_new RENAME TO error_tally;
            """
        )
    if "mastery" in tables:
        c.execute(
            "INSERT OR IGNORE INTO topic_seed (student_id, topic, score) "
            "SELECT student_id, topic, score FROM mastery"
        )
        c.execute("DROP TABLE mastery")


# ---------------- BKT ----------------

def _bkt_posterior(p, correct):
    if correct:
        num = p * (1 - P_SLIP)
        den = num + (1 - p) * P_GUESS
    else:
        num = p * P_SLIP
        den = num + (1 - p) * (1 - P_GUESS)
    return num / den if den else p


def _bkt_step(p, correct):
    """Posterior after observing one answer, then the learning transition."""
    post = _bkt_posterior(p, correct)
    return post + (1 - post) * P_TRANSIT


def _prior_for(c, sid, key):
    seed = c.execute(
        "SELECT score FROM topic_seed WHERE student_id = ? AND topic = ?", (sid, key)
    ).fetchone()
    if not seed:
        return P_INIT
    # Legacy topic scores were time-decayed, so they can sit near 0 or 1; clamp so a
    # seeded concept still moves on the first answer instead of being pinned.
    return min(0.95, max(0.05, seed["score"]))


def _mastery_update(c, sid, key, concept, correct, error_type):
    """Apply one BKT step for (topic, concept) and tally the error. Returns new P(L)."""
    row = c.execute(
        "SELECT p, answers FROM concept_mastery WHERE student_id = ? AND topic = ? AND concept = ?",
        (sid, key, concept),
    ).fetchone()
    if row:
        prev, answers = row["p"], row["answers"] + 1
    else:
        prev, answers = _prior_for(c, sid, key), 1
    p = round(_bkt_step(prev, correct), 4)
    c.execute(
        "INSERT INTO concept_mastery (student_id, topic, concept, p, answers, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(student_id, topic, concept) DO UPDATE SET p = excluded.p, "
        "answers = excluded.answers, updated_at = excluded.updated_at",
        (sid, key, concept, p, answers, time.time()),
    )
    if not correct and error_type:
        c.execute(
            "INSERT INTO error_tally (student_id, topic, concept, error_type, count) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(student_id, topic, concept, error_type) DO UPDATE SET count = count + 1",
            (sid, key, concept, error_type),
        )
    return p


def record_answer(subject, topic, correct, error_type=None, concept=None,
                  question="", answer="", feedback="", sid=None):
    """One graded answer. Updates BKT for exactly (topic, concept).

    Returns the new P(L), or None when the question carried no concept tag
    (recorded in history only — no concept evidence to update).
    """
    sid = _sid(sid)
    key = _key(subject, topic)
    correct = bool(correct)
    p = None
    if concept:
        with _conn() as c:
            p = _mastery_update(c, sid, key, concept, correct, error_type)
    with _conn() as c:
        c.execute(
            "INSERT INTO history (student_id, ts, topic, concept, question, answer, correct, error_type, feedback) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, time.time(), key, concept, question, answer, int(correct), error_type, feedback),
        )
    return p


def backfill_concept(history_id, concept, correct, error_type=None, sid=None):
    """Tag an already-recorded answer with its concept and apply the BKT step it missed.

    Used by tools/backfill_concepts.py to replay answers graded before the bank carried
    concepts. The history row keeps its original timestamp, so replaying in chronological
    order rebuilds the numbers a live session would have produced. No-op if the row
    already carries a concept.
    """
    sid = _sid(sid)
    with _conn() as c:
        row = c.execute(
            "SELECT topic, concept FROM history WHERE id = ? AND student_id = ?",
            (history_id, sid),
        ).fetchone()
        if not row or row["concept"]:
            return None
        c.execute("UPDATE history SET concept = ? WHERE id = ?", (concept, history_id))
        return _mastery_update(c, sid, row["topic"], concept, bool(correct), error_type)


def get_concepts(subject=None, topic=None, sid=None):
    """Per-concept BKT state, weakest first. Keys are 'subject:topic'."""
    sid = _sid(sid)
    if not sid:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT topic, concept, p, answers FROM concept_mastery WHERE student_id = ? "
            "ORDER BY p ASC, answers DESC", (sid,),
        ).fetchall()
    out = []
    for r in rows:
        subj, top = _split(r["topic"])
        if subject and subj != subject:
            continue
        if topic and top != topic:
            continue
        out.append({"key": r["topic"], "subject": subj, "topic": top,
                    "concept": r["concept"], "p": r["p"], "answers": r["answers"]})
    return out


def topic_mean_p(subject, topic, sid=None):
    rows = get_concepts(subject, topic, sid)
    if not rows:
        return None
    return round(sum(r["p"] for r in rows) / len(rows), 3)


def get_error_tally(subject=None, topic=None, concept=None, sid=None):
    """{(topic_key, concept): {error_type: count}} filtered by the given scope."""
    sid = _sid(sid)
    if not sid:
        return {}
    with _conn() as c:
        rows = c.execute(
            "SELECT topic, concept, error_type, count FROM error_tally WHERE student_id = ?", (sid,)
        ).fetchall()
    tally = {}
    for r in rows:
        subj, top = _split(r["topic"])
        if subject and subj != subject:
            continue
        if topic and top != topic:
            continue
        if concept and r["concept"] != concept:
            continue
        tally.setdefault((r["topic"], r["concept"]), {})[r["error_type"]] = r["count"]
    return tally


def get_history(limit=100, subject=None, sid=None):
    sid = _sid(sid)
    if not sid:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, topic, concept, question, correct, error_type FROM history "
            "WHERE student_id = ? ORDER BY id DESC LIMIT ?", (sid, limit),
        ).fetchall()
    out = []
    for r in rows:
        subj, topic = _split(r["topic"])
        if subject and subj != subject:
            continue
        d = dict(r)
        d["subject"] = subj
        d["topic"] = topic
        out.append(d)
    return out


def topic_signature(subject, topic, sid=None):
    """Snapshot of everything the lesson generator adapts to for one topic."""
    sid = _sid(sid)
    key = _key(subject, topic)
    with _conn() as c:
        rows = c.execute(
            "SELECT concept, p, answers FROM concept_mastery WHERE student_id = ? AND topic = ? "
            "ORDER BY concept", (sid, key),
        ).fetchall()
        errs = c.execute(
            "SELECT concept, error_type, count FROM error_tally WHERE student_id = ? AND topic = ? "
            "ORDER BY concept, error_type", (sid, key),
        ).fetchall()
    return json.dumps(
        [
            [[r["concept"], r["p"], r["answers"]] for r in rows],
            [[e["concept"], e["error_type"], e["count"]] for e in errs],
        ]
    )


def recommend_next(subject=None, valid_keys=None, sid=None):
    """Weakest concept by BKT. Before any practice data, fall back to what the
    student said they struggle with. Keys are 'subject:topic'."""
    concepts = get_concepts(subject, sid=sid)
    if concepts:
        weakest = concepts[0]
        errors = get_error_tally(subject, topic=weakest["topic"],
                                 concept=weakest["concept"], sid=sid).get(
            (weakest["key"], weakest["concept"]), {})
        top_error = max(errors.items(), key=lambda x: x[1])[0] if errors else None
        return {"key": weakest["key"], "subject": weakest["subject"], "topic": weakest["topic"],
                "concept": weakest["concept"], "score": weakest["p"],
                "error_type": top_error, "self_reported": False}
    student = get_student(sid)
    if student and valid_keys:
        for key in student["struggles"]:
            if valid_keys is None or key in valid_keys:
                if subject and not key.startswith(subject + ":"):
                    continue
                return {"key": key, "subject": _split(key)[0], "topic": _split(key)[1],
                        "concept": None, "score": None, "error_type": None, "self_reported": True}
    return None


# ---------------- Accounts ----------------
#
# Sign-in is email + password, hashed with scrypt through Werkzeug (no extra
# dependency, no third-party identity provider). Nothing here returns another
# account's identity to a caller that is not that account: the old list_students()
# dumped every name on the sign-in page and /login/<id> accepted a URL as proof,
# so any visitor could read any student's work. It is gone.

MIN_PASSWORD = 8            # NIST SP 800-63B floor; no composition rules, no maximum hassle
MAX_PASSWORD = 128          # scrypt cost scales with the input, so cap it: a 10 MB "password"
                            # would tie up a serverless request long enough to be free DoS
MAX_FAILED = 8              # then the account waits out LOCK_SECONDS
LOCK_SECONDS = 15 * 60
MIN_AGE = 13                # below this we do not take an account at all

# One place decides how a password is stored. scrypt through Werkzeug: memory-hard, a
# unique 16-byte salt inside every hash, constant-time comparison, and no third party
# holding identities. Nothing about a password is ever logged, and nothing is reversible.
# Raise these numbers later and accounts upgrade themselves silently on the next
# successful sign-in — no migration script, no forced resets, no plaintext anywhere.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT = 16
_SCRYPT_METHOD = f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# A student row whose email is NULL predates credentials (or was made by a tool) and
# cannot sign in until attach_account gives it one.
_DUMMY_HASH = ("scrypt:32768:8:1$6yKFmZbyoDPF1CgU$a159cc75f06ebfe36582a239c69382d8"
               "70778d8703a57caa69ec07d4fa21393b61f885a2f79653632fc33a568f11e1d1e15"
               "0815101a214446764381ed7435dcd")

# Long enough to catch the ones people actually reach for, short enough to keep here.
WEAK_PASSWORDS = frozenset("""
password passw0rd password1 password123 password1234 123456 1234567 12345678 123456789
1234567890 qwerty qwerty123 asdfgh letmein abc123 iloveyou monkey dragon sunshine
princess football welcome login admin guest master superman batman 000000 111111
a1b2c3 1q2w3e4r zxcvbnm naruto onepiece
""".split())


def normalise_email(email):
    return (email or "").strip().lower()


def valid_email(email):
    return bool(EMAIL_RE.match(normalise_email(email))) and len(email) <= 254


def password_problem(password):
    """A human reason this password cannot be used, or None if it can."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"That password is too long ({MAX_PASSWORD} characters maximum)."
    if password.lower() in WEAK_PASSWORDS:
        return "That password is too common to protect an account. Add a few more words."
    return None


def hash_password(password):
    # Werkzeug reads the scrypt costs from the method name itself ("scrypt:N:r:p"),
    # not from separate arguments, so SCRYPT_* are encoded there once.
    return generate_password_hash(password, method=_SCRYPT_METHOD,
                                  salt_length=SCRYPT_SALT)


def needs_rehash(stored):
    """True when a row was hashed under older parameters than SCRYPT_* now asks for."""
    return not str(stored or "").startswith(_SCRYPT_METHOD + "$")


def age_from(birth_date, today=None):
    """Whole years on the ISO date, or None if there is no usable date."""
    if not birth_date:
        return None
    try:
        y, m, d = (int(x) for x in str(birth_date)[:10].split("-"))
        born = datetime.date(y, m, d)
    except (ValueError, TypeError):
        return None
    today = today or datetime.date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def birth_date_problem(value):
    """A student-facing reason to reject a date of birth, or None. Blank is allowed:
    the field is asked for, not forced, until the consent flow is settled."""
    if not value:
        return None
    age = age_from(value)
    if age is None:
        return "That date of birth does not look right. Use the format 2010-04-17."
    if age < 0:
        return "That date is in the future."
    if age > 120:
        return "That date of birth is too far back to be right."
    return None


def is_child(birth_date):
    """Under the Kenya Data Protection Act 2019 a child is anyone below 18."""
    age = age_from(birth_date)
    return age is not None and age < 18


def _duplicate(exc):
    text = str(exc).lower()
    return "unique" in text or "duplicate key" in text


def _grade_problem(curriculum_slug, grade):
    """A year label only means something inside the qualification that uses it:
    'Form 3' is a KCSE year, 'Grade 11' is a CBC one, and the SAT has neither."""
    from curriculum import grades_for
    options = grades_for(curriculum_slug)
    if not options or grade in options:
        return None
    return "Pick your year from the list for that qualification."


def create_account(name, grade, email, password, curriculum="kcse", birth_date=None):
    """Register a new student. Raises ValueError with a student-facing message."""
    email = normalise_email(email)
    if not valid_email(email):
        raise ValueError("That does not look like an email address.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    problem = birth_date_problem(birth_date)
    if problem:
        raise ValueError(problem)
    if birth_date and age_from(birth_date) < MIN_AGE:
        raise ValueError("This course is written for students in secondary school and "
                         "above, so we cannot make an account for someone that young yet.")
    problem = _grade_problem(curriculum, grade)
    if problem:
        raise ValueError(problem)
    try:
        with _conn() as c:
            return c.insert(
                "INSERT INTO students (name, grade, struggles, curriculum, email, "
                "password_hash, birth_date, created_at) VALUES (?, ?, '[]', ?, ?, ?, ?, ?)",
                (name, grade, curriculum, email, hash_password(password),
                 birth_date or None, time.time()),
            )
    except Exception as exc:
        if _duplicate(exc):        # the unique index is the real guard against a race
            raise ValueError("An account already uses that email address.") from exc
        raise


def sign_in(email, password):
    """The sid for correct credentials, else None.

    Every branch costs one scrypt check and answers the same way, so neither timing nor
    wording tells an attacker whether the address has an account here.
    """
    email = normalise_email(email)
    with _conn() as c:
        row = c.execute(
            "SELECT id, password_hash, failed, locked_until FROM students "
            "WHERE lower(email) = ?", (email,)).fetchone()
    if not row:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if (row["locked_until"] or 0) > time.time():
        check_password_hash(row["password_hash"] or _DUMMY_HASH, password)
        return None
    ok = bool(row["password_hash"]) and check_password_hash(row["password_hash"], password)
    with _conn() as c:
        if ok:
            c.execute("UPDATE students SET failed = 0, locked_until = 0 WHERE id = ?",
                      (row["id"],))
            # The only moment the plaintext exists is right here, and this is the only
            # place it is ever used: a row hashed under old parameters is rewritten under
            # the current ones while we still hold the password.
            if needs_rehash(row["password_hash"]):
                c.execute("UPDATE students SET password_hash = ? WHERE id = ?",
                          (hash_password(password), row["id"]))
        else:
            failed = (row["failed"] or 0) + 1
            locked = time.time() + LOCK_SECONDS if failed >= MAX_FAILED else 0
            c.execute("UPDATE students SET failed = ?, locked_until = ? WHERE id = ?",
                      (failed, locked, row["id"]))
    return row["id"] if ok else None


def lock_seconds_left(email):
    with _conn() as c:
        row = c.execute("SELECT locked_until FROM students WHERE lower(email) = ?",
                        (normalise_email(email),)).fetchone()
    return max(0, round((row["locked_until"] or 0) - time.time())) if row else 0


def verify_password(sid, password):
    """Proof the person asking already knows the account, for the two actions a stolen
    cookie alone should not be able to take: changing the password and deleting."""
    with _conn() as c:
        row = c.execute("SELECT password_hash FROM students WHERE id = ?", (sid,)).fetchone()
    if not row or not row["password_hash"]:
        check_password_hash(_DUMMY_HASH, password)   # same cost as a real attempt
        return False
    return bool(check_password_hash(row["password_hash"], password))


def set_password(sid, password):
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    with _conn() as c:
        c.execute("UPDATE students SET password_hash = ?, failed = 0, locked_until = 0 "
                  "WHERE id = ?", (hash_password(password), sid))


def attach_account(sid, email, password):
    """Give an existing (pre-credentials) account a sign-in, keeping all its data."""
    email = normalise_email(email)
    if not valid_email(email):
        raise ValueError("That does not look like an email address.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    with _conn() as c:
        if not c.execute("SELECT id FROM students WHERE id = ?", (sid,)).fetchone():
            raise ValueError(f"No account with id {sid}.")
        try:
            c.execute("UPDATE students SET email = ?, password_hash = ?, failed = 0, "
                      "locked_until = 0 WHERE id = ?",
                      (email, hash_password(password), sid))
        except Exception as exc:
            if _duplicate(exc):
                raise ValueError(f"{email} already belongs to another account.") from exc
            raise


def account_email(sid):
    with _conn() as c:
        row = c.execute("SELECT email FROM students WHERE id = ?", (sid,)).fetchone()
    return row["email"] if row else None


def accounts_without_email():
    """Accounts created before sign-in existed — nobody can log in to these yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, grade, curriculum FROM students WHERE email IS NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def create_student(name, grade, curriculum="kcse"):
    raise RuntimeError(
        "create_student made an account nobody could sign in to; use create_account()")


def get_student(sid=None):
    sid = _sid(sid)
    if not sid:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT id, name, grade, struggles, confidence, style, curriculum, birth_date, "
            "email, created_at FROM students WHERE id = ?",
            (sid,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["struggles"] = json.loads(d["struggles"] or "[]")
    return d


def update_student(sid, struggles=None, confidence=None, style=None):
    with _conn() as c:
        if struggles is not None:
            c.execute("UPDATE students SET struggles = ? WHERE id = ?", (json.dumps(struggles), sid))
        if confidence is not None:
            c.execute("UPDATE students SET confidence = ? WHERE id = ?", (confidence, sid))
        if style is not None:
            c.execute("UPDATE students SET style = ? WHERE id = ?", (style, sid))


def set_curriculum(sid, curriculum):
    with _conn() as c:
        c.execute("UPDATE students SET curriculum = ? WHERE id = ?", (curriculum, sid))


# ---------------- Lessons ----------------

def save_lesson(subject, topic, lesson, sig, sid=None):
    sid = _sid(sid)
    with _conn() as c:
        c.execute(
            "INSERT INTO lessons (student_id, topic, lesson_json, created_at, profile_sig) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET lesson_json = excluded.lesson_json, "
            "created_at = excluded.created_at, profile_sig = excluded.profile_sig",
            (sid, _key(subject, topic), json.dumps(lesson), time.time(), sig),
        )


def load_lesson(subject, topic, sid=None):
    sid = _sid(sid)
    if not sid:
        return None, None
    with _conn() as c:
        row = c.execute(
            "SELECT lesson_json, profile_sig FROM lessons WHERE student_id = ? AND topic = ?",
            (sid, _key(subject, topic)),
        ).fetchone()
    if not row:
        return None, None
    return json.loads(row["lesson_json"]), row["profile_sig"]


def clear_lesson(subject, topic, sid=None):
    """Drop the cached lesson so the next poll rebuilds it from scratch."""
    sid = _sid(sid)
    if not sid:
        return
    with _conn() as c:
        c.execute("DELETE FROM lessons WHERE student_id = ? AND topic = ?",
                  (sid, _key(subject, topic)))


# ---------------- Lesson generation jobs ----------------
# A deployed function can't keep a thread alive after it has answered, so the work is
# claimed by whichever request asks for it and tracked here: pending -> running ->
# ready | error. The key carries the account, because lessons are per-student.

GEN_STALE_AFTER = 330  # seconds; a 'running' row older than this never finished


def queue_generation(key):
    """Ask for this lesson to be generated unless a run is already in flight."""
    with _conn() as c:
        c.execute(
            "INSERT INTO gen_jobs (key, status, error, updated_at) VALUES (?, 'pending', NULL, ?) "
            "ON CONFLICT(key) DO UPDATE SET status = 'pending', error = NULL, "
            "updated_at = excluded.updated_at WHERE gen_jobs.status IN ('ready', 'error')",
            (key, time.time()),
        )


def claim_generation(key):
    """True for the one request that gets to do the work."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "UPDATE gen_jobs SET status = 'running', updated_at = ? "
            "WHERE key = ? AND (status = 'pending' OR (status = 'running' AND updated_at < ?))",
            (now, key, now - GEN_STALE_AFTER),
        )
        return cur.rowcount == 1


def finish_generation(key, error=None):
    with _conn() as c:
        c.execute("UPDATE gen_jobs SET status = ?, error = ?, updated_at = ? WHERE key = ?",
                  ("error" if error else "ready", error, time.time(), key))


def generation_state(key):
    with _conn() as c:
        row = c.execute("SELECT status, error FROM gen_jobs WHERE key = ?", (key,)).fetchone()
    return (row["status"], row["error"]) if row else (None, None)


# ---------------- Uploaded documents ----------------

def save_document(subject, filename, pages=None, analysis=None, status="done", sid=None):
    sid = _sid(sid)
    with _conn() as c:
        return c.insert(
            "INSERT INTO documents (student_id, ts, subject, filename, pages, analysis_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, time.time(), subject, filename, pages, json.dumps(analysis) if analysis else None, status),
        )


def update_document(doc_id, analysis=None, status=None, pages=None, sid=None):
    """Change one upload's record — and only the caller's own upload.

    Returns how many rows changed, so a caller can tell that the id it was handed
    belongs to somebody else instead of quietly believing the write happened."""
    sid = _sid(sid)
    sets, values = [], []
    if analysis is not None:
        sets.append("analysis_json = ?")
        values.append(json.dumps(analysis))
    if status is not None:
        sets.append("status = ?")
        values.append(status)
    if pages is not None:
        sets.append("pages = ?")
        values.append(pages)
    if not sets:
        return 0
    values += [doc_id, sid]
    with _conn() as c:
        return c.execute(
            f"UPDATE documents SET {', '.join(sets)} WHERE id = ? AND student_id = ?",
            tuple(values)).rowcount


def get_documents(subject=None, limit=50, sid=None):
    sid = _sid(sid)
    if not sid:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ts, subject, filename, pages, analysis_json, status FROM documents "
            "WHERE student_id = ? ORDER BY id DESC LIMIT ?", (sid, limit),
        ).fetchall()
    out = []
    for r in rows:
        if subject and r["subject"] != subject:
            continue
        d = dict(r)
        d["analysis"] = json.loads(d.pop("analysis_json")) if d.get("analysis_json") else None
        out.append(d)
    return out


def apply_document_findings(subject, findings, sid=None):
    """Fold an analysed paper into the profile, per concept.

    findings: list of {"topic", "concept", "correct", "error_type", ...}.
    Each finding is one BKT observation on its (topic, concept).
    """
    scores = {}
    for f in findings or []:
        topic = f.get("topic")
        if not topic:
            continue
        scores[(topic, f.get("concept"))] = record_answer(
            subject, topic, bool(f.get("correct")),
            f.get("error_type"),
            concept=f.get("concept"),
            question=str(f.get("question", ""))[:4000],
            answer=str(f.get("answer", ""))[:4000],
            feedback=str(f.get("note", ""))[:2000],
            sid=sid,
        )
    return scores


# ---------------- Async grading jobs ----------------

def create_grading_job(sid, subject, topic, payload):
    with _conn() as c:
        return c.insert(
            "INSERT INTO grading_jobs (student_id, subject, topic, payload_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'grading', ?)",
            (sid, subject, topic, json.dumps(payload), time.time()),
        )


def claim_grading_job(job_id, sid=None):
    """True for the one request that gets to mark this paper. A 'running' row whose
    instance was recycled can be taken over once the function deadline has passed.
    Scoped to the owner: guessing another student's job id must not buy free work
    from this account's provider keys, or write into their result."""
    sid = _sid(sid)
    with _conn() as c:
        cur = c.execute(
            "UPDATE grading_jobs SET status = 'running' "
            "WHERE id = ? AND student_id = ? AND (status = 'grading' OR "
            "(status = 'running' AND created_at < ?))",
            (job_id, sid, time.time() - GEN_STALE_AFTER),
        )
        return cur.rowcount == 1


def get_grading_job(job_id, sid=None):
    """One job, and only when it belongs to the account asking (or is being read by
    the background worker that owns it through sid)."""
    sid = _sid(sid)
    if not sid:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT id, student_id, subject, topic, payload_json, status, result_json, created_at, finished_at "
            "FROM grading_jobs WHERE id = ? AND student_id = ?", (job_id, sid),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
    return d


def finish_grading_job(job_id, result, status="done", sid=None):
    sid = _sid(sid)
    with _conn() as c:
        c.execute(
            "UPDATE grading_jobs SET status = ?, result_json = ?, finished_at = ? "
            "WHERE id = ? AND student_id = ?",
            (status, json.dumps(result), time.time(), job_id, sid),
        )


def list_grading_jobs(sid, limit=20):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, subject, topic, status, created_at, finished_at FROM grading_jobs "
            "WHERE student_id = ? ORDER BY id DESC LIMIT ?", (sid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- Guided paper walkthroughs ----------------

def create_walkthrough(sid, subject, filename, questions):
    with _conn() as c:
        return c.insert(
            "INSERT INTO walkthroughs (student_id, subject, filename, questions_json, idx, status, created_at) "
            "VALUES (?, ?, ?, ?, 0, 'active', ?)",
            (sid, subject, filename, json.dumps(questions), time.time()),
        )


def get_walkthrough(wt_id, sid=None):
    """One walkthrough, only for its owner. The id arrives from the URL, so this is the
    whole of the protection on a student's questions and answers."""
    sid = _sid(sid)
    if not sid:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT id, student_id, subject, filename, questions_json, idx, status, created_at "
            "FROM walkthroughs WHERE id = ? AND student_id = ?", (wt_id, sid),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["questions"] = json.loads(d.pop("questions_json"))
    return d


def update_walkthrough(wt_id, idx=None, status=None, sid=None):
    """Move the marker on, but never on someone else's paper."""
    sid = _sid(sid)
    sets, values = [], []
    if idx is not None:
        sets.append("idx = ?")
        values.append(idx)
    if status is not None:
        sets.append("status = ?")
        values.append(status)
    if not sets:
        return 0
    values += [wt_id, sid]
    with _conn() as c:
        return c.execute(
            f"UPDATE walkthroughs SET {', '.join(sets)} WHERE id = ? AND student_id = ?",
            tuple(values)).rowcount


def load_walkthrough_step(wt_id, idx, attempt, sid=None):
    """The board lesson already built for this question, if this student has built it."""
    sid = _sid(sid)
    if not sid:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT s.lesson_json FROM walkthrough_steps s "
            "JOIN walkthroughs w ON w.id = s.walkthrough_id "
            "WHERE s.walkthrough_id = ? AND s.idx = ? AND s.attempt = ? "
            "AND w.student_id = ?",
            (wt_id, idx, attempt, sid),
        ).fetchone()
    return json.loads(row["lesson_json"]) if row else None


def save_walkthrough_step(wt_id, idx, attempt, lesson, sid=None):
    """Keep a built step. A prefetch and the request that shows the same step can race;
    whichever lands first wins and the other is discarded, since both hold the same thing.

    The row is made from the walkthroughs table itself, so a step can only ever be
    attached to a paper the caller owns."""
    sid = _sid(sid)
    with _conn() as c:
        return c.execute(
            "INSERT INTO walkthrough_steps "
            "(walkthrough_id, idx, attempt, lesson_json, created_at) "
            "SELECT id, ?, ?, ?, ? FROM walkthroughs WHERE id = ? AND student_id = ? "
            "ON CONFLICT (walkthrough_id, idx, attempt) DO NOTHING",
            (idx, attempt, json.dumps(lesson), time.time(), wt_id, sid)).rowcount


def list_walkthroughs(sid, limit=20):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, subject, filename, idx, status, created_at, questions_json FROM walkthroughs "
            "WHERE student_id = ? ORDER BY id DESC LIMIT ?", (sid, limit),
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        try:
            row["total"] = len(json.loads(row.pop("questions_json") or "[]"))
        except (ValueError, TypeError):
            row["total"] = 0
        out.append(row)
    return out


# ---------------- Your data: read it, change it, take it away ----------------
#
# KDPA gives a student the right to a copy of what we hold and the right to have it
# removed. Both start here, and both are scoped to one student id — nothing in this
# section can reach another account.

# (section name, table, columns, column to sort by). Every one of these tables
# carries student_id. Sorting is by a real column because Postgres has no rowid.
_OWNED_SECTIONS = (
    ("topic_scores", "topic_seed", ("topic", "score"), "topic"),
    ("concept_mastery", "concept_mastery",
     ("topic", "concept", "p", "answers", "updated_at"), "topic"),
    ("mistake_patterns", "error_tally", ("topic", "concept", "error_type", "count"), "topic"),
    ("answers", "history", ("ts", "topic", "concept", "question", "answer", "correct",
                            "error_type", "feedback"), "ts"),
    ("lessons", "lessons", ("topic", "created_at", "lesson_json"), "topic"),
    ("papers", "documents", ("ts", "subject", "filename", "pages", "status", "analysis_json"),
     "ts"),
    ("walkthroughs", "walkthroughs", ("subject", "filename", "idx", "status", "created_at",
                                      "questions_json"), "id"),
    ("graded_papers", "grading_jobs", ("subject", "topic", "status", "created_at",
                                       "finished_at", "payload_json", "result_json"), "id"),
)

# Tables holding a JSON string; the "_json" suffix is dropped once parsed.
_OWNED_DELETE = ("topic_seed", "concept_mastery", "error_tally", "history", "lessons",
                 "documents", "grading_jobs", "walkthroughs")


def _decoded(row):
    """Swap any *_json column for the parsed object, keeping the row readable if parsing
    fails — a corrupt cell should not cost the student the rest of their export."""
    out = dict(row)
    for key in [k for k in out if str(k).endswith("_json")]:
        raw = out.pop(key)
        try:
            out[key[:-5]] = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            out[key[:-5]] = raw
    return out


def export_account(sid):
    """Everything we hold for one student, as plain JSON-serialisable data.

    Deliberately absent: password_hash (a one-way scramble, but no reason to hand it
    round), the lockout counters, and any uploaded file — the paper itself is never
    stored, only the analysis we produced from it.
    """
    sid = _sid(sid)
    if not sid:
        raise ValueError("No account signed in.")
    with _conn() as c:
        row = c.execute(
            "SELECT id, name, grade, curriculum, email, birth_date, struggles, confidence, "
            "style, created_at FROM students WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise ValueError("No account with that id.")
        account = _decoded(row)
        account["struggles"] = json.loads(account["struggles"] or "[]")
        sections = {"account": [account]}

        for name, table, cols, order in _OWNED_SECTIONS:
            rows = c.execute(
                f"SELECT {', '.join(cols)} FROM {table} WHERE student_id = ? "
                f"ORDER BY {order}", (sid,)).fetchall()
            sections[name] = [_decoded(r) for r in rows]

        steps = c.execute(
            "SELECT walkthrough_id, idx, attempt, created_at, lesson_json FROM walkthrough_steps "
            "WHERE walkthrough_id IN (SELECT id FROM walkthroughs WHERE student_id = ?) "
            "ORDER BY walkthrough_id, idx, attempt", (sid,)).fetchall()
        sections["walkthrough_steps"] = [_decoded(s) for s in steps]
    return {
        "exported_at": time.time(),
        "format_version": 1,
        "note": "Generated by Brightboard for the account named below. Uploaded files are "
                "not included: they are deleted once analysed.",
        "data": sections,
    }


def delete_account(sid):
    """Erase one account and everything hanging off it. Returns how many rows went.

    Order matters: the steps table has no student_id of its own, so it goes while its
    parent walkthroughs can still be named.
    """
    sid = _sid(sid)
    if not sid:
        raise ValueError("No account signed in.")
    counts = {}
    with _conn() as c:
        if not c.execute("SELECT id FROM students WHERE id = ?", (sid,)).fetchone():
            raise ValueError("No account with that id.")
        wt_ids = [r["id"] for r in c.execute(
            "SELECT id FROM walkthroughs WHERE student_id = ?", (sid,)).fetchall()]
        if wt_ids:
            # Deleted first: this table has no student_id, so its only honest scope is
            # the walkthroughs that belong to this account.
            counts["walkthrough_steps"] = c.execute(
                "DELETE FROM walkthrough_steps WHERE walkthrough_id IN ("
                + ",".join("?" * len(wt_ids)) + ")", tuple(wt_ids)).rowcount
        for table in _OWNED_DELETE:
            counts[table] = c.execute(
                f"DELETE FROM {table} WHERE student_id = ?", (sid,)).rowcount
        counts["students"] = c.execute("DELETE FROM students WHERE id = ?", (sid,)).rowcount
    return counts


def data_inventory(sid):
    """How many rows of each kind the account holds — the numbers shown on /account
    before anyone downloads or deletes anything. Counts only, no content."""
    sid = _sid(sid)
    if not sid:
        raise ValueError("No account signed in.")
    out = {}
    with _conn() as c:
        for name, table, _cols, _order in _OWNED_SECTIONS:
            row = c.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE student_id = ?",
                            (sid,)).fetchone()
            out[name] = row["n"]
        row = c.execute(
            "SELECT COUNT(*) AS n FROM walkthrough_steps WHERE walkthrough_id IN "
            "(SELECT id FROM walkthroughs WHERE student_id = ?)", (sid,)).fetchone()
        out["walkthrough_steps"] = row["n"]
    return out


def update_profile(sid, name=None, grade=None, birth_date=None, curriculum=None,
                   style=None, confidence=None):
    """Change the profile fields a student controls, validating before any write."""
    sid = _sid(sid)
    if not sid:
        raise ValueError("No account signed in.")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Your name cannot be empty.")
        if len(name) > 60:
            raise ValueError("That name is too long.")
    if curriculum is not None:
        from curriculum import curricula
        if curriculum not in {c["slug"] for c in curricula()}:
            raise ValueError("That curriculum does not exist.")
    if grade is not None:
        # The year has to be valid for the qualification it is saved with — which may be
        # the one arriving in this very request, so switching curriculum and year
        # together is allowed and switching only the year is checked against the old one.
        problem = _grade_problem(curriculum or (get_student(sid) or {}).get("curriculum", "kcse"),
                                 grade)
        if problem:
            raise ValueError(problem)
    if birth_date is not None:
        birth_date = (birth_date or "").strip() or None
        problem = birth_date_problem(birth_date)
        if problem:
            raise ValueError(problem)
        if birth_date and (age_from(birth_date) or 0) < MIN_AGE:
            raise ValueError("Accounts start at age " + str(MIN_AGE) + ".")
    if style is not None and style not in STUDY_STYLES:
        raise ValueError("Choose a study style from the list.")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        raise ValueError("Choose a confidence level from the list.")

    writes, values = [], []
    for col, val in (("name", name), ("grade", grade), ("birth_date", birth_date),
                     ("curriculum", curriculum), ("style", style),
                     ("confidence", confidence)):
        if val is not None:
            writes.append(col + " = ?")
            values.append(val)
    if not writes:
        return
    values.append(sid)
    with _conn() as c:
        c.execute(f"UPDATE students SET {', '.join(writes)} WHERE id = ?", tuple(values))


init_db()
