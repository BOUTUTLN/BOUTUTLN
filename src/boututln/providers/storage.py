"""Local raw data store (Parquet files, queried with DuckDB).

Files live under ``data/raw`` which is git-ignored: KB quotes may be used only
for the user's own investing and must not be redistributed (KB terms Art. 5(3)).
Prices are stored as decimal strings to stay exact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from boututln.domain.market import Market
from boututln.domain.money import Currency
from boututln.providers.base import Bar
from boututln.providers.validation import dedupe

_SCHEMA = pa.schema(
    [
        ("market", pa.string()),
        ("symbol", pa.string()),
        ("timeframe", pa.string()),
        ("bar_time", pa.string()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("currency", pa.string()),
        ("source", pa.string()),
        ("available_at", pa.string()),
        ("ingested_at", pa.string()),
    ]
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


class ParquetBarStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _file(self, market: Market, symbol: str, timeframe: str) -> Path:
        return self.root / "bars" / timeframe / market.value / f"{symbol}.parquet"

    def write(self, bars: list[Bar]) -> int:
        """Merge bars into per-symbol files, dropping duplicates. Returns rows written."""
        groups: dict[tuple[Market, str, str], list[Bar]] = {}
        for b in bars:
            groups.setdefault((b.market, b.symbol, b.timeframe), []).append(b)
        total = 0
        for (market, symbol, tf), items in groups.items():
            path = self._file(market, symbol, tf)
            merged = dedupe([*self.read(market, symbol, tf), *items])
            merged.sort(key=lambda b: (b.bar_time, b.source))
            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(
                [
                    {
                        "market": b.market.value,
                        "symbol": b.symbol,
                        "timeframe": b.timeframe,
                        "bar_time": _iso(b.bar_time),
                        "open": format(b.open, "f"),
                        "high": format(b.high, "f"),
                        "low": format(b.low, "f"),
                        "close": format(b.close, "f"),
                        "volume": format(b.volume, "f"),
                        "currency": b.currency.value,
                        "source": b.source,
                        "available_at": _iso(b.available_at),
                        "ingested_at": _iso(b.ingested_at),
                    }
                    for b in merged
                ],
                schema=_SCHEMA,
            )
            tmp = path.with_suffix(".tmp")
            pq.write_table(table, tmp)
            tmp.replace(path)  # atomic replace on Windows and POSIX
            total += len(merged)
        return total

    def read(self, market: Market, symbol: str, timeframe: str = "1d") -> list[Bar]:
        path = self._file(market, symbol, timeframe)
        if not path.exists():
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT * FROM read_parquet(?) ORDER BY bar_time", [str(path)]
            ).fetchall()
            cols = [d[0] for d in con.description]
        finally:
            con.close()
        out = []
        for r in rows:
            d = dict(zip(cols, r, strict=True))
            out.append(
                Bar(
                    market=Market(d["market"]),
                    symbol=d["symbol"],
                    timeframe=d["timeframe"],
                    bar_time=datetime.fromisoformat(d["bar_time"]),
                    open=Decimal(d["open"]),
                    high=Decimal(d["high"]),
                    low=Decimal(d["low"]),
                    close=Decimal(d["close"]),
                    volume=Decimal(d["volume"]),
                    currency=Currency(d["currency"]),
                    source=d["source"],
                    available_at=datetime.fromisoformat(d["available_at"]),
                    ingested_at=datetime.fromisoformat(d["ingested_at"]),
                )
            )
        return out
