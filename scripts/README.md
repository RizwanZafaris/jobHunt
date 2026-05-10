# scripts/

Operational utilities for the jobHunt repo. Each script is self-contained
and runnable from the repo root.

## Inventory

| Script | Purpose | Deps |
|---|---|---|
| `check-prod-config.py` | Pre-deploy config-drift detector. Reads `.env.example` as the canonical list and verifies the running env satisfies every required var. | stdlib only |
| `validate_g2.py` | End-to-end smoke test that triggers a real G2 resume build via the deployed API and polls until completion. | `httpx` |

## `check-prod-config.py`

### What it does

1. Parses `.env.example` to get the canonical list of vars, their
   section (from `# === Section ===` headers), and tier (from
   `# tier: required|recommended|optional` markers).
2. For each var, checks the live environment (or a supplied
   `.env` file) for:
   - **Presence** — non-empty string.
   - **Placeholder** — common stub values (`CHANGE_ME`,
     `YOUR_KEY_HERE`, `REPLACE`, `TODO`, `<...>`, etc.).
   - **Format** — small validator dict (URLs start with `https://`,
     `OPENAI_API_KEY` starts with `sk-`, ports are 1-65535, etc.).
3. Prints a clean table: var name | section | tier | status | reason.
4. Exits non-zero if any required-tier var fails.

It **never echoes actual values** — only var names and static reason
strings. Safe to run in CI logs and on shared terminals.

### When to run

| Moment | Command |
|---|---|
| Before pushing a Railway deploy | `python scripts/check-prod-config.py` |
| Before booting the API locally  | `python scripts/check-prod-config.py --env-file .env` |
| In CI on `.env.example` changes | (handled by `.github/workflows/config-check.yml`) |
| Local smoke test of a prod env  | `python scripts/check-prod-config.py --env-file .env.production` |

### Flags

- `--env-file PATH` — load a `.env`-style file instead of the current
  process environment.
- `--ignore-optional` — only fail the run on `required`-tier issues;
  `recommended` and `optional` issues become warnings.
- `--env-example PATH` — point at a non-default canonical spec
  (default: `<repo>/.env.example`).

### Adding a new env var

1. Add it under the right `# === Section ===` header in `.env.example`.
2. Put a `# tier: required|recommended|optional` line on the line
   above it.
3. (If the var has a non-trivial format) add a validator entry to
   `EXACT_VALIDATORS` or one of the suffix/name override sets in
   `scripts/check-prod-config.py`.
4. Open the PR. The `config-check` workflow runs automatically.

## Pattern for adding new scripts

- **Stdlib-only by default.** If the script needs a third-party
  dependency, add it to `requirements.txt` (or `_pending_deps_*.txt`
  if you want to defer the install) and document the dep in the
  inventory table above.
- **Repo-root invocation.** Use `Path(__file__).resolve().parent.parent`
  to anchor file paths at the repo root, so the script works
  regardless of cwd.
- **Never log secrets.** No values, no tokens, no API keys — only
  names, statuses, and static reason strings.
- **Non-zero exit on failure.** Scripts called from CI or pre-deploy
  hooks must return 1 (or higher) when they detect a problem.
