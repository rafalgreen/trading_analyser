# Agent instructions

Trading Analyser — scraper TradingView (Playwright/CDP), panel FastAPI, fundamenty yfinance. Szczegóły: [README.md](./README.md).

## Agent skills

Matt Pocock engineering skills (`/.agents/skills/`) read configuration from this section and `docs/agents/`.

### Issue tracker

GitHub Issues on `rafalgreen/trading_analyser` via `gh` CLI; external PRs are **not** a triage surface. See [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).

### Triage labels

Canonical five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) — 1:1 with GitHub label names. See [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md).

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/` at repo root (created lazily by `/grill-with-docs`). See [`docs/agents/domain.md`](docs/agents/domain.md).
