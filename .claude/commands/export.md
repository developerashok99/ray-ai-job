Re-export the JobPilot Excel sheet from the current database state.

This regenerates jobs_output.xlsx from all jobs currently in the DB with score >= 7.
Useful after running clean-db or after manual score edits.

Run using the Bash tool:

```bash
cd "c:/Users/ashok/Desktop/JobPilot_AI" && python -c "
import os, sys
os.chdir(r'c:/Users/ashok/Desktop/JobPilot_AI')
sys.path.insert(0, '.')
from storage.excel_export import export_to_excel
result = export_to_excel()
print('Export complete:', result)
"
```

After running, report:
- How many jobs were exported to each sheet (All Matches, Last 24hrs, Internship Friendly)
- The output file path
- Any errors that occurred
