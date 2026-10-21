# Visual design review (agent, Oct 21 2026)

Verdict: the base is solid: amber/black discipline, tabular numbers, numbered panels with function codes, GO key, F-keys. It reads like a toy rather than a pro terminal for five reasons: solid amber title strips everywhere, loose tables at 1440px, big empty areas, mixed time formats, and no heat or micro-charts.

## Prioritized fixes
1. HIGH: Panel headers are too heavy. Make them a dark bar with amber text: `.panel-head{background:#1a1206;color:var(--amber);border-bottom:1px solid var(--amber-dim)}`, `.meta` dim, `.code` amber chip with black text. Keep a solid amber strip only for one focus panel (`.panel.hot`, e.g. the Scan Board).
2. HIGH: Tables are sparse at wide widths. Use `table.fit{width:auto;min-width:60%}` for Short Book, Positions and Blotter, or let the last column absorb the slack.
3. HIGH: Page titles look like a blog. Turn them into a Bloomberg function-header row: fn badge plus the title in white at 14px/700, a one-line dim description at 11px, and red soft keys on the right (`95) Alpaca 96) Export 97) Settings`, #ff5a4f). Height about 28px.
4. HIGH: Date and time formats are mixed. Standardize: dates as MM/DD/YY, 24h times as 15:45, ages as `24M AGO`, and make `etTime` return `MM/DD HH:mm`.
5. MED: Lowercase values break the all-caps voice. Uppercase `.kv dd`; prose lines get 11px dim.
6. MED: An orphan grey stat tile shows at 500px. Give `.stats` a transparent background and put the borders on the children.
7. MED: The tape's ▲ glyph renders oversized. Set its size to 8px, and set the duration from the item count (`items.length*4s`).
8. LOW/MED: The tape's SHORT badge is noisy. Use an outline or a single `S` glyph, and mark the picked ticker white with an amber underline.
9. HIGH: The score has no heat coloring. Use `background: rgba(255,160,40,(score-30)/70*0.35)`, bold.
10. MED: The meter columns are confusing. Rename them MOVE/CRWD/FTD/STRY, and color the meter fill on a ramp: dim below 4/10, amber up to 7/10, yellow #ffd23f above.
11. MED: The flag tags are noisy. Use 3–4 letter codes with a title tooltip and a legend; set the tags at 9px.
12. MED: Rows are too tall (about 46px). Split MCAP and LAST into their own columns, keep flags inline, `td{padding:2px 6px}`, so all 20 rows fit in about 450px.
13. LOW: Excluded rows still look green. Dim them (#3d6b52); show `NO BORROW` in neg at 70% opacity.
14. MED: Empty states waste space. Use one 28px left-bordered line, and fill the leftover space with a System log panel.
15. MED: The F-keys look like web tabs. Make them flat key caps; `.on` gets an amber border and #1a1206 background. Hide the labels under 700px so the keys fit on one row.
16. MED: The command bar wraps the clock at 500px. Move `.cmd` to its own full-width row under 600px.
17. LOW: The GO key. Label it `<GO>`; use `background:#0f7b3a; box-shadow: inset 0 -2px 0 #064d22`, and give the brand chip a keycap inset too.
18. LOW: The panel meta wraps on phones. Use nowrap with an ellipsis.
19. MED: Sign discipline. Prefix every value with +/-; zero shows as dim 0.00; align consistently.
20. MED: The login is generic. Frame it as a terminal sign-on: a SIGN ON panel, staggered boot lines, a `<GO>` button, and no tape on /login.
21. LOW: The status bar should be sticky at the bottom with live fields (rotation countdown, last sync, positions, gross).
22. LOW: Blink only warn/bad pills; give ok a steady dot.

## Signature ideas
1. Function autocomplete dropdown in the command bar (↑/↓ to choose, Enter to GO). `TICKER <GO>` scrolls to the ticker's row and flashes it; `HELP <GO>` lists the commands.
2. Numbered menu actions: `2 <GO>` focuses panel 2, scrolling to it and flashing its header amber.
3. Tick flash on value changes: green #0f3d22 when up, red #4a1010 when down, fading over 700ms, via a `<Num>` client component.
4. A red TOP alert banner under the tape for failures, stale heartbeats and rejects, plus a System log panel.
5. A mini candle glyph per scan row, built from change and upper wick, an inline SVG about 10×24.
6. A rotation countdown, `T−13:18:42`, that turns yellow inside 15 minutes and blinks red in the last minute.
7. A security header strip on /shorting: one cell per position, white on navy #0b2a4a with yellow tickers.
8. Physical-key styling on the F-keys, GO and brand: `box-shadow: inset 0 1px 0 rgba(255,255,255,.25), inset 0 -2px 0 rgba(0,0,0,.4)`. No CRT scanlines.
