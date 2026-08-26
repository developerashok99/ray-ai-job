Run the JobPilot AI health monitor check right now.

This checks:
- Whether the pipeline process is currently running
- DB quality (senior/experience slippage in recent matches)
- Score distribution (is the AI being too generous?)
- Today's DB stats

Run using the Bash tool:

```bash
cd "c:/Users/ashok/Desktop/JobPilot_AI" && python -c "
import os, sys
os.chdir(r'c:/Users/ashok/Desktop/JobPilot_AI')
sys.path.insert(0, '.')
from monitor import run_monitor
result = run_monitor()
if result.get('quality_issues'):
    print(f'\n[!] {len(result[\"quality_issues\"])} quality issue(s) — check quality_issues.txt')
else:
    print('\n[OK] System healthy')
"
```

After running, summarize the results clearly:
- Is the pipeline running? When did it last scrape?
- Any quality issues found? (senior roles or 2+yr jobs slipping through)
- What's the high-score percentage? Is it suspicious?
- Overall health verdict.
