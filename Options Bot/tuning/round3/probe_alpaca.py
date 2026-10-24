"""One read-only probe: how far back do Alpaca SIP daily bars go?"""
import os
import requests
import alpaca_env

src = alpaca_env.load()
h = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
r = requests.get("https://data.alpaca.markets/v2/stocks/bars",
                 params={"symbols": "SPY", "timeframe": "1Day", "start": "2005-01-01", "end": "2016-03-01",
                         "adjustment": "raw", "feed": "sip", "limit": 5},
                 headers=h, timeout=30)
js = r.json()
print(src, r.status_code, [b["t"] for b in js.get("bars", {}).get("SPY", [])][:5])
