"""Optional: parse the official KB sample responses if the user placed them locally.

Download the spec JSON from the KB portal and copy e.g. ``IVU10140.json`` into
``tests/local_kb_samples/`` (git-ignored). Skipped when the folder is empty.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from boututln.brokers.kb import models as m
from boututln.brokers.kb.envelope import unwrap

SAMPLES = Path(__file__).parent / "local_kb_samples"

PARSERS: dict[str, Callable[[dict[str, Any]], object]] = {
    "IVU10140": lambda b: m.Quote.parse_domestic("005930", b),
    "GSS10030": lambda b: m.Quote.parse_overseas("NVDA", b),
    "SSQM0004": m.DomesticDeposit.parse,
    "SSQM1801": m.DomesticHoldings.parse,
    "SSQM2952": m.DomesticBalance.parse,
    "SKQM3350": m.OverseasCash.parse,
    "SPQM2226": m.OverseasBalance.parse,
    "IVA60190": m.FxRates.parse,
}


@pytest.mark.parametrize("code", sorted(PARSERS))
def test_official_sample_parses(code: str) -> None:
    f = SAMPLES / f"{code}.json"
    if not f.exists():
        pytest.skip("official KB sample not present locally")
    data = json.loads(f.read_text(encoding="utf-8-sig"))
    body = unwrap(200, data["output"])
    assert PARSERS[code](body) is not None
