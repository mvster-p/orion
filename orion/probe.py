"""Live checks against public profile APIs. No people-search pages are fetched."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .models import Query, Row, clean_handle, email_parts, handle_ok, valid_email

UA = "ORION-PublicSearch/1.0 (personal terminal; public profile check)"
TIMEOUT = 6.0


@dataclass
class Job:
    source: str
    url: str
    fn: Callable[[], Row]


def _row(source: str, url: str, status: str, detail: str, elapsed_ms: int | None = None) -> Row:
    return Row(
        source=source,
        category="live",
        tier="live",
        status=status,
        detail=detail[:88],
        url=url,
        mode="probe",
        elapsed_ms=elapsed_ms,
    )


def fetch(url: str, accept: str = "application/json, text/html;q=0.8") -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": accept},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(180_000), ""
    except urllib.error.HTTPError as exc:
        body = exc.read(80_000) if exc.fp else b""
        return exc.code, body, ""
    except Exception as exc:
        return 0, b"", str(exc)


def _json(body: bytes) -> dict | list | None:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _classify(status: int, error: str) -> str | None:
    if status == 0:
        return "error"
    if status in {401, 403, 429, 503}:
        return "blocked"
    if status == 404:
        return "miss"
    if status != 200:
        return "error"
    return None


def _detail_for(status: str, error: str, status_code: int) -> str:
    if status == "error" and error:
        return error.splitlines()[0][:70]
    if status == "blocked" and status_code == 429:
        return "rate limited"
    if status == "blocked":
        return f"http {status_code}"
    if status == "error":
        return f"http {status_code}" if status_code else "request failed"
    if status == "miss":
        return "no public profile"
    return ""


def _skip(source: str, url: str, detail: str) -> Row:
    return _row(source, url, "skip", detail)


def _github(handle: str) -> Row:
    page = f"https://github.com/{handle}"
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", handle):
        return _skip("GitHub API", page, "handle shape not accepted")
    status, body, error = fetch(f"https://api.github.com/users/{handle}")
    gate = _classify(status, error)
    if gate:
        return _row("GitHub API", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("login"):
        return _row("GitHub API", page, "miss", "no public profile")
    bits = [str(data.get("name") or data["login"])]
    if data.get("company"):
        bits.append(str(data["company"]).strip())
    if data.get("location"):
        bits.append(str(data["location"]))
    repos = data.get("public_repos")
    if isinstance(repos, int):
        bits.append(f"{repos} public repos")
    return _row("GitHub API", page, "hit", " · ".join(bits))


def _gitlab(handle: str) -> Row:
    page = f"https://gitlab.com/{handle}"
    status, body, error = fetch(
        "https://gitlab.com/api/v4/users?username=" + urllib.parse.quote(handle)
    )
    gate = _classify(status, error)
    if gate:
        return _row("GitLab API", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, list) or not data:
        return _row("GitLab API", page, "miss", "no public profile")
    user = data[0]
    name = str(user.get("name") or user.get("username") or handle)
    return _row("GitLab API", page, "hit", name)


def _hn(handle: str) -> Row:
    page = f"https://news.ycombinator.com/user?id={urllib.parse.quote(handle)}"
    status, body, error = fetch(f"https://hacker-news.firebaseio.com/v0/user/{urllib.parse.quote(handle)}.json")
    gate = _classify(status, error)
    if gate:
        return _row("Hacker News", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if data is None:
        return _row("Hacker News", page, "miss", "no public profile")
    if not isinstance(data, dict):
        return _row("Hacker News", page, "miss", "no public profile")
    karma = data.get("karma")
    detail = f"karma {karma}" if karma is not None else "account exists"
    return _row("Hacker News", page, "hit", detail)


def _chess(handle: str) -> Row:
    page = f"https://www.chess.com/member/{handle}"
    status, body, error = fetch(f"https://api.chess.com/pub/player/{urllib.parse.quote(handle.lower())}")
    gate = _classify(status, error)
    if gate:
        return _row("Chess.com", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("username"):
        return _row("Chess.com", page, "miss", "no public profile")
    bits = [str(data.get("name") or data["username"])]
    if data.get("country"):
        bits.append(str(data["country"]).rstrip("/").split("/")[-1])
    return _row("Chess.com", page, "hit", " · ".join(bits))


def _docker(handle: str) -> Row:
    page = f"https://hub.docker.com/u/{handle}"
    status, body, error = fetch(f"https://hub.docker.com/v2/users/{urllib.parse.quote(handle)}/")
    gate = _classify(status, error)
    if gate:
        return _row("Docker Hub", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("username"):
        return _row("Docker Hub", page, "miss", "no public profile")
    bits = [str(data.get("full_name") or data["username"])]
    if data.get("location"):
        bits.append(str(data["location"]))
    return _row("Docker Hub", page, "hit", " · ".join(bits))


def _codeberg(handle: str) -> Row:
    page = f"https://codeberg.org/{handle}"
    status, body, error = fetch(f"https://codeberg.org/api/v1/users/{urllib.parse.quote(handle)}")
    gate = _classify(status, error)
    if gate:
        return _row("Codeberg", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("login"):
        return _row("Codeberg", page, "miss", "no public profile")
    return _row("Codeberg", page, "hit", str(data.get("full_name") or data["login"]))


def _devto(handle: str) -> Row:
    page = f"https://dev.to/{handle}"
    status, body, error = fetch(
        "https://dev.to/api/users/by_username?url=" + urllib.parse.quote(handle)
    )
    gate = _classify(status, error)
    if gate:
        return _row("Dev.to", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("username"):
        return _row("Dev.to", page, "miss", "no public profile")
    bits = [str(data.get("name") or data["username"])]
    if data.get("location"):
        bits.append(str(data["location"]))
    return _row("Dev.to", page, "hit", " · ".join(bits))


def _keybase(handle: str) -> Row:
    page = f"https://keybase.io/{handle}"
    status, body, error = fetch(
        "https://keybase.io/_/api/1.0/user/lookup.json?usernames=" + urllib.parse.quote(handle)
    )
    gate = _classify(status, error)
    if gate:
        return _row("Keybase", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    them = data.get("them") if isinstance(data, dict) else None
    if not isinstance(them, list) or not them or not isinstance(them[0], dict):
        return _row("Keybase", page, "miss", "no public profile")
    basics = them[0].get("basics") or {}
    profile = them[0].get("profile") or {}
    name = profile.get("full_name") or basics.get("username") or handle
    return _row("Keybase", page, "hit", str(name))


def _wikipedia(handle: str) -> Row:
    page = f"https://en.wikipedia.org/wiki/User:{urllib.parse.quote(handle)}"
    api = (
        "https://en.wikipedia.org/w/api.php?action=query&list=users&format=json&ususers="
        + urllib.parse.quote(handle)
    )
    status, body, error = fetch(api)
    gate = _classify(status, error)
    if gate:
        return _row("Wikipedia", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    users = (((data or {}) if isinstance(data, dict) else {}).get("query") or {}).get("users") or []
    if not users or "missing" in users[0] or "userid" not in users[0]:
        return _row("Wikipedia", page, "miss", "no account")
    return _row("Wikipedia", page, "hit", f"account {users[0].get('userid')}")


def _mastodon(handle: str) -> Row:
    page = f"https://mastodon.social/@{handle}"
    status, body, error = fetch(
        "https://mastodon.social/api/v1/accounts/lookup?acct=" + urllib.parse.quote(handle)
    )
    gate = _classify(status, error)
    if gate:
        return _row("Mastodon", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("username"):
        return _row("Mastodon", page, "miss", "no public profile")
    bits = [str(data.get("display_name") or data["username"])]
    followers = data.get("followers_count")
    if isinstance(followers, int) and followers:
        bits.append(f"{followers} followers")
    return _row("Mastodon", page, "hit", " · ".join(bits))


def _bluesky(handle: str) -> Row:
    actor = handle if "." in handle else f"{handle.lower()}.bsky.social"
    page = f"https://bsky.app/profile/{actor}"
    status, body, error = fetch(
        "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=" + urllib.parse.quote(actor)
    )
    if status == 400:
        return _row("Bluesky", page, "miss", "no public profile")
    gate = _classify(status, error)
    if gate:
        return _row("Bluesky", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict) or not data.get("handle"):
        return _row("Bluesky", page, "miss", "no public profile")
    return _row("Bluesky", page, "hit", str(data.get("displayName") or data["handle"]))


def _pypi(handle: str) -> Row:
    page = f"https://pypi.org/user/{handle}/"
    status, body, error = fetch(page, accept="text/html")
    gate = _classify(status, error)
    if gate == "miss" or (status == 200 and b"couldn" in body.lower() and b"find" in body.lower()):
        return _row("PyPI", page, "miss", "no public profile")
    if gate:
        return _row("PyPI", page, gate, _detail_for(gate, error, status))
    return _row("PyPI", page, "hit", "profile page")


def _youtube(handle: str) -> Row:
    page = f"https://www.youtube.com/@{handle}"
    api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(page, safe="")
    status, body, error = fetch(api)
    gate = _classify(status, error)
    if gate:
        return _row("YouTube", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    if not isinstance(data, dict):
        return _row("YouTube", page, "miss", "no public channel")
    return _row("YouTube", page, "hit", str(data.get("author_name") or data.get("title") or "channel"))


def _reddit(handle: str) -> Row:
    page = f"https://www.reddit.com/user/{handle}"
    status, body, error = fetch(f"https://www.reddit.com/user/{urllib.parse.quote(handle)}/about.json")
    gate = _classify(status, error)
    if gate:
        return _row("Reddit", page, gate, _detail_for(gate, error, status))
    data = _json(body)
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict) or not inner.get("name"):
        return _row("Reddit", page, "miss", "no public profile")
    karma = inner.get("total_karma")
    detail = f"karma {karma}" if isinstance(karma, int) else "account exists"
    return _row("Reddit", page, "hit", detail)


def _gravatar(email: str) -> Row:
    import hashlib

    digest = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    page = f"https://www.gravatar.com/avatar/{digest}?d=404&s=80"
    status, _body, error = fetch(page, accept="image/*")
    if status == 404:
        return _row("Gravatar", page, "miss", "no public avatar")
    gate = _classify(status, error)
    if gate:
        return _row("Gravatar", page, gate, _detail_for(gate, error, status))
    return _row("Gravatar", page, "hit", "public avatar")


def _dns(domain: str) -> Row:
    page = f"https://www.whois.com/whois/{domain}"
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return _row("Domain DNS", page, "miss", "name does not resolve")
    except Exception as exc:
        return _row("Domain DNS", page, "error", str(exc)[:70])
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        return _row("Domain DNS", page, "miss", "name does not resolve")
    return _row("Domain DNS", page, "hit", ", ".join(ips[:3]))


def _mx(domain: str) -> Row:
    page = f"https://www.whois.com/whois/{domain}"
    try:
        completed = subprocess.run(
            ["nslookup", "-type=mx", domain],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except Exception as exc:
        return _row("Mail MX", page, "error", str(exc)[:70])
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    hosts: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if "mail exchanger" in lower:
            host = line.split("=")[-1].strip().rstrip(".")
            if host and host not in hosts:
                hosts.append(host)
    if not hosts:
        return _row("Mail MX", page, "miss", "no MX records")
    return _row("Mail MX", page, "hit", ", ".join(hosts[:3]))


def jobs_for(query: Query) -> list[Job]:
    if query.kind == "username":
        handle = clean_handle(query.raw)
        if not handle_ok(handle):
            return []
        makers = (
            ("GitHub API", _github),
            ("GitLab API", _gitlab),
            ("Hacker News", _hn),
            ("Chess.com", _chess),
            ("Docker Hub", _docker),
            ("Codeberg", _codeberg),
            ("Dev.to", _devto),
            ("Keybase", _keybase),
            ("Wikipedia", _wikipedia),
            ("Mastodon", _mastodon),
            ("Bluesky", _bluesky),
            ("PyPI", _pypi),
            ("YouTube", _youtube),
            ("Reddit", _reddit),
        )
        return [Job(name, "", lambda maker=maker, handle=handle: maker(handle)) for name, maker in makers]
    if query.kind == "email" and valid_email(query.raw):
        _local, domain = email_parts(query.raw)
        email = query.raw.strip()
        return [
            Job("Gravatar", "", lambda email=email: _gravatar(email)),
            Job("Domain DNS", "", lambda domain=domain: _dns(domain)),
            Job("Mail MX", "", lambda domain=domain: _mx(domain)),
        ]
    return []


def run_job(job: Job) -> Row:
    started = time.perf_counter()
    try:
        row = job.fn()
    except Exception as exc:
        row = _row(job.source, job.url or "https://localhost/probe", "error", type(exc).__name__)
    row.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return row


