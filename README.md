# trading-bot-sim

Paper-trading simulator: takes manually-entered signals (later: from Discord),
watches real Yahoo Finance prices, and simulates entries/stops/exits against
a virtual portfolio, with guardrails. No dashboard yet — CLI only for now.

## Structure

```
config.json          Starting balance + all guardrail/strategy parameters
src/
  config.py          Loads config.json
  models.py          Signal, Position, Trade dataclasses
  market_data.py     get_current_price(ticker) via yfinance
  portfolio.py        Portfolio: cash, open positions, trade history, equity/P&L
  guardrails.py        check_entry_allowed(): position size, concurrency, daily loss cap,
                        max trades/day, per-ticker cooldown
  strategy.py           Crossing detection + process_signal(): entry / trailing stop / exit
  engine.py              run_tick(): fetches live prices for active signals, runs strategy
  persistence.py          Load/save portfolio + signal state as JSON under data/
  logging_utils.py         Appends every poll and event to data/poll_log.jsonl
tests/                Unit tests (stdlib unittest, no network, no extra deps)
main.py               CLI: add-signal / signals / tick / status / history
data/                 Runtime state (gitignored) — created on first run
```

## How a signal maps to strategy fields

Given a signal like:

```
SUNE   current price 3.10   entry 3.34-3.38   major breakout 3.59
       targets 3.75, 4.10, 4.50   (dip levels ignored)
```

- `entry_trigger` = **3.38** (top of the entry range — the only thing that
  fires an entry in this version). If a signal gives a single level instead
  of a range, that level is the trigger.
- `major_breakout_level` (3.59) and `dip_levels` are stored for reference
  only; they don't affect entry/exit logic yet.
- `profit_targets` = [3.75, 4.10, 4.50], always evaluated ascending.

## Strategy logic (as implemented)

1. **Entry**: position opens when polled price crosses up through
   `entry_trigger`. Size = 25% of current equity (cash + mark-to-market open
   positions). Initial stop = 3% below the fill price.
2. **Trailing**: as price crosses up through each profit target (in order),
   the stop moves to `max(current_stop, target * 0.99)` — i.e. 1% below that
   target, and never moves down.
