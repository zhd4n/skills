# skills

Public Codex skills published from `zhd4n`.

## Available Skills

- `youtrack` - Manage YouTrack issues, comments, work items, custom fields, workflows, and time reports over the official REST API

## Install

Install directly from GitHub:

```bash
npx skills add https://github.com/zhd4n/skills --skill youtrack
```

Skill page:

- [skills.sh/zhd4n/skills/youtrack](https://skills.sh/zhd4n/skills/youtrack)

## Quick Setup

The YouTrack skill expects either saved credentials or environment variables:

```bash
python3 scripts/youtrack_api.py setup \
  --url "https://example.youtrack.cloud" \
  --token "perm:..."
```

Or:

```bash
export YOUTRACK_BASE_URL="https://example.youtrack.cloud"
export YOUTRACK_TOKEN="perm:..."
```

Saved setup uses `~/.config/youtrack/config.json`.

## What The YouTrack Skill Covers

- issue create, get, search, update, and delete
- comment create, update, list, and delete
- work item create, update, list, delete, and `set-period`
- project, user, work type, field, state, and workflow discovery
- user-based period reports over `/api/workItems`

## Agent Examples

### Codex

After installing the skill, ask Codex directly:

```text
Use $youtrack to list my unresolved issues assigned to me.
Use $youtrack to log 90 minutes on T-123 for today with text "Implementation".
Use $youtrack to report jane.doe's work from 2026-03-01 to 2026-03-31 grouped by issue.
```

### Claude Code

With the skill installed in Claude Code, invoke it directly:

```text
/youtrack issue search --query "#Unresolved assignee: me" --top 10
/youtrack work create T-123 --date 2026-03-31 --duration "90" --text "Implementation" --author jane.doe
/youtrack report period --from 2026-03-01 --to 2026-03-31 --user jane.doe --group-by issue
```

Claude Code can also auto-discover the skill from natural-language prompts when the task matches the skill description.

## CLI Examples

Create and update an issue:

```bash
python3 scripts/youtrack_api.py issue create \
  --project T \
  --summary "Investigate API regression" \
  --assignee jane.doe \
  --state "In Progress"

python3 scripts/youtrack_api.py issue update T-123 \
  --summary "Investigate API timeout regression" \
  --field "Priority=Critical"
```

Search issues and inspect workflow data:

```bash
python3 scripts/youtrack_api.py issue search --query "#Unresolved assignee: me" --top 10
python3 scripts/youtrack_api.py state list --project T --pretty
```

Track time on an issue:

```bash
python3 scripts/youtrack_api.py work create T-123 \
  --date 2026-03-31 \
  --duration "90" \
  --text "Implementation" \
  --author jane.doe
```

Generate a period report:

```bash
python3 scripts/youtrack_api.py report period \
  --from 2026-03-01 \
  --to 2026-03-31 \
  --user jane.doe \
  --group-by issue
```

See `youtrack/SKILL.md` for the full command surface and guardrails.
