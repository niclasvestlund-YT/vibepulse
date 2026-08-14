#!/usr/bin/env python3
"""Concept mockups at exact 480x480, matched to the real device rasters
in docs/img/. Palette and hero geometry come from
design/vibepulse/studio-design.json; layout proportions were read off
vibepulse-claude-week.png and vibepulse-codex-needs-you.png.

These are CONCEPT mockups for a brainstorm. Not Studio captures, not
approved designs, not physical-review evidence.
"""
import pathlib

BG      = "#000000"
TEXT    = "#FFFFFF"
MUTED   = "#9298A2"
DIM     = "#5C687B"
TRACK   = "#303238"
HAIR    = "#202328"
CLAUDE  = "#D97757"
CODEX   = "#6F78FF"
GO      = "#4FB286"
WARN    = "#FF9F2F"

FONT = "IBM Plex Sans,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif"
MONO = "IBM Plex Mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

W = H = 480
SAFE = 22
CW = 436


def head(title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{title}">'
        f'<title>{title}</title>'
        f'<rect width="{W}" height="{H}" fill="{BG}"/>'
        f'<g font-family="{FONT}" font-variant-numeric="tabular-nums">'
    )


TAIL = "</g></svg>"


def t(x, y, s, size=17, fill=TEXT, weight=400, ls=0, anchor="start", font=None):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" letter-spacing="{ls}" text-anchor="{anchor}"'
        + (f' font-family="{font}"' if font else "")
        + f'>{s}</text>'
    )


def dots(active=0, n=5, y=459):
    """Page indicator, as on the real device."""
    out = []
    cx = 240 - ((n - 1) * 11) / 2
    for i in range(n):
        if i == active:
            out.append(f'<rect x="{cx-9:.0f}" y="{y-3}" width="18" height="5" rx="2.5" fill="#C8CDD6"/>')
        else:
            out.append(f'<circle cx="{cx:.0f}" cy="{y}" r="2.5" fill="#2B3442"/>')
        cx += 11
    return "".join(out)


def logo(accent, glyph="■"):
    """The circular provider badge from the top-left of the hero."""
    return (
        f'<circle cx="38" cy="34" r="16" fill="none" stroke="{accent}" stroke-width="2.5"/>'
        f'<rect x="30" y="27" width="16" height="12" rx="2.5" fill="{accent}"/>'
        f'<circle cx="34.5" cy="31.5" r="1.7" fill="{BG}"/>'
        f'<circle cx="41.5" cy="31.5" r="1.7" fill="{BG}"/>'
        f'<rect x="33" y="35" width="10" height="1.8" rx=".9" fill="{BG}"/>'
    )


def hero_chrome(provider, accent, right_label):
    """Top row + hairline, exactly as the quota pages render it."""
    return (
        logo(accent)
        + t(62, 44, provider, 27, TEXT, 700, 2.2)
        + t(480 - SAFE, 42, right_label, 15, MUTED, 600, 1.6, "end")
        + f'<line x1="{SAFE}" y1="62" x2="{480-SAFE}" y2="62" stroke="{HAIR}" stroke-width="1"/>'
    )


def bar(y, segments, height=24):
    """Rounded progress bar: list of (fraction, colour) left to right."""
    out = [f'<rect x="{SAFE}" y="{y}" width="{CW}" height="{height}" rx="{height/2}" fill="{TRACK}"/>']
    out.append(f'<clipPath id="bc{y}"><rect x="{SAFE}" y="{y}" width="{CW}" height="{height}" rx="{height/2}"/></clipPath>')
    out.append(f'<g clip-path="url(#bc{y})">')
    x = SAFE
    for frac, col in segments:
        w = CW * frac
        out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{height}" fill="{col}"/>')
        x += w
    out.append("</g>")
    return "".join(out)


def stat_row(left_val, left_lab, right_val, right_lab,
             left_col=TEXT, right_col=TEXT, vy=378, ly=410):
    return (
        t(SAFE, vy, left_val, 38, left_col, 700, -0.6)
        + t(SAFE, ly, left_lab, 15, MUTED, 600, 1.6)
        + t(480 - SAFE, vy, right_val, 38, right_col, 700, -0.6, "end")
        + t(480 - SAFE, ly, right_lab, 15, MUTED, 600, 1.6, "end")
    )


