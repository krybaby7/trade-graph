# News alarm synthetic fixtures

Hand-written test data for the T44 news alarm. Nothing here was recorded from a real feed or from Jev. Every
file's evidence label is `synthetic_fixture`, and tests and reports show that label next to each headline.

## Why synthetic

On 2026-10-16 the cloud build network refused every feed host (status.kraken.com, blog.kraken.com,
coindesk.com, cointelegraph.com, decrypt.co) with a proxy 403, and TypeSafe's documentation host did not
resolve. The cloud build plan allows a synthetic fixture in the published format in that case. Recording one
small fixture of each real feed is step L2 on the owner's PC.

## Feed files

`manifest.json` maps each recommended feed to its file and the real URL it stands in for.

| Feed id | File | Format | Stands in for |
|---|---|---|---|
| `kraken-status` | `kraken-status.atom.xml` | Atom 1.0 | `https://status.kraken.com/history.atom` |
| `kraken-blog` | `kraken-blog.rss.xml` | RSS 2.0 | `https://blog.kraken.com/feed` |
| `coindesk` | `coindesk.rss.xml` | RSS 2.0 | `https://www.coindesk.com/arc/outboundfeeds/rss/` |
| `cointelegraph` | `cointelegraph.rss.xml` | RSS 2.0 | `https://cointelegraph.com/rss` |
| `decrypt` | `decrypt.rss.xml` | RSS 2.0 | `https://decrypt.co/feed` |

What the items cover: a Kraken incident about a held coin, an old resolved incident (outside the check
window), a delisting and a listing of invented coins (`EXMPL`, `TSTC`), an ordinary Bitcoin headline, an
exploit elsewhere, a regulatory headline, a headline written as an instruction to the system (the injection
test), the same item twice in one feed and a tracking parameter (`utm_source`) on a link (repeat detection).
Every title ends in "(synthetic)". The coins and events are invented and say nothing about real companies.

## jev-scripted-answers.json

Scripted Jev replies keyed by headline link, for tests and for the fixture report
(`trade-graph news-alarm-poll --fixture-feeds tests/fixtures/news --scripted-jev
tests/fixtures/news/jev-scripted-answers.json`). The numbers are invented. A reply that leaves an option out
answers it 0. The reply shape (`answers` → question name → `probabilities` and optional `confidence`, plus
`model` and `usage`) follows the facts recorded in source S40; the exact field names are confirmed only by the
first credentialed call (step L2). A scripted reply is never reported as a real Jev or paid test.
