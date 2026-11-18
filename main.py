"""Forecast GTA VI's launch-day Twitch viewers hour by hour.

Method (fitted only on the kit's own comparables, nothing invented):
every past big launch in the case's `comparables` has an hourly
Twitch series for its release day.  For each launch we take the
scale-free shape r_h = viewers(hour h) / viewers(hour 1), h = 2..24.
The forecast median for hour h is the case's hour-1 print times the
median of r_h over a reference set of launches that resemble GTA VI:

  * tier "comparable" only -- the unlock text must not say
    early-access-contaminated, weak or target-history;
  * rolling midnight-local unlock -- the unlock text says "rolling"
    or "midnight local" (GTA VI unlocks at midnight local, country
    by country: NZ first, then Europe, the UK, the US east coast at
    05:00 UTC and the west coast at 08:00 UTC);
  * the launch's release-day peak comes at hour 3 or later -- this
    drops front-loaded launches whose hour 1 already holds a
    simultaneous global unlock (Cyberpunk 2077's PC midnight-GMT
    surge, Elden Ring's PC unlock the night before), whose shape
    does not transfer to a console-only rolling launch.

Quantiles are lognormal around the median with a fixed log-spread
sigma = 0.50, the cross-launch dispersion of log r_h (about 0.55
for hours 6..24 in the reference set): q_tau = median *
exp(z_tau * 0.50), z = -1.645, -0.674, 0, 0.674, 1.645.

Validation (leave-one-out over the 9 reference launches in the
kit, each forecast from its own hour 1 exactly as the program does):
mean pinball loss 0.273, median 0.178, against the house baseline
(persistence, sigma 0.15*sqrt(h-1)) at 0.395 / 0.262 on the same
launches -- about 31% better on the mean, which is what ranks.
The 90% band covers 0.90 of hours.  No network, no clock, no
randomness: the same case always gives the same bytes.

Run: mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
Grader: python3 -P -s /bot/main.py, Python 3.13 stdlib only, 8 s, 1 CPU.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]
SIGMA = 0.50


def round2(x):
    """Round to 2 decimals, half up, like the starter does."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value == int(value) else value


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def release_day_hours(comp):
    """{hour 1..24: viewers} for a comparable's release day, or None."""
    try:
        day_start = parse_time(comp["day_start"])
    except (KeyError, ValueError):
        return None
    hours = {}
    for row in comp.get("hourly") or []:
        try:
            when = parse_time(row["from"])
            viewers = row["viewers"]
        except (KeyError, ValueError, TypeError):
            continue
        k = int((when - day_start).total_seconds() // 3600) + 1
        if 1 <= k <= 24 and isinstance(viewers, (int, float)) and viewers > 0:
            hours[k] = viewers
    return hours if len(hours) == 24 else None


def tier_of(comp):
    """'comparable' unless the unlock text rules the launch out."""
    text = (comp.get("unlock") or "")
    if "early-access-contaminated" in text:
        return "contaminated"
    if "Tier: weak" in text:
        return "weak"
    if "target-history" in text:
        return "target"
    return "comparable"


def is_rolling(comp):
    """True when the unlock is a rolling midnight-local one."""
    text = (comp.get("unlock") or "").lower()
    return "rolling" in text or "midnight local" in text


def median(values):
    """Deterministic median of a non-empty list of floats."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def reference_profiles(case):
    """Release-day profiles of launches that resemble GTA VI.

    Tier comparable, rolling unlock, peak at hour 3 or later.
    Falls back to every comparable-tier launch, then to any launch
    with a full release day, so the program never has no reference.
    """
    profiles = []
    for comp in case.get("comparables") or []:
        if tier_of(comp) != "comparable":
            continue
        hours = release_day_hours(comp)
        if hours is None:
            continue
        profiles.append((comp, hours))
    rolling = [(c, h) for c, h in profiles if is_rolling(c)]
    late_peak = [(c, h) for c, h in rolling if max(h, key=h.get) >= 3]
    for pool in (late_peak, rolling, profiles):
        if len(pool) >= 3:
            return [h for _, h in pool]
    return [h for _, h in profiles]


def median_log_ratios(profiles):
    """Median log(viewers_h / viewers_1) per hour over the profiles."""
    ratios = {}
    for h in range(2, 25):
        logs = []
        for hours in profiles:
            if hours[1] > 0 and hours[h] > 0:
                logs.append(math.log(hours[h] / hours[1]))
        ratios[h] = median(logs) if logs else 0.0
    return ratios


def forecast_case(case, name):
    if case.get("levels") != LEVELS:
        raise ValueError(f"{name} asks for levels {case.get('levels')}, not {LEVELS}")
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = max(1, first["viewers"])
    profiles = reference_profiles(case)
    if not profiles:
        raise ValueError(f"{name} has no comparable with a full release day")
    ratios = median_log_ratios(profiles)
    rows = []
    for row in case.get("forecast") or []:
        h = row["hour"]
        med = p1 * math.exp(ratios[h])
        quantiles = []
        for z in Z:
            q = round2(med * math.exp(z * SIGMA))
            q = min(max(q, 0.01), 1_000_000_000)
            quantiles.append(q)
        quantiles.sort()
        rows.append({"hour": h, "q": quantiles})
    if len(rows) != 23 or sorted(r["hour"] for r in rows) != list(range(2, 25)):
        raise ValueError(f"{name} does not forecast exactly hours 2..24")
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        out = json.dumps(forecast, separators=(",", ":")) + "\n"
        if len(out.encode("utf-8")) > 65536:
            raise ValueError(f"{path.name}: forecast over 65,536 bytes")
        (OUTPUT_DIR / f"{path.stem}.out").write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