# ---------------------------------------------------------------- 1. latency
def latency():
    s = head("Human-latency meter: agents waited 34 minutes on you today")
    s += hero_chrome("CLAUDE", CLAUDE, "2 CHATS ACTIVE")
    s += t(SAFE, 93, "BLOCKED ON YOU · TODAY", 23, MUTED, 500, 2.6)
    # hero number + unit, matching the 164px hero scale
    s += t(SAFE, 268, "34", 164, TEXT, 700, -6)
    s += t(SAFE + 196, 268, "min", 44, MUTED, 600, 0)
    # how that time was split between the two providers
    s += bar(304, [(0.62, CLAUDE), (0.23, CODEX)])
    s += stat_row("4m 12s", "BLOCKED RIGHT NOW", "11m", "LONGEST WAIT",
                  left_col=WARN)
    s += dots(2)
    return s + TAIL


# ------------------------------------------------------------- 2. approval
def approval():
    s = head("Approval prompt: allow npm test, with approve, deny and leave it")
    s += f'<rect x="14" y="14" width="452" height="452" rx="40" fill="none" stroke="{CLAUDE}" stroke-width="2"/>'
    s += t(240, 48, "CLAUDE · TORGET", 20, CLAUDE, 700, 3.4, "middle")
    s += t(240, 104, "ALLOW THIS?", 42, TEXT, 700, -0.5, "middle")
    s += t(240, 138, "Bash", 19, MUTED, 600, 1.8, "middle")
    # the command, in full, in mono - the thing you are actually deciding about
    s += f'<rect x="{SAFE}" y="160" width="{CW}" height="86" rx="10" fill="#0B0F15" stroke="{HAIR}"/>'
    s += t(240, 194, "npm test -- --runInBand", 20, TEXT, 500, 0, "middle", MONO)
    s += t(240, 224, "in ~/vibepulse", 16, DIM, 400, 0, "middle", MONO)
    # three buttons
    s += f'<rect x="{SAFE}" y="270" width="206" height="66" rx="12" fill="#0E1F19" stroke="{GO}" stroke-width="2"/>'
    s += t(125, 311, "APPROVE", 22, GO, 700, 2.2, "middle")
    s += f'<rect x="252" y="270" width="206" height="66" rx="12" fill="#1E1013" stroke="{CLAUDE}" stroke-width="2"/>'
    s += t(355, 311, "DENY", 22, CLAUDE, 700, 2.2, "middle")
    s += f'<rect x="{SAFE}" y="350" width="436" height="58" rx="12" fill="none" stroke="{TRACK}" stroke-width="2" stroke-dasharray="6 5"/>'
    s += t(240, 386, "LEAVE IT — ASK ME IN THE TERMINAL", 18, MUTED, 600, 1.8, "middle")
    # countdown: what happens if you do nothing
    s += t(240, 442, "118s LEFT · THEN THE TERMINAL ASKS", 16, DIM, 500, 1.6, "middle")
    return s + TAIL


# --------------------------------------------- 3. approval, cannot show it all
def approval_truncated():
    s = head("Approval prompt with the command too long to display, so approve is disabled")
    s += f'<rect x="14" y="14" width="452" height="452" rx="40" fill="none" stroke="{WARN}" stroke-width="2"/>'
    s += t(240, 48, "CLAUDE · TORGET", 20, WARN, 700, 3.4, "middle")
    s += t(240, 104, "CAN'T SHOW IT ALL", 38, TEXT, 700, -0.5, "middle")
    s += t(240, 138, "Bash", 19, MUTED, 600, 1.8, "middle")
    s += f'<rect x="{SAFE}" y="160" width="{CW}" height="86" rx="10" fill="#0B0F15" stroke="{WARN}" stroke-dasharray="5 4"/>'
    s += t(240, 192, "git push --force origin main &amp;&amp;", 19, TEXT, 500, 0, "middle", MONO)
    s += t(240, 220, "curl -s https://… | sh…", 19, DIM, 500, 0, "middle", MONO)
    # APPROVE is present but visibly dead: the honesty invariant, applied to a button
    s += f'<rect x="{SAFE}" y="270" width="206" height="66" rx="12" fill="none" stroke="{TRACK}" stroke-width="2" stroke-dasharray="6 5"/>'
    s += t(125, 305, "APPROVE", 22, "#3F4855", 700, 2.2, "middle")
    s += t(125, 326, "DISABLED", 13, "#3F4855", 600, 2, "middle")
    s += f'<rect x="252" y="270" width="206" height="66" rx="12" fill="#1E1013" stroke="{CLAUDE}" stroke-width="2"/>'
    s += t(355, 311, "DENY", 22, CLAUDE, 700, 2.2, "middle")
    s += f'<rect x="{SAFE}" y="350" width="436" height="58" rx="12" fill="none" stroke="{TRACK}" stroke-width="2" stroke-dasharray="6 5"/>'
    s += t(240, 386, "LEAVE IT — ASK ME IN THE TERMINAL", 18, MUTED, 600, 1.8, "middle")
    s += t(240, 442, "TOO LONG TO READ HERE", 16, WARN, 600, 1.6, "middle")
    return s + TAIL


