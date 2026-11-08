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
    with _conn() as c:
        c.execute(
            "INSERT INTO history (student_id, ts, topic, concept, question, answer, correct, error_type, feedback) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, time.time(), key, concept, question, answer, int(correct), error_type, feedback),
        )
    return p


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


def _duplicate(exc):
    text = str(exc).lower()
    return "unique" in text or "duplicate key" in text


def create_account(name, grade, email, password, curriculum="kcse"):
    """Register a new student. Raises ValueError with a student-facing message."""
    email = normalise_email(email)
    if not valid_email(email):
        raise ValueError("That does not look like an email address.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    try:
        with _conn() as c:
            return c.insert(
                "INSERT INTO students (name, grade, struggles, curriculum, email, "
                "password_hash, created_at) VALUES (?, ?, '[]', ?, ?, ?, ?)",
                (name, grade, curriculum, email,
                 generate_password_hash(password, method="scrypt"), time.time()),
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


def set_password(sid, password):
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    with _conn() as c:
        c.execute("UPDATE students SET password_hash = ?, failed = 0, locked_until = 0 "
                  "WHERE id = ?", (generate_password_hash(password, method="scrypt"), sid))


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
                      (email, generate_password_hash(password, method="scrypt"), sid))
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
            "SELECT id, name, grade, struggles, confidence, style, curriculum FROM students WHERE id = ?",
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


def update_document(doc_id, analysis=None, status=None, pages=None):
    with _conn() as c:
        if analysis is not None:
            c.execute("UPDATE documents SET analysis_json = ? WHERE id = ?", (json.dumps(analysis), doc_id))
        if status is not None:
            c.execute("UPDATE documents SET status = ? WHERE id = ?", (status, doc_id))
        if pages is not None:
            c.execute("UPDATE documents SET pages = ? WHERE id = ?", (pages, doc_id))


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


def claim_grading_job(job_id):
    """True for the one request that gets to mark this paper. A 'running' row whose
    instance was recycled can be taken over once the function deadline has passed."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE grading_jobs SET status = 'running' "
            "WHERE id = ? AND (status = 'grading' OR (status = 'running' AND created_at < ?))",
            (job_id, time.time() - GEN_STALE_AFTER),
        )
        return cur.rowcount == 1


def get_grading_job(job_id, sid=None):
    with _conn() as c:
        row = c.execute(
            "SELECT id, student_id, subject, topic, payload_json, status, result_json, created_at, finished_at "
            "FROM grading_jobs WHERE id = ?", (job_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if sid is not None and d["student_id"] != sid:
        return None
    d["payload"] = json.loads(d.pop("payload_json"))
    d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
    return d


def finish_grading_job(job_id, result, status="done"):
    with _conn() as c:
        c.execute(
            "UPDATE grading_jobs SET status = ?, result_json = ?, finished_at = ? WHERE id = ?",
            (status, json.dumps(result), time.time(), job_id),
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
    with _conn() as c:
        row = c.execute(
            "SELECT id, student_id, subject, filename, questions_json, idx, status, created_at "
            "FROM walkthroughs WHERE id = ?", (wt_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if sid is not None and d["student_id"] != sid:
        return None
    d["questions"] = json.loads(d.pop("questions_json"))
    return d


def update_walkthrough(wt_id, idx=None, status=None):
    with _conn() as c:
        if idx is not None:
            c.execute("UPDATE walkthroughs SET idx = ? WHERE id = ?", (idx, wt_id))
        if status is not None:
            c.execute("UPDATE walkthroughs SET status = ? WHERE id = ?", (status, wt_id))


def load_walkthrough_step(wt_id, idx, attempt):
    """The board lesson already built for this question, if anyone has built it."""
    with _conn() as c:
        row = c.execute(
            "SELECT lesson_json FROM walkthrough_steps "
            "WHERE walkthrough_id = ? AND idx = ? AND attempt = ?",
            (wt_id, idx, attempt),
        ).fetchone()
    return json.loads(row["lesson_json"]) if row else None


def save_walkthrough_step(wt_id, idx, attempt, lesson):
    """Keep a built step. A prefetch and the request that shows the same step can race;
    whichever lands first wins and the other is discarded, since both hold the same thing."""
    with _conn() as c:
        c.execute(
            "INSERT INTO walkthrough_steps "
            "(walkthrough_id, idx, attempt, lesson_json, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (walkthrough_id, idx, attempt) DO NOTHING",
            (wt_id, idx, attempt, json.dumps(lesson), time.time()),
        )


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


init_db()
