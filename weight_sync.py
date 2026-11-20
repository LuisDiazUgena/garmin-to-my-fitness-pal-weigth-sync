#!/usr/bin/env python3
"""Copies your weight from Garmin Connect to MyFitnessPal.

    ./weight_sync.py              # sync the last 7 days
    ./weight_sync.py --days 30    # a longer window
    ./weight_sync.py --dry-run    # show what would change, write nothing

For each day of the window with a weigh-in in Garmin Connect (the latest one of the day), the
weight is written to MyFitnessPal when MyFitnessPal has none for that day or a different one.
Weights that MyFitnessPal itself sent to Garmin Connect are ignored, so the two never loop.

Garmin Connect is read with the official Personal API (an API key). MyFitnessPal has no public
API: its own web app's endpoints are used with the session cookie of a logged-in browser, which
MyFitnessPal renews on every visit (the renewed one is saved, see MFP_SESSION_FILE).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent

GARMIN_API = "https://personal.connectapi.garmin.com"
GARMIN_TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
GARMIN_TOKEN_LIFETIME = timedelta(hours=23)  # Garmin's bearer tokens last 24 h

MFP = "https://www.myfitnesspal.com"
MFP_SESSION_COOKIE = "__Secure-next-auth.session-token"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

TOLERANCE_KG = 0.05  # MyFitnessPal keeps one decimal


class SessionExpired(Exception):
    """The MyFitnessPal browser session is no longer valid: a new cookie is needed."""


# ─── Configuration ──────────────────────────────────────────────────────────

def load_env(path: Path) -> None:
    """KEY=value lines into the environment, without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def setting_path(name: str, default: Path) -> Path:
    return Path(os.path.expanduser(os.environ.get(name) or str(default)))


# ─── Garmin Connect ─────────────────────────────────────────────────────────