# ------------------------------------------------------------ 4. panic stop
def panic():
    s = head("Panic stop engaged: everything pending denied and held")
    s += f'<rect x="14" y="14" width="452" height="452" rx="40" fill="none" stroke="{CLAUDE}" stroke-width="3"/>'
    s += t(240, 52, "PANIC STOP", 20, CLAUDE, 700, 4, "middle")
    # one dominant thing
    s += t(240, 168, "STOPPED", 78, TEXT, 700, -2, "middle")
    s += t(240, 214, "3 PENDING CALLS DENIED", 22, MUTED, 600, 2, "middle")
    s += f'<line x1="{SAFE+60}" y1="248" x2="{480-SAFE-60}" y2="248" stroke="{HAIR}" stroke-width="1"/>'
    # which agents it hit
    s += f'<circle cx="150" cy="292" r="7" fill="{CLAUDE}"/>'
    s += t(168, 299, "CLAUDE · 2", 24, TEXT, 600, 1.4)
    s += f'<circle cx="150" cy="332" r="7" fill="{CODEX}"/>'
    s += t(168, 339, "CODEX · 1", 24, TEXT, 600, 1.4)
    s += t(240, 396, "HOLDING — NOTHING GETS APPROVED", 19, WARN, 600, 1.6, "middle")
    s += t(240, 444, "LONG-PRESS KEY3 TO RELEASE", 16, DIM, 600, 1.8, "middle")
    return s + TAIL


# ----------------------------------------------------------- 5. review debt
def review_debt():
    s = head("Review debt: 1240 unreviewed lines, oldest three days")
    s += hero_chrome("CLAUDE", CLAUDE, "4 REPOS TOUCHED")
    s += t(SAFE, 93, "UNREVIEWED", 23, MUTED, 500, 2.6)
    s += t(SAFE, 262, "1 240", 128, TEXT, 700, -4)
    s += t(SAFE + 318, 262, "lines", 34, MUTED, 600, 0)
    # a fill level, not a percentage: it has no honest denominator
    s += bar(304, [(0.71, WARN)])
    s += stat_row("3 DAYS", "OLDEST UNREVIEWED", "62%", "AI-AUTHORED",
                  left_col=WARN)
    s += dots(3)
    return s + TAIL


# --------------------------------------------------------------- 6. go/no-go
def go_no_go():
    s = head("Go or no-go: short tasks only, two hours to reset")
    s += hero_chrome("CLAUDE", CLAUDE, "2 CHATS ACTIVE")
    s += t(SAFE, 93, "CAN I START SOMETHING BIG?", 23, MUTED, 500, 2.2)
    # the verdict replaces the number - same pixels, a decision instead of a datum
    s += t(SAFE, 192, "SHORT", 67, WARN, 700, -2)
    s += t(SAFE, 258, "TASKS ONLY", 67, WARN, 700, -2)
    s += bar(304, [(0.73, CLAUDE)])
    s += stat_row("73%", "FABLE WEEK USED", "2D 4H", "TO RESET")
    s += dots(1)
    return s + TAIL



# ============================================================ SCREENSHOT TEST
# Round two. The filter is no longer "is it non-redundant" but "would a vibe
# coder post this and would someone else think: I need that."

# ------------------------------------------------------------------ 7. value
def value_multiple():
    s = head("Value from your plan: 3.1 times, 312 dollars of API value on a 100 dollar plan")
    s += hero_chrome("CLAUDE", CLAUDE, "AUGUST")
    s += t(SAFE, 93, "VALUE FROM YOUR PLAN", 23, MUTED, 500, 2.6)
    s += t(SAFE, 268, "3.1", 164, TEXT, 700, -6)
    s += t(SAFE + 232, 268, "\u00d7", 88, GO, 700, 0)
    # bar with the break-even point marked: past the tick you are winning
    s += bar(304, [(0.32, TRACK), (0.68, GO)])
    s += f'<rect x="{SAFE + 0.32*CW - 1.5:.0f}" y="298" width="3" height="36" rx="1.5" fill="{TEXT}"/>'
    s += t(SAFE + 0.32*CW, 292, "BREAK EVEN", 12, MUTED, 600, 1.4, "middle")
    s += stat_row("$312", "AT LIST API PRICES", "$100", "YOU PAY", left_col=GO)
    s += dots(4)
    return s + TAIL


