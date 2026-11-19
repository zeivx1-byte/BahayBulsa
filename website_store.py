"""Account-scoped storage for the BahayBulsa website."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
import json
from pathlib import Path
import re
from secrets import token_bytes, token_urlsafe
import sqlite3
from time import time


_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}\Z")
_SESSION_SECONDS = 14 * 24 * 60 * 60
_PASSWORD_ITERATIONS = 600_000
_HISTORY_LIMIT = 8
_CENTAVO = Decimal("0.01")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    salt BLOB NOT NULL,
    password_hash BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash BLOB PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboard (
    owner_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0,
    organizer_state TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS transactions (
    owner_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    posted_on TEXT NOT NULL,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    note TEXT NOT NULL,
    PRIMARY KEY (owner_id, id)
);
CREATE INDEX IF NOT EXISTS transaction_owner_date ON transactions(owner_id, posted_on, id);
CREATE TABLE IF NOT EXISTS monthly_preferences (
    owner_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    preferences TEXT NOT NULL,
    PRIMARY KEY (owner_id, month)
);
CREATE TABLE IF NOT EXISTS conversations (
    owner_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0,
    history TEXT NOT NULL DEFAULT '[]',
    memory TEXT
);
"""


@dataclass(frozen=True)
class Account:
    id: int
    username: str


@dataclass(frozen=True)
class Conversation:
    revision: int
    history: list[dict[str, str]]
    memory: dict | None


class UsernameUnavailable(ValueError):
    """An account already uses the normalized username."""


class RevisionConflict(ValueError):
    """The dashboard or conversation was updated by another request."""


def _password_hash(password: str, salt: bytes) -> bytes:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)


def _token_hash(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()


def _transaction_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "date": row["posted_on"], "category": row["category"],
        "amount": row["amount_cents"] / 100, "type": row["kind"], "note": row["note"],
    }


def _transaction_values(owner_id: int, position: int, row: dict) -> tuple:
    amount = Decimal(str(row["amount"])).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    return (owner_id, row["id"], position, row["date"], row["category"], int(amount * 100),
            row["type"], row.get("note", ""))


def _select_transactions(db: sqlite3.Connection, owner_id: int, start: date, end: date, limit: int) -> list[dict]:
    return [_transaction_row(row) for row in db.execute(
        "SELECT id, posted_on, category, amount_cents, kind, '' AS note FROM transactions "
        "WHERE owner_id = ? AND posted_on BETWEEN ? AND ? ORDER BY posted_on, id LIMIT ?",
        (owner_id, start.isoformat(), end.isoformat(), min(max(0, limit), 5001)),
    )]


def _select_organizer_state(db: sqlite3.Connection, owner_id: int) -> dict:
    row = db.execute("SELECT organizer_state FROM dashboard WHERE owner_id = ?", (owner_id,)).fetchone()
    return json.loads(row["organizer_state"])


def _select_preferences(db: sqlite3.Connection, owner_id: int, month: str) -> dict:
    row = db.execute(
        "SELECT preferences FROM monthly_preferences WHERE owner_id = ? AND month = ?", (owner_id, month),
    ).fetchone()
    return json.loads(row["preferences"]) if row else {
        "monthly_budget": None, "category_limits": {}, "savings_goal": None,
    }


