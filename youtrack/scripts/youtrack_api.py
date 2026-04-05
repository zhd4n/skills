#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request


DEFAULT_PAGE_SIZE = 100
DEFAULT_ISSUE_FIELDS = (
    "id,idReadable,summary,description,project(id,name,shortName),"
    "customFields(name,$type,value(id,name,login,presentation))"
)
DEFAULT_COMMENT_FIELDS = "id,text,author(id,login),created,updated"
DEFAULT_WORK_FIELDS = (
    "id,date,text,author(id,login),creator(id,login),"
    "duration(minutes,presentation),type(id,name)"
)
DEFAULT_GLOBAL_WORK_FIELDS = (
    "id,date,text,author(id,login),duration(minutes,presentation),"
    "type(id,name),issue(id,idReadable,summary,project(id,name,shortName))"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
CONFIG_DIR_NAME = "youtrack"
LEGACY_CONFIG_DIR_NAME = "youtrack-rest"
CONFIG_FILE_NAME = "config.json"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_GET_RETRY_ATTEMPTS = 3
RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_REPORT_WORKERS = 4
PROJECT_FIELDS_LIST_FIELDS = "id,$type,canBeEmpty,emptyFieldText,field(id,name,$type),bundle(id,name,$type)"
WORKFLOW_FIELDS = "id,name,title"
BUNDLE_VALUE_FIELDS = {
    "BuildBundle": "id,name,presentation",
    "EnumBundle": "id,name,presentation,isResolved",
    "OwnedBundle": "id,name,presentation",
    "StateBundle": "id,name,presentation,isResolved",
    "UserBundle": "id,login,name,fullName",
    "VersionBundle": "id,name,presentation",
}
BUNDLE_VALUE_PATHS = {
    "BuildBundle": ("build", "values"),
    "EnumBundle": ("enum", "values"),
    "OwnedBundle": ("ownedField", "values"),
    "StateBundle": ("state", "values"),
    "UserBundle": ("user", "aggregatedUsers"),
    "VersionBundle": ("version", "values"),
}
DURATION_TOKEN_RE = re.compile(r"(\d+)\s*([hm])", re.IGNORECASE)


class ApiError(RuntimeError):
    pass


class HttpApiError(ApiError):
    pass


class CommandApiError(ApiError):
    pass


class TransportApiError(ApiError):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str


def load_config(env: dict[str, str] | None = None) -> Config:
    source = env if env is not None else os.environ
    base_url = (source.get("YOUTRACK_BASE_URL") or "").strip()
    token = (source.get("YOUTRACK_TOKEN") or "").strip()
    if not base_url or not token:
        config_data = load_saved_config(source)
        if not base_url:
            base_url = config_data.get("base_url", "")
        if not token:
            token = config_data.get("token", "")
    missing = [
        name
        for name, value in (
            ("YOUTRACK_BASE_URL", base_url),
            ("YOUTRACK_TOKEN", token),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    return Config(base_url=base_url.rstrip("/"), token=token)


def build_config_path(config_dir_name: str, env: dict[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    config_home = (source.get("XDG_CONFIG_HOME") or "").strip()
    if config_home:
        return Path(config_home).expanduser() / config_dir_name / CONFIG_FILE_NAME
    home = (source.get("HOME") or "").strip()
    if home:
        return Path(home).expanduser() / ".config" / config_dir_name / CONFIG_FILE_NAME
    return Path.home() / ".config" / config_dir_name / CONFIG_FILE_NAME


def get_config_path(env: dict[str, str] | None = None) -> Path:
    return build_config_path(CONFIG_DIR_NAME, env)


def get_legacy_config_path(env: dict[str, str] | None = None) -> Path:
    return build_config_path(LEGACY_CONFIG_DIR_NAME, env)


def read_config_file(config_path: Path) -> dict[str, str]:
    try:
        raw = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid config file at {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config file at {config_path}: expected an object")
    return {
        "base_url": str(raw.get("base_url", "")).strip().rstrip("/"),
        "token": str(raw.get("token", "")).strip(),
    }


def load_saved_config(env: dict[str, str] | None = None) -> dict[str, str]:
    for config_path in (get_config_path(env), get_legacy_config_path(env)):
        if config_path.exists():
            return read_config_file(config_path)
    return {}


def save_config(*, base_url: str, token: str, env: dict[str, str] | None = None) -> Path:
    normalized_url = base_url.strip().rstrip("/")
    normalized_token = token.strip()
    if not normalized_url or not normalized_token:
        raise ValueError("setup requires non-empty --url and --token")
    config_path = get_config_path(env)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"base_url": normalized_url, "token": normalized_token}
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(config_path, 0o600)
    return config_path


def escape_command_token(token: str) -> str:
    token = token.strip()
    if not token:
        raise ValueError("Empty command token is not allowed")
    return token.replace("\\", "\\\\").replace('"', '\\"')


def quote_command_token(token: str) -> str:
    token = token.strip()
    escaped = escape_command_token(token)
    if escaped != token or any(ch.isspace() for ch in token):
        return f'"{escaped}"'
    return token


def split_field_assignment(assignment: str) -> tuple[str, str]:
    if "=" not in assignment:
        raise ValueError(f"Invalid field assignment: {assignment!r}. Use Name=Value.")
    name, value = assignment.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError(f"Invalid field assignment: {assignment!r}. Use Name=Value.")
    return name, value


def build_command_query(
    *,
    state: str | None = None,
    assignee: str | None = None,
    fields: list[str] | None = None,
    safe_delete_command: str | None = None,
) -> str:
    tokens: list[str] = []
    if safe_delete_command:
        tokens.append(safe_delete_command.strip())
    if state:
        tokens.extend(("State", escape_command_token(state)))
    if assignee:
        tokens.extend(("Assignee", escape_command_token(assignee)))
    for assignment in fields or []:
        name, value = split_field_assignment(assignment)
        tokens.extend((escape_command_token(name), escape_command_token(value)))
    return " ".join(token for token in tokens if token)


def parse_duration_input(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        raise ValueError("Duration value must not be empty")
    if text.isdigit():
        minutes = int(text)
        if minutes <= 0:
            raise ValueError("Duration minutes must be positive")
        return {"minutes": minutes}
    return {"presentation": text}


def parse_duration_minutes(value: str) -> int:
    text = value.strip()
    if not text:
        raise ValueError("Duration value must not be empty")
    if text.isdigit():
        minutes = int(text)
        if minutes <= 0:
            raise ValueError("Duration minutes must be positive")
        return minutes
    total = 0
    cursor = 0
    matched = False
    for match in DURATION_TOKEN_RE.finditer(text):
        if text[cursor:match.start()].strip():
            raise ValueError(f"Unsupported duration for exact minute conversion: {value!r}")
        amount = int(match.group(1))
        unit = match.group(2).lower()
        total += amount * 60 if unit == "h" else amount
        cursor = match.end()
        matched = True
    if not matched or text[cursor:].strip():
        raise ValueError(f"Unsupported duration for exact minute conversion: {value!r}")
    if total <= 0:
        raise ValueError("Duration minutes must be positive")
    return total


def format_minutes(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


def parse_iso_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def date_to_epoch_ms(date_text: str, *, end_of_day: bool = False) -> int:
    start = parse_iso_date(date_text)
    if end_of_day:
        start = start + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000)


def ms_to_iso_date(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()


def iter_period_dates(date_from: str, date_to: str, *, weekdays_only: bool = False) -> list[str]:
    start = parse_iso_date(date_from).date()
    end = parse_iso_date(date_to).date()
    if end < start:
        raise ValueError(f"Invalid date range: {date_from}..{date_to}")
    dates: list[str] = []
    current = start
    while current <= end:
        if not weekdays_only or current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def aggregate_work_items(work_items: list[dict[str, Any]]) -> dict[str, Any]:
    days: dict[str, dict[str, Any]] = {}
    total_minutes = 0

    for item in work_items:
        date = ms_to_iso_date(item["date"])
        issue = item.get("issue") or {}
        issue_id = issue.get("idReadable") or issue.get("id") or "UNKNOWN"
        issue_summary = issue.get("summary") or ""
        minutes = int(((item.get("duration") or {}).get("minutes")) or 0)
        total_minutes += minutes

        day_bucket = days.setdefault(date, {"date": date, "total_minutes": 0, "issues": {}})
        day_bucket["total_minutes"] += minutes

        issue_bucket = day_bucket["issues"].setdefault(
            issue_id,
            {
                "idReadable": issue_id,
                "summary": issue_summary,
                "total_minutes": 0,
            },
        )
        issue_bucket["total_minutes"] += minutes

    sorted_days = []
    for date in sorted(days):
        issue_rows = sorted(days[date]["issues"].values(), key=lambda item: item["idReadable"])
        for issue in issue_rows:
            issue["total_presentation"] = format_minutes(issue["total_minutes"])
        sorted_days.append(
            {
                "date": date,
                "total_minutes": days[date]["total_minutes"],
                "total_presentation": format_minutes(days[date]["total_minutes"]),
                "issues": issue_rows,
            }
        )

    return {
        "days": sorted_days,
        "total_minutes": total_minutes,
        "total_presentation": format_minutes(total_minutes),
    }


def render_report_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for day in report["days"]:
        lines.append(f"{day['date']}  {day['total_presentation']}")
        for issue in day["issues"]:
            summary = f" {issue['summary']}" if issue["summary"] else ""
            lines.append(f"  - {issue['idReadable']}{summary}: {issue['total_presentation']}")
    lines.append(f"TOTAL {report['total_presentation']}")
    return "\n".join(lines)


def render_bucket_report_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for bucket in report.get("buckets", []):
        label = bucket.get("label") or bucket.get("key") or "UNKNOWN"
        key = bucket.get("key") or label
        if label == key:
            lines.append(f"{key}: {bucket['total_presentation']}")
        else:
            lines.append(f"{key} {label}: {bucket['total_presentation']}")
    lines.append(f"TOTAL {report['total_presentation']}")
    return "\n".join(lines)


def scalar_to_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_generic_text(value: Any, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(render_generic_text(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {scalar_to_text(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(render_generic_text(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {scalar_to_text(item)}")
        return lines
    return [f"{prefix}{scalar_to_text(value)}"]


def render_text(result: Any) -> str:
    if isinstance(result, dict) and {"days", "total_presentation"}.issubset(result):
        return render_report_text(result)
    if isinstance(result, dict) and {"buckets", "total_presentation"}.issubset(result):
        return render_bucket_report_text(result)
    return "\n".join(render_generic_text(result))


def render_report_csv(report: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["row_type", "group_by", "date", "key", "label", "total_minutes", "total_presentation"])
    if "days" in report:
        for day in report["days"]:
            for issue in day["issues"]:
                writer.writerow(
                    [
                        "item",
                        "day",
                        day["date"],
                        issue["idReadable"],
                        issue.get("summary", ""),
                        issue["total_minutes"],
                        issue["total_presentation"],
                    ]
                )
            writer.writerow(["day_total", "day", day["date"], "", "", day["total_minutes"], day["total_presentation"]])
        writer.writerow(["grand_total", "day", "", "", "", report["total_minutes"], report["total_presentation"]])
        return output.getvalue()
    if "buckets" in report:
        for bucket in report["buckets"]:
            writer.writerow(
                [
                    "item",
                    report.get("group_by", ""),
                    "",
                    bucket.get("key", ""),
                    bucket.get("label", ""),
                    bucket["total_minutes"],
                    bucket["total_presentation"],
                ]
            )
        writer.writerow(["grand_total", report.get("group_by", ""), "", "", "", report["total_minutes"], report["total_presentation"]])
        return output.getvalue()
    raise ValueError("CSV output is only supported for report results")


def retry_delay_seconds(attempt_index: int) -> float:
    return min(2.0, 0.5 * (2**attempt_index))


def should_retry(method: str, *, status_code: int | None = None, url_error: bool = False) -> bool:
    if method.upper() != "GET":
        return False
    if url_error:
        return True
    return status_code in RETRYABLE_HTTP_STATUS_CODES


def paginate_items(items: list[dict[str, Any]], *, top: int, skip: int) -> list[dict[str, Any]]:
    skip = max(skip, 0)
    top = max(top, 0)
    return items[skip : skip + top]


def filter_users(users: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    return [
        user
        for user in users
        if needle in str(user.get("id", "")).casefold()
        or needle in str(user.get("login", "")).casefold()
        or needle in str(user.get("name", "")).casefold()
        or needle in str(user.get("fullName", "")).casefold()
    ]


def get_nested_value(item: dict[str, Any], path: str) -> Any:
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def find_exact_match(items: list[dict[str, Any]], ref: str, paths: Iterable[str]) -> dict[str, Any] | None:
    needle = ref.casefold()
    for item in items:
        for path in paths:
            value = get_nested_value(item, path)
            if value is not None and needle == str(value).casefold():
                return item
    return None


def find_exact_user_match(users: list[dict[str, Any]], user_ref: str) -> dict[str, Any] | None:
    return find_exact_match(users, user_ref, ("id", "login", "name", "fullName"))


def is_bad_request_error(exc: HttpApiError) -> bool:
    return str(exc).startswith("HTTP 400 ") or str(exc).startswith("Unexpected status 400 ")


def aggregate_work_items_by(work_items: list[dict[str, Any]], group_by: str) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    total_minutes = 0

    for item in work_items:
        minutes = int(((item.get("duration") or {}).get("minutes")) or 0)
        total_minutes += minutes

        if group_by == "issue":
            issue = item.get("issue") or {}
            key = issue.get("idReadable") or issue.get("id") or "UNKNOWN"
            label = issue.get("summary") or key
            extra = {"idReadable": key, "summary": issue.get("summary") or ""}
        elif group_by == "user":
            author = item.get("author") or {}
            key = author.get("login") or author.get("id") or "UNKNOWN"
            label = author.get("login") or author.get("name") or key
            extra = {"login": author.get("login"), "name": author.get("name")}
        elif group_by == "project":
            project = ((item.get("issue") or {}).get("project")) or {}
            key = project.get("shortName") or project.get("id") or "UNKNOWN"
            label = project.get("name") or key
            extra = {"shortName": project.get("shortName"), "name": project.get("name")}
        elif group_by == "type":
            work_type = item.get("type") or {}
            key = work_type.get("name") or work_type.get("id") or "UNKNOWN"
            label = work_type.get("name") or key
            extra = {"name": work_type.get("name")}
        else:
            raise ValueError(f"Unsupported group_by: {group_by}")

        bucket = buckets.setdefault(
            str(key),
            {"key": str(key), "label": str(label), "total_minutes": 0, "items": 0, **extra},
        )
        bucket["total_minutes"] += minutes
        bucket["items"] += 1

    ordered = [buckets[key] for key in sorted(buckets)]
    for bucket in ordered:
        bucket["total_presentation"] = format_minutes(bucket["total_minutes"])

    return {
        "group_by": group_by,
        "buckets": ordered,
        "total_minutes": total_minutes,
        "total_presentation": format_minutes(total_minutes),
    }


def encode_path_value(value: str) -> str:
    return parse.quote(value, safe="")


def build_request_headers(*, token: str, has_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {token}",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def maybe_json(response_body: bytes) -> Any:
    if not response_body:
        return None
    text = response_body.decode("utf-8")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class YouTrackClient:
    def __init__(self, config: Config):
        self.config = config
        self._user_cache: dict[str, dict[str, Any]] = {}
        self._project_cache: dict[str, dict[str, Any]] = {}
        self._work_type_cache: dict[str, dict[str, Any]] = {}
        self._workflow_cache: dict[str, dict[str, Any]] = {}
        self._projects_list_cache: list[dict[str, Any]] | None = None
        self._work_types_list_cache: list[dict[str, Any]] | None = None
        self._workflows_list_cache: list[dict[str, Any]] | None = None
        self._project_fields_cache: dict[str, list[dict[str, Any]]] = {}
        self._project_field_lookup_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._workflow_rules_cache: dict[str, list[dict[str, Any]]] = {}
        self._bundle_values_cache: dict[str, list[dict[str, Any]]] = {}

    def _cache_entity(self, cache: dict[str, dict[str, Any]], item: dict[str, Any], paths: Iterable[str]) -> dict[str, Any]:
        for path in paths:
            value = get_nested_value(item, path)
            if value is not None:
                cache[str(value).casefold()] = item
        return item

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | list[Any] | None = None,
        expected: Iterable[int] = (200,),
    ) -> Any:
        query = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            query[key] = value
        url = f"{self.config.base_url}/api{path}"
        if query:
            url = f"{url}?{parse.urlencode(query, doseq=True)}"
        payload = None
        headers = build_request_headers(token=self.config.token, has_body=body is not None)
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        attempts = DEFAULT_GET_RETRY_ATTEMPTS if method.upper() == "GET" else 1
        for attempt in range(attempts):
            req = request.Request(url, data=payload, method=method.upper(), headers=headers)
            try:
                with request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                    status = getattr(response, "status", response.getcode())
                    if status not in set(expected):
                        raise HttpApiError(f"Unexpected status {status} for {method} {path}")
                    return maybe_json(response.read())
            except error.HTTPError as exc:
                body_data = maybe_json(exc.read())
                if attempt < attempts - 1 and should_retry(method, status_code=exc.code):
                    time.sleep(retry_delay_seconds(attempt))
                    continue
                raise HttpApiError(f"HTTP {exc.code} for {method} {path}: {body_data}") from exc
            except error.URLError as exc:
                if attempt < attempts - 1 and should_retry(method, url_error=True):
                    time.sleep(retry_delay_seconds(attempt))
                    continue
                raise TransportApiError(f"Request failed for {method} {path}: {exc.reason}") from exc
        raise AssertionError("Unreachable retry loop exit")

    def _paginate(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        skip = 0
        items: list[dict[str, Any]] = []
        while True:
            page = self._request(
                "GET",
                path,
                params={**params, "$skip": skip, "$top": DEFAULT_PAGE_SIZE},
            )
            if not page:
                break
            items.extend(page)
            if len(page) < DEFAULT_PAGE_SIZE:
                break
            skip += DEFAULT_PAGE_SIZE
        return items

    def resolve_project(self, project_ref: str) -> dict[str, Any]:
        cache_key = project_ref.casefold()
        cached = self._project_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            projects = self._request(
                "GET",
                "/admin/projects",
                params={"fields": "id,name,shortName", "query": project_ref, "$top": DEFAULT_PAGE_SIZE, "$skip": 0},
            )
        except HttpApiError as exc:
            # Older or stricter servers reject query= on this collection.
            if not is_bad_request_error(exc):
                raise
            projects = []
        exact_match = find_exact_match(projects or [], project_ref, ("id", "name", "shortName"))
        if exact_match is not None:
            return self._cache_entity(self._project_cache, exact_match, ("id", "name", "shortName"))
        projects = self.list_projects()
        exact_match = find_exact_match(projects, project_ref, ("id", "name", "shortName"))
        if exact_match is not None:
            return self._cache_entity(self._project_cache, exact_match, ("id", "name", "shortName"))
        raise ApiError(f"Project not found: {project_ref}")

    def resolve_user(self, user_ref: str) -> dict[str, Any]:
        cache_key = user_ref.casefold()
        cached = self._user_cache.get(cache_key)
        if cached is not None:
            return cached
        users = self._request(
            "GET",
            "/users",
            params={
                "fields": "id,login,name,fullName",
                "query": user_ref,
                "$top": DEFAULT_PAGE_SIZE,
                "$skip": 0,
            },
        )
        exact_match = find_exact_user_match(users or [], user_ref)
        if exact_match is not None:
            return self._cache_entity(self._user_cache, exact_match, ("id", "login", "name", "fullName"))
        users = self._paginate("/users", params={"fields": "id,login,name,fullName"})
        exact_match = find_exact_user_match(users, user_ref)
        if exact_match is not None:
            return self._cache_entity(self._user_cache, exact_match, ("id", "login", "name", "fullName"))
        raise ApiError(f"User not found: {user_ref}")

    def resolve_command_assignee(self, user_ref: str) -> str:
        user = self.resolve_user(user_ref)
        login = user.get("login")
        if not login:
            raise ApiError(f"Resolved user is missing login: {user_ref}")
        return str(login)

    def resolve_work_type(self, work_type_ref: str) -> dict[str, Any]:
        cache_key = work_type_ref.casefold()
        cached = self._work_type_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            work_types = self._request(
                "GET",
                "/admin/timeTrackingSettings/workItemTypes",
                params={"fields": "id,name", "query": work_type_ref, "$top": DEFAULT_PAGE_SIZE, "$skip": 0},
            )
        except HttpApiError as exc:
            # Older or stricter servers reject query= on this collection.
            if not is_bad_request_error(exc):
                raise
            work_types = []
        exact_match = find_exact_match(work_types or [], work_type_ref, ("id", "name"))
        if exact_match is not None:
            return self._cache_entity(self._work_type_cache, exact_match, ("id", "name"))
        work_types = self.list_work_types()
        exact_match = find_exact_match(work_types, work_type_ref, ("id", "name"))
        if exact_match is not None:
            return self._cache_entity(self._work_type_cache, exact_match, ("id", "name"))
        raise ApiError(f"Work item type not found: {work_type_ref}")

    def list_projects(self) -> list[dict[str, Any]]:
        if self._projects_list_cache is None:
            self._projects_list_cache = self._paginate("/admin/projects", params={"fields": "id,name,shortName"})
            for project in self._projects_list_cache:
                self._cache_entity(self._project_cache, project, ("id", "name", "shortName"))
        return list(self._projects_list_cache)

    def get_project(self, project_ref: str) -> dict[str, Any]:
        return self.resolve_project(project_ref)

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/users/me", params={"fields": "id,login,name,fullName"})

    def search_users(self, query: str, *, top: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        users = self._request(
            "GET",
            "/users",
            params={
                "fields": "id,login,name,fullName",
                "query": query,
                "$top": top,
                "$skip": skip,
            },
        )
        for user in users or []:
            self._cache_entity(self._user_cache, user, ("id", "login", "name", "fullName"))
        return users

    def list_work_types(self) -> list[dict[str, Any]]:
        if self._work_types_list_cache is None:
            self._work_types_list_cache = self._paginate(
                "/admin/timeTrackingSettings/workItemTypes",
                params={"fields": "id,name"},
            )
            for work_type in self._work_types_list_cache:
                self._cache_entity(self._work_type_cache, work_type, ("id", "name"))
        return list(self._work_types_list_cache)

    def _post_command(self, issue_id: str, query: str, *, comment: str | None = None) -> dict[str, Any]:
        result = self._request(
            "POST",
            "/commands",
            params={"fields": "id,query,issues(id,idReadable),commands(id,description,error,delete)"},
            body={"issues": [{"id": issue_id}], "query": query, "comment": comment},
        )
        commands = result.get("commands") if isinstance(result, dict) else None
        if commands:
            errors = [
                f"{command.get('description') or query}: {command.get('error')}"
                for command in commands
                if isinstance(command, dict) and command.get("error")
            ]
            if errors:
                raise CommandApiError("; ".join(errors))
        return result

    def get_issue(self, issue_ref: str, *, fields: str = DEFAULT_ISSUE_FIELDS) -> dict[str, Any]:
        return self._request("GET", f"/issues/{encode_path_value(issue_ref)}", params={"fields": fields})

    def search_issues(
        self,
        query: str,
        *,
        top: int = 50,
        skip: int = 0,
        fields: str = DEFAULT_ISSUE_FIELDS,
    ) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/issues",
            params={"query": query, "$top": top, "$skip": skip, "fields": fields},
        )

    def apply_command(self, issue_ref: str, query: str, *, comment: str | None = None) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id,idReadable,summary")
        return self._post_command(issue["id"], query, comment=comment)

    def create_issue(
        self,
        *,
        project_ref: str,
        summary: str,
        description: str | None = None,
        assignee: str | None = None,
        state: str | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_ref)
        resolved_assignee = self.resolve_command_assignee(assignee) if assignee else None
        payload: dict[str, Any] = {"project": {"id": project["id"]}, "summary": summary}
        if description is not None:
            payload["description"] = description
        created = self._request("POST", "/issues", params={"fields": "id,idReadable"}, body=payload)
        query = build_command_query(state=state, assignee=resolved_assignee, fields=fields or [])
        if query:
            issue_id = created["id"]
            issue_ref = created.get("idReadable") or issue_id
            try:
                self._post_command(issue_id, query)
            except (HttpApiError, CommandApiError) as exc:
                try:
                    self._request("DELETE", f"/issues/{encode_path_value(issue_id)}", expected=(200, 204))
                except ApiError as rollback_exc:
                    raise ApiError(
                        f"Created issue {issue_ref} but command update failed: {exc}. "
                        f"Rollback failed: {rollback_exc}"
                    ) from exc
                raise ApiError(
                    f"Created issue {issue_ref} but command update failed: {exc}. Rollback succeeded."
                ) from exc
            except TransportApiError as exc:
                raise ApiError(
                    f"Created issue {issue_ref} but command update outcome is ambiguous: {exc}. "
                    "Inspect manually."
                ) from exc
        return self.get_issue(created.get("idReadable") or created["id"])

    def update_issue(
        self,
        issue_ref: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        assignee: str | None = None,
        state: str | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id,idReadable")
        resolved_assignee = self.resolve_command_assignee(assignee) if assignee else None
        body: dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if body:
            self._request(
                "POST",
                f"/issues/{encode_path_value(issue['id'])}",
                params={"fields": "id,idReadable"},
                body=body,
            )
        query = build_command_query(state=state, assignee=resolved_assignee, fields=fields or [])
        if query:
            self.apply_command(issue["id"], query)
        return self.get_issue(issue["id"])

    def delete_issue(
        self,
        issue_ref: str,
        *,
        mode: str,
        safe_delete_command: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id,idReadable,summary")
        if mode == "safe":
            if not safe_delete_command:
                raise ValueError("Safe delete requires --safe-delete-command")
            command_result = self.apply_command(issue["id"], safe_delete_command)
            return {"mode": "safe", "issue": issue, "result": command_result}
        if not confirm:
            raise ValueError("Hard delete requires --confirm")
        self._request("DELETE", f"/issues/{encode_path_value(issue['id'])}", expected=(200, 204))
        return {"mode": "hard", "issue": issue}

    def list_comments(self, issue_ref: str, *, top: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        issue = self.get_issue(issue_ref, fields="id")
        return self._request(
            "GET",
            f"/issues/{encode_path_value(issue['id'])}/comments",
            params={"fields": DEFAULT_COMMENT_FIELDS, "$top": top, "$skip": skip},
        )

    def create_comment(self, issue_ref: str, *, text: str) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        return self._request(
            "POST",
            f"/issues/{encode_path_value(issue['id'])}/comments",
            params={"fields": DEFAULT_COMMENT_FIELDS},
            body={"text": text},
        )

    def update_comment(self, issue_ref: str, comment_id: str, *, text: str) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        return self._request(
            "POST",
            f"/issues/{encode_path_value(issue['id'])}/comments/{encode_path_value(comment_id)}",
            params={"fields": DEFAULT_COMMENT_FIELDS},
            body={"text": text},
        )

    def delete_comment(self, issue_ref: str, comment_id: str) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        self._request(
            "DELETE",
            f"/issues/{encode_path_value(issue['id'])}/comments/{encode_path_value(comment_id)}",
            expected=(200, 204),
        )
        return {"deleted": True, "comment_id": comment_id}

    def _build_work_payload(
        self,
        *,
        date_text: str | None = None,
        duration: str | None = None,
        text: str | None = None,
        author_ref: str | None = None,
        type_ref: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if date_text is not None:
            payload["date"] = date_to_epoch_ms(date_text)
        if duration is not None:
            payload["duration"] = parse_duration_input(duration)
        if text is not None:
            payload["text"] = text
        if author_ref is not None:
            user = self.resolve_user(author_ref)
            payload["author"] = {"id": user["id"]}
        if type_ref is not None:
            work_type = self.resolve_work_type(type_ref)
            payload["type"] = {"id": work_type["id"]}
        return payload

    def list_work(self, issue_ref: str, *, top: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        issue = self.get_issue(issue_ref, fields="id")
        return self._request(
            "GET",
            f"/issues/{encode_path_value(issue['id'])}/timeTracking/workItems",
            params={"fields": DEFAULT_WORK_FIELDS, "$top": top, "$skip": skip},
        )

    def create_work(
        self,
        issue_ref: str,
        *,
        date_text: str,
        duration: str,
        text: str | None = None,
        author_ref: str | None = None,
        type_ref: str | None = None,
    ) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        payload = self._build_work_payload(
            date_text=date_text,
            duration=duration,
            text=text,
            author_ref=author_ref,
            type_ref=type_ref,
        )
        return self._request(
            "POST",
            f"/issues/{encode_path_value(issue['id'])}/timeTracking/workItems",
            params={"fields": DEFAULT_WORK_FIELDS},
            body=payload,
        )

    def update_work(
        self,
        issue_ref: str,
        work_id: str,
        *,
        date_text: str | None = None,
        duration: str | None = None,
        text: str | None = None,
        author_ref: str | None = None,
        type_ref: str | None = None,
    ) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        payload = self._build_work_payload(
            date_text=date_text,
            duration=duration,
            text=text,
            author_ref=author_ref,
            type_ref=type_ref,
        )
        if not payload:
            raise ValueError("Work update requires at least one field to change")
        return self._request(
            "POST",
            f"/issues/{encode_path_value(issue['id'])}/timeTracking/workItems/{encode_path_value(work_id)}",
            params={"fields": DEFAULT_WORK_FIELDS},
            body=payload,
        )

    def delete_work(self, issue_ref: str, work_id: str) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id")
        self._request(
            "DELETE",
            f"/issues/{encode_path_value(issue['id'])}/timeTracking/workItems/{encode_path_value(work_id)}",
            expected=(200, 204),
        )
        return {"deleted": True, "work_id": work_id}

    def set_work_period(
        self,
        issue_ref: str,
        *,
        date_from: str,
        date_to: str,
        duration: str,
        author_ref: str,
        text: str | None = None,
        type_ref: str | None = None,
        weekdays_only: bool = False,
    ) -> dict[str, Any]:
        issue = self.get_issue(issue_ref, fields="id,idReadable,summary")
        user = self.resolve_user(author_ref)
        target_minutes = parse_duration_minutes(duration)
        target_dates = iter_period_dates(date_from, date_to, weekdays_only=weekdays_only)
        target_work_type = self.resolve_work_type(type_ref) if type_ref is not None else None
        relevant = self._fetch_work_items_for_user(
            user,
            date_from=date_from,
            date_to=date_to,
            query=issue.get("idReadable") or issue_ref,
        )
        items_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in relevant:
            item_issue = item.get("issue") or {}
            if item_issue.get("id") == issue.get("id") or item_issue.get("idReadable") == issue.get("idReadable"):
                items_by_date[ms_to_iso_date(item["date"])].append(item)

        result = {
            "issue": {"id": issue["id"], "idReadable": issue.get("idReadable"), "summary": issue.get("summary")},
            "author": {"id": user["id"], "login": user.get("login")},
            "range": {"from": date_from, "to": date_to},
            "duration": duration,
            "target_minutes": target_minutes,
            "target_days": len(target_dates),
            "weekdays_only": weekdays_only,
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
        }

        for date_text in target_dates:
            day_items = sorted(items_by_date.get(date_text, []), key=lambda item: str(item.get("id", "")))
            if not day_items:
                self.create_work(
                    issue_ref,
                    date_text=date_text,
                    duration=duration,
                    text=text,
                    author_ref=author_ref,
                    type_ref=type_ref,
                )
                result["created"] += 1
                continue

            keep = day_items[0]
            keep_minutes = int(((keep.get("duration") or {}).get("minutes")) or 0)
            keep_text = keep.get("text")
            keep_type_id = get_nested_value(keep, "type.id")
            keep_type_name = get_nested_value(keep, "type.name")
            needs_update = keep_minutes != target_minutes
            if text is not None and keep_text != text:
                needs_update = True
            if target_work_type is not None and (
                keep_type_id != target_work_type.get("id") and keep_type_name != target_work_type.get("name")
            ):
                needs_update = True
            if needs_update:
                self.update_work(
                    issue_ref,
                    str(keep["id"]),
                    date_text=date_text,
                    duration=str(target_minutes),
                    text=text,
                    author_ref=author_ref,
                    type_ref=type_ref,
                )
                result["updated"] += 1
            else:
                result["unchanged"] += 1

            for extra in day_items[1:]:
                self.delete_work(issue_ref, str(extra["id"]))
                result["deleted"] += 1

        return result

    def list_fields(self, project_ref: str) -> list[dict[str, Any]]:
        project = self.resolve_project(project_ref)
        cached = self._project_fields_cache.get(project["id"])
        if cached is None:
            cached = self._paginate(
                f"/admin/projects/{encode_path_value(project['id'])}/customFields",
                params={"fields": PROJECT_FIELDS_LIST_FIELDS},
            )
            self._project_fields_cache[project["id"]] = cached
            for item in cached:
                for key in (item.get("id"), get_nested_value(item, "field.id"), get_nested_value(item, "field.name")):
                    if key:
                        self._project_field_lookup_cache[(project["id"], str(key).casefold())] = item
        return list(cached)

    def resolve_project_field(self, project_ref: str, field_ref: str) -> dict[str, Any]:
        project = self.resolve_project(project_ref)
        cache_key = (project["id"], field_ref.casefold())
        cached = self._project_field_lookup_cache.get(cache_key)
        if cached is not None:
            return cached
        fields = self.list_fields(project_ref)
        exact_match = find_exact_match(fields, field_ref, ("id", "field.id", "field.name"))
        if exact_match is not None:
            for key in (exact_match.get("id"), get_nested_value(exact_match, "field.id"), get_nested_value(exact_match, "field.name")):
                if key:
                    self._project_field_lookup_cache[(project["id"], str(key).casefold())] = exact_match
            return exact_match
        raise ApiError(f"Field not found in project {project.get('shortName') or project['id']}: {field_ref}")

    def list_field_values(self, project_ref: str, field_ref: str) -> list[dict[str, Any]]:
        project_field = self.resolve_project_field(project_ref, field_ref)
        bundle = project_field.get("bundle") or {}
        bundle_id = bundle.get("id")
        bundle_type = bundle.get("$type")
        if not bundle_id or not bundle_type:
            field_name = get_nested_value(project_field, "field.name") or project_field.get("id") or field_ref
            raise ApiError(f"Field does not expose bundle-backed values: {field_name}")
        cached = self._bundle_values_cache.get(str(bundle_id))
        if cached is not None:
            return list(cached)
        endpoint_parts = BUNDLE_VALUE_PATHS.get(str(bundle_type))
        fields = BUNDLE_VALUE_FIELDS.get(str(bundle_type))
        if endpoint_parts is None or fields is None:
            raise ApiError(f"Unsupported bundle type for value discovery: {bundle_type}")
        category, subpath = endpoint_parts
        values = self._paginate(
            f"/admin/customFieldSettings/bundles/{category}/{encode_path_value(str(bundle_id))}/{subpath}",
            params={"fields": fields},
        )
        self._bundle_values_cache[str(bundle_id)] = values
        return list(values)

    def list_states(self, project_ref: str) -> list[dict[str, Any]]:
        for project_field in self.list_fields(project_ref):
            bundle = project_field.get("bundle") or {}
            if bundle.get("$type") == "StateBundle":
                field_ref = (
                    get_nested_value(project_field, "field.id")
                    or get_nested_value(project_field, "field.name")
                    or project_field["id"]
                )
                return self.list_field_values(project_ref, str(field_ref))
        raise ApiError(f"State field not found in project {project_ref}")

    def list_workflows(self) -> list[dict[str, Any]]:
        if self._workflows_list_cache is None:
            self._workflows_list_cache = self._paginate("/admin/workflows", params={"fields": WORKFLOW_FIELDS})
            for workflow in self._workflows_list_cache:
                self._cache_entity(self._workflow_cache, workflow, ("id", "name", "title"))
        return list(self._workflows_list_cache)

    def resolve_workflow(self, workflow_ref: str) -> dict[str, Any]:
        cache_key = workflow_ref.casefold()
        cached = self._workflow_cache.get(cache_key)
        if cached is not None:
            return cached
        workflows = self.list_workflows()
        exact_match = find_exact_match(workflows, workflow_ref, ("id", "name", "title"))
        if exact_match is not None:
            return self._cache_entity(self._workflow_cache, exact_match, ("id", "name", "title"))
        raise ApiError(f"Workflow not found: {workflow_ref}")

    def list_workflow_rules(self, workflow_ref: str) -> list[dict[str, Any]]:
        workflow = self.resolve_workflow(workflow_ref)
        cached = self._workflow_rules_cache.get(workflow["id"])
        if cached is None:
            cached = self._paginate(
                f"/admin/workflows/{encode_path_value(workflow['id'])}/rules",
                params={"fields": "id,name,title"},
            )
            self._workflow_rules_cache[workflow["id"]] = cached
        return list(cached)

    def _fetch_work_items_for_user(
        self,
        user: dict[str, Any],
        *,
        date_from: str,
        date_to: str,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._paginate(
            "/workItems",
            params={
                "author": user["id"],
                "startDate": date_from,
                "endDate": date_to,
                "query": query,
                "fields": DEFAULT_GLOBAL_WORK_FIELDS,
            },
        )

    def report_period(
        self,
        *,
        users: list[str],
        date_from: str,
        date_to: str,
        query: str | None = None,
        group_by: str = "day",
    ) -> dict[str, Any]:
        resolved_users = [self.resolve_user(user_ref) for user_ref in users]
        collected: list[dict[str, Any]] = []
        if len(resolved_users) <= 1:
            for user in resolved_users:
                collected.extend(
                    self._fetch_work_items_for_user(user, date_from=date_from, date_to=date_to, query=query)
                )
        else:
            max_workers = min(DEFAULT_REPORT_WORKERS, len(resolved_users))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for items in executor.map(
                    lambda user: self._fetch_work_items_for_user(user, date_from=date_from, date_to=date_to, query=query),
                    resolved_users,
                ):
                    collected.extend(items)

        report = aggregate_work_items(collected) if group_by == "day" else aggregate_work_items_by(collected, group_by)
        report["users"] = [{"id": user["id"], "login": user.get("login")} for user in resolved_users]
        report["range"] = {"from": date_from, "to": date_to}
        report["items"] = len(collected)
        return report


def build_parser() -> argparse.ArgumentParser:
    def add_output_args(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--format", choices=("json", "text"), default="json")
        command_parser.add_argument("--pretty", action="store_true")

    parser = argparse.ArgumentParser(description="Manage YouTrack over REST API.")
    subparsers = parser.add_subparsers(dest="resource", required=True)

    setup = subparsers.add_parser("setup", help="Save YouTrack credentials for this skill.")
    setup.add_argument("--url", required=True)
    setup.add_argument("--token", required=True)
    add_output_args(setup)

    issue = subparsers.add_parser("issue", help="Create, read, search, update, and delete issues.")
    issue_sub = issue.add_subparsers(dest="action", required=True)

    issue_create = issue_sub.add_parser("create", help="Create an issue.")
    issue_create.add_argument("--project", required=True)
    issue_create.add_argument("--summary", required=True)
    issue_create.add_argument("--description")
    issue_create.add_argument("--assignee")
    issue_create.add_argument("--state")
    issue_create.add_argument("--field", dest="fields", action="append", default=[])
    add_output_args(issue_create)

    issue_get = issue_sub.add_parser("get", help="Get a single issue.")
    issue_get.add_argument("issue")
    issue_get.add_argument("--fields", default=DEFAULT_ISSUE_FIELDS)
    add_output_args(issue_get)

    issue_search = issue_sub.add_parser("search", help="Search issues by query.")
    issue_search.add_argument("--query", required=True)
    issue_search.add_argument("--top", type=int, default=50)
    issue_search.add_argument("--skip", type=int, default=0)
    issue_search.add_argument("--fields", default=DEFAULT_ISSUE_FIELDS)
    add_output_args(issue_search)

    issue_update = issue_sub.add_parser("update", help="Update issue fields.")
    issue_update.add_argument("issue")
    issue_update.add_argument("--summary")
    issue_update.add_argument("--description")
    issue_update.add_argument("--assignee")
    issue_update.add_argument("--state")
    issue_update.add_argument("--field", dest="fields", action="append", default=[])
    add_output_args(issue_update)

    issue_delete = issue_sub.add_parser("delete", help="Delete an issue safely or permanently.")
    issue_delete.add_argument("issue")
    issue_delete.add_argument("--mode", choices=("safe", "hard"), required=True)
    issue_delete.add_argument("--safe-delete-command")
    issue_delete.add_argument("--confirm", action="store_true")
    add_output_args(issue_delete)

    comment = subparsers.add_parser("comment", help="Create, list, update, and delete comments.")
    comment_sub = comment.add_subparsers(dest="action", required=True)

    comment_list = comment_sub.add_parser("list", help="List issue comments.")
    comment_list.add_argument("issue")
    comment_list.add_argument("--top", type=int, default=50)
    comment_list.add_argument("--skip", type=int, default=0)
    add_output_args(comment_list)

    comment_create = comment_sub.add_parser("create", help="Create a comment.")
    comment_create.add_argument("issue")
    comment_create.add_argument("--text", required=True)
    add_output_args(comment_create)

    comment_update = comment_sub.add_parser("update", help="Update a comment.")
    comment_update.add_argument("issue")
    comment_update.add_argument("comment_id")
    comment_update.add_argument("--text", required=True)
    add_output_args(comment_update)

    comment_delete = comment_sub.add_parser("delete", help="Delete a comment.")
    comment_delete.add_argument("issue")
    comment_delete.add_argument("comment_id")
    add_output_args(comment_delete)

    work = subparsers.add_parser("work", help="Create, list, update, and delete work items.")
    work_sub = work.add_subparsers(dest="action", required=True)

    work_list = work_sub.add_parser("list", help="List work items for an issue.")
    work_list.add_argument("issue")
    work_list.add_argument("--top", type=int, default=50)
    work_list.add_argument("--skip", type=int, default=0)
    add_output_args(work_list)

    work_set_period = work_sub.add_parser("set-period", help="Set exact per-day work for an issue and user over a date range.")
    work_set_period.add_argument("issue")
    work_set_period.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    work_set_period.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    work_set_period.add_argument("--author", required=True)
    work_set_period.add_argument("--duration", required=True, help="Minutes or hour/minute presentation")
    work_set_period.add_argument("--text")
    work_set_period.add_argument("--type")
    work_set_period.add_argument("--weekdays-only", action="store_true")
    add_output_args(work_set_period)

    work_create = work_sub.add_parser("create", help="Create a work item.")
    work_create.add_argument("issue")
    work_create.add_argument("--date", required=True, help="YYYY-MM-DD")
    work_create.add_argument("--duration", required=True, help="Minutes or YouTrack duration presentation")
    work_create.add_argument("--text")
    work_create.add_argument("--author")
    work_create.add_argument("--type")
    add_output_args(work_create)

    work_update = work_sub.add_parser("update", help="Update a work item.")
    work_update.add_argument("issue")
    work_update.add_argument("work_id")
    work_update.add_argument("--date", help="YYYY-MM-DD")
    work_update.add_argument("--duration", help="Minutes or YouTrack duration presentation")
    work_update.add_argument("--text")
    work_update.add_argument("--author")
    work_update.add_argument("--type")
    add_output_args(work_update)

    work_delete = work_sub.add_parser("delete", help="Delete a work item.")
    work_delete.add_argument("issue")
    work_delete.add_argument("work_id")
    add_output_args(work_delete)

    project = subparsers.add_parser("project", help="Read YouTrack project metadata.")
    project_sub = project.add_subparsers(dest="action", required=True)
    project_get = project_sub.add_parser("get", help="Get a single project.")
    project_get.add_argument("project")
    add_output_args(project_get)
    project_list = project_sub.add_parser("list", help="List accessible projects.")
    add_output_args(project_list)

    user = subparsers.add_parser("user", help="Read YouTrack user metadata.")
    user_sub = user.add_subparsers(dest="action", required=True)
    user_me = user_sub.add_parser("me", help="Show the authenticated user.")
    add_output_args(user_me)
    user_search = user_sub.add_parser("search", help="Search users by login or name.")
    user_search.add_argument("--query", required=True)
    user_search.add_argument("--top", type=int, default=50)
    user_search.add_argument("--skip", type=int, default=0)
    add_output_args(user_search)

    work_type = subparsers.add_parser("work-type", help="Read YouTrack work item type metadata.")
    work_type_sub = work_type.add_subparsers(dest="action", required=True)
    work_type_list = work_type_sub.add_parser("list", help="List work item types.")
    add_output_args(work_type_list)

    field = subparsers.add_parser("field", help="Inspect project field metadata and values.")
    field_sub = field.add_subparsers(dest="action", required=True)
    field_list = field_sub.add_parser("list", help="List project custom fields.")
    field_list.add_argument("--project", required=True)
    add_output_args(field_list)
    field_values = field_sub.add_parser("values", help="List bundle-backed values for a project field.")
    field_values.add_argument("--project", required=True)
    field_values.add_argument("--field", required=True)
    add_output_args(field_values)

    state = subparsers.add_parser("state", help="Inspect available State values in a project.")
    state_sub = state.add_subparsers(dest="action", required=True)
    state_list = state_sub.add_parser("list", help="List available State values for a project.")
    state_list.add_argument("--project", required=True)
    add_output_args(state_list)

    workflow = subparsers.add_parser("workflow", help="Inspect workflow metadata.")
    workflow_sub = workflow.add_subparsers(dest="action", required=True)
    workflow_list = workflow_sub.add_parser("list", help="List installed workflows.")
    add_output_args(workflow_list)
    workflow_rules = workflow_sub.add_parser("rules", help="List rules for a workflow.")
    workflow_rules.add_argument("workflow")
    add_output_args(workflow_rules)

    report = subparsers.add_parser("report", help="Build work item reports.")
    report_sub = report.add_subparsers(dest="action", required=True)
    report_period = report_sub.add_parser("period", help="Summarize spent time for one or more users.")
    report_period.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    report_period.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    report_period.add_argument("--user", action="append", dest="users", default=[], required=True)
    report_period.add_argument("--query")
    report_period.add_argument("--group-by", choices=("day", "issue", "user", "project", "type"), default="day")
    report_period.add_argument("--format", choices=("json", "text", "csv"), default="json")
    report_period.add_argument("--pretty", action="store_true")

    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.resource == "issue" and args.action == "delete":
        if args.mode == "safe" and not args.safe_delete_command:
            raise ValueError("safe delete requires --safe-delete-command")
        if args.mode == "hard" and not args.confirm:
            raise ValueError("hard delete requires --confirm")
    if args.resource == "issue" and args.action == "update":
        if not any([args.summary, args.description, args.assignee, args.state, args.fields]):
            raise ValueError("issue update requires at least one change")
    if args.resource == "work" and args.action == "update":
        if not any([args.date, args.duration, args.text, args.author, args.type]):
            raise ValueError("work update requires at least one change")
    if args.resource == "work" and args.action == "set-period":
        iter_period_dates(args.date_from, args.date_to, weekdays_only=args.weekdays_only)
        parse_duration_minutes(args.duration)


def emit_result(result: Any, *, output_format: str = "json", pretty: bool = False) -> None:
    if output_format == "csv":
        print(render_report_csv(result), end="")
        return
    if output_format == "text":
        print(render_text(result))
        return
    print(
        json.dumps(
            result,
            indent=2 if pretty else None,
            ensure_ascii=False,
            sort_keys=True,
            separators=None if pretty else (",", ":"),
        )
    )


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args)

    if args.resource == "setup":
        config_path = save_config(base_url=args.url, token=args.token, env=env)
        emit_result(
            {
                "base_url": args.url.rstrip("/"),
                "config_path": str(config_path),
                "saved": True,
                "token_saved": True,
            },
            output_format=args.format,
            pretty=args.pretty,
        )
        return 0

    config = load_config(env)
    client = YouTrackClient(config)

    if args.resource == "issue":
        if args.action == "create":
            result = client.create_issue(
                project_ref=args.project,
                summary=args.summary,
                description=args.description,
                assignee=args.assignee,
                state=args.state,
                fields=args.fields,
            )
        elif args.action == "get":
            result = client.get_issue(args.issue, fields=args.fields)
        elif args.action == "search":
            result = client.search_issues(args.query, top=args.top, skip=args.skip, fields=args.fields)
        elif args.action == "update":
            result = client.update_issue(
                args.issue,
                summary=args.summary,
                description=args.description,
                assignee=args.assignee,
                state=args.state,
                fields=args.fields,
            )
        elif args.action == "delete":
            result = client.delete_issue(
                args.issue,
                mode=args.mode,
                safe_delete_command=args.safe_delete_command,
                confirm=args.confirm,
            )
        else:
            raise AssertionError(f"Unhandled issue action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "comment":
        if args.action == "list":
            result = client.list_comments(args.issue, top=args.top, skip=args.skip)
        elif args.action == "create":
            result = client.create_comment(args.issue, text=args.text)
        elif args.action == "update":
            result = client.update_comment(args.issue, args.comment_id, text=args.text)
        elif args.action == "delete":
            result = client.delete_comment(args.issue, args.comment_id)
        else:
            raise AssertionError(f"Unhandled comment action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "work":
        if args.action == "list":
            result = client.list_work(args.issue, top=args.top, skip=args.skip)
        elif args.action == "set-period":
            result = client.set_work_period(
                args.issue,
                date_from=args.date_from,
                date_to=args.date_to,
                duration=args.duration,
                author_ref=args.author,
                text=args.text,
                type_ref=args.type,
                weekdays_only=args.weekdays_only,
            )
        elif args.action == "create":
            result = client.create_work(
                args.issue,
                date_text=args.date,
                duration=args.duration,
                text=args.text,
                author_ref=args.author,
                type_ref=args.type,
            )
        elif args.action == "update":
            result = client.update_work(
                args.issue,
                args.work_id,
                date_text=args.date,
                duration=args.duration,
                text=args.text,
                author_ref=args.author,
                type_ref=args.type,
            )
        elif args.action == "delete":
            result = client.delete_work(args.issue, args.work_id)
        else:
            raise AssertionError(f"Unhandled work action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "project":
        if args.action == "get":
            result = client.get_project(args.project)
        elif args.action == "list":
            result = client.list_projects()
        else:
            raise AssertionError(f"Unhandled project action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "user":
        if args.action == "me":
            result = client.get_me()
        elif args.action == "search":
            result = client.search_users(args.query, top=args.top, skip=args.skip)
        else:
            raise AssertionError(f"Unhandled user action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "work-type":
        if args.action == "list":
            result = client.list_work_types()
        else:
            raise AssertionError(f"Unhandled work-type action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "field":
        if args.action == "list":
            result = client.list_fields(args.project)
        elif args.action == "values":
            result = client.list_field_values(args.project, args.field)
        else:
            raise AssertionError(f"Unhandled field action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "state":
        if args.action == "list":
            result = client.list_states(args.project)
        else:
            raise AssertionError(f"Unhandled state action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "workflow":
        if args.action == "list":
            result = client.list_workflows()
        elif args.action == "rules":
            result = client.list_workflow_rules(args.workflow)
        else:
            raise AssertionError(f"Unhandled workflow action: {args.action}")
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    if args.resource == "report" and args.action == "period":
        result = client.report_period(
            users=args.users,
            date_from=args.date_from,
            date_to=args.date_to,
            query=args.query,
            group_by=args.group_by,
        )
        emit_result(result, output_format=args.format, pretty=args.pretty)
        return 0

    raise AssertionError(f"Unhandled resource/action: {args.resource}/{args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