# ------------------------------------------------------------- 8. ai-authored
def ai_share():
    s = head("Agent-written today: 84 percent of shipped lines")
    s += hero_chrome("CLAUDE", CLAUDE, "3 REPOS")
    s += t(SAFE, 93, "AGENT-WRITTEN TODAY", 23, MUTED, 500, 2.6)
    s += t(SAFE, 268, "84", 164, TEXT, 700, -6)
    s += t(SAFE + 210, 268, "%", 88, CLAUDE, 700, 0)
    s += bar(304, [(0.84, CLAUDE)])
    s += stat_row("1 240", "LINES SHIPPED", "12", "COMMITS")
    s += dots(3)
    return s + TAIL


# ---------------------------------------------------------------- 9. now
def now_playing():
    s = head("Now playing: what both agents are doing right now")
    s += hero_chrome("LIVE", TEXT, "2 AGENTS")
    # agent one, the loud one
    s += f'<circle cx="{SAFE+7}" cy="120" r="7" fill="{CLAUDE}"/>'
    s += t(SAFE + 26, 127, "vibepulse", 40, TEXT, 700, -1)
    s += t(480 - SAFE, 127, "4m 12s", 30, MUTED, 600, 0, "end")
    s += t(SAFE + 26, 158, "EDITING  tokenserver.py", 19, CLAUDE, 600, 1.4)
    # a throughput trace: the device looks alive, not like a gauge
    pts = [(0,26),(18,12),(34,30),(52,6),(68,22),(86,14),(104,34),(122,10),
           (140,24),(158,4),(176,28),(194,16),(212,32),(230,8),(248,20),
           (266,12),(284,26),(302,6),(320,22),(338,16),(356,30),(374,10),
           (392,24),(410,14),(428,28)]
    d = " ".join(f"{'M' if i==0 else 'L'}{SAFE+x},{212-y}" for i,(x,y) in enumerate(pts))
    s += f'<path d="{d}" fill="none" stroke="{CLAUDE}" stroke-width="2.5" stroke-linejoin="round" opacity=".85"/>'
    s += t(SAFE, 238, "1 840 TOK/S", 15, MUTED, 600, 1.6)
    s += f'<line x1="{SAFE}" y1="262" x2="{480-SAFE}" y2="262" stroke="{HAIR}"/>'
    # agent two
    s += f'<circle cx="{SAFE+7}" cy="300" r="7" fill="{CODEX}"/>'
    s += t(SAFE + 26, 307, "torget", 40, TEXT, 700, -1)
    s += t(480 - SAFE, 307, "0m 48s", 30, MUTED, 600, 0, "end")
    s += t(SAFE + 26, 338, "SEARCHING  spec/", 19, CODEX, 600, 1.4)
    s += f'<line x1="{SAFE}" y1="370" x2="{480-SAFE}" y2="370" stroke="{HAIR}"/>'
    s += t(SAFE, 404, "NEITHER IS WAITING ON YOU", 17, GO, 600, 1.8)
    s += dots(0)
    return s + TAIL


# ------------------------------------------------------------ 10. odometer
def odometer():
    s = head("Lifetime odometer: 4218903 tokens since the device was plugged in")
    s += hero_chrome("VIBEPULSE", CLAUDE, "SINCE 14 JUNE")
    s += t(SAFE, 93, "TOKENS, ALL TIME", 23, MUTED, 500, 2.6)
    # an odometer wants every digit, so this is smaller than a hero percentage
    s += t(SAFE, 232, "4 218 903", 88, TEXT, 700, -3)
    # segmented run, one block per week, the way a physical counter reads
    x = SAFE
    for i, frac in enumerate([.4,.55,.7,.5,.85,.65,.95,.75,.6,.9,.8,1.0]):
        h = 6 + frac * 38
        col = CLAUDE if i % 3 else CODEX
        s += f'<rect x="{x:.0f}" y="{318-h:.0f}" width="26" height="{h:.0f}" rx="3" fill="{col}" opacity=".9"/>'
        x += 35
    s += t(SAFE, 372, "142", 38, TEXT, 700, -0.6)
    s += t(SAFE, 404, "SESSIONS", 15, MUTED, 600, 1.6)
    s += t(480 - SAFE, 372, "61 DAYS", 38, TEXT, 700, -0.6, "end")
    s += t(480 - SAFE, 404, "PLUGGED IN", 15, MUTED, 600, 1.6, "end")
    s += dots(4)
    return s + TAIL


MOCKUPS = {
    "latency-meter": latency,
    "approval-prompt": approval,
    "approval-truncated": approval_truncated,
    "panic-stop": panic,
    "review-debt": review_debt,
    "go-no-go": go_no_go,
    "value-multiple": value_multiple,
    "ai-share": ai_share,
    "now-playing": now_playing,
    "odometer": odometer,
}

if __name__ == "__main__":
    out = pathlib.Path("/home/user/vibepulse/docs/img/mockups")
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in MOCKUPS.items():
        svg = fn()
        (out / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"{name}.svg  {len(svg):>6} bytes")
