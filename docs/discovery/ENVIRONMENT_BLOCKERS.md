# Environment blockers

**Date:** 2026-09-13

| Check | Result |
| --- | --- |
| Python | 3.12.10 `VERIFIED` |
| Ableton registry | empty |
| Ableton installation candidates | missing at the time of this historical report |
| `%APPDATA%\Ableton` | missing |
| Ableton process | not running |
| TCP 9877 | closed |
| Remote Script installed | no — installer blocked until Live prefs exist |

```
BLOCKED_BY_ENVIRONMENT
Missing:
Ableton Live installation
```

Exact first command after installation:

```
python -m copilot.cli detect
```
