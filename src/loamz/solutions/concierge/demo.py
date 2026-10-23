"""A believable folder of personal and work files, so the Concierge can be tried before pointing it at yours."""

from __future__ import annotations

import os
import time
from pathlib import Path

FILES = {
    "notes/northwind-call.md": "# Northwind call, 14 Oct\nPriya confirmed the indemnity cap is $2M. We agreed to countersign by end of day Friday. Legal files at 5pm.\n",
    "notes/ideas.md": "# Ideas\n- a weekly letter to investors\n- automate the mail digest\n- try paper trading the momentum idea\n",
    "notes/hiring.txt": "Hiring: two analyst roles. Mark from the recruiting firm sent an intro. Decision after the board sync on Thursday.\n",
    "finance/positions.csv": "symbol,qty,avg_price\nAAPL,40,181.20\nMSFT,15,402.10\nNVDA,12,118.50\n",
    "finance/q3-expenses.csv": "month,category,amount\nJul,cloud,4210\nAug,cloud,4580\nSep,cloud,4390\nSep,legal,12000\n",
    "finance/q3-letter-draft.md": "# Q3 letter (draft)\nPerformance was driven by two positions. Risk section needs Dana's rewrite of section 3.\n",
    "contracts/northwind-summary.txt": "Northwind LLC master agreement. Term two years. Indemnity cap $2M pending legal confirmation. Payment net 30.\n",
    "contracts/northwind-signed.pdf": "%PDF-1.4 (pretend)",
    "contracts/scan-0042.png": "png",
    "reports/fund-dashboard.html": "<html><body><h1>Fund dashboard</h1><p>NAV up 2.3% in October. Cash 18%.</p></body></html>",
    "config/settings.json": '{"timezone": "Europe/Lisbon", "currency": "USD"}',
}


def make_demo_folder(root: Path) -> Path:
    now = time.time()
    for n, (name, content) in enumerate(FILES.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        os.utime(path, (now - n * 86400, now - n * 86400))
    return root