class WebsiteStore:
    """Persist website accounts, ledgers and bounded conversational context.

    All ledger methods take the owner ID resolved by the caller from a session.
    The separate desktop JSON storage is never read or used by this store.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.touch(mode=0o600, exist_ok=False)
        except FileExistsError:
            pass
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _transaction(self, *, write: bool = False):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db

    def register(self, username: str, password: str) -> Account:
        username = username.strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError("Choose a username with 3–32 letters, numbers, dots, hyphens or underscores.")
        if not 8 <= len(password) <= 128:
            raise ValueError("Use a password with 8–128 characters.")
        salt = token_bytes(16)
        password_hash = _password_hash(password, salt)
        with self._transaction(write=True) as db:
            cursor = db.execute(
                "INSERT INTO accounts(username, username_key, salt, password_hash) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(username_key) DO NOTHING",
                (username, username.casefold(), salt, password_hash),
            )
            if cursor.rowcount == 0:
                raise UsernameUnavailable("That username is already in use.")
            owner_id = cursor.lastrowid
            db.execute("INSERT INTO dashboard(owner_id) VALUES (?)", (owner_id,))
            db.execute("INSERT INTO conversations(owner_id) VALUES (?)", (owner_id,))
        return Account(owner_id, username)

    def login(self, username: str, password: str) -> tuple[Account, str] | None:
        if len(password) > 128:
            return None
        with self._transaction() as db:
            row = db.execute(
                "SELECT id, username, salt, password_hash FROM accounts WHERE username_key = ?",
                (username.strip().casefold(),),
            ).fetchone()
        salt = row["salt"] if row else bytes(16)
        expected = row["password_hash"] if row else bytes(32)
        if not compare_digest(_password_hash(password, salt), expected) or row is None:
            return None
        account = Account(row["id"], row["username"])
        return account, self.create_session(account.id)

    def create_session(self, owner_id: int) -> str:
        token = token_urlsafe(32)
        with self._transaction(write=True) as db:
            db.execute(
                "INSERT INTO sessions(token_hash, owner_id, expires_at) VALUES (?, ?, ?)",
                (_token_hash(token), owner_id, int(time()) + _SESSION_SECONDS),
            )
        return token

    def resolve_session(self, token: str) -> Account | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT accounts.id, accounts.username FROM sessions JOIN accounts ON accounts.id = sessions.owner_id "
                "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (_token_hash(token), int(time())),
            ).fetchone()
        return Account(row["id"], row["username"]) if row else None

    def revoke_session(self, token: str) -> None:
        with self._transaction(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def load_dashboard(self, owner_id: int) -> tuple[int, dict]:
        with self._transaction() as db:
            row = db.execute(
                "SELECT revision, organizer_state FROM dashboard WHERE owner_id = ?", (owner_id,),
            ).fetchone()
            transactions = db.execute(
                "SELECT * FROM transactions WHERE owner_id = ? ORDER BY position", (owner_id,),
            )
            return row["revision"], {**json.loads(row["organizer_state"]),
                                     "transactions": [_transaction_row(tx) for tx in transactions]}

    def load_organizer_state(self, owner_id: int) -> dict:
        """Read organizer metadata without scanning posted transactions."""
        with self._transaction() as db:
            return _select_organizer_state(db, owner_id)

    def save_dashboard(self, owner_id: int, state: dict, expected_revision: int) -> int:
        metadata = {key: value for key, value in state.items() if key != "transactions"}
        with self._transaction(write=True) as db:
            update = db.execute(
                "UPDATE dashboard SET revision = revision + 1, organizer_state = ? "
                "WHERE owner_id = ? AND revision = ?",
                (json.dumps(metadata), owner_id, expected_revision),
            )
            if update.rowcount == 0:
                raise RevisionConflict("The dashboard changed. Refresh and try again.")
            db.execute("DELETE FROM transactions WHERE owner_id = ?", (owner_id,))
            db.executemany(
                "INSERT INTO transactions(owner_id, id, position, posted_on, category, amount_cents, kind, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_transaction_values(owner_id, position, tx) for position, tx in enumerate(state["transactions"])),
            )
        return expected_revision + 1

    def query_transactions(self, owner_id: int, start: date, end: date, limit: int = 5001) -> list[dict]:
        with self._transaction() as db:
            return _select_transactions(db, owner_id, start, end, limit)

    def load_chat_snapshot(
        self, owner_id: int, start: date, end: date, month: str, limit: int = 5001,
    ) -> tuple[list[dict], dict, dict, dict]:
        """Read the dated ledger, queued bills and monthly settings in one database snapshot."""
        month_start = date.fromisoformat(f"{month}-01")
        previous_month = (month_start - timedelta(days=1)).isoformat()[:7] if month_start > date.min else None
        with self._transaction() as db:
            rows = _select_transactions(db, owner_id, start, end, limit)
            organizer_state = _select_organizer_state(db, owner_id)
            preferences = _select_preferences(db, owner_id, month)
            previous_preferences = _select_preferences(db, owner_id, previous_month) if previous_month else {}
        return rows, organizer_state, preferences, previous_preferences

    def load_preferences(self, owner_id: int, month: str) -> dict:
        with self._transaction() as db:
            return _select_preferences(db, owner_id, month)

    def save_preferences(self, owner_id: int, month: str, preferences: dict) -> None:
        with self._transaction(write=True) as db:
            db.execute(
                "INSERT INTO monthly_preferences(owner_id, month, preferences) VALUES (?, ?, ?) "
                "ON CONFLICT(owner_id, month) DO UPDATE SET preferences = excluded.preferences",
                (owner_id, month, json.dumps(preferences)),
            )

    def load_conversation(self, owner_id: int) -> Conversation:
        with self._transaction() as db:
            row = db.execute(
                "SELECT revision, history, memory FROM conversations WHERE owner_id = ?", (owner_id,),
            ).fetchone()
        return Conversation(row["revision"], json.loads(row["history"]), json.loads(row["memory"] or "null"))

    def append_conversation(
        self, owner_id: int, expected_revision: int, user_message: str, assistant_reply: str, memory: dict | None,
    ) -> Conversation:
        with self._transaction(write=True) as db:
            row = db.execute("SELECT revision, history FROM conversations WHERE owner_id = ?", (owner_id,)).fetchone()
            if row["revision"] != expected_revision:
                raise RevisionConflict("The conversation changed. Refresh and try again.")
            history = json.loads(row["history"])
            history.extend([{"role": "user", "content": user_message[:1000]},
                            {"role": "assistant", "content": assistant_reply[:1000]}])
            history = history[-_HISTORY_LIMIT:]
            revision = expected_revision + 1
            db.execute(
                "UPDATE conversations SET revision = ?, history = ?, memory = ? WHERE owner_id = ?",
                (revision, json.dumps(history), json.dumps(memory), owner_id),
            )
        return Conversation(revision, history, memory)

    def reset_conversation(self, owner_id: int) -> Conversation:
        with self._transaction(write=True) as db:
            row = db.execute(
                "UPDATE conversations SET revision = revision + 1, history = '[]', memory = NULL "
                "WHERE owner_id = ? RETURNING revision",
                (owner_id,),
            ).fetchone()
        return Conversation(row["revision"], [], None)
