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

See [youtrack/SKILL.md](/Users/zhzh/Projects/zhd4n-projects/skills/youtrack/SKILL.md) for the full command surface and guardrails.
