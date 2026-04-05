import argparse
import importlib.util
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "youtrack" / "scripts" / "youtrack_api.py"


def load_module():
    spec = importlib.util.spec_from_file_location("youtrack_api", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_script_as_main(argv, env):
    stdout = io.StringIO()
    stderr = io.StringIO()
    original_argv = sys.argv[:]
    try:
        sys.argv = [str(MODULE_PATH), *argv]
        with mock.patch.dict(os.environ, env, clear=True):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with unittest.TestCase().assertRaises(SystemExit) as exc:
                    runpy.run_path(str(MODULE_PATH), run_name="__main__")
    finally:
        sys.argv = original_argv
    return exc.exception.code, stdout.getvalue(), stderr.getvalue()


class YouTrackApiTests(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(MODULE_PATH.exists(), f"Expected script at {MODULE_PATH}")

    def test_load_config_requires_env_vars(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "YOUTRACK_BASE_URL"):
                module.load_config({"HOME": temp_dir})

    def test_load_config_reads_from_skill_config_file(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"HOME": temp_dir}
            config_path = Path(temp_dir) / ".config" / "youtrack-rest" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://example.youtrack.cloud",
                        "token": "perm-config-token",
                    }
                )
            )

            config = module.load_config(env)

        self.assertEqual(config.base_url, "https://example.youtrack.cloud")
        self.assertEqual(config.token, "perm-config-token")

    def test_load_config_prefers_env_over_skill_config_file(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / ".config" / "youtrack-rest" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://config.youtrack.cloud",
                        "token": "perm-config-token",
                    }
                )
            )
            env = {
                "HOME": temp_dir,
                "YOUTRACK_BASE_URL": "https://env.youtrack.cloud",
                "YOUTRACK_TOKEN": "perm-env-token",
            }

            config = module.load_config(env)

        self.assertEqual(config.base_url, "https://env.youtrack.cloud")
        self.assertEqual(config.token, "perm-env-token")

    def test_save_config_writes_to_skill_specific_path(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"HOME": temp_dir}

            config_path = module.save_config(
                base_url="https://example.youtrack.cloud/",
                token="perm-setup-token",
                env=env,
            )

            saved = json.loads(config_path.read_text())

        self.assertEqual(
            config_path,
            Path(temp_dir) / ".config" / "youtrack" / "config.json",
        )
        self.assertEqual(saved["base_url"], "https://example.youtrack.cloud")
        self.assertEqual(saved["token"], "perm-setup-token")

    def test_build_command_query_combines_common_updates(self):
        module = load_module()

        query = module.build_command_query(
            state="In Progress",
            assignee="jane.doe",
            fields=["Priority=Critical"],
        )

        self.assertEqual(query, 'State In Progress Assignee jane.doe Priority Critical')

    def test_build_command_query_escapes_embedded_quotes(self):
        module = load_module()

        query = module.build_command_query(
            state='Needs "Review"',
            fields=["Fix versions=Laravel + Angular 1.0"],
        )

        self.assertEqual(
            query,
            'State Needs \\"Review\\" Fix versions Laravel + Angular 1.0',
        )

    def test_parse_duration_input_supports_minutes(self):
        module = load_module()

        self.assertEqual(module.parse_duration_input("120"), {"minutes": 120})

    def test_parse_duration_input_supports_presentation(self):
        module = load_module()

        self.assertEqual(module.parse_duration_input("1h 30m"), {"presentation": "1h 30m"})

    def test_parse_duration_minutes_supports_numeric_and_hour_minute_presentations(self):
        module = load_module()

        self.assertEqual(module.parse_duration_minutes("120"), 120)
        self.assertEqual(module.parse_duration_minutes("8h"), 480)
        self.assertEqual(module.parse_duration_minutes("1h 30m"), 90)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            module.parse_duration_minutes("   ")
        with self.assertRaisesRegex(ValueError, "must be positive"):
            module.parse_duration_minutes("0")
        with self.assertRaisesRegex(ValueError, "Unsupported duration"):
            module.parse_duration_minutes("1h foo 30m")
        with self.assertRaisesRegex(ValueError, "must be positive"):
            module.parse_duration_minutes("0h 0m")
        with self.assertRaisesRegex(ValueError, "Unsupported duration"):
            module.parse_duration_minutes("1d")

    def test_build_request_headers_include_browser_signature(self):
        module = load_module()

        headers = module.build_request_headers(token="secret-token", has_body=True)

        self.assertEqual(headers["Authorization"], "Bearer secret-token")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertIn("Chrome/", headers["User-Agent"])

    def test_validate_delete_args_requires_safe_delete_command(self):
        module = load_module()
        parser = module.build_parser()
        args = parser.parse_args(["issue", "delete", "T-1", "--mode", "safe"])

        with self.assertRaisesRegex(ValueError, "safe-delete-command"):
            module.validate_args(args)

    def test_validate_delete_args_requires_confirm_for_hard_delete(self):
        module = load_module()
        parser = module.build_parser()
        args = parser.parse_args(["issue", "delete", "T-1", "--mode", "hard"])

        with self.assertRaisesRegex(ValueError, "--confirm"):
            module.validate_args(args)

    def test_build_parser_accepts_setup_project_and_user_commands(self):
        module = load_module()
        parser = module.build_parser()

        setup_args = parser.parse_args(
            ["setup", "--url", "https://example.youtrack.cloud", "--token", "perm-token"]
        )
        project_get_args = parser.parse_args(["project", "get", "T"])
        project_args = parser.parse_args(["project", "list", "--format", "text"])
        user_search_args = parser.parse_args(["user", "search", "--query", "zhd", "--top", "5"])
        user_args = parser.parse_args(["user", "me", "--pretty"])
        work_type_args = parser.parse_args(["work-type", "list"])
        report_args = parser.parse_args(
            [
                "report",
                "period",
                "--from",
                "2026-03-01",
                "--to",
                "2026-03-31",
                "--user",
                "alice",
                "--group-by",
                "project",
                "--format",
                "csv",
            ]
        )
        work_set_period_args = parser.parse_args(
            [
                "work",
                "set-period",
                "T-1",
                "--from",
                "2026-03-01",
                "--to",
                "2026-03-31",
                "--author",
                "alice",
                "--duration",
                "8h",
                "--weekdays-only",
            ]
        )

        self.assertEqual(setup_args.resource, "setup")
        self.assertEqual(project_get_args.action, "get")
        self.assertEqual(project_args.resource, "project")
        self.assertEqual(project_args.action, "list")
        self.assertEqual(project_args.format, "text")
        self.assertEqual(user_search_args.action, "search")
        self.assertEqual(user_search_args.top, 5)
        self.assertEqual(user_args.resource, "user")
        self.assertEqual(user_args.action, "me")
        self.assertTrue(user_args.pretty)
        self.assertEqual(work_type_args.resource, "work-type")
        self.assertEqual(report_args.group_by, "project")
        self.assertEqual(report_args.format, "csv")
        self.assertEqual(work_set_period_args.resource, "work")
        self.assertEqual(work_set_period_args.action, "set-period")
        self.assertTrue(work_set_period_args.weekdays_only)


    def test_build_parser_accepts_field_state_and_workflow_commands(self):
        module = load_module()
        parser = module.build_parser()

        field_list_args = parser.parse_args(["field", "list", "--project", "T", "--format", "text"])
        field_values_args = parser.parse_args(["field", "values", "--project", "T", "--field", "State"])
        state_list_args = parser.parse_args(["state", "list", "--project", "T", "--pretty"])
        workflow_list_args = parser.parse_args(["workflow", "list", "--format", "text"])
        workflow_rules_args = parser.parse_args(["workflow", "rules", "77-7"])

        self.assertEqual(field_list_args.resource, "field")
        self.assertEqual(field_list_args.action, "list")
        self.assertEqual(field_values_args.action, "values")
        self.assertEqual(state_list_args.resource, "state")
        self.assertEqual(state_list_args.action, "list")
        self.assertEqual(workflow_list_args.resource, "workflow")
        self.assertEqual(workflow_list_args.action, "list")
        self.assertEqual(workflow_rules_args.action, "rules")

    def test_aggregate_work_items_groups_by_day_and_issue(self):
        module = load_module()

        work_items = [
            {
                "date": 1772323200000,
                "duration": {"minutes": 60, "presentation": "1h"},
                "issue": {"idReadable": "T-1", "summary": "First task"},
                "author": {"login": "alice"},
            },
            {
                "date": 1772323200000,
                "duration": {"minutes": 30, "presentation": "30m"},
                "issue": {"idReadable": "T-1", "summary": "First task"},
                "author": {"login": "alice"},
            },
            {
                "date": 1772409600000,
                "duration": {"minutes": 120, "presentation": "2h"},
                "issue": {"idReadable": "T-2", "summary": "Second task"},
                "author": {"login": "bob"},
            },
        ]

        report = module.aggregate_work_items(work_items)

        self.assertEqual(report["total_minutes"], 210)
        self.assertEqual(report["days"][0]["date"], "2026-03-01")
        self.assertEqual(report["days"][0]["total_minutes"], 90)
        self.assertEqual(report["days"][0]["issues"][0]["idReadable"], "T-1")
        self.assertEqual(report["days"][0]["issues"][0]["total_minutes"], 90)
        self.assertEqual(report["days"][1]["date"], "2026-03-02")
        self.assertEqual(report["days"][1]["issues"][0]["idReadable"], "T-2")

    def test_report_period_uses_date_strings_for_work_items_endpoint(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.paginate_calls = []

            def resolve_user(self, user_ref: str):
                return {"id": f"id-{user_ref}", "login": user_ref}

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return []

        client = StubClient()

        report = client.report_period(
            users=["alice"],
            date_from="2026-03-31",
            date_to="2026-03-31",
            query="T-2400",
        )

        self.assertEqual(report["items"], 0)
        self.assertEqual(
            client.paginate_calls,
            [
                (
                    "/workItems",
                    {
                        "author": "id-alice",
                        "startDate": "2026-03-31",
                        "endDate": "2026-03-31",
                        "query": "T-2400",
                        "fields": module.DEFAULT_GLOBAL_WORK_FIELDS,
                    },
                )
            ],
        )

    def test_filter_users_matches_login_and_name_fields(self):
        module = load_module()
        users = [
            {"id": "1", "login": "alice", "name": "Alice", "fullName": "Alice Smith"},
            {"id": "2", "login": "bob", "name": "Bob", "fullName": "Bob Stone"},
            {"id": "3", "login": "alicia", "name": "Alicia", "fullName": "Alicia Keys"},
        ]

        matches = module.filter_users(users, "ali")

        self.assertEqual([user["login"] for user in matches], ["alice", "alicia"])

    def test_search_users_prefers_server_side_query(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [{"id": "1", "login": "alice", "name": "Alice", "fullName": "Alice Smith"}]

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("search_users should not scan the full /users collection")

        client = StubClient()

        users = client.search_users("ali", top=5, skip=10)

        self.assertEqual([user["login"] for user in users], ["alice"])
        self.assertEqual(
            client.request_calls,
            [
                (
                    "GET",
                    "/users",
                    {
                        "fields": "id,login,name,fullName",
                        "query": "ali",
                        "$top": 5,
                        "$skip": 10,
                    },
                )
            ],
        )

    def test_resolve_project_prefers_server_side_query_then_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [
                    {"id": "0-2", "shortName": "TA", "name": "Tazman Angular"},
                    {"id": "0-0", "shortName": "T", "name": "Tazman Dev"},
                ]

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("resolve_project should not scan the full project collection")

        client = StubClient()

        project = client.resolve_project("T")

        self.assertEqual(project["id"], "0-0")
        self.assertEqual(
            client.request_calls,
            [
                (
                    "GET",
                    "/admin/projects",
                    {
                        "fields": "id,name,shortName",
                        "query": "T",
                        "$top": module.DEFAULT_PAGE_SIZE,
                        "$skip": 0,
                    },
                )
            ],
        )

    def test_resolve_project_falls_back_to_full_scan_when_query_has_no_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []
                self.paginate_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [{"id": "0-2", "shortName": "TA", "name": "Tazman Angular"}]

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return [{"id": "0-0", "shortName": "T", "name": "Tazman Dev"}]

        client = StubClient()

        project = client.resolve_project("T")

        self.assertEqual(project["id"], "0-0")
        self.assertEqual(len(client.request_calls), 1)
        self.assertEqual(client.paginate_calls, [("/admin/projects", {"fields": "id,name,shortName"})])

    def test_resolve_project_falls_back_to_full_scan_when_query_is_rejected(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []
                self.paginate_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                raise module.HttpApiError("HTTP 400 for GET /admin/projects: {'error': 'Bad Request'}")

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return [{"id": "0-0", "shortName": "T", "name": "Tazman Dev"}]

        client = StubClient()

        project = client.resolve_project("T")

        self.assertEqual(project["id"], "0-0")
        self.assertEqual(len(client.request_calls), 1)
        self.assertEqual(client.paginate_calls, [("/admin/projects", {"fields": "id,name,shortName"})])

    def test_resolve_project_reraises_non_bad_request_query_errors(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                raise module.HttpApiError("HTTP 500 for GET /admin/projects: {'error': 'boom'}")

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("resolve_project should not fall back on non-400 errors")

        client = StubClient()

        with self.assertRaisesRegex(module.HttpApiError, "HTTP 500 for GET /admin/projects"):
            client.resolve_project("T")

    def test_resolve_work_type_prefers_server_side_query_then_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [
                    {"id": "78-1", "name": "Testing"},
                    {"id": "78-0", "name": "Development"},
                ]

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("resolve_work_type should not scan the full work type collection")

        client = StubClient()

        work_type = client.resolve_work_type("Development")

        self.assertEqual(work_type["id"], "78-0")
        self.assertEqual(
            client.request_calls,
            [
                (
                    "GET",
                    "/admin/timeTrackingSettings/workItemTypes",
                    {
                        "fields": "id,name",
                        "query": "Development",
                        "$top": module.DEFAULT_PAGE_SIZE,
                        "$skip": 0,
                    },
                )
            ],
        )

    def test_resolve_work_type_falls_back_to_full_scan_when_query_has_no_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []
                self.paginate_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [{"id": "78-2", "name": "Documentation"}]

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return [{"id": "78-0", "name": "Development"}]

        client = StubClient()

        work_type = client.resolve_work_type("Development")

        self.assertEqual(work_type["id"], "78-0")
        self.assertEqual(
            client.paginate_calls,
            [("/admin/timeTrackingSettings/workItemTypes", {"fields": "id,name"})],
        )

    def test_resolve_work_type_falls_back_to_full_scan_when_query_is_rejected(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []
                self.paginate_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                raise module.HttpApiError(
                    "HTTP 400 for GET /admin/timeTrackingSettings/workItemTypes: {'error': 'Bad Request'}"
                )

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return [{"id": "78-0", "name": "Development"}]

        client = StubClient()

        work_type = client.resolve_work_type("Development")

        self.assertEqual(work_type["id"], "78-0")
        self.assertEqual(len(client.request_calls), 1)
        self.assertEqual(
            client.paginate_calls,
            [("/admin/timeTrackingSettings/workItemTypes", {"fields": "id,name"})],
        )

    def test_resolve_work_type_reraises_non_bad_request_query_errors(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                raise module.HttpApiError(
                    "HTTP 500 for GET /admin/timeTrackingSettings/workItemTypes: {'error': 'boom'}"
                )

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("resolve_work_type should not fall back on non-400 errors")

        client = StubClient()

        with self.assertRaisesRegex(module.HttpApiError, "HTTP 500 for GET /admin/timeTrackingSettings/workItemTypes"):
            client.resolve_work_type("Development")

    def test_resolve_user_prefers_server_side_query_then_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [
                    {"id": "2", "login": "alice.smith", "name": "Alice Smith", "fullName": "Alice Smith"},
                    {"id": "1", "login": "alice", "name": "Alice", "fullName": "Alice Example"},
                ]

            def _paginate(self, path: str, *, params=None):
                raise AssertionError("resolve_user should not scan the full /users collection")

        client = StubClient()

        user = client.resolve_user("alice")

        self.assertEqual(user["id"], "1")
        self.assertEqual(
            client.request_calls,
            [
                (
                    "GET",
                    "/users",
                    {
                        "fields": "id,login,name,fullName",
                        "query": "alice",
                        "$top": module.DEFAULT_PAGE_SIZE,
                        "$skip": 0,
                    },
                )
            ],
        )

    def test_resolve_user_falls_back_to_full_scan_when_query_has_no_exact_match(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.request_calls = []
                self.paginate_calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.request_calls.append((method, path, dict(params or {})))
                return [
                    {"id": "2", "login": "alice.smith", "name": "Alice Smith", "fullName": "Alice Smith"},
                ]

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                return [
                    {"id": "1", "login": "alice", "name": "Alice", "fullName": "Alice Example"},
                ]

        client = StubClient()

        user = client.resolve_user("alice")

        self.assertEqual(user["id"], "1")
        self.assertEqual(len(client.request_calls), 1)
        self.assertEqual(
            client.paginate_calls,
            [("/users", {"fields": "id,login,name,fullName"})],
        )

    def test_resolve_command_assignee_requires_resolved_login(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def resolve_user(self, user_ref: str):
                if user_ref != "alice":
                    raise AssertionError(f"Unexpected user ref: {user_ref}")
                return {"id": "1-1", "name": "Alice Example"}

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "Resolved user is missing login: alice"):
            client.resolve_command_assignee("alice")

    def test_report_period_supports_project_grouping(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def resolve_user(self, user_ref: str):
                return {"id": f"id-{user_ref}", "login": user_ref}

            def _paginate(self, path: str, *, params=None):
                return [
                    {
                        "date": 1774915200000,
                        "duration": {"minutes": 60, "presentation": "1h"},
                        "author": {"login": "alice"},
                        "type": {"name": "Development"},
                        "issue": {
                            "idReadable": "T-1",
                            "summary": "One",
                            "project": {"shortName": "T", "name": "Tazman Dev"},
                        },
                    },
                    {
                        "date": 1774915200000,
                        "duration": {"minutes": 30, "presentation": "30m"},
                        "author": {"login": "alice"},
                        "type": {"name": "Testing"},
                        "issue": {
                            "idReadable": "T-2",
                            "summary": "Two",
                            "project": {"shortName": "GS", "name": "Goshow"},
                        },
                    },
                ]

        client = StubClient()

        report = client.report_period(
            users=["alice"],
            date_from="2026-03-31",
            date_to="2026-03-31",
            group_by="project",
        )

        self.assertEqual(report["group_by"], "project")
        self.assertEqual(report["total_minutes"], 90)
        self.assertEqual(report["buckets"][0]["key"], "GS")
        self.assertEqual(report["buckets"][0]["total_minutes"], 30)
        self.assertEqual(report["buckets"][1]["key"], "T")
        self.assertEqual(report["buckets"][1]["total_minutes"], 60)

    def test_render_report_csv_includes_day_totals_and_grand_total(self):
        module = load_module()

        report = {
            "days": [
                {
                    "date": "2026-03-31",
                    "total_minutes": 90,
                    "total_presentation": "1h 30m",
                    "issues": [
                        {
                            "idReadable": "T-1",
                            "summary": "One",
                            "total_minutes": 90,
                            "total_presentation": "1h 30m",
                        }
                    ],
                }
            ],
            "total_minutes": 90,
            "total_presentation": "1h 30m",
        }

        csv_output = module.render_report_csv(report).strip().splitlines()

        self.assertEqual(
            csv_output,
            [
                "row_type,group_by,date,key,label,total_minutes,total_presentation",
                "item,day,2026-03-31,T-1,One,90,1h 30m",
                "day_total,day,2026-03-31,,,90,1h 30m",
                "grand_total,day,,,,90,1h 30m",
            ],
        )

    def test_render_report_csv_includes_bucket_items_and_grand_total(self):
        module = load_module()

        report = {
            "group_by": "project",
            "buckets": [
                {
                    "key": "T",
                    "label": "Tazman Dev",
                    "total_minutes": 90,
                    "total_presentation": "1h 30m",
                }
            ],
            "total_minutes": 90,
            "total_presentation": "1h 30m",
        }

        csv_output = module.render_report_csv(report).strip().splitlines()

        self.assertEqual(
            csv_output,
            [
                "row_type,group_by,date,key,label,total_minutes,total_presentation",
                "item,project,,T,Tazman Dev,90,1h 30m",
                "grand_total,project,,,,90,1h 30m",
            ],
        )

    def test_emit_result_uses_compact_json_by_default_and_pretty_when_requested(self):
        module = load_module()

        compact = io.StringIO()
        with redirect_stdout(compact):
            module.emit_result({"b": 2, "a": 1}, output_format="json", pretty=False)

        pretty = io.StringIO()
        with redirect_stdout(pretty):
            module.emit_result({"b": 2, "a": 1}, output_format="json", pretty=True)

        self.assertEqual(compact.getvalue().strip(), '{"a":1,"b":2}')
        self.assertEqual(pretty.getvalue().strip(), '{\n  "a": 1,\n  "b": 2\n}')

    def test_emit_result_supports_text_output(self):
        module = load_module()

        output = io.StringIO()
        with redirect_stdout(output):
            module.emit_result(
                {"idReadable": "T-1", "summary": "Test issue"},
                output_format="text",
                pretty=False,
            )

        rendered = output.getvalue()
        self.assertIn("idReadable: T-1", rendered)
        self.assertIn("summary: Test issue", rendered)

    def test_emit_result_supports_csv_output_for_reports(self):
        module = load_module()

        output = io.StringIO()
        with redirect_stdout(output):
            module.emit_result(
                {
                    "group_by": "project",
                    "total_minutes": 90,
                    "total_presentation": "1h 30m",
                    "buckets": [
                        {
                            "key": "T",
                            "label": "Tazman Dev",
                            "total_minutes": 90,
                            "total_presentation": "1h 30m",
                        }
                    ],
                },
                output_format="csv",
                pretty=False,
            )

        rendered = output.getvalue().strip().splitlines()
        self.assertEqual(rendered[0], "row_type,group_by,date,key,label,total_minutes,total_presentation")
        self.assertEqual(rendered[1], "item,project,,T,Tazman Dev,90,1h 30m")
        self.assertEqual(rendered[2], "grand_total,project,,,,90,1h 30m")

    def test_create_issue_rolls_back_on_explicit_http_error(self):
        module = load_module()
        self.assertTrue(hasattr(module, "HttpApiError"))

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                if (method, path) == ("POST", "/issues"):
                    return {"id": "2-1", "idReadable": "T-1"}
                if (method, path) == ("POST", "/commands"):
                    raise module.HttpApiError("HTTP 400 for POST /commands: {'error':'Bad Request'}")
                if (method, path) == ("DELETE", "/issues/2-1"):
                    return None
                raise AssertionError(f"Unexpected request: {(method, path)}")

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "Rollback succeeded"):
            client.create_issue(project_ref="T", summary="Test", state="Open")

        self.assertEqual(
            [(method, path) for method, path, _params, _body in client.calls],
            [("POST", "/issues"), ("POST", "/commands"), ("DELETE", "/issues/2-1")],
        )

    def test_create_issue_reports_failed_rollback_after_http_error(self):
        module = load_module()
        self.assertTrue(hasattr(module, "HttpApiError"))

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                if (method, path) == ("POST", "/issues"):
                    return {"id": "2-1", "idReadable": "T-1"}
                if (method, path) == ("POST", "/commands"):
                    raise module.HttpApiError("HTTP 400 for POST /commands: {'error':'Bad Request'}")
                if (method, path) == ("DELETE", "/issues/2-1"):
                    raise module.HttpApiError("HTTP 500 for DELETE /issues/2-1: {'error':'boom'}")
                raise AssertionError(f"Unexpected request: {(method, path)}")

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "Rollback failed"):
            client.create_issue(project_ref="T", summary="Test", state="Open")

    def test_create_issue_does_not_roll_back_on_transport_error(self):
        module = load_module()
        self.assertTrue(hasattr(module, "TransportApiError"))

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path))
                if (method, path) == ("POST", "/issues"):
                    return {"id": "2-1", "idReadable": "T-1"}
                if (method, path) == ("POST", "/commands"):
                    raise module.TransportApiError("Request failed for POST /commands: timed out")
                raise AssertionError(f"Unexpected request: {(method, path)}")

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "Inspect manually"):
            client.create_issue(project_ref="T", summary="Test", state="Open")

        self.assertEqual(client.calls, [("POST", "/issues"), ("POST", "/commands")])

    def test_create_issue_rolls_back_on_command_body_error(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                if (method, path) == ("POST", "/issues"):
                    return {"id": "2-1", "idReadable": "T-1"}
                if (method, path) == ("POST", "/commands"):
                    return {
                        "id": "cmd-1",
                        "query": "State Missing",
                        "issues": [{"id": "2-1", "idReadable": "T-1"}],
                        "commands": [
                            {
                                "id": "cmd-item-1",
                                "description": "State Missing",
                                "error": "Unknown value: Missing",
                                "delete": False,
                            }
                        ],
                    }
                if (method, path) == ("DELETE", "/issues/2-1"):
                    return None
                raise AssertionError(f"Unexpected request: {(method, path)}")

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "Rollback succeeded"):
            client.create_issue(project_ref="T", summary="Test", state="Missing")

        self.assertEqual(
            [(method, path) for method, path, _params, _body in client.calls],
            [("POST", "/issues"), ("POST", "/commands"), ("DELETE", "/issues/2-1")],
        )

    def test_request_retries_retryable_get_errors(self):
        module = load_module()
        client = module.YouTrackClient(module.Config(base_url="https://example.test", token="secret"))

        attempts = []
        original_urlopen = module.request.urlopen
        original_sleep = module.time.sleep

        def fake_urlopen(req, timeout=None):
            attempts.append((req.full_url, timeout))
            if len(attempts) == 1:
                raise module.error.HTTPError(
                    req.full_url,
                    503,
                    "Service Unavailable",
                    {},
                    io.BytesIO(b'{"error":"temporary"}'),
                )

            class Response:
                status = 200

                def read(self):
                    return b'{"ok":true}'

                def getcode(self):
                    return 200

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return Response()

        try:
            module.request.urlopen = fake_urlopen
            module.time.sleep = lambda *_args, **_kwargs: None

            result = client._request("GET", "/health")
        finally:
            module.request.urlopen = original_urlopen
            module.time.sleep = original_sleep

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(attempts), 2)

    def test_save_config_rejects_empty_values(self):
        module = load_module()

        with self.assertRaisesRegex(ValueError, "non-empty"):
            module.save_config(base_url="", token="perm-token", env={"HOME": "/tmp"})

        with self.assertRaisesRegex(ValueError, "non-empty"):
            module.save_config(base_url="https://example.test", token="", env={"HOME": "/tmp"})

    def test_load_saved_config_rejects_invalid_json_and_non_object(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".config" / "youtrack-rest" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text("{bad json")
            with self.assertRaisesRegex(ValueError, "Invalid config file"):
                module.load_saved_config({"HOME": temp_dir})

            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "expected an object"):
                module.load_saved_config({"HOME": temp_dir})

    def test_load_saved_config_prefers_new_path_when_both_exist(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_root = Path(temp_dir) / ".config"
            new_path = config_root / "youtrack" / "config.json"
            legacy_path = config_root / "youtrack-rest" / "config.json"
            new_path.parent.mkdir(parents=True)
            legacy_path.parent.mkdir(parents=True)
            new_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://new.example",
                        "token": "new-token",
                    }
                )
            )
            legacy_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://old.example",
                        "token": "old-token",
                    }
                )
            )

            config = module.load_saved_config({"HOME": temp_dir})

        self.assertEqual(
            config,
            {"base_url": "https://new.example", "token": "new-token"},
        )

    def test_load_saved_config_falls_back_to_legacy_path(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / ".config" / "youtrack-rest" / "config.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://old.example",
                        "token": "old-token",
                    }
                )
            )

            config = module.load_saved_config({"HOME": temp_dir})

        self.assertEqual(
            config,
            {"base_url": "https://old.example", "token": "old-token"},
        )

    def test_quote_command_token_returns_plain_token_without_spaces(self):
        module = load_module()
        self.assertEqual(module.quote_command_token("PlainToken"), "PlainToken")

    def test_quote_command_token_quotes_tokens_with_spaces(self):
        module = load_module()
        self.assertEqual(module.quote_command_token("Needs Review"), '"Needs Review"')

    def test_split_field_assignment_rejects_invalid_input(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "Name=Value"):
            module.split_field_assignment("Priority")
        with self.assertRaisesRegex(ValueError, "Name=Value"):
            module.split_field_assignment("=Critical")

    def test_format_minutes_and_date_helpers_cover_edge_cases(self):
        module = load_module()
        self.assertEqual(module.format_minutes(0), "0m")
        self.assertEqual(module.format_minutes(60), "1h")
        self.assertEqual(module.ms_to_iso_date(module.date_to_epoch_ms("2026-03-31")), "2026-03-31")
        self.assertEqual(module.parse_iso_date("2026-03-31").tzinfo, module.timezone.utc)
        with self.assertRaisesRegex(ValueError, "Invalid date range"):
            module.iter_period_dates("2026-04-01", "2026-03-31")

    def test_render_generic_text_and_render_text_cover_nested_structures(self):
        module = load_module()
        rendered = module.render_generic_text(
            {
                "empty_dict": {},
                "empty_list": [],
                "nested": [{"flag": True}, None],
            }
        )
        self.assertIn("empty_dict:", rendered)
        self.assertIn("  {}", rendered)
        self.assertIn("empty_list:", rendered)
        self.assertIn("  []", rendered)
        self.assertIn("    flag: true", rendered)
        self.assertIn("  - null", rendered)
        self.assertIn("key: value", module.render_text({"key": "value"}))

    def test_render_report_csv_rejects_non_report_input(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "only supported for report results"):
            module.render_report_csv({"foo": "bar"})

    def test_retry_helpers_and_lookup_helpers_cover_edge_cases(self):
        module = load_module()
        self.assertEqual(module.retry_delay_seconds(10), 2.0)
        self.assertFalse(module.should_retry("POST", status_code=503))
        self.assertFalse(module.should_retry("GET", status_code=404))
        self.assertTrue(module.should_retry("GET", url_error=True))
        self.assertEqual(module.paginate_items([1, 2, 3], top=-1, skip=-5), [])
        self.assertEqual(module.get_nested_value({"a": {"b": 3}}, "a.b"), 3)
        self.assertIsNone(module.get_nested_value({"a": 3}, "a.b"))
        self.assertIsNone(module.find_exact_match([{"id": "1"}], "missing", ("id",)))

    def test_maybe_json_handles_empty_json_and_plain_text(self):
        module = load_module()
        self.assertIsNone(module.maybe_json(b""))
        self.assertIsNone(module.maybe_json("".encode()))
        self.assertEqual(module.maybe_json(b'{"ok":true}'), {"ok": True})
        self.assertEqual(module.maybe_json(b"plain text"), "plain text")

    def test_request_raises_http_and_transport_errors_without_retry_for_post(self):
        module = load_module()
        client = module.YouTrackClient(module.Config(base_url="https://example.test", token="secret"))
        original_urlopen = module.request.urlopen
        try:
            module.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
                module.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}'))
            )
            with self.assertRaisesRegex(module.HttpApiError, "HTTP 400"):
                client._request("POST", "/issues", body={"summary": "x"})

            module.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
                module.error.URLError("offline")
            )
            with self.assertRaisesRegex(module.TransportApiError, "offline"):
                client._request("POST", "/issues", body={"summary": "x"})
        finally:
            module.request.urlopen = original_urlopen

    def test_request_raises_for_unexpected_status_and_paginate_fetches_multiple_pages(self):
        module = load_module()
        client = module.YouTrackClient(module.Config(base_url="https://example.test", token="secret"))
        original_urlopen = module.request.urlopen

        class Response:
            def __init__(self, status, payload):
                self.status = status
                self._payload = payload

            def read(self):
                return self._payload

            def getcode(self):
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        try:
            module.request.urlopen = lambda req, timeout=None: Response(201, b"{}")
            with self.assertRaisesRegex(module.HttpApiError, "Unexpected status 201"):
                client._request("GET", "/health")

            pages = [
                [{"id": str(index)} for index in range(module.DEFAULT_PAGE_SIZE)],
                [{"id": "last"}],
            ]

            def fake_urlopen(req, timeout=None):
                payload = pages.pop(0)
                return Response(200, json.dumps(payload).encode())

            module.request.urlopen = fake_urlopen
            items = client._paginate("/users", params={"fields": "id"})
        finally:
            module.request.urlopen = original_urlopen

        self.assertEqual(items[0]["id"], "0")
        self.assertEqual(items[-1]["id"], "last")

    def test_list_projects_and_work_types_cache_their_lists(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.paginate_calls = []

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                if path == "/admin/projects":
                    return [{"id": "0-0", "shortName": "T", "name": "Tazman Dev"}]
                if path == "/admin/timeTrackingSettings/workItemTypes":
                    return [{"id": "78-0", "name": "Development"}]
                raise AssertionError(path)

        client = StubClient()
        self.assertEqual(client.list_projects()[0]["id"], "0-0")
        self.assertEqual(client.list_projects()[0]["id"], "0-0")
        self.assertEqual(client.list_work_types()[0]["id"], "78-0")
        self.assertEqual(client.list_work_types()[0]["id"], "78-0")
        self.assertEqual(
            client.paginate_calls,
            [
                ("/admin/projects", {"fields": "id,name,shortName"}),
                ("/admin/timeTrackingSettings/workItemTypes", {"fields": "id,name"}),
            ],
        )

    def test_get_me_and_search_issues_use_expected_params(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                return {"path": path}

        client = StubClient()
        client.get_me()
        client.search_issues("#Unresolved", top=3, skip=4)

        self.assertEqual(
            client.calls,
            [
                ("GET", "/users/me", {"fields": "id,login,name,fullName"}, None),
                ("GET", "/issues", {"query": "#Unresolved", "$top": 3, "$skip": 4, "fields": module.DEFAULT_ISSUE_FIELDS}, None),
            ],
        )

    def test_apply_command_and_issue_mutation_methods_cover_branches(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def resolve_user(self, user_ref: str):
                self.calls.append(("resolve_user", user_ref))
                return {"id": "1-1", "login": user_ref}

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                self.calls.append(("get_issue", issue_ref, fields))
                if fields == "id,idReadable,summary":
                    return {"id": "2-1", "idReadable": "T-1", "summary": "Test"}
                return {"id": "2-1", "idReadable": "T-1"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                return {"ok": True}

        client = StubClient()
        client.apply_command("T-1", 'State "Open"')
        client.update_issue("T-1", summary="New", description="Desc", assignee="alice", state="Open", fields=["Priority=High"])
        client.delete_issue("T-1", mode="safe", safe_delete_command="Remove")
        client.delete_issue("T-1", mode="hard", confirm=True)

        request_paths = [(call[0], call[1]) for call in client.calls if isinstance(call, tuple) and len(call) == 4]
        self.assertIn(("POST", "/commands"), request_paths)
        self.assertIn(("POST", "/issues/2-1"), request_paths)
        self.assertIn(("DELETE", "/issues/2-1"), request_paths)

    def test_issue_mutations_resolve_assignee_refs_before_command_query(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def resolve_user(self, user_ref: str):
                self.calls.append(("resolve_user", user_ref))
                return {"id": "1-1", "login": "alice.login", "name": "Alice Example"}

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                self.calls.append(("get_issue", issue_ref, fields))
                if issue_ref == "T-2":
                    return {"id": "2-2", "idReadable": "T-2"}
                return {"id": "2-1", "idReadable": "T-1"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                if (method, path) == ("POST", "/issues"):
                    return {"id": "2-2", "idReadable": "T-2"}
                return {"ok": True}

        client = StubClient()
        client.create_issue(project_ref="T", summary="Created", assignee="Alice Example")
        client.update_issue("T-1", assignee="Alice Example")

        command_bodies = [
            body
            for call in client.calls
            if isinstance(call, tuple)
            and len(call) == 4
            and (call[0], call[1]) == ("POST", "/commands")
            and isinstance(call[3], dict)
            for body in [call[3]]
        ]
        self.assertEqual(
            [body["query"] for body in command_bodies],
            ["Assignee alice.login", "Assignee alice.login"],
        )

    def test_comment_methods_cover_all_comment_operations(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                return [{"id": "7-1"}] if method == "GET" else {"id": "7-1"}

        client = StubClient()
        client.list_comments("T-1", top=5, skip=2)
        client.create_comment("T-1", text="Hello")
        client.update_comment("T-1", "7-1", text="Updated")
        result = client.delete_comment("T-1", "7-1")

        self.assertEqual(result, {"deleted": True, "comment_id": "7-1"})
        self.assertEqual(
            [(method, path) for method, path, _params, _body in client.calls],
            [
                ("GET", "/issues/2-1/comments"),
                ("POST", "/issues/2-1/comments"),
                ("POST", "/issues/2-1/comments/7-1"),
                ("DELETE", "/issues/2-1/comments/7-1"),
            ],
        )

    def test_work_payload_and_work_methods_cover_all_operations(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1"}

            def resolve_user(self, user_ref: str):
                return {"id": "1-1", "login": user_ref}

            def resolve_work_type(self, work_type_ref: str):
                return {"id": "78-0", "name": work_type_ref}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {}), body))
                return [{"id": "w-1"}] if method == "GET" else {"id": "w-1"}

        client = StubClient()
        payload = client._build_work_payload(
            date_text="2026-03-31",
            duration="90",
            text="Implementation",
            author_ref="alice",
            type_ref="Development",
        )
        self.assertEqual(payload["author"], {"id": "1-1"})
        self.assertEqual(payload["type"], {"id": "78-0"})

        client.list_work("T-1", top=5, skip=2)
        client.create_work("T-1", date_text="2026-03-31", duration="90", text="Implementation", author_ref="alice", type_ref="Development")
        client.update_work("T-1", "w-1", date_text="2026-03-31", duration="2h", text="Updated", author_ref="alice", type_ref="Development")
        result = client.delete_work("T-1", "w-1")

        self.assertEqual(result, {"deleted": True, "work_id": "w-1"})
        self.assertEqual(
            [(method, path) for method, path, _params, _body in client.calls],
            [
                ("GET", "/issues/2-1/timeTracking/workItems"),
                ("POST", "/issues/2-1/timeTracking/workItems"),
                ("POST", "/issues/2-1/timeTracking/workItems/w-1"),
                ("DELETE", "/issues/2-1/timeTracking/workItems/w-1"),
            ],
        )

    def test_set_work_period_sets_exact_daily_minutes_for_target_dates(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.actions = []

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1", "idReadable": "T-1", "summary": "Task"}

            def resolve_user(self, user_ref: str):
                return {"id": "1-1", "login": user_ref}

            def resolve_work_type(self, work_type_ref: str):
                return {"id": "78-0", "name": work_type_ref}

            def _fetch_work_items_for_user(self, user, *, date_from, date_to, query=None):
                return [
                    {
                        "id": "w-1",
                        "date": module.date_to_epoch_ms("2026-03-03"),
                        "text": "Old",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 240, "presentation": "4h"},
                        "type": {"name": "Testing"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                    {
                        "id": "w-2",
                        "date": module.date_to_epoch_ms("2026-03-04"),
                        "text": "Old",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 480, "presentation": "8h"},
                        "type": {"name": "Testing"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                    {
                        "id": "w-3",
                        "date": module.date_to_epoch_ms("2026-03-04"),
                        "text": "Extra",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 60, "presentation": "1h"},
                        "type": {"name": "Development"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                    {
                        "id": "w-other",
                        "date": module.date_to_epoch_ms("2026-03-05"),
                        "text": "Ignore",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 480, "presentation": "8h"},
                        "type": {"name": "Development"},
                        "issue": {"id": "2-9", "idReadable": "T-9", "summary": "Other"},
                    },
                ]

            def create_work(self, issue_ref, *, date_text, duration, text=None, author_ref=None, type_ref=None):
                self.actions.append(("create", issue_ref, date_text, duration, text, author_ref, type_ref))
                return {"id": f"created-{date_text}"}

            def update_work(self, issue_ref, work_id, *, date_text=None, duration=None, text=None, author_ref=None, type_ref=None):
                self.actions.append(("update", issue_ref, work_id, date_text, duration, text, author_ref, type_ref))
                return {"id": work_id}

            def delete_work(self, issue_ref, work_id):
                self.actions.append(("delete", issue_ref, work_id))
                return {"deleted": True, "work_id": work_id}

        client = StubClient()
        result = client.set_work_period(
            "T-1",
            date_from="2026-03-03",
            date_to="2026-03-05",
            duration="8h",
            author_ref="alice",
            type_ref="Development",
            text="Focus",
            weekdays_only=True,
        )

        self.assertEqual(result["target_days"], 3)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["unchanged"], 0)
        self.assertEqual(
            client.actions,
            [
                ("update", "T-1", "w-1", "2026-03-03", "480", "Focus", "alice", "Development"),
                ("update", "T-1", "w-2", "2026-03-04", "480", "Focus", "alice", "Development"),
                ("delete", "T-1", "w-3"),
                ("create", "T-1", "2026-03-05", "8h", "Focus", "alice", "Development"),
            ],
        )

    def test_set_work_period_does_not_delete_duplicates_before_successful_update(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.actions = []

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1", "idReadable": "T-1", "summary": "Task"}

            def resolve_user(self, user_ref: str):
                return {"id": "1-1", "login": user_ref}

            def resolve_work_type(self, work_type_ref: str):
                return {"id": "78-0", "name": "Development"}

            def _fetch_work_items_for_user(self, user, *, date_from, date_to, query=None):
                return [
                    {
                        "id": "w-1",
                        "date": module.date_to_epoch_ms("2026-03-04"),
                        "text": "Old",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 240, "presentation": "4h"},
                        "type": {"id": "78-0", "name": "Development"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                    {
                        "id": "w-2",
                        "date": module.date_to_epoch_ms("2026-03-04"),
                        "text": "Extra",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 60, "presentation": "1h"},
                        "type": {"id": "78-0", "name": "Development"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                ]

            def update_work(self, issue_ref, work_id, *, date_text=None, duration=None, text=None, author_ref=None, type_ref=None):
                self.actions.append(("update", issue_ref, work_id, date_text, duration, text, author_ref, type_ref))
                raise module.ApiError("update failed")

            def delete_work(self, issue_ref, work_id):
                self.actions.append(("delete", issue_ref, work_id))
                return {"deleted": True, "work_id": work_id}

        client = StubClient()

        with self.assertRaisesRegex(module.ApiError, "update failed"):
            client.set_work_period(
                "T-1",
                date_from="2026-03-04",
                date_to="2026-03-04",
                duration="8h",
                author_ref="alice",
                type_ref="Development",
                text="Focus",
            )

        self.assertEqual(
            client.actions,
            [
                ("update", "T-1", "w-1", "2026-03-04", "480", "Focus", "alice", "Development"),
            ],
        )

    def test_set_work_period_treats_matching_type_id_as_unchanged(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.actions = []

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1", "idReadable": "T-1", "summary": "Task"}

            def resolve_user(self, user_ref: str):
                return {"id": "1-1", "login": user_ref}

            def resolve_work_type(self, work_type_ref: str):
                return {"id": "78-0", "name": "Development"}

            def _fetch_work_items_for_user(self, user, *, date_from, date_to, query=None):
                return [
                    {
                        "id": "w-1",
                        "date": module.date_to_epoch_ms("2026-03-04"),
                        "text": "Focus",
                        "author": {"login": user["login"]},
                        "duration": {"minutes": 480, "presentation": "8h"},
                        "type": {"id": "78-0", "name": "Development"},
                        "issue": {"id": "2-1", "idReadable": "T-1", "summary": "Task"},
                    },
                ]

            def update_work(self, issue_ref, work_id, *, date_text=None, duration=None, text=None, author_ref=None, type_ref=None):
                self.actions.append(("update", issue_ref, work_id, date_text, duration, text, author_ref, type_ref))
                return {"id": work_id}

        client = StubClient()
        result = client.set_work_period(
            "T-1",
            date_from="2026-03-04",
            date_to="2026-03-04",
            duration="8h",
            author_ref="alice",
            type_ref="78-0",
            text="Focus",
        )

        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(client.actions, [])

    def test_update_work_requires_at_least_one_change(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1"}

        client = StubClient(module.Config(base_url="https://example.test", token="secret"))
        with self.assertRaisesRegex(ValueError, "at least one field"):
            client.update_work("T-1", "w-1")

    def test_field_and_workflow_discovery_methods_cover_caches_and_errors(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.paginate_calls = []

            def resolve_project(self, project_ref: str):
                return {"id": "0-0", "shortName": "T"}

            def _paginate(self, path: str, *, params=None):
                self.paginate_calls.append((path, dict(params or {})))
                if path == "/admin/projects/0-0/customFields":
                    return [
                        {
                            "id": "98-3",
                            "field": {"id": "72-3", "name": "State"},
                            "bundle": {"id": "81-4", "$type": "StateBundle"},
                        },
                        {
                            "id": "98-9",
                            "field": {"id": "72-9", "name": "Text Field"},
                        },
                    ]
                if path == "/admin/customFieldSettings/bundles/state/81-4/values":
                    return [{"id": "1", "name": "Open", "isResolved": False}]
                if path == "/admin/workflows":
                    return [{"id": "77-7", "name": "comments", "title": "Comments"}]
                if path == "/admin/workflows/77-7/rules":
                    return [{"id": "86-29", "name": "no-comments", "title": "No Comments"}]
                raise AssertionError(path)

        client = StubClient()
        self.assertEqual(client.list_fields("T")[0]["id"], "98-3")
        self.assertEqual(client.list_fields("T")[0]["id"], "98-3")
        self.assertEqual(client.list_field_values("T", "State")[0]["name"], "Open")
        self.assertEqual(client.list_field_values("T", "State")[0]["name"], "Open")
        self.assertEqual(client.list_states("T")[0]["name"], "Open")
        with self.assertRaisesRegex(module.ApiError, "does not expose bundle-backed values"):
            client.list_field_values("T", "Text Field")
        self.assertEqual(client.list_workflows()[0]["id"], "77-7")
        self.assertEqual(client.list_workflows()[0]["id"], "77-7")
        self.assertEqual(client.list_workflow_rules("Comments")[0]["id"], "86-29")
        self.assertEqual(client.list_workflow_rules("Comments")[0]["id"], "86-29")
        with self.assertRaisesRegex(module.ApiError, "Workflow not found"):
            client.resolve_workflow("missing")

    def test_list_states_uses_state_bundle_not_hardcoded_field_name(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def resolve_project(self, project_ref: str):
                return {"id": "0-0", "shortName": "T"}

            def list_fields(self, project_ref: str):
                return [
                    {
                        "id": "98-3",
                        "field": {"id": "72-3", "name": "Workflow State"},
                        "bundle": {"id": "81-4", "$type": "StateBundle"},
                    },
                    {
                        "id": "98-9",
                        "field": {"id": "72-9", "name": "Priority"},
                        "bundle": {"id": "81-9", "$type": "EnumBundle"},
                    },
                ]

            def _paginate(self, path: str, *, params=None):
                if path == "/admin/customFieldSettings/bundles/state/81-4/values":
                    return [{"id": "1", "name": "Open", "isResolved": False}]
                raise AssertionError(path)

        client = StubClient(module.Config(base_url="https://example.test", token="secret"))

        self.assertEqual(client.list_states("T")[0]["name"], "Open")

    def test_list_states_raises_when_project_has_no_state_bundle(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def resolve_project(self, project_ref: str):
                return {"id": "0-0", "shortName": "T"}

            def list_fields(self, project_ref: str):
                return [
                    {
                        "id": "98-9",
                        "field": {"id": "72-9", "name": "Priority"},
                        "bundle": {"id": "81-9", "$type": "EnumBundle"},
                    }
                ]

        client = StubClient(module.Config(base_url="https://example.test", token="secret"))

        with self.assertRaisesRegex(module.ApiError, "State field not found in project T"):
            client.list_states("T")

    def test_report_period_uses_thread_pool_for_multiple_users(self):
        module = load_module()

        class FakeExecutor:
            last_max_workers = None

            def __init__(self, max_workers):
                FakeExecutor.last_max_workers = max_workers

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def map(self, func, iterable):
                return [func(item) for item in iterable]

        class StubClient(module.YouTrackClient):
            def resolve_user(self, user_ref: str):
                return {"id": f"id-{user_ref}", "login": user_ref}

            def _fetch_work_items_for_user(self, user, *, date_from, date_to, query=None):
                return [
                    {
                        "date": 1774915200000,
                        "duration": {"minutes": 30, "presentation": "30m"},
                        "author": {"login": user["login"]},
                        "issue": {"idReadable": f"{user['login']}-1", "summary": "Task"},
                    }
                ]

        client = StubClient(module.Config(base_url="https://example.test", token="secret"))
        original_executor = module.ThreadPoolExecutor
        try:
            module.ThreadPoolExecutor = FakeExecutor
            report = client.report_period(users=["alice", "bob"], date_from="2026-03-31", date_to="2026-03-31")
        finally:
            module.ThreadPoolExecutor = original_executor

        self.assertEqual(FakeExecutor.last_max_workers, 2)
        self.assertEqual(report["total_minutes"], 60)
        self.assertEqual(report["items"], 2)

    def test_validate_args_requires_issue_and_work_updates_to_change_something(self):
        module = load_module()
        parser = module.build_parser()

        issue_args = parser.parse_args(["issue", "update", "T-1"])
        work_args = parser.parse_args(["work", "update", "T-1", "w-1"])

        with self.assertRaisesRegex(ValueError, "issue update requires at least one change"):
            module.validate_args(issue_args)
        with self.assertRaisesRegex(ValueError, "work update requires at least one change"):
            module.validate_args(work_args)

    def test_main_dispatches_all_supported_resources(self):
        module = load_module()
        emitted = []

        class FakeClient:
            def __init__(self, config):
                self.config = config

            def create_issue(self, **kwargs): return {"method": "issue.create", "kwargs": kwargs}
            def get_issue(self, *args, **kwargs): return {"method": "issue.get"}
            def search_issues(self, *args, **kwargs): return {"method": "issue.search"}
            def update_issue(self, *args, **kwargs): return {"method": "issue.update"}
            def delete_issue(self, *args, **kwargs): return {"method": "issue.delete"}
            def list_comments(self, *args, **kwargs): return {"method": "comment.list"}
            def create_comment(self, *args, **kwargs): return {"method": "comment.create"}
            def update_comment(self, *args, **kwargs): return {"method": "comment.update"}
            def delete_comment(self, *args, **kwargs): return {"method": "comment.delete"}
            def list_work(self, *args, **kwargs): return {"method": "work.list"}
            def set_work_period(self, *args, **kwargs): return {"method": "work.set-period"}
            def create_work(self, *args, **kwargs): return {"method": "work.create"}
            def update_work(self, *args, **kwargs): return {"method": "work.update"}
            def delete_work(self, *args, **kwargs): return {"method": "work.delete"}
            def get_project(self, *args, **kwargs): return {"method": "project.get"}
            def list_projects(self, *args, **kwargs): return {"method": "project.list"}
            def get_me(self, *args, **kwargs): return {"method": "user.me"}
            def search_users(self, *args, **kwargs): return {"method": "user.search"}
            def list_work_types(self, *args, **kwargs): return {"method": "work-type.list"}
            def list_fields(self, *args, **kwargs): return {"method": "field.list"}
            def list_field_values(self, *args, **kwargs): return {"method": "field.values"}
            def list_states(self, *args, **kwargs): return {"method": "state.list"}
            def list_workflows(self, *args, **kwargs): return {"method": "workflow.list"}
            def list_workflow_rules(self, *args, **kwargs): return {"method": "workflow.rules"}
            def report_period(self, *args, **kwargs): return {"method": "report.period"}

        with mock.patch.object(module, "YouTrackClient", FakeClient), mock.patch.object(
            module, "emit_result", side_effect=lambda result, **kwargs: emitted.append((result, kwargs))
        ):
            env = {"YOUTRACK_BASE_URL": "https://example.test", "YOUTRACK_TOKEN": "perm-token"}
            cases = [
                (["issue", "create", "--project", "T", "--summary", "Test"], "issue.create"),
                (["issue", "get", "T-1"], "issue.get"),
                (["issue", "search", "--query", "#Unresolved"], "issue.search"),
                (["issue", "update", "T-1", "--summary", "New"], "issue.update"),
                (["issue", "delete", "T-1", "--mode", "hard", "--confirm"], "issue.delete"),
                (["comment", "list", "T-1"], "comment.list"),
                (["comment", "create", "T-1", "--text", "x"], "comment.create"),
                (["comment", "update", "T-1", "7-1", "--text", "x"], "comment.update"),
                (["comment", "delete", "T-1", "7-1"], "comment.delete"),
                (["work", "list", "T-1"], "work.list"),
                (
                    [
                        "work",
                        "set-period",
                        "T-1",
                        "--from",
                        "2026-03-01",
                        "--to",
                        "2026-03-31",
                        "--author",
                        "alice",
                        "--duration",
                        "8h",
                        "--weekdays-only",
                    ],
                    "work.set-period",
                ),
                (["work", "create", "T-1", "--date", "2026-03-31", "--duration", "30"], "work.create"),
                (["work", "update", "T-1", "w-1", "--text", "x"], "work.update"),
                (["work", "delete", "T-1", "w-1"], "work.delete"),
                (["project", "get", "T"], "project.get"),
                (["project", "list"], "project.list"),
                (["user", "me"], "user.me"),
                (["user", "search", "--query", "alice"], "user.search"),
                (["work-type", "list"], "work-type.list"),
                (["field", "list", "--project", "T"], "field.list"),
                (["field", "values", "--project", "T", "--field", "State"], "field.values"),
                (["state", "list", "--project", "T"], "state.list"),
                (["workflow", "list"], "workflow.list"),
                (["workflow", "rules", "comments"], "workflow.rules"),
                (["report", "period", "--from", "2026-03-31", "--to", "2026-03-31", "--user", "alice"], "report.period"),
            ]
            for argv, expected in cases:
                with self.subTest(argv=argv):
                    emitted.clear()
                    exit_code = module.main(argv, env=env)
                    self.assertEqual(exit_code, 0)
                    self.assertEqual(emitted[0][0]["method"], expected)

    def test_main_setup_and_invalid_dispatch_paths(self):
        module = load_module()

        emitted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = module.main(
                ["setup", "--url", "https://example.test", "--token", "perm-token"],
                env={"HOME": temp_dir},
            )
            self.assertEqual(exit_code, 0)
            saved = json.loads((Path(temp_dir) / ".config" / "youtrack" / "config.json").read_text())
            self.assertEqual(saved["base_url"], "https://example.test")

        class FakeParser:
            def __init__(self, namespace):
                self.namespace = namespace

            def parse_args(self, argv):
                return self.namespace

        invalid_namespaces = [
            argparse.Namespace(resource="issue", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="comment", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="work", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="project", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="user", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="work-type", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="field", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="state", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="workflow", action="unknown", format="json", pretty=False),
            argparse.Namespace(resource="other", action="unknown", format="json", pretty=False),
        ]

        for namespace in invalid_namespaces:
            with self.subTest(resource=namespace.resource):
                with mock.patch.object(module, "build_parser", return_value=FakeParser(namespace)), mock.patch.object(
                    module, "load_config", return_value=module.Config(base_url="https://example.test", token="perm-token")
                ), mock.patch.object(module, "YouTrackClient", return_value=object()):
                    with self.assertRaises(AssertionError):
                        module.main([])

    def test_run_script_as_main_covers_success_and_error_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            code, _stdout, stderr = run_script_as_main(
                ["setup", "--url", "https://example.test", "--token", "perm-token"],
                {"HOME": temp_dir},
            )
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")

        with tempfile.TemporaryDirectory() as temp_dir:
            code, _stdout, stderr = run_script_as_main(["user", "me"], {"HOME": temp_dir})
            self.assertEqual(code, 1)
            self.assertIn("Error:", stderr)

    def test_helper_edge_cases_cover_remaining_config_and_parsing_branches(self):
        module = load_module()

        self.assertEqual(
            module.get_config_path({"XDG_CONFIG_HOME": "/tmp/config-home"}),
            Path("/tmp/config-home") / "youtrack" / "config.json",
        )
        self.assertEqual(
            module.get_config_path({"HOME": "/tmp/home-dir"}),
            Path("/tmp/home-dir") / ".config" / "youtrack" / "config.json",
        )
        with mock.patch.object(module.Path, "home", return_value=Path("/tmp/fallback-home")):
            self.assertEqual(
                module.get_config_path({}),
                Path("/tmp/fallback-home") / ".config" / "youtrack" / "config.json",
            )

        with self.assertRaisesRegex(ValueError, "Empty command token"):
            module.quote_command_token("   ")
        self.assertEqual(
            module.build_command_query(safe_delete_command="Remove", state="Open"),
            'Remove State Open',
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            module.parse_duration_input("   ")
        with self.assertRaisesRegex(ValueError, "must be positive"):
            module.parse_duration_input("0")
        end_of_day = module.date_to_epoch_ms("2026-03-31", end_of_day=True)
        self.assertEqual(module.ms_to_iso_date(end_of_day), "2026-03-31")

    def test_renderers_and_grouped_aggregators_cover_remaining_output_branches(self):
        module = load_module()

        day_report = {
            "days": [
                {
                    "date": "2026-03-31",
                    "total_minutes": 45,
                    "total_presentation": "45m",
                    "issues": [
                        {"idReadable": "T-1", "summary": "Fix", "total_minutes": 45, "total_presentation": "45m"}
                    ],
                }
            ],
            "total_minutes": 45,
            "total_presentation": "45m",
        }
        self.assertIn("2026-03-31  45m", module.render_report_text(day_report))
        self.assertIn("TOTAL 45m", module.render_text(day_report))

        bucket_report = {
            "group_by": "user",
            "buckets": [
                {"key": "alice", "label": "alice", "total_minutes": 30, "total_presentation": "30m"},
                {"key": "bob", "label": "Bob Jones", "total_minutes": 60, "total_presentation": "1h"},
            ],
            "total_minutes": 90,
            "total_presentation": "1h 30m",
        }
        bucket_text = module.render_bucket_report_text(bucket_report)
        self.assertIn("alice: 30m", bucket_text)
        self.assertIn("bob Bob Jones: 1h", bucket_text)
        self.assertIn("TOTAL 1h 30m", module.render_text(bucket_report))
        self.assertEqual(module.render_generic_text("plain"), ["plain"])

        grouped_items = [
            {
                "date": module.date_to_epoch_ms("2026-03-31"),
                "duration": {"minutes": 30},
                "issue": {
                    "id": "2-1",
                    "idReadable": "T-1",
                    "summary": "Fix login",
                    "project": {"id": "0-0", "shortName": "T", "name": "Tazman"},
                },
                "author": {"id": "1-1", "login": "alice", "name": "Alice"},
                "type": {"id": "78-0", "name": "Development"},
            }
        ]
        self.assertEqual(module.aggregate_work_items_by(grouped_items, "issue")["buckets"][0]["idReadable"], "T-1")
        self.assertEqual(module.aggregate_work_items_by(grouped_items, "user")["buckets"][0]["login"], "alice")
        self.assertEqual(module.aggregate_work_items_by(grouped_items, "type")["buckets"][0]["name"], "Development")
        with self.assertRaisesRegex(ValueError, "Unsupported group_by"):
            module.aggregate_work_items_by(grouped_items, "invalid")

    def test_request_and_lookup_edge_paths_cover_remaining_branches(self):
        module = load_module()
        client = module.YouTrackClient(module.Config(base_url="https://example.test", token="secret"))
        original_urlopen = module.request.urlopen
        original_sleep = module.time.sleep

        class EmptyDecodable:
            def __bool__(self):
                return True

            def decode(self, _encoding):
                return ""

        self.assertIsNone(module.maybe_json(EmptyDecodable()))

        attempts = []

        class Response:
            status = 200

            def read(self):
                return b'{"ok":true}'

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(req, timeout=None):
            attempts.append(req.full_url)
            self.assertNotIn("drop=", req.full_url)
            if len(attempts) == 1:
                raise module.error.URLError("temporary dns")
            return Response()

        try:
            module.request.urlopen = fake_urlopen
            module.time.sleep = lambda *_args, **_kwargs: None
            result = client._request("GET", "/health", params={"drop": None, "keep": "yes"})
        finally:
            module.request.urlopen = original_urlopen
            module.time.sleep = original_sleep

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(attempts), 2)

        class EmptyPageClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = 0

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls += 1
                return []

        empty_client = EmptyPageClient()
        self.assertEqual(empty_client._paginate("/users"), [])
        self.assertEqual(empty_client.calls, 1)

        cached_client = module.YouTrackClient(module.Config(base_url="https://example.test", token="secret"))
        cached_client._project_cache["t"] = {"id": "0-0", "shortName": "T"}
        cached_client._user_cache["alice"] = {"id": "1-1", "login": "alice"}
        cached_client._work_type_cache["development"] = {"id": "78-0", "name": "Development"}
        self.assertEqual(cached_client.resolve_project("T")["id"], "0-0")
        self.assertEqual(cached_client.resolve_user("alice")["id"], "1-1")
        self.assertEqual(cached_client.resolve_work_type("Development")["id"], "78-0")

        class MissingLookupClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                return []

            def _paginate(self, path, *, params=None):
                return []

        missing_client = MissingLookupClient()
        with self.assertRaisesRegex(module.ApiError, "Project not found"):
            missing_client.resolve_project("T")
        with self.assertRaisesRegex(module.ApiError, "User not found"):
            missing_client.resolve_user("alice")
        with self.assertRaisesRegex(module.ApiError, "Work item type not found"):
            missing_client.resolve_work_type("Development")

        class IssueClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.calls = []

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                self.calls.append((method, path, dict(params or {})))
                return {"idReadable": "T-1"}

        issue_client = IssueClient()
        issue_client.get_issue("T-1")
        self.assertEqual(
            issue_client.calls[0],
            ("GET", "/issues/T-1", {"fields": module.DEFAULT_ISSUE_FIELDS}),
        )

        original_attempts = module.DEFAULT_GET_RETRY_ATTEMPTS
        try:
            module.DEFAULT_GET_RETRY_ATTEMPTS = 0
            with self.assertRaisesRegex(AssertionError, "Unreachable retry loop exit"):
                client._request("GET", "/health")
        finally:
            module.DEFAULT_GET_RETRY_ATTEMPTS = original_attempts

    def test_issue_delete_and_create_edge_paths_cover_remaining_branches(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def __init__(self):
                super().__init__(module.Config(base_url="https://example.test", token="secret"))
                self.created_payload = None

            def resolve_project(self, project_ref: str):
                return {"id": "0-0"}

            def _request(self, method, path, *, params=None, body=None, expected=(200,)):
                if (method, path) == ("POST", "/issues"):
                    self.created_payload = body
                    return {"id": "2-1", "idReadable": "T-1"}
                raise AssertionError(f"Unexpected request: {(method, path)}")

            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1", "idReadable": "T-1", "summary": "Created"}

        client = StubClient()
        result = client.create_issue(project_ref="T", summary="Test", description="Desc")
        self.assertEqual(result["idReadable"], "T-1")
        self.assertEqual(client.created_payload["description"], "Desc")

        class DeleteClient(module.YouTrackClient):
            def get_issue(self, issue_ref: str, *, fields=module.DEFAULT_ISSUE_FIELDS):
                return {"id": "2-1", "idReadable": "T-1", "summary": "Test"}

        delete_client = DeleteClient(module.Config(base_url="https://example.test", token="secret"))
        with self.assertRaisesRegex(ValueError, "Safe delete requires"):
            delete_client.delete_issue("T-1", mode="safe")
        with self.assertRaisesRegex(ValueError, "Hard delete requires"):
            delete_client.delete_issue("T-1", mode="hard", confirm=False)

    def test_field_and_workflow_resolution_fallback_branches_cover_remaining_paths(self):
        module = load_module()

        class FallbackFieldClient(module.YouTrackClient):
            def resolve_project(self, project_ref: str):
                return {"id": "0-0", "shortName": "T"}

            def list_fields(self, project_ref: str):
                return [
                    {
                        "id": "98-3",
                        "field": {"id": "72-3", "name": "State"},
                        "bundle": {"id": "81-4", "$type": "StateBundle"},
                    }
                ]

        field_client = FallbackFieldClient(module.Config(base_url="https://example.test", token="secret"))
        self.assertEqual(field_client.resolve_project_field("T", "State")["id"], "98-3")
        with self.assertRaisesRegex(module.ApiError, "Field not found in project T"):
            field_client.resolve_project_field("T", "Missing")

        class UnsupportedBundleClient(module.YouTrackClient):
            def resolve_project_field(self, project_ref: str, field_ref: str):
                return {
                    "id": "98-5",
                    "field": {"id": "72-5", "name": "Severity"},
                    "bundle": {"id": "81-9", "$type": "UnsupportedBundle"},
                }

        unsupported_client = UnsupportedBundleClient(module.Config(base_url="https://example.test", token="secret"))
        with self.assertRaisesRegex(module.ApiError, "Unsupported bundle type"):
            unsupported_client.list_field_values("T", "Severity")

        class FallbackWorkflowClient(module.YouTrackClient):
            def list_workflows(self):
                return [{"id": "77-7", "name": "comments", "title": "Comments"}]

        workflow_client = FallbackWorkflowClient(module.Config(base_url="https://example.test", token="secret"))
        self.assertEqual(workflow_client.resolve_workflow("Comments")["id"], "77-7")

    def test_get_project_delegates_to_resolve_project(self):
        module = load_module()

        class StubClient(module.YouTrackClient):
            def resolve_project(self, project_ref: str):
                return {"id": "0-0", "shortName": project_ref}

        client = StubClient(module.Config(base_url="https://example.test", token="secret"))
        self.assertEqual(client.get_project("T")["shortName"], "T")


if __name__ == "__main__":
    unittest.main()
