# Charts / data-viz review (agent, Oct 21 2026)

The demo draws no charts because snaps and trades are empty. Add simulated demo history, clearly labeled as such, so the charts can be checked.
Score weights are 35·move_volume + 20·crowding + 10·ftd + 35·catalyst, renormalized over the components present.

## Validated tokens (dark, surface #07090b)
- Green #3ddc84 and red #ff4d4d serve as status P&L colors, with position as the backup cue.
- Amber #ffa028 is a documented exception to the lightness band.
- Score components in fixed order: move #3987e5, crowd #d55181, ftd #9085e9, story #199e70. Don't place loss-red marks inside this chart.
- Strategy overlay: at most 3 series (#ffa028, #3987e5, #d55181). Use small multiples beyond that.
- Heatmap ramps:
  - Green #1b5e39, #26884f, #31b36a, #3ddc84
  - Red #7f2222, #ab2d2d, #d83c3c, #ff4d4d
  - Neutral #1a2026
- Amber ramp #5f4214, #99621a, #d0821f, #ffa028. Context gray #5b636b.
- New CSS tokens: `--grid:#1a2026; --rule-strong:#3a424a; --ctx:#5b636b; --c1:#3987e5; --c2:#d55181; --c3:#9085e9; --c4:#199e70; --pos-fill:rgba(61,220,132,.10); --neg-fill:rgba(255,77,77,.10)`

## Fixes to the existing charts
- **Gridlines and baseline:** no dashes. Grid uses --grid; the baseline is solid --rule-strong with a direct label ("START $10,000").
- **Tick labels:** replace money() with fmtTick(v, step). Use decimals when the step is below 1, compact $10.4K for large values, and key ticks by index.
- **Accessibility:** the hit rect gets tabIndex, arrow/Home/End/Escape keys and focus handling. Add a hidden table view for every chart.
- **useWidth:** start at null (or use useLayoutEffect) so the chart doesn't jump when it renders.
- **Shared helpers:** move them into components/chart-kit.ts (useWidth, niceTicks, fmtTick, Tip, keyboard hook).
- **EquityChart:**
  - Area fill: remove it, or fill relative to the baseline (pos-fill above, neg-fill below, via clipPath).
  - Add an end value label and always label the last date.
  - Tooltip shows value, then date, day P&L and % vs start. Clamp it on both sides and flip it below when near the top.
  - Crosshair uses --rule-strong.
- **PnlBars:**
  - Round only the data end of each bar (path).
  - Widths: bw = min(24, band − gap), with gap = 2 when band ≥ 6, otherwise 0.
  - A null day draws no bar and its tooltip says "no sync". Drop or mark the first day, since it may include the deposit.
  - Draw the zero line solid, after the bars.
  - Label only the best and worst days.
  - Tooltip shows P&L, then date, running total and % of the prior day's equity.
  - Share the hover index with EquityChart for a linked crosshair.

## New charts
Shared types:
```ts
type DatePt = { date: string; value: number };
interface ChartBase { height?: number; ariaLabel: string; hoverIndex?: number | null; onHover?: (i: number | null) => void }
```
1. **DrawdownChart** (underwater). dd = equity / running max − 1. Draw a 2px --neg line with neg-fill and a 0% rule at the top. Label the maximum ("MAX −8.4% · Oct 3"). A `compact` mode gives a 40–60px strip.
   `interface DrawdownProps extends ChartBase { equity: DatePt[]; compact?: boolean }`
2. **TradeReturnHistogram.** 1% bins with ±15% clamp bins on the ends and a bin edge at 0. Bins are colored neg or pos. AVG and MED lines are labeled. Tooltip shows count, range and tickers. With fewer than 5 trades, fall back to a strip plot.
   `interface TradeHistProps extends ChartBase { trades: { id: string; ticker: string; pnl_pct: number }[]; binPct?: number; clampPct?: number }`
3. **ExposureBars.** Two horizontal bar columns aligned on rows: % of equity in amber with a target tick, and unrealized $ as diverging bars. Total gross % sits in the header.
   `interface ExposureProps extends ChartBase { equity: number; rows: { ticker: string; marketValue: number; targetPct: number | null; unrealizedPl: number; unrealizedPlpc: number }[] }`
4. **Sparkline**, 72×18, for stat tiles and table cells. Draw a --ctx line with an amber end dot and a `<title>`.
   `interface SparklineProps { values: number[]; width?: number; height?: number; baseline?: number | 'first'; ariaLabel: string }`
5. **ScoreContributionBar.** A stacked bar on a 0–100 scale whose segments sum to short_score, replacing the 4 meters. It has an inline 120px cell mode and a panel-board mode with a CUT line at the 5th pick. Legend "MOVE 35 · CROWD 20 · FTD 10 · STORY 35". Segment tooltips show points and raw × weight.
   `interface ScoreBarProps { components: Partial<Record<CompKey, number | null>>; score: number; weights: Record<CompKey, number>; width?: number; dimmed?: boolean; picked?: boolean }`
6. **StrategySmallMultiples** (Options). One panel per strategy on a shared y-scale, indexed to 100, with the benchmark as a --ctx line and a compact drawdown strip under each panel. Panels are sortable and share a synced crosshair.
7. **RiskReturnScatter** (Options). x = |max DD|, y = CAGR, optional Sharpe as point size. The selected point is amber; label it and the Pareto front. Nearest-point hover; click to select.
8. **MonthlyReturnsHeatmap.** Rows are years, columns Jan–Dec plus a YR column. Diverging ramps with bins at 0.25/0.5/1.5/3/5%. Each cell shows its signed % as text.
9. **ScanFunnel.** Scanned → shortable → no real catalyst → picked, using the amber ramp, with drop reasons and the dropped tickers in the tooltip.

Build order: chart-kit fixes and demo data, then ScoreContributionBar, then Drawdown and Exposure, then Histogram and Heatmap, then the Options charts once the backtester emits results.