3. **Exit**: position closes when price crosses down through the *current*
   stop level (whatever it's been trailed to). No take-profit exit — the
   trailing stop is the only way out.
4. **"Crossing"** is polling-based, not tick-based: each check compares the
   latest price to the price from the *previous* check. `last < level <=
   current` = crossed up, `last > level >= current` = crossed down. On a
   signal's very first check (no previous price yet), already being at/through
   the level counts as crossed. Every poll and every triggered event is
   logged with a timestamp to `data/poll_log.jsonl` for debugging.

## Guardrails (all in `config.json`)

| Rule | Default |
|---|---|
| Max position size | 25% of equity |
| Max concurrent open positions | 2 |
| Daily loss cap (halts new entries for the day) | 7% of day's starting equity |
| Max new position entries per day | 4 (exits/stop-adjustments don't count) |
| Cooldown before re-entering the same ticker | 45 minutes after that ticker's position closes |

Guardrails only block **new entries**. An already-open position's trailing
stop and exit always continue to run, even mid-cooldown or after the daily
loss cap trips — the guardrails cap risk-taking, not risk management.

## Setup (not run yet — needs your go-ahead)

```bash
pip install -r requirements.txt
```

The only dependency is `yfinance` (for real Yahoo Finance prices). Tests
don't need it — they exercise the pure portfolio/guardrail/strategy logic
with prices passed in directly, no network calls.

## Usage (once dependencies are installed)

```bash
# Add a signal to watch
python main.py add-signal --ticker SUNE --ref-price 3.10 --entry 3.34-3.38 \
    --major-breakout 3.59 --targets 3.75,4.10,4.50

# List signals and their status
python main.py signals

# Fetch live prices and evaluate all active signals once
python main.py tick

# Portfolio snapshot (cash, equity, open positions)
python main.py status

# Closed trade history + realized P&L
python main.py history
```

Run `tick` periodically (every few minutes) to simulate polling — there's no
background scheduler yet. Each `tick` is one full evaluation pass.

## Not built yet

- Discord signal ingestion (manual `add-signal` stands in for it)
- Scheduler/loop to call `tick` automatically at an interval
- Visual dashboard (positions, trade history, cumulative P&L)

## Gold Asian-session ORB strategy (separate engine)

A second, independent engine implementing the "Asian Session Gold ORB"
strategy from [Peachy Investor's YouTube video](https://www.youtube.com/watch?v=di_UoCcHen8),
trading **micro gold futures (MGC)** via Webull's futures paper account.
It's fully separate from the equity signal engine above — separate config
(`gold_config.json`), separate state (`data/gold_state.json`), separate CLI
commands — because contracts and short-selling don't fit the dollar-based,
long-only `Portfolio`/`Signal` model.

### The video's strategy vs. what's coded here

The video is **discretionary**: it leans on hand-drawn "key pivot levels,"
a 4-hour-candle read of the broader trend, and judgment calls about candle
reactions. That can't be encoded literally, so this implementation
substitutes objective, backtestable rules for the discretionary parts:

| Video concept | This implementation |
|---|---|
| ORB box at CME's 18:00 ET gold-futures open | Same — `gold_orb.compute_orb()`, a 15-minute box |
| "Larger point of view" bias (4H candles, hand-marked KPLs) | EMA(9)/EMA(21) stack on 1-hour closes — bullish if `close > ema9 > ema21`, bearish if reversed, else no trade |
| Wait for Tokyo/Hong Kong volume ("golden time" ~21:00 ET) | Configurable entry window, default 2h–6h after the 18:00 open |
| ORB "held as support/resistance" reaction | No close against the bias side of the ORB box since session open |
| Entry on a deep retrace into the 4/9 EMA bands + candle-structure flip | Retrace: prior bar touches the slow EMA. Flip: current bar closes through the fast EMA *and* through the prior bar's high/low, in the bias direction |
| Stop near the retracement swing | Lowest/highest of the last N bars before entry, ± a tick buffer |
| Discretionary KPL targets, trailed with Heikin-Ashi | R-multiples of initial risk (default 1R/2R/3R), stop trails up/down as each is hit, never loosens |
| Base-hit sizing, multiple entries per session | `max_entries_per_session` (default 2) |

None of this is backtested against history — it's forward-only paper trading
from here on. Treat it as a reasonable-effort codification, not a faithful
replica of what a discretionary trader would actually do.

### Why Webull mirroring works differently here

Webull's **equity** paper API supports bracket orders (entry + attached
protective stop as one combo). Its **futures** API does not — Webull's docs
say combo orders (OTO/OTOCO/OCO) aren't supported for futures at all.  So
`gold_futures_mirror.py` places a plain `MARKET` entry order, waits for it
to fill, then places a *separate* resting `STOP_LOSS` order as the
protective stop, and trails that stop with cancel/replace as local targets
are hit — same "our engine decides, Webull mirrors independently" philosophy
as `webull_mirror.py`, just without the combo-order plumbing.

Also: gold futures trade ~23 hours a day; this strategy specifically runs
overnight (6pm–onward ET) when US equities are closed. A gold ETF (GLD)
can't be substituted for the futures leg without losing the entire premise
of the strategy, which is why this needed a real futures account rather
than reusing the existing equity mirror.

### Usage

```bash
python main.py gold-tick      # fetch live GC=F bars, evaluate the strategy once
python main.py gold-status    # current session, position, unrealized P&L
python main.py gold-history   # closed gold trades + realized P&L
```

Like the equity engine, there's no scheduler — run `gold-tick` periodically
(every 5 minutes, matching `poll_interval_minutes` in `gold_config.json`)
during the entry window (roughly 8pm–midnight ET) to actually catch signals.

### Config (`gold_config.json`)

Key fields: `contracts_per_trade` (micro contracts per entry, default 1),
`profit_target_r_multiples`, `max_entries_per_session`, the entry-window and
session-flatten timing (all expressed as minutes after the 18:00 ET open,
to sidestep midnight-wraparound bugs), and `webull_futures_mirror_enabled`
(default `false` — flip to `true` to mirror local decisions to your Webull
sandbox futures account; requires `WEBULL_APP_KEY`/`WEBULL_APP_SECRET` in
`.env`, same credentials as the equity mirror). The tradeable MGC contract
month is resolved automatically each run by probing Webull's sandbox
(contracts roll off before expiry on a schedule that isn't published
anywhere crawlable, so this asks the API directly rather than guessing).
