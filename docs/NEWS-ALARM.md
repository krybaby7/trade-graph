# News alarm (T44, shadow mode)

Status: built and tested in source on 2026-10-16 (cloud package W1-A, decisions D140–D144). Not deployed.
No real feed was recorded and no Jev call was made: the cloud network refused every feed host, and no key
exists. Tests use labelled synthetic feeds and scripted Jev replies.

## In one minute

- Every few minutes, software reads a handful of free public news feeds and saves each new headline with
  its link and time.
- If you have switched Jev on and installed a key, Jev answers three fixed questions about each fresh
  headline. Every Jev call gets a receipt at USD0.042 per million input tokens; a call whose usage is not
  reported stays "unresolved", never free.
- The system then logs whom the headline would have woken: the Leader, and the Trader too when the news
  concerns a coin the paper account holds. **Nobody is woken yet.** Real wake-ups are T45.
- A headline is only data. A headline that tries to give orders changes nothing.

## Feeds

Free, public, no key or account (D138 item 10, D142). Checked on 2026-10-16; the cloud network refused every
host, so the first real recording of each feed is step L2.

| Feed id | Publisher | Address | Kind |
|---|---|---|---|
| `kraken-status` | Kraken status page | `https://status.kraken.com/history.atom` | exchange status (Atom) |
| `kraken-blog` | Kraken blog | `https://blog.kraken.com/feed` | exchange announcements (RSS) |
| `coindesk` | CoinDesk | `https://www.coindesk.com/arc/outboundfeeds/rss/` | news outlet (RSS) |
| `cointelegraph` | Cointelegraph | `https://cointelegraph.com/rss` | news outlet (RSS) |
| `decrypt` | Decrypt | `https://decrypt.co/feed` | news outlet (RSS) |

One plain GET per feed per poll, with the previous `ETag` and `Last-Modified` so an unchanged feed is not
downloaded again. No redirects, at most 2 MB, no cookie or credential, and any document that declares a DTD or
entities is refused. Each item keeps its title, link, publisher, publication time and receipt time. Repeats
are detected by link (scheme and host in lower case, no `#fragment`, no `utm_` parameters). If a feed moves,
the owner replaces it in the settings file.

## The questions (question set `news-alarm-questions-v1`)

The headline goes into Jev's `state` as quoted data; the question texts never contain it.

1. `concerns` (choice): which watched coin the headline concerns, or `kraken` (the exchange itself), or
   `none`. Watched coins are the coins the paper account holds, the coins on its mandate, any extra coins the
   owner lists in the settings, and the day's focus coins once T37 exists. A Jev choice allows 255 options, so
   at most 253 coins.
2. `event_type` (choice): `hack_or_exploit`, `delisting`, `listing`, `exchange_outage_or_maintenance`,
   `regulatory_action` or `other`.
3. `holder_wants_to_know` (yes/no): would someone holding that coin (or keeping coins on Kraken) want to know
   within the hour?

Only headlines published within the check window (1 hour) are sent to Jev. Older ones are stored and listed
as "not checked".

## The would-wake rule (`news-alarm-rule-v1`)

An alarm setting, not a Trader confidence gate. It can only add wake-ups (D116); property tests check that a
more urgent answer, a lower threshold or a held coin never removes one.

- The subject is the `concerns` option with the highest probability. Tied options all count, and a coin tied
  with `none` counts.
- A headline would wake someone when its subject is not `none` and Jev's probability of "yes" for
  `holder_wants_to_know` is at least the threshold (default 0.5).
- It would wake the Leader. It would also wake the Trader when the subject is a coin the account holds, or
  Kraken itself while the account holds anything.
- The event type is logged for the Leader's note (T45) and is not part of the rule.

## Spending

- Price: the dated card `typesafe-jev-1.13.0-2026-10-15` (USD0.042 per million input tokens, output free,
  read from TypeSafe's models page on 2026-10-15, source S40). A newer price is a new dated card in the
  settings file; real calls refuse a card verified more than 30 days ago.
- Each call reserves the largest request Jev accepts (64,000 input tokens, about USD0.0027) before it is sent,
  and settles to the reported usage afterwards. Role `news_alarm`, purpose `news_alarm:jev`.
- Unreported usage, a reply from another model (price unknown), a timeout or an interrupted call keeps the
  full reservation as unresolved. HTTP errors are receipted from the usage the error reports, if any.
- Scripted replies are synthetic receipts, kept apart from real spending.
- Testing alert at USD5 of real Jev spending (D138 item 10): the report says when it is reached. It is an
  alert; nothing pauses. The owner's existing money limits still apply: a call with no room is not sent.

## Commands

Both commands use the paper state database (`runtime/trade_graph.sqlite` by default).

```text
# Once: a paper account to watch (skip on an existing installation).
trade-graph init

# Fixture run, no network and no key: synthetic feeds and scripted Jev replies.
trade-graph news-alarm-poll --config tests/fixtures/news/settings.synthetic.json \
    --fixture-feeds tests/fixtures/news --scripted-jev tests/fixtures/news/jev-scripted-answers.json

# Real feeds, Jev off (the default): headlines are collected and logged, nothing is sent to Jev.
trade-graph news-alarm-poll

# Twelve polls, one every 5 minutes (stop early with Ctrl-C; finished polls are kept).
trade-graph news-alarm-poll --polls 12

# Read the log. Plain English, or JSON (default). Default window: the last 24 hours.
trade-graph news-alarm-report --format text
trade-graph news-alarm-report --since 2026-10-16T00:00:00Z --until 2026-10-17T00:00:00Z
```

The fixture settings add the invented coin `EXMPL` to the watch list (`extra_watch_coins`), because the
scripted replies name it. `config/news-alarm.example.json` holds the recommended defaults, with Jev off.

The report reads only: it opens the database read-only, never creates or upgrades it, calls nothing and never
recomputes an answer or a verdict.

## Switching Jev on (owner only)

1. Sign up at console.typesafe.ai yourself and use only typesafe.ai addresses (D137).
2. Put the key in `~/.local/share/trade-graph-owner/typesafe/typesafe.key`: the folder mode 0700, the file
   mode 0600, both owned by you, the file holding only the key. The key never enters Git, a log, a record or
   anything a model reads; it is sent only in the request header.
3. Copy `config/news-alarm.example.json` to a private place, set `"calls_enabled": true` under `jev`, and run
   `trade-graph news-alarm-poll --config <that file>`. With calls switched on and no readable key, the
   command stops and sends nothing.

## Not yet confirmed

- The wire field names beyond those recorded in S40 (`question`, `options`, `levels`, `probabilities`,
  `confidence`, `id`) and the `Authorization: Bearer` header are this build's reading of the published API.
  The first credentialed call (step L2) confirms them. A reply in another shape is logged as
  `malformed_answers`, still receipted, and wakes nobody.
- The feed addresses above were not reachable from the cloud. Step L2 records one real sample of each.
- Running the poll every 5 minutes on the owner's PC (a scheduled task or the service) is an installation
  step (W6-D, L3). T44 adds no scheduler.
