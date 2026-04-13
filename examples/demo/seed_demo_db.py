"""
Seed a realistic SaaS demo SQLite database for L3 Agent.

Tables:
  - users (10,000 rows)
  - events (200,000 rows)
  - daily_metrics (90 rows)
  - experiments (2,000 rows)
  - user_daily_activity (300,000 rows)

Includes a deliberate DAU anomaly: ALL users in JP and IN experience
a 3-day outage ~30 days ago (both platforms), causing a visible DAU drop
that then recovers.  The anomaly is country-driven, not platform-driven.

Usage:
    python -m examples.demo.seed_demo_db          # from project root
    python examples/demo/seed_demo_db.py           # direct execution
"""
from __future__ import annotations

import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEED = 42
DB_PATH = Path(__file__).parent / "demo.sqlite"

NUM_USERS = 10_000
NUM_EVENTS = 200_000
NUM_EXPERIMENT_USERS = 2_000

SIGNUP_WINDOW_DAYS = 180
EVENT_WINDOW_DAYS = 90
METRIC_WINDOW_DAYS = 90

# Country distribution
COUNTRY_WEIGHTS = {
    "US": 0.40,
    "GB": 0.15,
    "DE": 0.10,
    "JP": 0.10,
    "BR": 0.10,
    "IN": 0.15,
}

PLATFORM_WEIGHTS = {"ios": 0.55, "android": 0.40, "web": 0.05}
TIER_WEIGHTS = {"free": 0.80, "basic": 0.15, "premium": 0.05}
AGE_GROUPS = ["18-24", "25-34", "35-44", "45-54", "55+"]

EVENT_TYPES = [
    "session_start",
    "page_view",
    "feature_use",
    "chat_start",
    "purchase",
    "subscription_change",
]
EVENT_TYPE_WEIGHTS = [0.30, 0.30, 0.15, 0.12, 0.05, 0.08]

