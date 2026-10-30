// ICT 2024 Mentorship — Lectures 1–5 (6 models) with LucidFlex account rules
// NinjaTrader 8 strategy. Use a 1-minute chart with the "CME US Index Futures ETH" trading hours template.
// Order names: L1 / L2 / L3A / L3P / L4 / L5 (same as the TradingView version).
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Text;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public enum Ict24Account { Flex25KEval, Flex25KFunded, Flex50KEval, Flex50KFunded, Flex100KEval, Flex100KFunded, Flex150KEval, Flex150KFunded, Custom }
    public enum Ict24EntryMode { FirstPdArrayTouched, OrderBlock, FairValueGap, Breaker, ScaleInAll }
    public enum Ict24Stop1 { Post830HighLow, RaidExtreme }
    public enum Ict24Tp { NextLiquidity, FixedRR }
    public enum Ict24TpL2 { FibMinus2And2p5Staged, NextLiquidity, FixedRR }
    public enum Ict24TpPm { LegExtreme, MinusHalfExtension, NextLiquidity }
    public enum Ict24TpL5 { InitialLiquidityThenPool, NextLiquidity, FixedRR }
    public enum Ict24Quiet { Off, NoMorningTrade, NoNewsSpike, Both }
    public enum Ict24Bias { GapSideSetsDirection, PriorDayClose }
    public enum Ict24News { AutoDetectSpike, EveryDay }

    public class ICT2024LucidFlex : Strategy
    {
        #region Internal types
        private class PendingOrder
        {
            public string Signal;
            public int Dir;
            public int Qty;
            public double Price;
            public double Stop;
            public double Target;
            public bool Filled;
        }

        private class Model
        {
            public int Id;
            public int State;          // 0 scanning, 1 liquidity taken (waiting MSS), 2 armed, 3 in trade
            public int Dir;
            public double X = double.NaN;
            public int XBar;
            public double MLvl = double.NaN;
            public int MBar;
            public double Stop = double.NaN;
            public double TpA = double.NaN;
            public double TpB = double.NaN;
            public int Setups;
            public string Ev = "Waiting";
            public List<PendingOrder> Orders = new List<PendingOrder>();

            public Model(int id) { Id = id; }

            public void Rst(string ev)
            {
                State = 0;
                Ev = ev;
                Orders.Clear();
            }
        }
        #endregion

        #region Fields
        private TimeZoneInfo est;
        private Model M1, M2, M3, M4, M5, M6;
        private Model[] allModels;
        private int lastOwner;
        private DateTime prevOpen = DateTime.MinValue;
        private int tagN;

        // swings (current bar)
        private double ph = double.NaN, pl = double.NaN;
        private DateTime pivT;
        private int pivB;
        private double lastPH = double.NaN, lastPL = double.NaN;
        private int lastPHBar, lastPLBar;

        // liquidity pools
        private readonly List<double> p7H = new List<double>(), p7L = new List<double>();
        private readonly List<double> p8H = new List<double>(), p8L = new List<double>();
        private readonly List<double> p12H = new List<double>(), p12L = new List<double>();
        private readonly List<double> p18H = new List<double>(), p18L = new List<double>();
        private double e7SH = double.NaN, e7SL = double.NaN, e8SH = double.NaN, e8SL = double.NaN;
        private double e12SH = double.NaN, e12SL = double.NaN, e18SH = double.NaN, e18SL = double.NaN;
        private int e7SHB, e7SLB, e8SHB, e8SLB, e12SHB, e12SLB, e18SHB, e18SLB;

        // gaps
        private readonly List<double> gapHi = new List<double>(), gapLo = new List<double>();
        private readonly List<string> gapTags = new List<string>();
        private double nwogH = double.NaN, nwogL = double.NaN, l5GapH = double.NaN, l5GapL = double.NaN;

        // sessions
        private double lonH = double.NaN, lonL = double.NaN, rthH = double.NaN, rthL = double.NaN;
        private double prevDayH = double.NaN, prevDayL = double.NaN, prevDayC = double.NaN;
        private double lunchH = double.NaN, lunchL = double.NaN, ethH = double.NaN, ethL = double.NaN, pdMid = double.NaN;
        private bool newsDay;

        // model helpers
        private double open07 = double.NaN, p0700H = double.NaN, p0700L = double.NaN, p830H = double.NaN, p830L = double.NaN;
        private double legS = double.NaN, legE = double.NaN, pmOte = double.NaN;
        private double brk5 = double.NaN, fvg5 = double.NaN, leg5 = double.NaN, l4Entry = double.NaN;
        private double ibsl = double.NaN, issl = double.NaN, p19H = double.NaN, p19L = double.NaN;
        private double legS6 = double.NaN, legE6 = double.NaN, l5Ote = double.NaN;
        private bool lonTouched, amTraded;

        // Lucid tracking (profit relative to the account's starting balance)
        private double dayStartP, maxDayP, eodHighP, tierP, rtBase;
        private bool acctDead, dayLocked, evalPassed, inRealtime;
        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = @"ICT 2024 Mentorship Lectures 1-5 (6 models) with LucidFlex account rules. 1-minute chart, ETH trading hours.";
                Name = "ICT2024LucidFlex";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 3;
                EntryHandling = EntryHandling.UniqueEntries;
                IsExitOnSessionCloseStrategy = false;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.Infinite;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 2;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 40;
                IsInstantiatedOnEachOptimizationIteration = true;

                LucidAccount = Ict24Account.Flex50KEval;
                SimulateMll = true;
                StopAtPass = true;
                DailyStopUsd = 0;
                LucidFlatHour = 16;
                LucidFlatMinute = 40;
                StartingProfit = 0;
                HighestEodProfit = 0;
                LargestDayProfit = 0;

                RiskUsd = 250;
                MaxQty = 20;
                StopBuf = 2.0;
                SizeBuf = 1.0;
                PivLen = 2;
                EqTol = 0;
                Lookback = 10;
                DayFlatHour = 15;
                DayFlatMinute = 55;
                GapTargets = true;
                DrawObjects = true;

                UseL1 = true;
                L1CutHour = 11;
                L1EntryMode = Ict24EntryMode.FirstPdArrayTouched;
                L1StopMode = Ict24Stop1.Post830HighLow;
                L1Tp = Ict24Tp.NextLiquidity;
                L1RR = 2.0;
                L1MinRR = 1.0;

                UseL2 = true;
                L2CutHour = 10;
                NeedLonRetrace = true;
                First30 = false;
                BrkFallback = true;
                L2Tp = Ict24TpL2.FibMinus2And2p5Staged;
                L2RR = 2.0;
                L2MinRR = 1.0;

                UseL3A = true;
                KeepNdog = 4;
                UseNwog = true;
                L3CutHour = 10;
                L3Bias = Ict24Bias.GapSideSetsDirection;
                L3Tp = Ict24Tp.NextLiquidity;
                L3RR = 2.0;
                L3MinRR = 1.0;

                UseL3P = true;
                PmQuiet = Ict24Quiet.NoMorningTrade;
                OteFib = 0.705;
                L3PTp = Ict24TpPm.LegExtreme;

                UseL4 = true;
                NewsMode = Ict24News.AutoDetectSpike;
                NewsMult = 2.5;
                L4CutHour = 11;
                APlusOnly = false;
                PdFilter = true;
                L4Bias = Ict24Bias.GapSideSetsDirection;
                L4Tp = Ict24Tp.NextLiquidity;
                L4RR = 2.0;
                L4MinRR = 1.0;

                UseL5 = true;
                NeedInit = true;
                L5Fib = 0.705;
                L5Tp = Ict24TpL5.InitialLiquidityThenPool;
                L5RR = 2.0;
                L5MinRR = 1.0;
                L5ExitHour = 0;
            }
            else if (State == State.DataLoaded)
            {
                est = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
                M1 = new Model(1);
                M2 = new Model(2);
                M3 = new Model(3);
                M4 = new Model(4);
                M5 = new Model(5);
                M6 = new Model(6);
                allModels = new Model[] { M1, M2, M3, M4, M5, M6 };
            }
            else if (State == State.Realtime)
            {
                // Live: Lucid tracking restarts from the values you enter for the real account
                inRealtime = true;
                rtBase = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
                dayStartP = StartingProfit;
                maxDayP = LargestDayProfit;
                eodHighP = Math.Max(HighestEodProfit, StartingProfit);
                tierP = StartingProfit;
                acctDead = false;
                dayLocked = false;
                evalPassed = false;
            }
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution == null || execution.Order == null)
                return;
            if (execution.Order.OrderState != OrderState.Filled && execution.Order.OrderState != OrderState.PartFilled)
                return;
            OrderAction a = execution.Order.OrderAction;
            if (a != OrderAction.Buy && a != OrderAction.SellShort)
                return;
            string nm = execution.Order.Name;
            int id = OwnerOf(nm);
            if (id == 0)
                return;
            lastOwner = id;
            Model md = allModels[id - 1];
            foreach (PendingOrder o in md.Orders)
                if (o.Signal == nm)
                    o.Filled = true;
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            DateTime bo = Et(Time[0]).AddMinutes(-BarsPeriod.Value);   // bar OPEN time (New York)
            if (CurrentBar < Math.Max(40, 2 * PivLen + 3))
            {
                prevOpen = bo;
                return;
            }
            DateTime bc = Et(Time[0]);                                   // bar CLOSE time (New York)
            DateTime day = bo.Date;
            DayOfWeek dow = bo.DayOfWeek;
            bool isWeekday = dow >= DayOfWeek.Monday && dow <= DayOfWeek.Friday;

            DateTime t0700 = day.AddHours(7);
            DateTime tCut2 = day.AddHours(L2CutHour);
            DateTime tCut3 = day.AddHours(L3CutHour);
            DateTime tEq = day.AddHours(8);
            DateTime tRaid = day.AddHours(8).AddMinutes(30);
            DateTime tCut = day.AddHours(L1CutHour);
            DateTime tCut4 = day.AddHours(L4CutHour);
            DateTime t1200 = day.AddHours(12);
            DateTime t1330 = day.AddHours(13).AddMinutes(30);
            DateTime t1430 = day.AddHours(14).AddMinutes(30);
            DateTime tFlat = day.AddHours(DayFlatHour).AddMinutes(DayFlatMinute);
            DateTime tLonS = day.AddHours(2);
            DateTime tLonE = day.AddHours(5);
            DateTime tRthS = day.AddHours(9).AddMinutes(30);
            DateTime tRthE = day.AddHours(16);
            DateTime tEth = day.AddHours(18);
            DateTime t1900 = day.AddHours(19);
            DateTime t2100 = day.AddHours(21);
            DateTime tLuFlat = day.AddHours(LucidFlatHour).AddMinutes(LucidFlatMinute);
            DateTime tP7E = tCut2;
            if (tCut3 > tP7E) tP7E = tCut3;
            if (tCut4 > tP7E) tP7E = tCut4;

            bool is0700 = Cross(t0700, bo);
            bool isL1Start = Cross(tEq, bo);
            bool is830 = Cross(tRaid, bo);
            bool is1200 = Cross(t1200, bo);
            bool is1330 = Cross(t1330, bo);
            bool is1900 = Cross(t1900, bo);
            bool lonStart = Cross(tLonS, bo);
            bool rthStart = Cross(tRthS, bo);
            bool isSessOpen = Cross(tEth, bo);
            bool rthEndBar = bo < tRthE && bc >= tRthE;

            // ═════════ SESSION LIQUIDITY ═════════
            if (lonStart) { lonH = High[0]; lonL = Low[0]; }
            else if (bo >= tLonS && bo < tLonE && Ok(lonH)) { lonH = Math.Max(lonH, High[0]); lonL = Math.Min(lonL, Low[0]); }

            if (rthStart) { rthH = High[0]; rthL = Low[0]; }
            else if (bo >= tRthS && bo < tRthE && Ok(rthH)) { rthH = Math.Max(rthH, High[0]); rthL = Math.Min(rthL, Low[0]); }
            if (rthEndBar && Ok(rthH)) { prevDayH = rthH; prevDayL = rthL; prevDayC = Close[0]; }

            if (is1200) { lunchH = High[0]; lunchL = Low[0]; }
            else if (bo >= t1200 && bo < t1330 && Ok(lunchH)) { lunchH = Math.Max(lunchH, High[0]); lunchL = Math.Min(lunchL, Low[0]); }

            if (isSessOpen) { ethH = High[0]; ethL = Low[0]; }
            else if (Ok(ethH) && (bo < tRaid || bo >= tEth)) { ethH = Math.Max(ethH, High[0]); ethL = Math.Min(ethL, Low[0]); }
            if (is830 && Ok(ethH)) pdMid = (ethH + ethL) / 2;

            // 8:30 news spike detector
            if (is830)
            {
                double avg = 0;
                for (int i = 1; i <= 30; i++) avg += High[i] - Low[i];
                avg /= 30.0;
                newsDay = (High[0] - Low[0]) >= NewsMult * avg;
                if (newsDay && DrawObjects && (UseL4 || PmQuiet == Ict24Quiet.NoNewsSpike || PmQuiet == Ict24Quiet.Both))
                    Draw.Text(this, Tag("news"), "8:30 news spike", 0, High[0] + 10 * TickSize, Brushes.Gray);
            }

            // ═════════ NDOG / NWOG ═════════
            if (isSessOpen)
            {
                double gH = Math.Max(Close[1], Open[0]);
                double gL = Math.Min(Close[1], Open[0]);
                l5GapH = gH;
                l5GapL = gL;
                if (dow == DayOfWeek.Sunday)
                {
                    nwogH = gH;
                    nwogL = gL;
                    if (DrawObjects)
                    {
                        RemoveGapDrawings("NWOG");
                        DrawGap("NWOG", gH, gL, Brushes.Fuchsia);
                    }
                }
                else
                {
                    gapHi.Insert(0, gH);
                    gapLo.Insert(0, gL);
                    string tg = Tag("NDOG");
                    gapTags.Insert(0, tg);
                    if (DrawObjects) DrawGap(tg, gH, gL, Brushes.Gold);
                    while (gapHi.Count > KeepNdog)
                    {
                        int last = gapHi.Count - 1;
                        gapHi.RemoveAt(last);
                        gapLo.RemoveAt(last);
                        if (DrawObjects) RemoveGapDrawings(gapTags[last]);
                        gapTags.RemoveAt(last);
                    }
                }
            }

            // ═════════ SWINGS ═════════
            ph = double.NaN;
            pl = double.NaN;
            bool isPh = true, isPl = true;
            double cH = High[PivLen], cL = Low[PivLen];
            for (int i = 1; i <= PivLen; i++)
            {
                if (!(cH > High[PivLen + i] && cH > High[PivLen - i])) isPh = false;
                if (!(cL < Low[PivLen + i] && cL < Low[PivLen - i])) isPl = false;
            }
            if (isPh) ph = cH;
            if (isPl) pl = cL;
            pivT = Et(Time[PivLen]).AddMinutes(-BarsPeriod.Value);
            pivB = CurrentBar - PivLen;
            if (Ok(ph)) { lastPH = ph; lastPHBar = pivB; }
            if (Ok(pl)) { lastPL = pl; lastPLBar = pivB; }

            // ═════════ OWNER ═════════
            int owner = Position.MarketPosition == MarketPosition.Flat ? 0 : lastOwner;
            if (owner == 1 || owner == 2 || owner == 3 || owner == 5) amTraded = true;

            // ═════════ LUCID ACCOUNT RULES ═════════
            double cum = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
            double closedP = inRealtime ? StartingProfit + (cum - rtBase) : cum;
            double openP = Position.MarketPosition == MarketPosition.Flat ? 0 : Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, Close[0]);
            double equityP = closedP + openP;
            if (isSessOpen)
            {
                maxDayP = Math.Max(maxDayP, closedP - dayStartP);
                eodHighP = Math.Max(eodHighP, closedP);
                tierP = closedP;
                dayStartP = closedP;
                dayLocked = false;
            }
            double floorP = Math.Min(eodHighP - MllUsd(), 100.0);
            double dayNowP = equityP - dayStartP;
            double largestDay = Math.Max(maxDayP, closedP - dayStartP);
            if (!IsCustom() && SimulateMll && !acctDead && equityP <= floorP)
            {
                acctDead = true;
                if (DrawObjects) Draw.Text(this, Tag("dead"), "Max Loss Limit reached - trading stopped", 0, High[0] + 14 * TickSize, Brushes.Red);
            }
            if (!IsCustom() && IsEval() && !evalPassed && closedP >= TargetUsd() && largestDay <= 0.5 * closedP)
            {
                evalPassed = true;
                if (DrawObjects) Draw.Text(this, Tag("pass"), "Evaluation passed", 0, High[0] + 14 * TickSize, Brushes.LimeGreen);
            }
            if (DailyStopUsd > 0 && !dayLocked && dayNowP <= -DailyStopUsd) dayLocked = true;
            bool inLuClose = !IsCustom() && bc >= tLuFlat && bo < tEth;
            bool gateOk = !acctDead && !dayLocked && !(evalPassed && StopAtPass) && !inLuClose;
            int capQty = CapQty();
            string guardWhy = acctDead ? "Max Loss Limit" : dayLocked ? "Daily loss stop" : inLuClose ? "Lucid 4:45 PM close-out" : "Evaluation passed";

            if (!gateOk && (Position.MarketPosition != MarketPosition.Flat || AnyActive()))
            {
                FlattenAll("Lucid guard");
                foreach (Model md in allModels) md.Rst("Guard: " + guardWhy);
                l4Entry = double.NaN;
            }

            // ═════════ 7:00 AM RESET — Lectures 2, 3 AM, 4 ═════════
            if (is0700)
            {
                p7H.Clear(); p7L.Clear();
                e7SH = double.NaN; e7SL = double.NaN;
                open07 = Open[0];
                lonTouched = false;
                p0700H = High[0]; p0700L = Low[0];
                amTraded = false;
                newsDay = false;
                M2.Setups = 0; M3.Setups = 0; M5.Setups = 0;
                if (owner != 2) M2.Rst("Scanning from 7:00");
                if (owner != 3) M3.Rst("Watching NDOG / NWOG from 7:00");
                if (owner != 5) M5.Rst("Waiting for 8:30 release");
            }
            else if (bo >= t0700 && bo < tFlat && Ok(p0700H))
            {
                p0700H = Math.Max(p0700H, High[0]);
                p0700L = Math.Min(p0700L, Low[0]);
            }
            if (bo >= t0700 && bo < tCut2 && Ok(lonH) && High[0] >= lonL && Low[0] <= lonH) lonTouched = true;

            bool drawP7 = DrawObjects && (UseL2 || UseL3A || UseL4);
            PoolH(p7H, ref e7SH, ref e7SHB, t0700, tP7E, drawP7);
            PoolL(p7L, ref e7SL, ref e7SLB, t0700, tP7E, drawP7);
            bool sw7H = Sweep(p7H, true);
            bool sw7L = Sweep(p7L, false);

            double tapLo, tapHi;
            int tapDir = Tap(out tapLo, out tapHi);

            // ═════════ LECTURE 2 — 7:00 AM LIQUIDITY HUNT + IFVG ═════════
            bool in30 = bo.Minute < 30 && bo.Hour >= 7 && bo.Hour <= 9;
            bool raidWin2 = UseL2 && isWeekday && bo >= t0700 && bo < tCut2 && M2.Setups < 1 && (!First30 || in30) && (!NeedLonRetrace || lonTouched);
            if (raidWin2 && M2.State <= 1)
            {
                if (sw7H && !sw7L && Ok(lastPL)) { StartRaid(M2, -1, "L2 hunt"); M2.Ev = "REH hunted -> waiting bearish MSS"; }
                else if (sw7L && !sw7H && Ok(lastPH)) { StartRaid(M2, 1, "L2 hunt"); M2.Ev = "REL hunted -> waiting bullish MSS"; }
            }
            bool mss2 = false;
            if (M2.State == 1)
            {
                mss2 = TrackMss(M2);
                if (!mss2 && bo >= tCut2) M2.Rst("No MSS before cutoff");
            }
            if (mss2)
            {
                M2.Setups++;
                Mark("L2 MSS", M2.Dir, Brushes.DodgerBlue);
                if (Busy(2, owner, gateOk)) M2.Rst("MSS skipped - another model active or Lucid guard");
                else
                {
                    int dr = M2.Dir;
                    M2.Stop = dr < 0 ? M2.X + StopBuf : M2.X - StopBuf;
                    int kX = CurrentBar - M2.XBar, kS = CurrentBar - M2.MBar;
                    double e2 = double.NaN;
                    string id2 = "";
                    double itop, ibot; int ibar;
                    FindIfvg(dr, kX, kS, out itop, out ibot, out ibar);
                    bool inverted = Ok(itop) && (dr < 0 ? Close[0] < ibot : Close[0] > itop);
                    double ce = Ok(itop) ? Rt((itop + ibot) / 2) : double.NaN;
                    if (inverted && (dr < 0 ? (ce > Close[0] && ce < M2.X) : (ce < Close[0] && ce > M2.X)))
                    {
                        e2 = ce; id2 = "L2 IFVG";
                        DrawZone(ibar, itop, ibot, Brushes.DodgerBlue, "IFVG");
                    }
                    else if (BrkFallback)
                    {
                        double be, bf; int bb;
                        FindBreaker(dr, kS, out be, out bf, out bb);
                        if (Ok(be) && (dr < 0 ? (be > Close[0] && be < M2.X) : (be < Close[0] && be > M2.X)))
                        {
                            e2 = be; id2 = "L2 BRK";
                            DrawZone(bb, Math.Max(be, bf), Math.Min(be, bf), Brushes.Orange, "Breaker");
                        }
                    }
                    if (!Ok(e2)) M2.Rst("MSS but no IFVG / breaker");
                    else
                    {
                        int n2 = Qty(e2, M2.Stop, RiskUsd, capQty);
                        if (n2 < 1) M2.Rst("Stop too wide for $ risk");
                        else
                        {
                            double rng = dr < 0 ? M2.X - open07 : open07 - M2.X;
                            bool useFib = L2Tp == Ict24TpL2.FibMinus2And2p5Staged && Ok(open07) && rng > 0;
                            double fibA = useFib ? Rt(open07 + dr * 2.0 * rng) : double.NaN;
                            double fibB = useFib ? Rt(open07 + dr * 2.5 * rng) : double.NaN;
                            bool fibOk = useFib && (dr < 0 ? fibA < e2 : fibA > e2);
                            M2.Orders.Clear();
                            if (fibOk && n2 >= 2)
                            {
                                int q1 = n2 / 2;
                                AddOrder(M2, id2 + " a", dr, q1, e2, M2.Stop, fibA);
                                AddOrder(M2, id2 + " b", dr, n2 - q1, e2, M2.Stop, fibB);
                                M2.TpA = fibA; M2.TpB = fibB;
                            }
                            else
                            {
                                double tgt = fibOk ? fibA : Target(e2, M2.Stop, dr, p7H, p7L, L2Tp == Ict24TpL2.NextLiquidity, L2RR, L2MinRR);
                                AddOrder(M2, id2, dr, n2, e2, M2.Stop, tgt);
                                M2.TpA = tgt; M2.TpB = double.NaN;
                            }
                            M2.State = 2;
                            M2.Ev = "MSS -> limit at " + (id2 == "L2 IFVG" ? "IFVG CE (50%)" : "breaker");
                        }
                    }
                }
            }
            if (M2.State == 2 && !mss2)
            {
                if (owner == 2) { M2.State = 3; M2.Ev = "Entry filled"; }
                else if (owner != 0) M2.Rst("Cancelled - another model filled first");
                else if ((M2.Dir < 0 && High[0] >= M2.X) || (M2.Dir > 0 && Low[0] <= M2.X)) M2.Rst("Price ran the hunt extreme before entry");
                else if (bo >= tCut2) M2.Rst("No retrace before cutoff");
            }
            else if (M2.State == 3 && owner != 2) M2.Rst("Trade closed");

            // ═════════ LECTURE 4 — NEWS DAY (priority after 8:30) ═════════
            bool newsWin5 = UseL4 && isWeekday && bo >= tRaid && bo < tCut4 && M5.Setups < 1 && (NewsMode == Ict24News.EveryDay || newsDay);
            if (newsWin5 && M5.State <= 1 && tapDir != 0)
            {
                double gapMid = (tapLo + tapHi) / 2;
                bool pdOk = !PdFilter || !Ok(pdMid) || (tapDir < 0 ? gapMid >= pdMid : gapMid <= pdMid);
                bool biasOk5 = L4Bias == Ict24Bias.GapSideSetsDirection || !Ok(prevDayC) || (tapDir < 0 ? Close[0] < prevDayC : Close[0] > prevDayC);
                bool swingOk5 = tapDir < 0 ? Ok(lastPL) : Ok(lastPH);
                if (pdOk && biasOk5 && swingOk5)
                {
                    StartRaid(M5, tapDir, "L4 gap draw");
                    M5.Ev = tapDir < 0 ? "Drew into gap in premium -> waiting bearish MSS" : "Drew into gap in discount -> waiting bullish MSS";
                }
            }
            bool mss5 = false;
            if (M5.State == 1)
            {
                bool raw5 = TrackMss(M5);
                mss5 = raw5 && (!APlusOnly || bo >= tRthS);
                if (!mss5 && bo >= tCut4) M5.Rst("No MSS before cutoff");
            }
            if (mss5)
            {
                M5.Setups++;
                Mark(bo >= tRthS ? "L4 MSS A+" : "L4 MSS", M5.Dir, Brushes.Magenta);
                if (Busy(5, owner, gateOk)) M5.Rst("MSS skipped - another model active or Lucid guard");
                else
                {
                    int dr = M5.Dir;
                    M5.Stop = dr < 0 ? M5.X + StopBuf : M5.X - StopBuf;
                    int kX = CurrentBar - M5.XBar, kS = CurrentBar - M5.MBar;
                    double be, bf, fe, ff; int bb, fb;
                    FindBreaker(dr, kS, out be, out bf, out bb);
                    FindFvgLeg(dr, kX, out fe, out ff, out fb);
                    brk5 = be;
                    fvg5 = fe;
                    double le = dr < 0 ? Low[0] : High[0];
                    for (int k = 0; k <= kX && k < CurrentBar; k++)
                        le = dr < 0 ? Math.Min(le, Low[k]) : Math.Max(le, High[k]);
                    leg5 = le;
                    if (Ok(be)) DrawZone(bb, Math.Max(be, bf), Math.Min(be, bf), Brushes.Orange, "Breaker");
                    if (Ok(fe)) DrawZone(fb, Math.Max(fe, ff), Math.Min(fe, ff), Brushes.Teal, dr < 0 ? "SIBI" : "BISI");
                    if (!Ok(brk5) && !Ok(fvg5)) M5.Rst("MSS but no breaker / FVG");
                    else { M5.State = 2; M5.Ev = "MSS -> waiting for retrace past 50% into breaker / FVG"; }
                }
            }
            if (M5.State == 2)
            {
                if (owner == 5) { M5.State = 3; M5.Ev = "Entry filled"; }
                else if (owner != 0) { M5.Rst("Cancelled - another model filled first"); l4Entry = double.NaN; }
                else
                {
                    int dr = M5.Dir;
                    leg5 = dr < 0 ? Math.Min(leg5, Low[0]) : Math.Max(leg5, High[0]);
                    double mid5 = (M5.X + leg5) / 2;
                    double best5 = double.NaN;
                    foreach (double c in new double[] { brk5, fvg5 })
                        if (Ok(c) && (dr < 0 ? (c >= mid5 && c > Close[0] && c < M5.X) : (c <= mid5 && c < Close[0] && c > M5.X)))
                            best5 = !Ok(best5) ? c : (dr < 0 ? Math.Min(best5, c) : Math.Max(best5, c));
                    M5.Orders.Clear();
                    if ((dr < 0 && High[0] >= M5.X) || (dr > 0 && Low[0] <= M5.X)) { M5.Rst("Price ran the pre-MSS extreme before entry"); l4Entry = double.NaN; }
                    else if (bo >= tCut4) { M5.Rst("No retrace before cutoff"); l4Entry = double.NaN; }
                    else if (!Ok(best5)) l4Entry = double.NaN;
                    else
                    {
                        int n5 = Qty(best5, M5.Stop, RiskUsd, capQty);
                        if (n5 >= 1)
                        {
                            double tgt5 = Target(best5, M5.Stop, dr, p7H, p7L, L4Tp == Ict24Tp.NextLiquidity, L4RR, L4MinRR);
                            AddOrder(M5, "L4 PD", dr, n5, best5, M5.Stop, tgt5);
                            M5.TpA = tgt5;
                            l4Entry = best5;
                        }
                    }
                }
            }
            else if (M5.State == 3 && owner != 5) { M5.Rst("Trade closed"); l4Entry = double.NaN; }

            // ═════════ LECTURE 3 AM — NDOG / NWOG TAP ═════════
            bool tapWin3 = UseL3A && isWeekday && bo >= t0700 && bo < tCut3 && M3.Setups < 1;
            if (tapWin3 && M3.State <= 1 && tapDir != 0)
            {
                bool biasOk = L3Bias == Ict24Bias.GapSideSetsDirection || !Ok(prevDayC) || (tapDir < 0 ? Close[0] < prevDayC : Close[0] > prevDayC);
                bool swingOk = tapDir < 0 ? Ok(lastPL) : Ok(lastPH);
                if (biasOk && swingOk)
                {
                    StartRaid(M3, tapDir, "L3 gap tap");
                    M3.Ev = tapDir < 0 ? "Gap above tapped -> waiting bearish MSS" : "Gap below tapped -> waiting bullish MSS";
                }
            }
            bool mss3 = false;
            if (M3.State == 1)
            {
                mss3 = TrackMss(M3);
                if (!mss3 && bo >= tCut3) M3.Rst("No MSS before cutoff");
            }
            if (mss3)
            {
                M3.Setups++;
                Mark("L3 MSS", M3.Dir, Brushes.Gold);
                if (Busy(3, owner, gateOk)) M3.Rst("MSS skipped - another model active or Lucid guard");
                else
                {
                    int dr = M3.Dir;
                    double rawStop3 = dr < 0 ? Math.Max(p0700H, M3.X) : Math.Min(p0700L, M3.X);
                    M3.Stop = dr < 0 ? rawStop3 + StopBuf : rawStop3 - StopBuf;
                    int kX = CurrentBar - M3.XBar, kS = CurrentBar - M3.MBar;
                    double it3, ib3, be3, bf3; int ibar3, bb3;
                    FindIfvg(dr, kX, kS, out it3, out ib3, out ibar3);
                    FindBreaker(dr, kS, out be3, out bf3, out bb3);
                    double ce3 = Ok(it3) ? Rt((it3 + ib3) / 2) : double.NaN;
                    bool inv3 = Ok(it3) && (dr < 0 ? Close[0] < ib3 : Close[0] > it3);
                    bool okI = inv3 && (dr < 0 ? (ce3 > Close[0] && ce3 < rawStop3) : (ce3 < Close[0] && ce3 > rawStop3));
                    bool okB = Ok(be3) && (dr < 0 ? (be3 > Close[0] && be3 < rawStop3) : (be3 < Close[0] && be3 > rawStop3));
                    double e3 = double.NaN;
                    string id3 = "";
                    if (okI && okB)
                    {
                        bool pickI = dr < 0 ? ce3 <= be3 : ce3 >= be3;
                        e3 = pickI ? ce3 : be3;
                        id3 = pickI ? "L3A IFVG" : "L3A BRK";
                    }
                    else if (okI) { e3 = ce3; id3 = "L3A IFVG"; }
                    else if (okB) { e3 = be3; id3 = "L3A BRK"; }
                    if (okI) DrawZone(ibar3, it3, ib3, Brushes.DodgerBlue, "IFVG");
                    if (okB) DrawZone(bb3, Math.Max(be3, bf3), Math.Min(be3, bf3), Brushes.Orange, "Breaker");

                    if (!Ok(e3)) M3.Rst("MSS but no IFVG / breaker");
                    else
                    {
                        int n3 = Qty(e3, M3.Stop, RiskUsd, capQty);
                        if (n3 < 1) M3.Rst("Stop too wide for $ risk");
                        else
                        {
                            double tgt3 = Target(e3, M3.Stop, dr, p7H, p7L, L3Tp == Ict24Tp.NextLiquidity, L3RR, L3MinRR);
                            M3.Orders.Clear();
                            AddOrder(M3, id3, dr, n3, e3, M3.Stop, tgt3);
                            M3.TpA = tgt3;
                            M3.State = 2;
                            M3.Ev = "MSS -> limit at " + (id3 == "L3A IFVG" ? "IFVG CE (50%)" : "breaker");
                        }
                    }
                }
            }
            if (M3.State == 2 && !mss3)
            {
                if (owner == 3) { M3.State = 3; M3.Ev = "Entry filled"; }
                else if (owner != 0) M3.Rst("Cancelled - another model filled first");
                else if ((M3.Dir < 0 && High[0] >= M3.Stop - StopBuf) || (M3.Dir > 0 && Low[0] <= M3.Stop + StopBuf)) M3.Rst("Price ran the post-7:00 extreme before entry");
                else if (bo >= tCut3) M3.Rst("No retrace before cutoff");
            }
            else if (M3.State == 3 && owner != 3) M3.Rst("Trade closed");

            // ═════════ LECTURE 1 — 8:30 AM MODEL ═════════
            if (isL1Start)
            {
                p8H.Clear(); p8L.Clear();
                e8SH = double.NaN; e8SL = double.NaN;
                M1.Setups = 0;
                if (owner != 1) M1.Rst("Scanning for REH / REL");
            }
            if (is830) { p830H = High[0]; p830L = Low[0]; }
            else if (bo >= tRaid && bo < tFlat && Ok(p830H)) { p830H = Math.Max(p830H, High[0]); p830L = Math.Min(p830L, Low[0]); }

            bool drawP8 = DrawObjects && UseL1 && !(UseL2 || UseL3A || UseL4);
            PoolH(p8H, ref e8SH, ref e8SHB, tEq, tCut, drawP8);
            PoolL(p8L, ref e8SL, ref e8SLB, tEq, tCut, drawP8);
            bool sw8H = Sweep(p8H, true);
            bool sw8L = Sweep(p8L, false);

            bool raidWin1 = UseL1 && isWeekday && bo >= tRaid && bo < tCut && M1.Setups < 1;
            if (raidWin1 && M1.State <= 1)
            {
                if (sw8H && !sw8L && Ok(lastPL)) { StartRaid(M1, -1, "L1 raid"); M1.Ev = "REH raided -> waiting bearish MSS"; }
                else if (sw8L && !sw8H && Ok(lastPH)) { StartRaid(M1, 1, "L1 raid"); M1.Ev = "REL raided -> waiting bullish MSS"; }
            }
            bool mss1 = false;
            if (M1.State == 1)
            {
                mss1 = TrackMss(M1);
                if (!mss1 && bo >= tCut) M1.Rst("No MSS before cutoff");
            }
            if (mss1)
            {
                M1.Setups++;
                Mark("L1 MSS", M1.Dir, Brushes.MediumPurple);
                if (Busy(1, owner, gateOk)) M1.Rst("MSS skipped - another model active or Lucid guard");
                else
                {
                    int dr = M1.Dir;
                    double rawStop = L1StopMode == Ict24Stop1.RaidExtreme ? M1.X : (dr < 0 ? Math.Max(p830H, M1.X) : Math.Min(p830L, M1.X));
                    M1.Stop = dr < 0 ? rawStop + StopBuf : rawStop - StopBuf;
                    int kX = CurrentBar - M1.XBar, kS = CurrentBar - M1.MBar;
                    double obE, obF, brkE, brkF, fvgE, fvgF; int obB, brkB, fvgB;
                    FindOb(dr, kX, out obE, out obF, out obB);
                    FindBreaker(dr, kS, out brkE, out brkF, out brkB);
                    FindFvgLeg(dr, kX, out fvgE, out fvgF, out fvgB);

                    List<string> ids = new List<string>();
                    List<double> edges = new List<double>();
                    if (L1EntryMode == Ict24EntryMode.FirstPdArrayTouched)
                    {
                        double best = double.NaN;
                        foreach (double ev in new double[] { obE, fvgE, brkE })
                            if (Valid1(ev, dr, M1.Stop))
                                best = !Ok(best) ? ev : (dr < 0 ? Math.Min(best, ev) : Math.Max(best, ev));
                        if (Ok(best)) { ids.Add("L1 E1"); edges.Add(best); }
                    }
                    else if (L1EntryMode == Ict24EntryMode.OrderBlock) { if (Valid1(obE, dr, M1.Stop)) { ids.Add("L1 OB"); edges.Add(obE); } }
                    else if (L1EntryMode == Ict24EntryMode.FairValueGap) { if (Valid1(fvgE, dr, M1.Stop)) { ids.Add("L1 FVG"); edges.Add(fvgE); } }
                    else if (L1EntryMode == Ict24EntryMode.Breaker) { if (Valid1(brkE, dr, M1.Stop)) { ids.Add("L1 BRK"); edges.Add(brkE); } }
                    else
                    {
                        if (Valid1(obE, dr, M1.Stop)) { ids.Add("L1 OB"); edges.Add(obE); }
                        if (Valid1(fvgE, dr, M1.Stop)) { ids.Add("L1 FVG"); edges.Add(fvgE); }
                        if (Valid1(brkE, dr, M1.Stop)) { ids.Add("L1 BRK"); edges.Add(brkE); }
                    }

                    int nE = ids.Count, placed = 0;
                    M1.Orders.Clear();
                    M1.TpA = double.NaN;
                    if (nE > 0)
                    {
                        double budget = RiskUsd / nE;
                        int capEach = capQty / nE;
                        for (int i = 0; i < nE; i++)
                        {
                            double tp = Target(edges[i], M1.Stop, dr, p8H, p8L, L1Tp == Ict24Tp.NextLiquidity, L1RR, L1MinRR);
                            int n = Qty(edges[i], M1.Stop, budget, capEach);
                            if (n >= 1)
                            {
                                AddOrder(M1, ids[i], dr, n, edges[i], M1.Stop, tp);
                                placed++;
                                if (!Ok(M1.TpA)) M1.TpA = tp;
                            }
                        }
                    }
                    if (placed > 0) { M1.State = 2; M1.Ev = "MSS -> " + placed + " limit order(s) at PD array"; }
                    else M1.Rst("MSS but no valid PD array (or size = 0)");

                    if (Ok(obE)) DrawZone(obB, Math.Max(obE, obF), Math.Min(obE, obF), Brushes.MediumPurple, "OB");
                    if (Ok(fvgE)) DrawZone(fvgB, Math.Max(fvgE, fvgF), Math.Min(fvgE, fvgF), Brushes.Teal, dr < 0 ? "SIBI" : "BISI");
                    if (Ok(brkE)) DrawZone(brkB, Math.Max(brkE, brkF), Math.Min(brkE, brkF), Brushes.Orange, "Breaker");
                }
            }
            if (M1.State == 2 && !mss1)
            {
                if (owner == 1) { M1.State = 3; M1.Ev = "Entry filled"; }
                else if (owner != 0) M1.Rst("Cancelled - another model filled first");
                else if ((M1.Dir < 0 && High[0] >= M1.Stop - StopBuf) || (M1.Dir > 0 && Low[0] <= M1.Stop + StopBuf)) M1.Rst("Price ran the extreme before entry");
                else if (bo >= tCut) M1.Rst("No retrace into PD array before cutoff");
            }
            else if (M1.State == 3)
            {
                if (owner != 1) M1.Rst("Trade closed");
                else if (bo >= tCut) M1.Orders.RemoveAll(o => !o.Filled);
            }

            // ═════════ LECTURE 3 PM — POST-LUNCH OTE ═════════
            if (is1200)
            {
                p12H.Clear(); p12L.Clear();
                e12SH = double.NaN; e12SL = double.NaN;
                M4.Setups = 0;
                if (owner != 4) M4.Rst("Lunch range building");
            }
            if (is1330 && Ok(lunchH))
            {
                p12H.Add(lunchH);
                p12L.Add(lunchL);
                if (DrawObjects && UseL3P)
                {
                    int ago = Math.Min(90, CurrentBar);
                    Draw.Line(this, Tag("lh"), false, ago, lunchH, 0, lunchH, Brushes.Aqua, DashStyleHelper.Dash, 1);
                    Draw.Line(this, Tag("ll"), false, ago, lunchL, 0, lunchL, Brushes.Aqua, DashStyleHelper.Dash, 1);
                }
            }
            bool drawP12 = DrawObjects && UseL3P;
            PoolH(p12H, ref e12SH, ref e12SHB, t1200, t1430, drawP12);
            PoolL(p12L, ref e12SL, ref e12SLB, t1200, t1430, drawP12);
            bool sw12H = Sweep(p12H, true);
            bool sw12L = Sweep(p12L, false);

            bool quietOk = PmQuiet == Ict24Quiet.Off
                || (PmQuiet == Ict24Quiet.NoMorningTrade && !amTraded)
                || (PmQuiet == Ict24Quiet.NoNewsSpike && !newsDay)
                || (PmQuiet == Ict24Quiet.Both && !amTraded && !newsDay);
            bool pmWin = UseL3P && isWeekday && bo >= t1330 && bo < t1430 && M4.Setups < 1 && quietOk;
            if (pmWin && M4.State <= 1)
            {
                if (sw12H && !sw12L && Ok(lastPL)) { StartRaid(M4, -1, "L3 PM sweep"); M4.Ev = "PM high swept -> waiting bearish MSS"; }
                else if (sw12L && !sw12H && Ok(lastPH)) { StartRaid(M4, 1, "L3 PM sweep"); M4.Ev = "PM low swept -> waiting bullish MSS"; }
            }
            bool mss4 = false;
            if (M4.State == 1)
            {
                mss4 = TrackMss(M4);
                if (!mss4 && bo >= t1430) M4.Rst("No MSS before 2:30");
            }
            if (mss4)
            {
                M4.Setups++;
                Mark("L3 PM MSS", M4.Dir, Brushes.Aqua);
                if (Busy(4, owner, gateOk)) M4.Rst("MSS skipped - another model active or Lucid guard");
                else
                {
                    legS = M4.X;
                    legE = M4.Dir > 0 ? High[0] : Low[0];
                    M4.State = 2;
                    M4.Ev = "MSS -> OTE limit trailing the leg";
                }
            }
            if (M4.State == 2)
            {
                if (owner == 4) { M4.State = 3; M4.Ev = "Entry filled at OTE"; }
                else if (owner != 0) M4.Rst("Cancelled - another model filled first");
                else
                {
                    int dr = M4.Dir;
                    legE = dr > 0 ? Math.Max(legE, High[0]) : Math.Min(legE, Low[0]);
                    double legLen = Math.Abs(legE - legS);
                    pmOte = Rt(legE - dr * OteFib * legLen);
                    M4.Stop = legS - dr * StopBuf;
                    double tgt4 = L3PTp == Ict24TpPm.LegExtreme ? legE
                        : L3PTp == Ict24TpPm.MinusHalfExtension ? legE + dr * 0.5 * legLen
                        : Target(pmOte, M4.Stop, dr, p12H, p12L, true, 2.0, 1.0);
                    M4.TpA = Rt(tgt4);
                    M4.Orders.Clear();
                    if ((dr > 0 && Low[0] <= legS) || (dr < 0 && High[0] >= legS)) M4.Rst("Leg origin broken before entry");
                    else if (bo >= t1430) M4.Rst("No OTE retrace by 2:30");
                    else
                    {
                        int n4 = Qty(pmOte, M4.Stop, RiskUsd, capQty);
                        if (n4 >= 1 && legLen > 0) AddOrder(M4, "L3P OTE", dr, n4, pmOte, M4.Stop, M4.TpA);
                    }
                }
            }
            else if (M4.State == 3 && owner != 4) M4.Rst("Trade closed");

            // ═════════ END OF DAY (day models only) ═════════
            bool eodNow = bc >= tFlat && bo < tEth;
            if (eodNow && ((Position.MarketPosition != MarketPosition.Flat && owner != 6)
                || M1.State > 0 || M2.State > 0 || M3.State > 0 || M4.State > 0 || M5.State > 0))
            {
                if (owner != 6) FlattenAll("EOD exit");
                M1.Rst("End of day"); M2.Rst("End of day"); M3.Rst("End of day"); M4.Rst("End of day"); M5.Rst("End of day");
                l4Entry = double.NaN;
            }

            // ═════════ LECTURE 5 — ASIAN SESSION NDOG ═════════
            if (isSessOpen)
            {
                p18H.Clear(); p18L.Clear();
                e18SH = double.NaN; e18SL = double.NaN;
                ibsl = double.NaN; issl = double.NaN;
                p19H = double.NaN; p19L = double.NaN;
                M6.Setups = 0;
                if (owner != 6) M6.Rst("NDOG formed - waiting for initial liquidity");
            }
            if (Ok(ph) && pivT >= tEth && !Ok(ibsl))
            {
                ibsl = ph;
                if (DrawObjects && UseL5) Draw.Text(this, Tag("ibsl"), "IBSL", CurrentBar - pivB, ph + 4 * TickSize, Brushes.MediumPurple);
            }
            if (Ok(pl) && pivT >= tEth && !Ok(issl))
            {
                issl = pl;
                if (DrawObjects && UseL5) Draw.Text(this, Tag("issl"), "ISSL", CurrentBar - pivB, pl - 4 * TickSize, Brushes.MediumPurple);
            }
            if (is1900) { p19H = High[0]; p19L = Low[0]; }
            else if (bo >= t1900 && Ok(p19H)) { p19H = Math.Max(p19H, High[0]); p19L = Math.Min(p19L, Low[0]); }

            PoolH(p18H, ref e18SH, ref e18SHB, tEth, t2100, false);
            PoolL(p18L, ref e18SL, ref e18SLB, tEth, t2100, false);
            Sweep(p18H, true);
            Sweep(p18L, false);

            bool l5Day = dow != DayOfWeek.Friday && dow != DayOfWeek.Saturday;
            bool l5Win = UseL5 && l5Day && bo >= t1900 && bo < t2100 && M6.Setups < 1 && Ok(l5GapH) && Ok(p19L);
            bool crossUp = Ok(l5GapH) && Close[0] > l5GapH && Close[1] <= l5GapH;
            bool crossDn = Ok(l5GapL) && Close[0] < l5GapL && Close[1] >= l5GapL;
            bool initOk = !NeedInit || (Ok(ibsl) && Ok(issl));
            if (l5Win && M6.State == 0 && initOk && (crossUp || crossDn))
            {
                if (Busy(6, owner, gateOk)) M6.Ev = "Gap close skipped - another model active or Lucid guard";
                else
                {
                    int dr6 = crossUp ? 1 : -1;
                    M6.Dir = dr6;
                    M6.Setups++;
                    legS6 = dr6 > 0 ? p19L : p19H;
                    legE6 = dr6 > 0 ? High[0] : Low[0];
                    M6.State = 2;
                    Mark(dr6 > 0 ? "L5 close above NDOG" : "L5 close below NDOG", -dr6, Brushes.MediumPurple);
                    M6.Ev = dr6 > 0 ? "Closed above NDOG -> OTE buy limit trailing" : "Closed below NDOG -> OTE sell limit trailing";
                }
            }
            if (M6.State == 2)
            {
                if (owner == 6) { M6.State = 3; M6.Ev = "Entry filled at OTE"; }
                else if (owner != 0) M6.Rst("Cancelled - another position open");
                else
                {
                    int dr6 = M6.Dir;
                    legE6 = dr6 > 0 ? Math.Max(legE6, High[0]) : Math.Min(legE6, Low[0]);
                    double len6 = Math.Abs(legE6 - legS6);
                    l5Ote = Rt(legE6 - dr6 * L5Fib * len6);
                    M6.Stop = legS6 - dr6 * StopBuf;
                    double risk6 = Math.Abs(l5Ote - M6.Stop);
                    double initT = dr6 > 0 ? ibsl : issl;
                    bool initTOk = Ok(initT) && (dr6 > 0 ? initT >= l5Ote + L5MinRR * risk6 : initT <= l5Ote - L5MinRR * risk6);
                    double tgt6 = (L5Tp == Ict24TpL5.InitialLiquidityThenPool && initTOk) ? initT
                        : Target(l5Ote, M6.Stop, dr6, p18H, p18L, L5Tp != Ict24TpL5.FixedRR, L5RR, L5MinRR);
                    M6.TpA = Rt(tgt6);
                    M6.Orders.Clear();
                    if ((dr6 > 0 && Low[0] <= legS6) || (dr6 < 0 && High[0] >= legS6)) M6.Rst("Post-7 PM extreme broken before entry");
                    else if (bo >= t2100) M6.Rst("No OTE retrace by 9 PM");
                    else
                    {
                        int n6 = Qty(l5Ote, M6.Stop, RiskUsd, capQty);
                        if (n6 >= 1 && len6 > 0) AddOrder(M6, "L5 OTE", dr6, n6, l5Ote, M6.Stop, M6.TpA);
                    }
                }
            }
            else if (M6.State == 3 && owner != 6) M6.Rst("Trade closed");

            bool inL5Hold = bo >= tEth || bo.Hour < L5ExitHour;
            if (owner == 6 && !inL5Hold)
            {
                FlattenAll("L5 time exit");
                M6.Rst("Time exit");
            }

            // ═════════ (RE)SUBMIT WORKING ORDERS ═════════
            // Orders are re-submitted every bar while a model is armed; anything not re-submitted is cancelled by NinjaTrader.
            if (gateOk)
                foreach (Model md in allModels)
                    if ((md.State == 2 || md.State == 3) && md.Orders.Count > 0)
                        Submit(md);

            // ═════════ STATUS PANEL ═════════
            if (State == State.Realtime || CurrentBar >= Count - 2)
            {
                double acctBal = AcctSize() * 1000.0;
                StringBuilder sb = new StringBuilder();
                sb.AppendLine("L2 7:00 IFVG: " + StateTxt(UseL2, M2) + " | " + M2.Ev);
                sb.AppendLine("L3 AM gap tap: " + StateTxt(UseL3A, M3) + " | " + M3.Ev);
                sb.AppendLine("L1 8:30: " + StateTxt(UseL1, M1) + " | " + M1.Ev);
                sb.AppendLine("L4 news day: " + StateTxt(UseL4, M5) + " | " + M5.Ev);
                sb.AppendLine("L3 PM OTE: " + StateTxt(UseL3P, M4) + " | " + M4.Ev);
                sb.AppendLine("L5 Asian NDOG: " + StateTxt(UseL5, M6) + " | " + M6.Ev);
                sb.AppendLine("News day today: " + (NewsMode == Ict24News.EveryDay ? "every day" : newsDay ? "yes (8:30 spike)" : "no"));
                sb.AppendLine("Lucid: " + LucidAccount + " | " + (IsCustom() ? "custom" : gateOk ? (evalPassed ? "passed - still trading" : "active") : guardWhy));
                sb.AppendLine("Max contracts now: " + capQty + (Instrument.MasterInstrument.Name.StartsWith("M") ? " micros" : " minis"));
                if (!IsCustom())
                {
                    sb.AppendLine("Max loss floor: $" + (acctBal + floorP).ToString("N0") + " | room: $" + (equityP - floorP).ToString("N0"));
                    if (IsEval())
                        sb.AppendLine("Consistency: " + (closedP > 0 ? (largestDay / closedP * 100).ToString("0.0") + "% (need <= 50%)" : "-"));
                }
                if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 1)
                    sb.AppendLine("WARNING: use a 1-minute chart");
                Draw.TextFixed(this, "ict24status", sb.ToString(), TextPosition.TopRight);
            }

            prevOpen = bo;
        }

        #region Helpers
        private DateTime Et(DateTime t)
        {
            return TimeZoneInfo.ConvertTime(t, Core.Globals.GeneralOptions.TimeZoneInfo, est);
        }

        private bool Cross(DateTime ts, DateTime bo)
        {
            return bo >= ts && prevOpen < ts;
        }

        private static bool Ok(double v)
        {
            return !double.IsNaN(v);
        }

        private double Rt(double p)
        {
            return Instrument.MasterInstrument.RoundToTickSize(p);
        }

        private string Tag(string p)
        {
            tagN++;
            return "ict24_" + p + "_" + tagN;
        }

        private int OwnerOf(string nm)
        {
            if (string.IsNullOrEmpty(nm)) return 0;
            if (nm.StartsWith("L1")) return 1;
            if (nm.StartsWith("L2")) return 2;
            if (nm.StartsWith("L3A")) return 3;
            if (nm.StartsWith("L3P")) return 4;
            if (nm.StartsWith("L4")) return 5;
            if (nm.StartsWith("L5")) return 6;
            return 0;
        }

        private bool AnyActive()
        {
            foreach (Model md in allModels)
                if (md.State > 0) return true;
            return false;
        }

        private bool Busy(int self, int owner, bool gateOk)
        {
            if (!gateOk || owner != 0) return true;
            foreach (Model md in allModels)
                if (md.Id != self && md.State >= 2) return true;
            return false;
        }

        private void FlattenAll(string why)
        {
            if (Position.MarketPosition == MarketPosition.Long) ExitLong(why);
            else if (Position.MarketPosition == MarketPosition.Short) ExitShort(why);
        }

        private void AddOrder(Model md, string signal, int dir, int qty, double price, double stop, double target)
        {
            PendingOrder o = new PendingOrder();
            o.Signal = signal;
            o.Dir = dir;
            o.Qty = qty;
            o.Price = Rt(price);
            o.Stop = Rt(stop);
            o.Target = Rt(target);
            o.Filled = false;
            md.Orders.Add(o);
        }

        private void Submit(Model md)
        {
            foreach (PendingOrder o in md.Orders)
            {
                if (o.Filled || o.Qty < 1) continue;
                SetStopLoss(o.Signal, CalculationMode.Price, o.Stop, false);
                SetProfitTarget(o.Signal, CalculationMode.Price, o.Target);
                if (o.Dir > 0) EnterLongLimit(0, false, o.Qty, o.Price, o.Signal);
                else EnterShortLimit(0, false, o.Qty, o.Price, o.Signal);
            }
        }

        private int Qty(double e, double s, double budget, int cap)
        {
            double per = (Math.Abs(e - s) + SizeBuf) * Instrument.MasterInstrument.PointValue;
            if (per <= 0 || double.IsNaN(per)) return 0;
            int n = (int)Math.Floor(budget / per);
            return Math.Min(n, cap);
        }

        private double Target(double e, double s, int dr, List<double> arrH, List<double> arrL, bool nextLiq, double rrv, double minRRv)
        {
            double risk = Math.Abs(e - s);
            double best = double.NaN;
            if (nextLiq)
            {
                double minD = minRRv * risk;
                if (dr < 0)
                {
                    foreach (double c in arrL)
                        if (c <= e - minD && (!Ok(best) || c > best)) best = c;
                    if (GapTargets)
                        foreach (double c in gapHi)
                            if (c <= e - minD && (!Ok(best) || c > best)) best = c;
                    foreach (double c in new double[] { lonL, prevDayL, (GapTargets && UseNwog) ? nwogH : double.NaN })
                        if (Ok(c) && c <= e - minD && (!Ok(best) || c > best)) best = c;
                }
                else
                {
                    foreach (double c in arrH)
                        if (c >= e + minD && (!Ok(best) || c < best)) best = c;
                    if (GapTargets)
                        foreach (double c in gapLo)
                            if (c >= e + minD && (!Ok(best) || c < best)) best = c;
                    foreach (double c in new double[] { lonH, prevDayH, (GapTargets && UseNwog) ? nwogL : double.NaN })
                        if (Ok(c) && c >= e + minD && (!Ok(best) || c < best)) best = c;
                }
            }
            return Rt(Ok(best) ? best : e + dr * rrv * risk);
        }

        private bool Sweep(List<double> arr, bool isHigh)
        {
            bool swept = false;
            for (int i = arr.Count - 1; i >= 0; i--)
            {
                double lv = arr[i];
                if ((isHigh && High[0] > lv) || (!isHigh && Low[0] < lv))
                {
                    arr.RemoveAt(i);
                    swept = true;
                }
            }
            return swept;
        }

        private void PoolH(List<double> arr, ref double prevVal, ref int prevBar, DateTime startTs, DateTime endTs, bool drawIt)
        {
            if (!Ok(ph) || pivT < startTs || pivT >= endTs) return;
            if (Ok(prevVal) && ph < prevVal && (EqTol == 0 || prevVal - ph <= EqTol))
            {
                arr.Add(prevVal);
                if (drawIt)
                {
                    int ago = Math.Max(0, CurrentBar - prevBar);
                    Draw.Line(this, Tag("reh"), false, ago, prevVal, 0, prevVal, Brushes.Red, DashStyleHelper.Dash, 1);
                    Draw.Text(this, Tag("rehT"), "REH", ago, prevVal + 3 * TickSize, Brushes.Red);
                }
            }
            prevVal = ph;
            prevBar = pivB;
        }

        private void PoolL(List<double> arr, ref double prevVal, ref int prevBar, DateTime startTs, DateTime endTs, bool drawIt)
        {
            if (!Ok(pl) || pivT < startTs || pivT >= endTs) return;
            if (Ok(prevVal) && pl > prevVal && (EqTol == 0 || pl - prevVal <= EqTol))
            {
                arr.Add(prevVal);
                if (drawIt)
                {
                    int ago = Math.Max(0, CurrentBar - prevBar);
                    Draw.Line(this, Tag("rel"), false, ago, prevVal, 0, prevVal, Brushes.LimeGreen, DashStyleHelper.Dash, 1);
                    Draw.Text(this, Tag("relT"), "REL", ago, prevVal - 3 * TickSize, Brushes.LimeGreen);
                }
            }
            prevVal = pl;
            prevBar = pivB;
        }

        private void StartRaid(Model md, int dr, string tag)
        {
            md.State = 1;
            md.Dir = dr;
            md.X = dr < 0 ? High[0] : Low[0];
            md.XBar = CurrentBar;
            md.MLvl = dr < 0 ? lastPL : lastPH;
            md.MBar = dr < 0 ? lastPLBar : lastPHBar;
            if (DrawObjects)
                Draw.Text(this, Tag("raid"), tag, 0, dr < 0 ? High[0] + 6 * TickSize : Low[0] - 6 * TickSize, dr < 0 ? Brushes.Red : Brushes.LimeGreen);
        }

        private bool TrackMss(Model md)
        {
            if (md.Dir < 0)
            {
                if (High[0] > md.X)
                {
                    md.X = High[0];
                    md.XBar = CurrentBar;
                    if (Ok(lastPL)) { md.MLvl = lastPL; md.MBar = lastPLBar; }
                }
                if (Ok(pl) && pivB < md.XBar && pivB > md.MBar) { md.MLvl = pl; md.MBar = pivB; }
                return Ok(md.MLvl) && Close[0] < md.MLvl && md.MLvl < md.X;
            }
            else
            {
                if (Low[0] < md.X)
                {
                    md.X = Low[0];
                    md.XBar = CurrentBar;
                    if (Ok(lastPH)) { md.MLvl = lastPH; md.MBar = lastPHBar; }
                }
                if (Ok(ph) && pivB < md.XBar && pivB > md.MBar) { md.MLvl = ph; md.MBar = pivB; }
                return Ok(md.MLvl) && Close[0] > md.MLvl && md.MLvl > md.X;
            }
        }

        private bool Valid1(double e, int dr, double stop)
        {
            if (!Ok(e)) return false;
            return dr < 0 ? (e > Close[0] && e < stop - StopBuf) : (e < Close[0] && e > stop + StopBuf);
        }

        private void FindOb(int dr, int kX, out double e, out double f, out int b)
        {
            e = double.NaN; f = double.NaN; b = -1;
            for (int k = kX; k <= kX + Lookback && k < CurrentBar; k++)
            {
                if ((dr < 0 && Close[k] > Open[k]) || (dr > 0 && Close[k] < Open[k]))
                {
                    e = dr < 0 ? Low[k] : High[k];
                    f = dr < 0 ? High[k] : Low[k];
                    b = CurrentBar - k;
                    break;
                }
            }
        }

        private void FindBreaker(int dr, int kS, out double e, out double f, out int b)
        {
            e = double.NaN; f = double.NaN; b = -1;
            for (int k = kS; k <= kS + Lookback && k < CurrentBar; k++)
            {
                if ((dr < 0 && Close[k] < Open[k]) || (dr > 0 && Close[k] > Open[k]))
                {
                    e = dr < 0 ? Low[k] : High[k];
                    f = dr < 0 ? High[k] : Low[k];
                    b = CurrentBar - k;
                    break;
                }
            }
        }

        private void FindFvgLeg(int dr, int kX, out double e, out double f, out int b)
        {
            e = double.NaN; f = double.NaN; b = -1;
            if (kX < 2) return;
            for (int k = 0; k <= kX - 2 && k + 2 < CurrentBar; k++)
            {
                if (dr < 0 && High[k] < Low[k + 2] && Close[k + 1] < Open[k + 1])
                {
                    e = High[k]; f = Low[k + 2]; b = CurrentBar - k - 2;
                    break;
                }
                if (dr > 0 && Low[k] > High[k + 2] && Close[k + 1] > Open[k + 1])
                {
                    e = Low[k]; f = High[k + 2]; b = CurrentBar - k - 2;
                    break;
                }
            }
        }

        private void FindIfvg(int dr, int kX, int kS, out double top, out double bot, out int b)
        {
            top = double.NaN; bot = double.NaN; b = -1;
            if (kS - 2 < kX) return;
            for (int k = kX; k <= kS - 2 && k + 2 < CurrentBar; k++)
            {
                if (dr < 0 && Low[k] > High[k + 2] && Close[k + 1] > Open[k + 1])
                {
                    top = Low[k]; bot = High[k + 2]; b = CurrentBar - k - 2;
                    break;
                }
                if (dr > 0 && High[k] < Low[k + 2] && Close[k + 1] < Open[k + 1])
                {
                    top = Low[k + 2]; bot = High[k]; b = CurrentBar - k - 2;
                    break;
                }
            }
        }

        private int Tap(out double glo, out double ghi)
        {
            glo = double.NaN; ghi = double.NaN;
            for (int i = 0; i < gapLo.Count; i++)
            {
                double lo = gapLo[i], hi = gapHi[i];
                if (Close[1] < lo && High[0] >= lo) { glo = lo; ghi = hi; return -1; }
                if (Close[1] > hi && Low[0] <= hi) { glo = lo; ghi = hi; return 1; }
            }
            if (UseNwog && Ok(nwogL))
            {
                if (Close[1] < nwogL && High[0] >= nwogL) { glo = nwogL; ghi = nwogH; return -1; }
                if (Close[1] > nwogH && Low[0] <= nwogH) { glo = nwogL; ghi = nwogH; return 1; }
            }
            return 0;
        }

        private void Mark(string text, int dr, Brush b)
        {
            if (!DrawObjects) return;
            double y = dr < 0 ? Low[0] - 8 * TickSize : High[0] + 8 * TickSize;
            Draw.Text(this, Tag("mk"), text, 0, y, b);
        }

        private void DrawZone(int startBar, double top, double bottom, Brush b, string text)
        {
            if (!DrawObjects || startBar < 0 || !Ok(top) || !Ok(bottom)) return;
            int ago = Math.Max(0, CurrentBar - startBar);
            string tg = Tag("zone");
            Draw.Rectangle(this, tg, false, ago, top, 0, bottom, b, b, 15);
            Draw.Text(this, tg + "t", text, ago, top + 2 * TickSize, b);
        }

        private void DrawGap(string tg, double hi, double lo, Brush b)
        {
            DateTime t0 = Time[0];
            DateTime t1 = Time[0].AddDays(4);
            Draw.Rectangle(this, tg, false, t0, hi, t1, lo, b, b, 12);
            Draw.Line(this, tg + "q1", false, t0, lo + 0.25 * (hi - lo), t1, lo + 0.25 * (hi - lo), b, DashStyleHelper.Dot, 1);
            Draw.Line(this, tg + "q2", false, t0, lo + 0.50 * (hi - lo), t1, lo + 0.50 * (hi - lo), b, DashStyleHelper.Dot, 1);
            Draw.Line(this, tg + "q3", false, t0, lo + 0.75 * (hi - lo), t1, lo + 0.75 * (hi - lo), b, DashStyleHelper.Dot, 1);
        }

        private void RemoveGapDrawings(string tg)
        {
            RemoveDrawObject(tg);
            RemoveDrawObject(tg + "q1");
            RemoveDrawObject(tg + "q2");
            RemoveDrawObject(tg + "q3");
        }

        private string StateTxt(bool on, Model md)
        {
            if (!on) return "Off";
            if (md.State == 0) return "Scanning";
            if (md.State == 1) return md.Dir < 0 ? "Liquidity taken above -> waiting bearish MSS" : "Liquidity taken below -> waiting bullish MSS";
            if (md.State == 2) return "Armed - waiting for retrace";
            return "In trade";
        }

        // ─── Lucid account tables (LucidFlex help center) ───
        private bool IsCustom()
        {
            return LucidAccount == Ict24Account.Custom;
        }

        private bool IsEval()
        {
            return LucidAccount == Ict24Account.Flex25KEval || LucidAccount == Ict24Account.Flex50KEval
                || LucidAccount == Ict24Account.Flex100KEval || LucidAccount == Ict24Account.Flex150KEval;
        }

        private int AcctSize()
        {
            switch (LucidAccount)
            {
                case Ict24Account.Flex25KEval:
                case Ict24Account.Flex25KFunded: return 25;
                case Ict24Account.Flex50KEval:
                case Ict24Account.Flex50KFunded: return 50;
                case Ict24Account.Flex100KEval:
                case Ict24Account.Flex100KFunded: return 100;
                default: return 150;
            }
        }

        private double MllUsd()
        {
            int s = AcctSize();
            return s == 25 ? 1000 : s == 50 ? 2000 : s == 100 ? 3000 : 4500;
        }

        private double TargetUsd()
        {
            int s = AcctSize();
            return s == 25 ? 1250 : s == 50 ? 3000 : s == 100 ? 6000 : 9000;
        }

        private int MicrosAllowed()
        {
            int s = AcctSize();
            if (IsEval()) return s == 25 ? 20 : s == 50 ? 40 : s == 100 ? 60 : 100;
            double p = tierP;
            if (s == 25) return p < 1000 ? 10 : 20;
            if (s == 50) return p < 1000 ? 20 : p < 2000 ? 30 : 40;
            if (s == 100) return p < 1000 ? 30 : p < 2000 ? 40 : p < 3000 ? 50 : 60;
            return p < 1000 ? 40 : p < 2000 ? 50 : p < 3000 ? 60 : p < 4500 ? 80 : 100;
        }

        private int CapQty()
        {
            if (IsCustom()) return MaxQty;
            bool micro = Instrument.MasterInstrument.Name.StartsWith("M");
            int c = micro ? MicrosAllowed() : MicrosAllowed() / 10;
            return Math.Max(1, Math.Min(MaxQty, c));
        }
        #endregion

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Lucid account", Order = 1, GroupName = "01. Lucid account rules")]
        public Ict24Account LucidAccount { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Simulate Max Loss Limit (stop if breached)", Order = 2, GroupName = "01. Lucid account rules")]
        public bool SimulateMll { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Stop trading once evaluation is passed", Order = 3, GroupName = "01. Lucid account rules")]
        public bool StopAtPass { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Personal daily loss stop ($, 0 = off)", Order = 4, GroupName = "01. Lucid account rules")]
        public double DailyStopUsd { get; set; }

        [NinjaScriptProperty]
        [Range(15, 16)]
        [Display(Name = "Lucid safety flatten hour (NY)", Order = 5, GroupName = "01. Lucid account rules")]
        public int LucidFlatHour { get; set; }

        [NinjaScriptProperty]
        [Range(0, 44)]
        [Display(Name = "Lucid safety flatten minute", Order = 6, GroupName = "01. Lucid account rules")]
        public int LucidFlatMinute { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "LIVE: account P/L when starting ($)", Order = 7, GroupName = "01. Lucid account rules")]
        public double StartingProfit { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "LIVE: highest end-of-day P/L so far ($)", Order = 8, GroupName = "01. Lucid account rules")]
        public double HighestEodProfit { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "LIVE: largest single-day profit so far ($)", Order = 9, GroupName = "01. Lucid account rules")]
        public double LargestDayProfit { get; set; }

        [NinjaScriptProperty]
        [Range(1, double.MaxValue)]
        [Display(Name = "Max risk per setup ($)", Order = 1, GroupName = "02. General")]
        public double RiskUsd { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Max contracts per entry (your own cap)", Order = 2, GroupName = "02. General")]
        public int MaxQty { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Stop buffer (pts)", Order = 3, GroupName = "02. General")]
        public double StopBuf { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Sizing buffer (pts)", Order = 4, GroupName = "02. General")]
        public double SizeBuf { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Swing strength (candles each side)", Order = 5, GroupName = "02. General")]
        public int PivLen { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "REH / REL max difference (pts, 0 = any)", Order = 6, GroupName = "02. General")]
        public double EqTol { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Candles to search for OB / breaker", Order = 7, GroupName = "02. General")]
        public int Lookback { get; set; }

        [NinjaScriptProperty]
        [Range(0, 16)]
        [Display(Name = "Close day models at (hour NY)", Order = 8, GroupName = "02. General")]
        public int DayFlatHour { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "Close day models (minute)", Order = 9, GroupName = "02. General")]
        public int DayFlatMinute { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use NDOG / NWOG as targets", Order = 10, GroupName = "02. General")]
        public bool GapTargets { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Draw levels and labels", Order = 11, GroupName = "02. General")]
        public bool DrawObjects { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 1 (8:30)", Order = 1, GroupName = "03. Lecture 1")]
        public bool UseL1 { get; set; }

        [NinjaScriptProperty]
        [Range(9, 16)]
        [Display(Name = "No new setups after (hour)", Order = 2, GroupName = "03. Lecture 1")]
        public int L1CutHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Entry PD array", Order = 3, GroupName = "03. Lecture 1")]
        public Ict24EntryMode L1EntryMode { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Stop placement", Order = 4, GroupName = "03. Lecture 1")]
        public Ict24Stop1 L1StopMode { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 5, GroupName = "03. Lecture 1")]
        public Ict24Tp L1Tp { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 20)]
        [Display(Name = "R:R (fixed / fallback)", Order = 6, GroupName = "03. Lecture 1")]
        public double L1RR { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 20)]
        [Display(Name = "Min R:R for liquidity target", Order = 7, GroupName = "03. Lecture 1")]
        public double L1MinRR { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 2 (7:00 IFVG)", Order = 1, GroupName = "04. Lecture 2")]
        public bool UseL2 { get; set; }

        [NinjaScriptProperty]
        [Range(8, 16)]
        [Display(Name = "No new setups after (hour)", Order = 2, GroupName = "04. Lecture 2")]
        public int L2CutHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Require retrace into London range", Order = 3, GroupName = "04. Lecture 2")]
        public bool NeedLonRetrace { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Raid only in first 30 min of 7, 8, 9 AM", Order = 4, GroupName = "04. Lecture 2")]
        public bool First30 { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use breaker if no IFVG", Order = 5, GroupName = "04. Lecture 2")]
        public bool BrkFallback { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 6, GroupName = "04. Lecture 2")]
        public Ict24TpL2 L2Tp { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 20)]
        [Display(Name = "R:R (fixed / fallback)", Order = 7, GroupName = "04. Lecture 2")]
        public double L2RR { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 20)]
        [Display(Name = "Min R:R for liquidity target", Order = 8, GroupName = "04. Lecture 2")]
        public double L2MinRR { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 3 AM (gap tap)", Order = 1, GroupName = "05. Lecture 3 AM")]
        public bool UseL3A { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "NDOGs to keep", Order = 2, GroupName = "05. Lecture 3 AM")]
        public int KeepNdog { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Include NWOG", Order = 3, GroupName = "05. Lecture 3 AM")]
        public bool UseNwog { get; set; }

        [NinjaScriptProperty]
        [Range(8, 16)]
        [Display(Name = "Taps only from 7:00 until (hour)", Order = 4, GroupName = "05. Lecture 3 AM")]
        public int L3CutHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Bias filter", Order = 5, GroupName = "05. Lecture 3 AM")]
        public Ict24Bias L3Bias { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 6, GroupName = "05. Lecture 3 AM")]
        public Ict24Tp L3Tp { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 20)]
        [Display(Name = "R:R (fixed / fallback)", Order = 7, GroupName = "05. Lecture 3 AM")]
        public double L3RR { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 20)]
        [Display(Name = "Min R:R for liquidity target", Order = 8, GroupName = "05. Lecture 3 AM")]
        public double L3MinRR { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 3 PM (OTE)", Order = 1, GroupName = "06. Lecture 3 PM")]
        public bool UseL3P { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trade only on a quiet morning", Order = 2, GroupName = "06. Lecture 3 PM")]
        public Ict24Quiet PmQuiet { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 0.79)]
        [Display(Name = "OTE entry retracement", Order = 3, GroupName = "06. Lecture 3 PM")]
        public double OteFib { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 4, GroupName = "06. Lecture 3 PM")]
        public Ict24TpPm L3PTp { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 4 (news day)", Order = 1, GroupName = "07. Lecture 4")]
        public bool UseL4 { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "News day", Order = 2, GroupName = "07. Lecture 4")]
        public Ict24News NewsMode { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Spike size (x average candle range)", Order = 3, GroupName = "07. Lecture 4")]
        public double NewsMult { get; set; }

        [NinjaScriptProperty]
        [Range(9, 16)]
        [Display(Name = "No new setups after (hour)", Order = 4, GroupName = "07. Lecture 4")]
        public int L4CutHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "A-plus only (MSS at/after 9:30)", Order = 5, GroupName = "07. Lecture 4")]
        public bool APlusOnly { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Gap must be in premium / discount", Order = 6, GroupName = "07. Lecture 4")]
        public bool PdFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Bias filter", Order = 7, GroupName = "07. Lecture 4")]
        public Ict24Bias L4Bias { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 8, GroupName = "07. Lecture 4")]
        public Ict24Tp L4Tp { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 20)]
        [Display(Name = "R:R (fixed / fallback)", Order = 9, GroupName = "07. Lecture 4")]
        public double L4RR { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 20)]
        [Display(Name = "Min R:R for liquidity target", Order = 10, GroupName = "07. Lecture 4")]
        public double L4MinRR { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Lecture 5 (Asian NDOG)", Order = 1, GroupName = "08. Lecture 5")]
        public bool UseL5 { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Require initial BSL + SSL first", Order = 2, GroupName = "08. Lecture 5")]
        public bool NeedInit { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 0.79)]
        [Display(Name = "OTE entry retracement", Order = 3, GroupName = "08. Lecture 5")]
        public double L5Fib { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Take profit", Order = 4, GroupName = "08. Lecture 5")]
        public Ict24TpL5 L5Tp { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 20)]
        [Display(Name = "R:R (fixed / fallback)", Order = 5, GroupName = "08. Lecture 5")]
        public double L5RR { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 20)]
        [Display(Name = "Min R:R for liquidity target", Order = 6, GroupName = "08. Lecture 5")]
        public double L5MinRR { get; set; }

        [NinjaScriptProperty]
        [Range(0, 6)]
        [Display(Name = "Close L5 trade at (hour after midnight)", Order = 7, GroupName = "08. Lecture 5")]
        public int L5ExitHour { get; set; }
        #endregion
    }
}
