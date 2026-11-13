# Data quality report

- Files processed: **72**
- Raw rows read: **783**
- Clean rows kept: **720**
- Rows quarantined: **3**

| Issue | Rows affected | Action |
|---|---:|---|
| Exact / near duplicates | 60 | dropped (kept first) |
| CPU stored as text (`45%`) | 22 | parsed to number |
| Status casing / whitespace | 84 | normalised to UP / DOWN |
| CPU outside 0-100 | 12 | set to null, excluded from averages |
| Uptime over 60 min/hour | 8 | clipped to 60 |
| Uptime missing | 10 | excluded from uptime % |
| Quarantined: `unknown_system` | 3 | moved to quarantine.csv |