# Anomaly configuration
ANOMALY_START_OFFSET = 30  # days ago
ANOMALY_DURATION = 3       # days
ANOMALY_COUNTRIES = {"JP", "IN"}
# Drop affects ALL platforms in these countries (country-driven, not platform)
ANOMALY_DROP_RATE = 0.92  # 92% of JP/IN users go inactive -> near-zero DAU


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weighted_choice(options: dict[str, float], rng: random.Random) -> str:
    keys = list(options.keys())
    weights = list(options.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def _date_range(start: date, end: date):
    """Yield dates from start up to and including end."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# Table generators
# ---------------------------------------------------------------------------

def generate_users(rng: random.Random, today: date) -> list[tuple]:
    """Generate users table rows."""
    rows = []
    signup_start = today - timedelta(days=SIGNUP_WINDOW_DAYS)
    for uid in range(1, NUM_USERS + 1):
        # Bias toward more recent signups (exponential-ish distribution)
        day_offset = int(rng.expovariate(3.0 / SIGNUP_WINDOW_DAYS))
        day_offset = min(day_offset, SIGNUP_WINDOW_DAYS)
        signup_date = today - timedelta(days=day_offset)
        if signup_date < signup_start:
            signup_date = signup_start

        country = _weighted_choice(COUNTRY_WEIGHTS, rng)
        platform = _weighted_choice(PLATFORM_WEIGHTS, rng)
        tier = _weighted_choice(TIER_WEIGHTS, rng)
        age_group = rng.choice(AGE_GROUPS)

        rows.append((uid, str(signup_date), country, platform, tier, age_group))
    return rows


def generate_events(
    rng: random.Random, users: list[tuple], today: date
) -> list[tuple]:
    """Generate events table rows (~200k)."""
    events = []
    event_start = today - timedelta(days=EVENT_WINDOW_DAYS)
    anomaly_start = today - timedelta(days=ANOMALY_START_OFFSET)
    anomaly_end = anomaly_start + timedelta(days=ANOMALY_DURATION - 1)

    user_lookup = {u[0]: u for u in users}  # uid -> row

    eid = 0
    target_per_user = NUM_EVENTS / NUM_USERS  # ~20 events per user

    for user_row in users:
        uid = user_row[0]
        signup_date = date.fromisoformat(user_row[1])
        country = user_row[2]
        platform = user_row[3]

        # Users start generating events from signup or event_start, whichever is later
        user_start = max(signup_date, event_start)
        if user_start > today:
            continue

        active_days = (today - user_start).days + 1
        # ~60% of days active
        num_active_days = max(1, int(active_days * 0.6 * rng.uniform(0.5, 1.2)))
        active_dates = sorted(rng.sample(
            [user_start + timedelta(days=d) for d in range(active_days)],
            k=min(num_active_days, active_days),
        ))

        events_per_active_day = max(1, int(target_per_user / max(num_active_days, 1)))

        for edate in active_dates:
            # Apply anomaly: JP/IN users (ALL platforms) drop to near zero
            if (
                anomaly_start <= edate <= anomaly_end
                and country in ANOMALY_COUNTRIES
                and rng.random() < ANOMALY_DROP_RATE
            ):
                continue

            n_events = rng.randint(1, events_per_active_day * 2)
            for _ in range(n_events):
                eid += 1
                event_type = rng.choices(EVENT_TYPES, weights=EVENT_TYPE_WEIGHTS, k=1)[0]
                session_dur = 0
                if event_type == "session_start":
                    session_dur = int(rng.expovariate(1 / 300))  # ~300s avg
                    session_dur = min(session_dur, 3600)

                events.append((eid, uid, str(edate), event_type, session_dur))

                if len(events) >= NUM_EVENTS:
                    return events

    return events


def generate_daily_metrics(
    rng: random.Random,
    events: list[tuple],
    users: list[tuple],
    today: date,
) -> list[tuple]:
    """Aggregate events into daily_metrics rows."""
    event_start = today - timedelta(days=METRIC_WINDOW_DAYS)
    user_lookup = {u[0]: u for u in users}

    # Aggregate from events
    day_data: dict[str, dict] = {}
    for ev in events:
        _, uid, edate_str, etype, session_dur = ev
        if edate_str not in day_data:
            day_data[edate_str] = {
                "active_users": set(),
                "new_users": set(),
                "revenue": 0.0,
                "chat_sessions": 0,
                "session_durations": [],
            }
        d = day_data[edate_str]
        d["active_users"].add(uid)
        if etype == "session_start" and session_dur > 0:
            d["session_durations"].append(session_dur)
        if etype == "chat_start":
            d["chat_sessions"] += 1
        if etype == "purchase":
            d["revenue"] += round(rng.uniform(0.99, 49.99), 2)

    # Check new users from signup dates
    for u in users:
        signup_str = u[1]
        if signup_str in day_data:
            day_data[signup_str]["new_users"].add(u[0])

    rows = []
    for d in _date_range(event_start, today):
        ds = str(d)
        if ds not in day_data:
            continue
        dd = day_data[ds]
        dau = len(dd["active_users"])
        new_users = len(dd["new_users"])
        revenue = round(dd["revenue"], 2)
        chat_sessions = dd["chat_sessions"]
        durations = dd["session_durations"]
        avg_session = round(sum(durations) / len(durations), 1) if durations else 0

        # Add slight uptrend to revenue
        days_from_start = (d - event_start).days
        revenue_multiplier = 1.0 + (days_from_start / METRIC_WINDOW_DAYS) * 0.15
        revenue = round(revenue * revenue_multiplier, 2)

        rows.append((ds, dau, new_users, revenue, chat_sessions, avg_session))

    return rows


def generate_experiments(
    rng: random.Random, users: list[tuple], today: date
) -> list[tuple]:
    """Generate experiment assignments for 'new_onboarding_flow'."""
    rows = []
    # Select users who signed up in the last 60 days for the experiment
    experiment_start = today - timedelta(days=45)
    experiment_end = today - timedelta(days=31)

    eligible = [
        u for u in users
        if experiment_start <= date.fromisoformat(u[1]) <= experiment_end
    ]

    # Take up to NUM_EXPERIMENT_USERS
    selected = rng.sample(eligible, k=min(NUM_EXPERIMENT_USERS, len(eligible)))
    variants = ["control", "test_a", "test_b"]
    variant_weights = [0.34, 0.33, 0.33]

    for u in selected:
        uid = u[0]
        variant = rng.choices(variants, weights=variant_weights, k=1)[0]
        signup = date.fromisoformat(u[1])
        # First exposure is signup date or experiment start, whichever is later
        exposure = max(signup, experiment_start)
        rows.append((uid, "new_onboarding_flow", variant, str(exposure)))

    return rows


def generate_user_daily_activity(
    rng: random.Random,
    events: list[tuple],
    users: list[tuple],
    experiment_rows: list[tuple],
    today: date,
) -> list[tuple]:
    """Pre-aggregate per-user per-day activity from events."""
    # Build lookup: (uid, date) -> aggregates
    agg: dict[tuple[int, str], dict] = {}
    for ev in events:
        _, uid, edate_str, etype, session_dur = ev
        key = (uid, edate_str)
        if key not in agg:
            agg[key] = {
                "sessions": 0,
                "has_chat": False,
                "revenue": 0.0,
            }
        a = agg[key]
        if etype == "session_start":
            a["sessions"] += 1
        if etype == "chat_start":
            a["has_chat"] = True
        if etype == "purchase":
            a["revenue"] += round(rng.uniform(0.99, 49.99), 2)

    # Experiment variant lookup for retention simulation
    exp_variants = {row[0]: row[2] for row in experiment_rows}

    rows = []
    for (uid, edate_str), a in agg.items():
        is_active = True
        has_chat = a["has_chat"]
        session_count = a["sessions"]
        revenue = round(a["revenue"], 2)

        # Simulate variant retention effects for experiment users
        variant = exp_variants.get(uid)
        if variant == "test_a" and rng.random() < 0.03:
            # test_a slightly boosts activity (3% bonus sessions)
            session_count += 1
        elif variant == "test_b" and rng.random() < 0.05:
            # test_b slightly hurts retention (5% chance of losing a session)
            session_count = max(0, session_count - 1)
            if session_count == 0:
                is_active = False

        rows.append((uid, edate_str, is_active, has_chat, session_count, revenue))

    return rows


# ---------------------------------------------------------------------------
# Database creation
# ---------------------------------------------------------------------------

def create_database(db_path: Path, today: date | None = None):
    """Create and populate the demo SQLite database."""
    if today is None:
        today = date.today()

    rng = random.Random(SEED)

    # Remove existing DB
    if db_path.exists():
        db_path.unlink()
        print(f"  Removed existing {db_path}")

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create tables
    cursor.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            signup_date TEXT NOT NULL,
            country TEXT NOT NULL,
            platform TEXT NOT NULL,
            subscription_tier TEXT NOT NULL,
            age_group TEXT NOT NULL
        );

        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            session_duration_sec INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE daily_metrics (
            date TEXT PRIMARY KEY,
            dau INTEGER NOT NULL,
            new_users INTEGER NOT NULL,
            revenue_usd REAL NOT NULL,
            chat_sessions INTEGER NOT NULL,
            avg_session_sec REAL NOT NULL
        );

        CREATE TABLE experiments (
            user_id INTEGER NOT NULL,
            experiment_name TEXT NOT NULL,
            variant TEXT NOT NULL,
            first_exposure_date TEXT NOT NULL,
            PRIMARY KEY (user_id, experiment_name),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE user_daily_activity (
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            is_active BOOLEAN NOT NULL,
            has_chat BOOLEAN NOT NULL,
            session_count INTEGER NOT NULL,
            revenue_usd REAL DEFAULT 0,
            PRIMARY KEY (user_id, date),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE INDEX idx_events_date ON events(event_date);
        CREATE INDEX idx_events_user ON events(user_id);
        CREATE INDEX idx_events_type ON events(event_type);
        CREATE INDEX idx_uda_date ON user_daily_activity(date);
        CREATE INDEX idx_uda_user ON user_daily_activity(user_id);
        CREATE INDEX idx_experiments_name ON experiments(experiment_name);
    """)

    # Generate and insert data
    print("Generating users...")
    users = generate_users(rng, today)
    cursor.executemany(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", users
    )
    print(f"  {len(users):,} users inserted")

    print("Generating events...")
    events = generate_events(rng, users, today)
    cursor.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)", events
    )
    print(f"  {len(events):,} events inserted")

    print("Generating daily_metrics...")
    metrics = generate_daily_metrics(rng, events, users, today)
    cursor.executemany(
        "INSERT INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?)", metrics
    )
    print(f"  {len(metrics):,} daily_metrics rows inserted")

    print("Generating experiments...")
    experiments = generate_experiments(rng, users, today)
    cursor.executemany(
        "INSERT INTO experiments VALUES (?, ?, ?, ?)", experiments
    )
    print(f"  {len(experiments):,} experiment assignments inserted")

    print("Generating user_daily_activity...")
    uda = generate_user_daily_activity(rng, events, users, experiments, today)
    cursor.executemany(
        "INSERT INTO user_daily_activity VALUES (?, ?, ?, ?, ?, ?)", uda
    )
    print(f"  {len(uda):,} user_daily_activity rows inserted")

    conn.commit()
    conn.close()

    db_size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"\nDatabase created: {db_path} ({db_size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def seed_demo_db():
    """Public entry point for seeding the demo database."""
    print("Seeding L3 Agent demo database...")
    print(f"  Target: {DB_PATH}")
    create_database(DB_PATH)
    print("Done.")


if __name__ == "__main__":
    seed_demo_db()