class Garmin:
    """Read-only client of the Garmin Connect Personal API. The bearer token is saved and reused
    for 23 hours (the file can be shared with another tool using the same API key)."""

    def __init__(self, api_key: str, token_file: Path):
        self.api_key = api_key
        self.token_file = token_file
        self.session = requests.Session()
        token = self._saved_token()
        self.cached = token is not None
        self.session.headers["Authorization"] = f"Bearer {token or self._new_token()}"

    def _fingerprint(self) -> str:
        return hashlib.sha256(self.api_key.encode()).hexdigest()

    def _saved_token(self) -> str | None:
        try:
            saved = json.loads(self.token_file.read_text(encoding="utf-8"))
            fresh = datetime.now() - datetime.fromisoformat(saved["obtained"]) < GARMIN_TOKEN_LIFETIME
        except (OSError, ValueError, KeyError):
            return None
        return saved.get("token") if fresh and saved.get("source") == self._fingerprint() else None

    def _new_token(self) -> str:
        response = self.session.post(GARMIN_API + GARMIN_TOKEN_PATH, headers={"Authorization": f"ApiKey {self.api_key}"}, timeout=30)
        response.raise_for_status()
        token = response.text.strip()
        self.token_file.write_text(json.dumps({"source": self._fingerprint(), "token": token,
                                               "obtained": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        os.chmod(self.token_file, 0o600)
        return token

    def weights(self, start: date, end: date) -> dict[str, float]:
        """Day → kg: the latest weigh-in of each day, leaving out the ones MyFitnessPal sent."""
        url = f"{GARMIN_API}/weight-service/weight/range/{start}/{end}"
        response = self.session.get(url, params={"includeAll": "true"}, timeout=30)
        if response.status_code == 401 and self.cached:  # token revoked before its time
            self.cached = False
            self.session.headers["Authorization"] = f"Bearer {self._new_token()}"
            response = self.session.get(url, params={"includeAll": "true"}, timeout=30)
        response.raise_for_status()
        return daily_weights(response.json())


def daily_weights(payload: dict) -> dict[str, float]:
    found = {}
    for day in payload.get("dailyWeightSummaries") or []:
        entries = [w for w in day.get("allWeightMetrics") or [] if w.get("weight") and w.get("sourceType") != "MFP"]
        if entries:
            latest = max(entries, key=lambda w: w.get("timestampGMT") or 0)
            found[day["summaryDate"]] = round(latest["weight"] / 1000, 1)  # grams → kg
    return found


# ─── MyFitnessPal ───────────────────────────────────────────────────────────

def fingerprint(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


def current_cookie(configured: str, session_file: Path) -> str:
    """The renewed cookie saved after the last visit, unless a different one was configured since.
    The file's format is shared with other tools that renew the same session."""
    try:
        saved = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return configured
    return saved["cookie"] if saved.get("source") == fingerprint(configured) and saved.get("cookie") else configured


def save_cookie(configured: str, cookie: str, session_file: Path) -> None:
    session_file.write_text(json.dumps({"source": fingerprint(configured), "cookie": cookie}), encoding="utf-8")
    os.chmod(session_file, 0o600)


def parse_cookie(value: str) -> dict[str, str]:
    """The whole "Cookie:" header copied from the browser, or just the session token."""
    value = value.strip().removeprefix("Cookie:").strip()
    if "=" not in value:
        return {MFP_SESSION_COOKIE: value}
    return {n: v for n, _, v in (p.strip().partition("=") for p in value.split(";")) if n}


class MyFitnessPal:
    def __init__(self, cookie: str):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        for name, value in parse_cookie(cookie).items():
            self.session.cookies.set(name, value, domain=".myfitnesspal.com", path="/")
        response = self.session.get(f"{MFP}/api/auth/session", allow_redirects=False, timeout=30)
        response.raise_for_status()
        try:
            logged_in = bool(response.json().get("userId"))
        except ValueError:
            logged_in = False
        if not logged_in:
            raise SessionExpired("the MyFitnessPal session is not logged in: copy a new cookie from the browser")

    def cookie(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.session.cookies if c.name.startswith(MFP_SESSION_COOKIE))

    def _check(self, response: requests.Response) -> requests.Response:
        if response.is_redirect or response.status_code in (401, 403):
            raise SessionExpired(f"HTTP {response.status_code} from MyFitnessPal: copy a new cookie from the browser")
        response.raise_for_status()
        return response

    def weights(self, start: date, end: date) -> dict[str, list[dict]]:
        """Day → the weight entries MyFitnessPal has (normally one)."""
        found: dict[str, list[dict]] = {}
        offset = 0
        while True:
            response = self._check(self.session.get(
                f"{MFP}/api/user-measurements/measurements",
                params={"types": "Weight", "from": str(start), "to": str(end), "asc": "true", "limit": 100, "offset": offset},
                allow_redirects=False, timeout=30))
            page = response.json()
            for item in page.get("items") or []:
                found.setdefault(item["date"], []).append(item)
            if not page.get("has_more"):
                return found
            offset += len(page.get("items") or [])

    def delete(self, entry_id: str) -> None:
        self._check(self.session.delete(f"{MFP}/api/user-measurements/measurements/{entry_id}", allow_redirects=False, timeout=30))

    def add_weight(self, day: str, kg: float) -> None:
        self._check(self.session.put(
            f"{MFP}/api/user-measurements/measurements",
            json={"items": [{"type": "Weight", "date": day, "value": kg, "unit": "kilograms"}]},
            allow_redirects=False, timeout=30))
        time.sleep(0.3)  # gentle: these are the endpoints of MyFitnessPal's own web app


def to_kg(entry: dict) -> float:
    value = float(entry["value"])
    return round(value * 0.45359237, 1) if entry.get("unit") in ("pounds", "lbs", "lb") else value


def plan(garmin: dict[str, float], mfp: dict[str, list[dict]]) -> list[tuple[str, float, list[dict]]]:
    """(day, kg, MyFitnessPal entries to replace) for every day where MyFitnessPal must change."""
    changes = []
    for day, kg in sorted(garmin.items()):
        entries = mfp.get(day, [])
        if len(entries) == 1 and abs(to_kg(entries[0]) - kg) < TOLERANCE_KG:
            continue
        changes.append((day, kg, entries))
    return changes


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Copy your weight from Garmin Connect to MyFitnessPal")
    parser.add_argument("--days", type=int, default=7, help="days back to compare (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    if os.environ.get("SHARED_ENV_FILE"):  # credentials kept in another tool's .env
        load_env(setting_path("SHARED_ENV_FILE", ROOT / ".env"))
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY")
    configured_cookie = os.environ.get("MFP_SESSION_COOKIE")
    if not api_key or not configured_cookie:
        print("Missing GARMIN_PERSONAL_API_KEY or MFP_SESSION_COOKIE (see README)", file=sys.stderr)
        return 2
    session_file = setting_path("MFP_SESSION_FILE", ROOT / ".mfp-session.json")
    token_file = setting_path("GARMIN_TOKEN_FILE", ROOT / ".garmin-token.json")

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    garmin = Garmin(api_key, token_file).weights(start, end)

    try:
        mfp = MyFitnessPal(current_cookie(configured_cookie, session_file))
        try:
            changes = plan(garmin, mfp.weights(start, end))
            for day, kg, old in changes:
                before = ", ".join(f"{to_kg(e)} kg" for e in old) or "nothing"
                print(f"{day}: {kg} kg (MyFitnessPal had {before})")
                if not args.dry_run:
                    for entry in old:
                        mfp.delete(entry["id"])
                    mfp.add_weight(day, kg)
            if not changes:
                print(f"Up to date: {len(garmin)} weigh-ins in Garmin Connect in the last {args.days} days")
            elif args.dry_run:
                print("Dry run: nothing was written")
        finally:
            save_cookie(configured_cookie, mfp.cookie(), session_file)  # keep the renewed session
    except SessionExpired as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
