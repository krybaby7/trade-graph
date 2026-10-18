# Reviewed network routes

Status: built 2026-10-17 by cloud package W8-A (C3, decision D241). Paid research (Perplexity) is the first route;
cloud package W9-A (C6, decision D256) added the news feeds and TypeSafe as the second, `news_feeds`, the same way on
2026-10-18. Nothing here has been run against the internet: the cloud has no installation, Docker or key.

## In plain words

The installed service sits on a private Docker network whose only neighbour is the owner's reviewed proxy. Until now
that proxy had two doors: one for the subscription login (Codex or Claude) and one for public market data (Kraken and
Frankfurter). A **reviewed route** is one more door, for one kind of protected work, that opens only to the internet
addresses you listed for it. Perplexity gets its own door, so research calls never share the market-data door and the
market-data door never reaches Perplexity. The news check has a door of its own too: it opens only to the five news
feeds and to TypeSafe's Jev.

## How it is built

A route is enforced in three places; each one alone refuses an address you did not list.

1. **The reviewed catalogue, in code** (`kernel/subscription_proxy.py`, `REVIEWED_ROUTE_HOSTS`, a protected path). It
   names every host a route may ever admit. Today:

   | Route | Hosts | For |
   |---|---|---|
   | `paid_research` | `api.perplexity.ai` | Perplexity's Agent API (`/v1/agent`) and Search API (`/search`), D124, D150 |
   | `news_feeds` | `status.kraken.com`, `blog.kraken.com`, `www.coindesk.com`, `cointelegraph.com`, `decrypt.co`, `api.typesafe.ai` | The news check's five feeds (D138 item 10, D142) and Jev's one endpoint `/v1/systemone` (D137, D140), D256 |

   It is separate from `PROVIDER_HOSTS` (subscription login) and `MARKET_HOSTS` (Kraken, Frankfurter), and tests keep
   them all apart (the Kraken status page and blog are not the Kraken API host). Adding a host to the catalogue is a
   code change that goes through review.
2. **Your protected network profile** (`<owner>/subscription-network-profile.json`, root-owned, hash-pinned) lists
   each route you enable, its proxy port and the hosts it admits, a subset of the catalogue:

   ```json
   "reviewed_routes": {"paid_research": {"port": 8082, "hosts": ["api.perplexity.ai"]},
                       "news_feeds": {"port": 8083, "hosts": ["api.typesafe.ai", "blog.kraken.com", "cointelegraph.com",
                                                              "decrypt.co", "status.kraken.com", "www.coindesk.com"]}}
   ```

   Each route needs its own port, different from the provider and market ports. A host outside the catalogue, an
   unknown route name, a repeated or unsorted host, or a port clash is refused. A profile without `reviewed_routes`
   loads and hashes exactly as before (older installations are unchanged).
3. **The proxy** (`python -m trade_graph.kernel.subscription_proxy`, baked into your reviewed proxy image) opens one
   listener per route with the same list:

   ```text
   --provider-port 8080 --market-port 8081 --route paid_research=8082:api.perplexity.ai
   --route news_feeds=8083:api.typesafe.ai,blog.kraken.com,cointelegraph.com,decrypt.co,status.kraken.com,www.coindesk.com
   ```

   The route's listener applies the same checks as the other two: CONNECT to port 443 only, exact host, no
   credentials or extra headers, every DNS answer a public address, the TLS name equal to the CONNECT host, no
   encrypted client hello, bounded sizes and times. A host it was not given is refused before any DNS lookup.

The application side adds a fourth check: the Perplexity transport (`HttpxProviderHttp`) sends only to the two fixed
Perplexity endpoints, only through `http://<proxy>:<route port>`, with no environment proxy, and the installed route
is built only when your profile's `paid_research` route admits both endpoints
(`PreparedSubscriptionNetworkProfile.route_admits`).

## The second route: news feeds and TypeSafe (W9-A, D256)

Built as W8-A planned it: one catalogue entry (`NEWS_FEEDS` in `kernel/subscription_proxy.py`, each host's source named
in its comment); nothing else in the proxy or the profile changed. The callers live in
`application/news_installation.py`:

- `pinned_feed_fetcher(profile)`: T44's feed fetcher with `route_url("news_feeds")` as its only proxy, no environment
  proxy, no redirects, no local DNS lookup (the proxy resolves and admits only public addresses), and a feed refused
  before anything is sent unless `route_admits("news_feeds", url)` holds (reported as a lost feed, `route_not_listed`).
- `pinned_jev_transport(profile)`: T44's `PinnedJevHttp` (the one official endpoint) through the same port, no
  environment proxy. Jev checks are built only when the route admits `https://api.typesafe.ai/v1/systemone`.

The owner may list a subset of the hosts (for example without `api.typesafe.ai` while Jev stays off); the others are
then refused by the proxy and by the fetcher. Tests: `tests/security/test_news_feeds_route.py` (admits its hosts, refuses
every other host before DNS, never reachable from the other listeners, both transports' CONNECT passes the strict
admission) and `tests/unit/test_news_feed_route_fetcher.py`.

A third route is added the same way: one catalogue entry, a pinned transport using only `route_url` and refusing what
`route_admits` refuses, and the owner's profile and proxy lines.

## What the owner does (step L3)

- Add the `paid_research` route to `subscription-network-profile.json` (any free unprivileged port; 8082 is the
  suggestion) and re-pin the profile as step 3 already does for the new image.
- For the news check: add the `news_feeds` route too (8083 suggested; the six hosts above, in that sorted order).
- Recreate the reviewed proxy container with `--route paid_research=8082:api.perplexity.ai` and
  `--route news_feeds=8083:api.typesafe.ai,blog.kraken.com,cointelegraph.com,decrypt.co,status.kraken.com,www.coindesk.com`
  added to its command, and record the new review (`proxy_review_sha256`).
- `python -I -B -m trade_graph.kernel.deployment_image check-subscription` (inside the new image) prints
  `subscription_paper_profile_prepared` when the profile, including its routes, validates.

A Docker inspection proves the proxy's identity, not what it admits; the proxy's routing remains your separate review
(as in [FUNDED-PAPER-PROFILE.md](FUNDED-PAPER-PROFILE.md)).
