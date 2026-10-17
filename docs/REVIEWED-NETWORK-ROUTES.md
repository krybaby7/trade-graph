# Reviewed network routes

Status: built 2026-10-17 by cloud package W8-A (C3, decision D241). Paid research (Perplexity) is the first route;
W9-A (C6) adds the news feeds and TypeSafe as the second, the same way. Nothing here has been run against the
internet: the cloud has no installation, Docker or key.

## In plain words

The installed service sits on a private Docker network whose only neighbour is the owner's reviewed proxy. Until now
that proxy had two doors: one for the subscription login (Codex or Claude) and one for public market data (Kraken and
Frankfurter). A **reviewed route** is one more door, for one kind of protected work, that opens only to the internet
addresses you listed for it. Perplexity gets its own door, so research calls never share the market-data door and the
market-data door never reaches Perplexity.

## How it is built

A route is enforced in three places; each one alone refuses an address you did not list.

1. **The reviewed catalogue, in code** (`kernel/subscription_proxy.py`, `REVIEWED_ROUTE_HOSTS`, a protected path). It
   names every host a route may ever admit. Today:

   | Route | Hosts | For |
   |---|---|---|
   | `paid_research` | `api.perplexity.ai` | Perplexity's Agent API (`/v1/agent`) and Search API (`/search`), D124, D150 |

   It is separate from `PROVIDER_HOSTS` (subscription login) and `MARKET_HOSTS` (Kraken, Frankfurter), and a test
   keeps the three apart. Adding a host to the catalogue is a code change that goes through review.
2. **Your protected network profile** (`<owner>/subscription-network-profile.json`, root-owned, hash-pinned) lists
   each route you enable, its proxy port and the hosts it admits, a subset of the catalogue:

   ```json
   "reviewed_routes": {"paid_research": {"port": 8082, "hosts": ["api.perplexity.ai"]}}
   ```

   Each route needs its own port, different from the provider and market ports. A host outside the catalogue, an
   unknown route name, a repeated or unsorted host, or a port clash is refused. A profile without `reviewed_routes`
   loads and hashes exactly as before (older installations are unchanged).
3. **The proxy** (`python -m trade_graph.kernel.subscription_proxy`, baked into your reviewed proxy image) opens one
   listener per route with the same list:

   ```text
   --provider-port 8080 --market-port 8081 --route paid_research=8082:api.perplexity.ai
   ```

   The route's listener applies the same checks as the other two: CONNECT to port 443 only, exact host, no
   credentials or extra headers, every DNS answer a public address, the TLS name equal to the CONNECT host, no
   encrypted client hello, bounded sizes and times. A host it was not given is refused before any DNS lookup.

The application side adds a fourth check: the Perplexity transport (`HttpxProviderHttp`) sends only to the two fixed
Perplexity endpoints, only through `http://<proxy>:<route port>`, with no environment proxy, and the installed route
is built only when your profile's `paid_research` route admits both endpoints
(`PreparedSubscriptionNetworkProfile.route_admits`).

## Adding a second route (W9-A: news feeds and TypeSafe)

1. Add one entry to `REVIEWED_ROUTE_HOSTS` in `kernel/subscription_proxy.py`, for example
   `NEWS_FEEDS: frozenset({...the five feed hosts..., "api.typesafe.ai"})`, with a comment naming the source of each
   host. Nothing else in the proxy or the profile changes: both read the catalogue.
2. Build the caller's transport the same way as `application/perplexity_installation.pinned_transport`: the profile's
   `route_url("news_feeds")` as its only proxy, `trust_env=False`, and the fixed URLs it may reach; refuse to build it
   unless `route_admits("news_feeds", url)` holds for every one of them.
3. Extend the tests in `tests/security/test_reviewed_routes.py` (admits its hosts, refuses everything else, never
   reachable from the other listeners) and the L3 checklist: the owner adds the route to the profile and the proxy
   command, re-pins the profile and re-reviews the proxy.

## What the owner does (step L3)

- Add the `paid_research` route to `subscription-network-profile.json` (any free unprivileged port; 8082 is the
  suggestion) and re-pin the profile as step 3 already does for the new image.
- Recreate the reviewed proxy container with `--route paid_research=8082:api.perplexity.ai` added to its command, and
  record the new review (`proxy_review_sha256`).
- `python -I -B -m trade_graph.kernel.deployment_image check-subscription` (inside the new image) prints
  `subscription_paper_profile_prepared` when the profile, including its routes, validates.

A Docker inspection proves the proxy's identity, not what it admits; the proxy's routing remains your separate review
(as in [FUNDED-PAPER-PROFILE.md](FUNDED-PAPER-PROFILE.md)).
