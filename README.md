# BOUTUTLN AI Stock Trader (v0.5 Free Edition)

Local, zero-running-cost stock analysis and **paper trading** system for Korean and US
stocks (KB Securities Open API, separate KRW/USD books, local Ollama models).

- Live trading is **locked** in this build. Only `backtest` and `paper` modes exist.
- Design: [PLAN.md](PLAN.md) · P0 notes: [docs/p0-implementation.md](docs/p0-implementation.md)
- Windows setup: [docs/windows-local-setup.md](docs/windows-local-setup.md)

```bash
uv sync
```

```bash
uv run pytest
```
