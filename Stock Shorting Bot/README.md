# Stock Shorting Bot

Each trading day the bot shorts the five best end-of-day fades among the top 20 US gainers on the Alpaca **paper** account, and holds them until the next day's rotation.

| Time (ET) | Step |
|---|---|
| 15:40 | cron starts `daily.py --execute`. It reads the exchange calendar and waits. |
| 15:45 | Scan: [TradingView gainers](https://www.tradingview.com/markets/stocks-usa/market-movers-gainers/) with market cap above $25M and previous close above $3, top 20. |
| 15:45–15:50 | Short data, Alpaca borrow, headlines, then scoring and ranking. Everything is written to Supabase. |
| 15:55 | Cancel open orders, cover yesterday's shorts, then short the new top 5 with market orders. |
| 16:15 | cron runs `sync.py`: equity, positions and closed trades from Alpaca fills go to Supabase. |

On half days the times shift automatically to 12:45 / 12:55, because they are taken from the calendar's close. The bot refuses to send orders less than 2 minutes before the close.

## The checklist (`shortingbot/scoring.py`)

| # | Check | Source | How it counts |
|---|---|---|---|
| 1 | Move & volume: up 20–100% on 5–10× volume, upper wick, weak close | TradingView | 35% of the score |
| 2 | Crowding: DTC 1–5 and SI 5–15% of float is ideal | FINRA + yfinance | 20%. DTC > 10 **and** SI > 20% is **excluded** as a squeeze trap |
| 3 | Borrow | Alpaca `shortable` / `easy_to_borrow` | Gate: not shortable or hard to borrow is excluded |
| 4 | Fails-to-deliver: repeated big spikes relative to float are bad | SEC | 10% |
| 5 | Catalyst: real de-risking news vs hype | FMP if `FMP_API_KEY` is set, otherwise Yahoo RSS | 35%. Real catalysts (buyout, FDA approval, guidance raise, asset sale) are **excluded** |

When a block has no data, it drops out and the other weights renormalise. Every threshold is in `shortingbot/config.py`.

Sizing goes from rank 1 at 19% of equity down to rank 5 at 15%, about 85% gross short in total. If a name is already short and stays in the top 5, it is resized instead of closed and reopened, because Alpaca rejects that as a wash trade.

## Setup

```bash
cd "Stock Shorting Bot"
pip3 install -r requirements.txt
cp .env.example .env      # Alpaca paper keys, optional FMP key, Supabase URL + service key
python3 daily.py --now    # dry run: prints the ranked 20 and the 5 orders, sends nothing
```

## Commands

```bash
python3 daily.py                  # dry run, waits for 15:45
python3 daily.py --now            # dry run immediately
python3 daily.py --execute        # live paper run (what cron does)
python3 daily.py --execute --now  # trade immediately (market hours only)
python3 sync.py                   # pull Alpaca truth into Supabase
```

A second executed run on the same day is refused unless you pass `--force` (the guard file is `logs/last_execute.json`).

## Scheduling (lessons from the first bot)

```cron
40 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily.py --execute >> logs/cron.log 2>&1
15 16 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 sync.py >> logs/cron.log 2>&1
```

- The times above are ET only because this Mac's clock is ET. If the Mac's timezone changes, the cron times have to change too.
- `/usr/sbin/cron` needs Full Disk Access, because the folder is on the Desktop.
- The Mac has to be awake, plugged in, and **lid open** from 15:40 to 16:20 ET. A closed lid sleeps the Mac and no wake schedule survives it.
  ```bash
  sudo pmset repeat wakeorpoweron MTWRF 15:30:00
  sudo pmset -c sleep 0
  ```
- **If you rename or move this folder, update the crontab.** The first bot's cron kept pointing at a folder that no longer existed.
- A missed run now shows on the dashboard. The bot card turns red when there has been no heartbeat for more than a trading day.

## Known limits

- 85% gross short overnight with no stop loss. Spike shorts have fat-tailed losses. Run on paper only until the journal shows an edge.
- Free short interest data is 1–3 weeks old. There is no borrow-fee feed; Alpaca's easy-to-borrow flag stands in for it.
- Many small caps can't be shorted on Alpaca. The bot walks down the ranking and may end up with fewer than 5 names.
- The catalyst check is keyword rules over headlines. It catches buyouts and approvals, but it does not read the articles.

`docs/postmortem.html` covers what went wrong with the first bot.
