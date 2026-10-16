# Decision log

| ID | Accepted planning decision | Reason / revisit condition |
|---|---|---|
| D01 | Modular Python, one paper-runtime process, isolated Engineer runner | Small footprint; protected kernel process before broader mutable code |
| D02 | SQLite paper R1 and thin LangGraph | Local durable experiment; PostgreSQL when multi-host/writer requirements justify it |
| D03 | USD10,000 virtual spot account, BTC/USD and ETH/USD where supported, EUR reporting | Updated owner instruction; amounts/currencies remain configurable |
| D04 | Kraken public feed as technical reference; explicit broker capabilities | No live eligibility or fee-optimality conclusion |
| D05 | Both provider adapters; one-provider installation allowed | Independence without requiring two paid accounts |
| D06 | Six model roles with software administration/accounting/execution | Preserve responsibilities without permanent agent conversations |
| D07 | R1 implements real tested artifact/prompt/config changes | Complete improvement loop before arbitrary code authority |
| D08 | Real operating budget separate from virtual capital; paid calls disabled until configured | Optional lean EUR5/month example is not authorization or a permanent cap |
| D09 | Event-driven low-frequency reasoning with continuous software management | Avoid unnecessary token use without a per-trade committee |
| D10 | The 2026-09-29 handoff supplied a plan, task DAG and runnable planning utilities | Historical. Runtime code landed later at `a4bdce6`; this row does not describe the current tree |
| D11 | Paper resets cannot erase real expenses or refill the API allowance | Native accounting and cross-experiment cost provenance remain auditable |
| D12 | Provider-neutral adapters target REST bodies rather than vendor SDKs | Superseded for transport shape by D20. Fixture `parse` is still not a credentialed call |
| D13 | The Engineer sandbox is a fresh temporary git repository of allowlisted files | A worktree of the product repo would mount deployment git credentials. Revisit only with a scoped remote that cannot push protected paths |
| D14 | Paper and reserve fees use an explicit fixed conservative tier, maker 0.004 and taker 0.008 | Rolling venue volume is not known offline. Replace with the owner's actual fee schedule when a venue account exists |
| D15 | Superseded security claim: the multiprocessing/RPC example is only a protocol fixture, not an OS sandbox | Executable plugins and trusted promotion are disabled. Build and adversarially test a real protected boundary before broader code authority |
| D16 | Fixed in `9e908d4`: compare aggregate exposure and equity in the same currency using identified FX provenance | Numeric USD-versus-EUR comparisons are invalid even in a demo. Preserve the currency regression tests |
| D17 | Durable scheduler claims carry unique fencing tokens; schema migration 0002 is additive and transactional | Reclaim stale work without stale-worker result writes. Reconcile prior external effects before retry; task recovery never authorizes duplicate orders or free retries |
| D18 | Authorization reads the active persisted owner policy and mandate. Exposure and quote-age limits are the tighter of those revisions. `Execution` is bound to a `Broker` whose capabilities must match venue/mode, support client-id lookup, and declare withdrawals disabled. OpenAI, Anthropic and scripted adapters implement `InferenceAdapter` | Caller-supplied exposure caps are not authority. Fixture `parse` is not a network call. A discretionary mandate may name a strategy that is not prelisted; a non-discretionary mandate may not |
| D19 | Public Kraken metadata/ticker/WebSocket and Frankfurter ECB reference FX go through injectable transports. Scripted tests prove reconnect, gap backfill, out-of-order rejection and point-in-time replay. A separate public smoke is not an order test | 2026-09-30 smoke: AssetPairs XBTUSD, REST ticker, one v2 ticker snapshot, ECB USD/EUR. Reconnect was not exercised on the live socket. Reference FX is not an executable quote |
| D20 | Provider calls go through an injectable HTTP transport. A missing API key records synthetic receipts and does not reduce the real allowance. Tool continuation runs only registered tools and reserves each step. `enabled_providers` is the one-provider install switch | `HttpxProviderHttp` is present and was not called. No OpenAI or Anthropic credential probe was run. Scripted transport success is not billed-provider success |
| D21 | Schema repair, transport retry and provider fallback each take a new reservation and share the root-task limit. A fallback must name its own price card. Invoice reconciliation appends the unexplained difference and rejects a conflicting total for the same invoice id. Migration `0003` is additive | The invoice figure in tests is supplied, not downloaded from a provider. A paper cash deposit does not change the operating allowance |
| D22 | Pause advance cancels only the orders the profile names, and it records a terminal achieved state only after reconciliation. `FLATTEN` keeps its own reduce-only exit and stays `flattening` until inventory and outstanding orders are gone. `STOPPED` stays blocked while either remains. An empty paper fill page ends pagination | This is the paper broker. A live venue outage was not run. Unknown status is not treated as a completed cancel |
| D23 | One role worker holds the process lease. A reclaimed task runs reconciliation before its handler, and a mismatched expected version is dead-lettered with no handler call. A context snapshot is written before the attempt | Handlers are injected. This does not implement Research or Trader behavior, and the worker does not call a model provider |
| D24 | Research pages are stripped, hashed and cached with publication, retrieval, availability and expiry. A refresh inserts a new finding. Untrusted page text and stale findings stay out of Trader context. The Trader records enter, hold and no-action from a validated payload and does not ask the Leader | Two templates are unproven hypotheses. A model timeout is an activity failure, not a hold. No credentialed search provider is called |
| D25 | Lesson revisions append and must keep prior counterexamples. The grade is the process assessment. No-trade marks use only quotes available at the decision time. An optimisation proposal that disables reconciliation is rejected | This is not a forward-paper profitability study |
| D26 | The Leader can assign tasks, set a coalesced schedule and install a tighter mandate. The Leader cannot raise the owner allowance, lift an owner halt, or approve an individual order. Secretary digests report pause and task counts and do not create orders | This is routing, not a dashboard or a continuous organisation loop |
| D27 | The artifact runner copies only allowlisted paths into a temporary git tree, runs the installed checks module with a scrubbed environment, and stores the exit code under the checks-module hash. Activation reads that row. A `runner` label on the candidate is ignored | The local check is not a paid model call. Migration `0004` is additive. This does not isolate arbitrary code |
| D28 | Activation waits until no trader task is leased or running, cancels queued trader tasks pinned to another version, and compare-and-sets the artifact pointer. Rollback restores that pointer only and leaves fills, receipts and decision version ids in place | A running reconciliation task is not a trader decision. Rollback to a hash that was never active is rejected |
| D29 | Owner budget writes persist through the authenticated API and require CSRF for cookie sessions. Leader activation uses the controller attestation row. Organisation, cost and change pages are server projections. Live enablement stays closed | Paper equity and actual operating spend are labeled separately. Task text that looks like a key is redacted. This is not a hosted dashboard |
| D30 | Commissioned Engineer work persists request/run/provider/billing identity before gateway dispatch, scopes every receipt to the same task/root/version and defers bounded repairs/retries. Unknown dispatches wait for reconciliation; completed effects recover without a second model call | T13 application workflow completed on 2026-10-01; no paid provider call or operating-service soak |
| D31 | R1 checks consume bounded data over stdin in a non-root Linux seccomp process with denied filesystem/network/process operations. Fresh local recovery directories retain prior evidence, with three recoveries per invocation | Builds on `9376d2d`; fail closed on unsupported hosts. Data-only checker confinement does not grant executable plugins or satisfy T21 protected-kernel isolation |
| D32 | Historical `artifact_config` commissions cover registered JSON data grammars; prompt paths need explicit `prompt` owner/commission authority. Model/provider/graph artifacts without a registered grammar stay rejected | Compatibility preserves existing context-policy commissions without letting the JSON permission class silently authorize prompts |
| D33 | T14 persists actual tested bundles and manifests; activation requires a loadable predecessor and fences a monotonic generation. Consumers reconcile, reload actual bytes and acknowledge that generation. Rollback records a restored generation and reloads it without changing financial state | Extends D28 beyond pointer-only behavior. Additive application migration `0007` belongs to protected software; mutable artifacts cannot migrate the database |
| D34 | The controller pins functional observation gates before activation: three healthy decisions, zero failed samples, one hour for observation, 60 seconds for reload and a 262,144-byte context ceiling. Samples derive from scoped persisted requests/decisions/failures and provider usage. Task completion and observation commit atomically | Typed bounds are trusted controller configuration, not candidate prose. Failed health or a deadline triggers deterministic rollback; corrupt recovery blocks fresh decisions while reconciliation/protection and owner pauses persist. These gates do not certify profitability |
| D35 | Loaded prompts enter conservative request reservation bounds. Artifact schedules control only the artifact-owned review namespace; trusted routing supplies bounded monetary allocations. Report layouts select registered data sections without executable templates | Rebuilding prompts, context limits, registered strategy guidance, reports and schedules grants no additional provider/tool/code authority. Continuous service assembly remains T17 |
| D36 | Dashboard money is projected from one SQLite read snapshot over ledger events, receipts, allocations and reservations. Current marks use the tighter owner/mandate quote-age bound (30 seconds without policy); reference/daily FX defaults to seven days and other FX to one day, with an explicit runtime override | Historical event-time FX and stored receipt accruals are retained. Legacy cross-currency receipts lack linked source IDs; projections disclose that limitation rather than invent provenance |
| D37 | Dashboard owner policy and budget commands share a deployment revision and bind replay IDs to their actual portfolio/resource. Resume enters manage-only before reconciliation and fences newer owner controls. An emergency manage-only command remains available after interruption | Authenticated cookie writes require CSRF; browser retries preserve unknown command identity. A hard-crashed command remains visibly pending until controller recovery; it cannot be silently replayed |
| D38 | The dashboard supports protected venue/instrument/change-class/exposure settings, budgets and management profiles. Model-routing writes remain unavailable until an approved persisted consumer is assembled | This is an explicit T17 operating-service limitation. A successful configuration response cannot claim a routing change that no worker consumes |
| D39 | Role expense caps share one deployment allowance; their sum is not additional funding. Paper dashboard serving requires a private resolved database directory and binds loopback with one web worker | Global reservations and task commitments continue enforcing the shared pool. Dashboard construction starts no scheduler or paid provider |
| D40 | Trading drawdown uses retained point-in-time ledger/mark/FX observations and current valuation, adjusting subsequent external flows and embedded operating expenses in Decimal reporting units | This is sampled trading drawdown, not continuous or economic drawdown. Missing/stale observations stay provisional; absent historical allocation timestamps cannot support an invented economic curve |
| D41 | Offline acceptance binds concrete collected JUnit outcomes to source/lock/Python identity, separates test success from criterion coverage and scans generated evidence before CI upload | Missing/skipped/partial evidence cannot complete T16. Credential-free process-local socket denial does not certify OS-wide isolation, paid providers or live operation |
| D42 | Trusted scheduler roots default to 40 durable attempts across restart. Trader evidence excludes future-created lessons and current retired revisions without resurrecting predecessors | Cost/depth/descendant bounds still apply. Point-in-time context and retirement remain explicit rather than relying on model compliance |
| D43 | Provider outputs and registered tool arguments use bounded complete Draft 2020-12 JSON Schema validation with local references and denied external retrieval; rejected outputs retain supplied usage | Pins jsonschema/referencing. Byte/depth/node bounds apply before validation; typed domain contracts remain authoritative |
| D44 | Known public-feed degradation and unresolved same-account/symbol execution block increases; trusted reserves cover the broker's advertised fee bound, and PaperBroker advertises only implemented GTC | Reconciliation/protection remain available. Unsupported features produce typed refusals before effects rather than silently changing requested behavior |
| D45 | The installed paper runtime uses one lifetime database flock, durable service/worker leases, coalesced schedules and independent database handles for background role/feed work. Maintenance covers every paper account, while configured graph work is scoped | Slow calls and shutdown draining cannot suspend reconciliation/protection or release the exclusive service lock early. Mixed paper/live databases are refused before paper maintenance |
| D46 | Paid operation requires both private runtime opt-in and persisted owner paid permission, current immutable approved prices, credentialed routes and a separately bounded real allowance. Approved model routing is a distinct owner-granted artifact class | Installed defaults include no allowance or paid permission. Routing activates through the tested artifact lifecycle; no role gains credential, budget or provider approval authority |
| D47 | Owner commands retain controller-written identity/phase/effect evidence. Exclusive service startup recovers only proven local outcomes; ambiguous external outcomes remain failed for owner review | Recovery does not replay unknown effects. Cached terminal replies and pause achievements are fenced against newer commands; Leader may recover only its own pause |
| D48 | Funded paper observation reserves a fresh private report before effects, records elapsed time, actual versus upper-bound forecast expense and every unresolved held amount, and retains interruption evidence | Synthetic transports cannot certify credentialed providers. A recorded soak does not prove profitability, reconcile provider invoices or authorize live trading |
| D49 | Forward evaluation fixes its protocol and trial family before the held-out horizon, imports a complete sealed expense inventory, and compares paired net-economic blocks against three preregistered baselines with conditional Decimal uncertainty | All attempts, failed/setup/shared costs and variants remain visible. Sampled risk carries high-water marks across the full trial; missing flow timing, dependence, bounds or provenance produces insufficient evidence |
| D50 | Historical FX conversion requires source observation, validity and retrieval availability by the financial timestamp, including historical external flows | Daily reference dates stay intact; retrieval does not move them forward. Current cash may be valued while opening valuation and historical performance remain provisional |
| D51 | Kraken live preparation implements explicit normalized read-only reconciliation and guarded order mapping, with advertised fees prepared before writes. No live runtime is assembled or enabled by the paper CLI | Official wire-source review and synthetic conformance are preparation. Owner venue eligibility, least-privilege permissions, authenticated account evidence and any real-order authority remain separate prerequisites |
| D52 | A standalone owner-pinned process harness executes arbitrary candidate Python only after irreversible Linux seccomp confinement in a fresh scrubbed interpreter, accepting bounded numeric snapshots/proposals | Actual-host synthetic adversarial tests establish this scaffold's boundary. Production kernel/gateway/controller extraction, authenticated business RPC, immutable deployment images and host rollout remain unfinished; broader Engineer code classes stay disabled |
| D53 | Public acquisition refuses compressed/redirected/oversized responses and requires the wire's matching FX pair and actual canonical source date | Requested values cannot manufacture missing provenance. Current public probes remain proxy-blocked; local tests are not external acceptance |
| D54 | Forward-report snapshots bind exact retained source bytes and the complete current registry, protocol and expense inventory under one transaction | A later receipt, resolution or family variant invalidates old handoffs. Imported evidence remains unverified; snapshots do not authenticate future-paper economics |
| D55 | Native broker ownership is established only after complete response validation and can be reconstructed from scoped durable authorized-intent identities | Ambiguous/reused IDs, unsupported spot/ledger movements and inconsistent native responses fail closed. Authenticated current venue conformance remains pending |
| D56 | Live prerequisites use separately pinned private issuer evidence and current protected state; both readiness and enablement remain closed without authoritative upstream verification | Signed declarations and diagnostic labels cannot grant live authority. Dashboard claims are explicitly unverified and owner effects awaiting review stay pending |
| D57 | The opt-in protected paper runtime binds one-use authenticated requests to actual financial/authority/version state and journals decisions, intents and reservations atomically in the trusted parent | Confined source cannot reach financial services directly. Independent management, exclusion, cancellation draining and rollback preserve finance; full graph/model extraction and immutable intended-host release proof remain pending |
| D58 | Pure-feature plugins stage in sealed content-addressed private storage and receive parent-authenticated finite replay receipts from fresh confined processes | Tested-corpus repeatability is limited evidence. Broader class grants, shadow evaluation, immutable runtime builds and automatic production rollout remain separate T22 work |
| D59 | Delayed submission replies cannot overwrite newer fill/cancellation/recovery facts or release partial-fill reservations; conflicting native identities remain incomplete across reconciliation | Submission completion and reservation effects share a writer transaction. A known conflict requires explicit protected resolution rather than treating owned fills alone as complete account evidence |

| D60 | Optional model expense FX source IDs bind the exact stored pair, value and available source bytes before dispatch; usage receipts retain that frozen link and actual conversion value | Migration 0011 leaves legacy provenance NULL. Later source drift cannot erase billed facts and independently invalidates evidence; a source link does not authenticate an external publisher |

| D61 | Provider dispatches persist exact endpoint/request digests before external effects; the built-in HTTP transport refuses redirects/compression and bounds request/response bytes, elapsed time and network phases | Protected private response observations survive billing interruption, unknown attempts never redispatch, and custom transports stay unverified. Local wire hashes do not authenticate external acceptance, billing or profitability; raw bodies/headers/keys are absent from the journal |

| D62 | Operations observations retain bounded actual local database/configuration/package/host facts and backup/restore artifacts; future read-only venue collection requires a separately scoped owner grant and retains exact bounded native wire evidence | Local fingerprints do not designate the intended host. Synthetic transports cannot establish authenticated venue observation, eligibility, permanent key permissions or write/protection conformance |
| D63 | Runtime evidence binds before the untouched horizon, retains the complete bounded current database inventory and derives expense exports from protected native records | Native journal ownership, chronological availability, historical cost continuity and invoice arithmetic are independently audited. Retained bytes and linked source IDs do not authenticate external prices, bills or future market performance |
| D64 | The opt-in departmental runtime confines mutable graph planning/result transformation for all six actual durable handlers; provider routing, credentials, schemas, monetary reservations, invocation recovery and effects remain protected | Only parent-written atomic successful-effect acknowledgments establish departmental rollback baselines. Portfolio-scoped claims, independent management and fenced mutable-failure recovery preserve queued work, billing and finance; immutable OS images, intended-host proof and default-runtime adoption remain separate |
| D65 | Pure-feature runtime bundles contain sealed actual source, trusted worker/sandbox and manifest bytes; independently pinned Python environment checks precede confined execution | This is an executable application bundle, not a complete immutable OS/dependency deployment image. Bytecode lookup cannot substitute ambient unpinned cache files; host integrity and immutable distribution remain unfinished |
| D66 | Functional shadow executes independently pinned actual baseline and candidate bundles against retained point-in-time numeric cases, repeated under protected resource/compatibility/quality limits | Exact Decimal comparison and retained negative attempts survive protected-pin drift. Finite functional quality is not economic evidence, a broader Engineer grant or production activation/migration/rollback proof |
| D67 | Live readiness revalidates exact retained runtime, host and venue collector sources, inventories and scope; public economic status stays insufficient with issuer declarations separately labeled | Missing live instrument/policy bindings, host designation and authenticated provenance remain explicit pending reasons. Dashboard records and signed declarations cannot grant readiness, enablement, spending or live authority |
| D68 | Operations preflight validates exact public native instrument identities and retains private authenticated local observations using descriptor-based path checks | Public connectivity and funded observation must succeed separately; local host fingerprints do not designate the owner's intended host |
| D69 | Evaluation may bind bounded lossless compressed retention before its untouched horizon, with atomic snapshots and global stored/expanded limits | Exact retained bytes and complete historical costs survive longer bounded runs; authenticated external producers and genuine forward evidence remain required |
| D70 | Native fill quote principal is retained separately from reported price, quantity and fees and drives actual cash/FIFO accounting | Explicit precision, exact preflight and sticky execution-limit discrepancies prevent silent rounding or renewed increases. Unsupported fee vectors, corrections and factual partial-fill bounds remain closed |
| D71 | Live increases require the concrete durable pilot lifecycle before decision/reservation and dispatch; only fresh explicitly scoped history can release holds | The gate remains closed pending verified upstream evidence. Owned-intent completeness cannot establish full-account completeness, and collocated economic captures can become stale after live writes |
| D72 | The offline protected image pins the complete base/dependencies/application, validates exact created-container confinement and loads read-only owner pins before service admission | Actual local image/process/restart tests prepare intended-host review. The network-disabled image does not grant funded credentials, external connectivity or production authority |
| D73 | Offline plugin commissioning may activate exact synthetic candidates with authenticated health and deterministic rollback; a separate parent-staged Secretary class rehearses compatible sidecar migration | Finite functional evidence and disposable contraction do not grant production code classes, general migrations or a commissioned selected-application route. Financial history remains outside candidate authority |
| D74 | Native fills may retain exact signed per-asset fee components, and earlier fills append complete-prefix chronological correction facts and source manifests without rewriting prior journal groups | Charges and credits retain native provenance and point-in-time history. Replay remains bounded; later fee amendments and unsupported event-time ties require separate contracts |
| D75 | Paper secondary-asset fee holds retain immutable original plans and consume gross charges without replenishment from rebates | Unknown attempts and restart preserve commitments. Conditional partial-fill arithmetic supplies no factual venue bound or live reserve authority |
| D76 | Exact installed service-unit/configuration binding and actual two-process paper restart proof are independently retained | Local proof does not designate the intended host or establish actual systemd, off-host restore, delivered alerts or funded operation |
| D77 | Four-arm paper producers bind complete source/protocol/cost history before an untouched horizon; original historical captures remain verifiable after legitimate later operations | Historical verification must still reject omitted current costs/trials. Private authenticated owner mapping binds full live scope without authenticating external economics |
| D78 | Native discrepancy review uses concrete scope-bound owner receipts and revocation, with optional exact Execution integration | Synthetic capture leaves the incident pending. Source, finance, hold or controller drift blocks increases; review never resumes trading or releases uncertain commitments |
| D79 | Streaming financial continuity and protected provider origins publish a separate private witness after committed facts and before authority or outbound work | Complete bounded scans preserve history beyond the old snapshot cap. Whole-host rollback needs an external anchor; manifest transitions and oversized initial/correction replay remain separately reviewed work |
| D80 | A separately pinned funded-paper provider profile admits only exact owner files, endpoints, immutable proxy identity and inspected private confinement | Proxy route policy and intended host require external review. Public-data routing is not implemented by this profile; paid permission and real funding remain separate |
| D81 | Commissioned Secretary application preparation uses fenced leases, insert/CAS sample receipts, confined effects and authenticated completion time | On-time complete health recovers without repeated effects, even after the deadline; stale workers cannot rewrite or roll back replacement work. Production scheduling/classes, economic evidence and general migrations remain closed |
| D82 | Save the owner's broader crypto/stock/ETF opportunity-selection direction as [deferred work](MULTI-ASSET-DIRECTION.md), while continuing the existing crypto implementation now | Revisit on owner request or after reviewing initial crypto evidence. No equities, additional execution venues, spending or live authorization is implied. |
| D83 | Prioritize [usable point-in-time crypto history, explicit paper friction, actual operating connectivity and forward comparisons](CRYPTO-NEXT-STEPS.md) | Latest-quote context does not supply the starter templates' historical features; stored broker latency is not currently applied. Existing completed scopes and protections remain preserved; this plan is not new runtime acceptance evidence. |
| D84 | Confirm the eventual crypto execution venue before extensive fill-model work, and test that venue's own API | Kraken spot is the only implemented live adapter and remains a conditional candidate. Hosted virtual fills are still simulations; actual fill/fee acceptance needs a separately authorized small live pilot. Keep local replay/failure tests. Current practice API availability and authentic account acceptance remain unverified. |
| D85 | Add [Mission Control](MISSION-CONTROL.md) to visualize recorded progress, departmental activity and bounded Kraken test missions | Five-second authenticated projections separate project acceptance from runtime state and test results. Only local scripted rehearsal and fixed public Kraken checks can run; private-account/order/live tests remain pending. A private test-history sidecar preserves results without altering financial state or granting readiness. |
| D86 | Use the owner's [Windows PC with WSL2 Ubuntu](WINDOWS-LOCAL-TESTING.md) for initial testing; defer cloud hosting | This supersedes the proposed immediate VPS. Start with no-paid local/public checks; actual WSL2 acceptance remains pending. The current direct model API adapters are separate from Claude/Codex subscriptions, and no subscription-backed runtime adapter is implemented. |
| D87 | Connect a BTC/USD owner-local hidden-prompt command to the existing signed, pinned read-only collector and a minimal historical Mission Control projection | A fresh bounded grant is separate from graph authority. Private retained evidence stays outside the checkout and runtime; the sidecar receives verifier-derived public metadata only. Browser execution stays disabled, expiry and partial stages remain visible, and synthetic tests do not establish account connectivity, reconciliation, permission inventory or T17–T22 acceptance. |

| D88 | Accept legitimate Kraken public catalog asset codes of one to 32 characters, starting with an ASCII alphanumeric and allowing alphanumerics, dots and underscores thereafter | Retained and current public Assets contain one-character codes and USD_CREDIT. Validate the full catalog without expanding selected BTC/USD scope; retain credit identity and existing accounting/authority contracts. See the 2026-10-06 [verification](../IMPLEMENTATION-STATUS.md). |
| D89 | Supply active strategy fields from completed Kraken hourly candles through a bounded public-only collector for BTC/USD and ETH/USD | [Documented lookbacks and Decimal formulas](KRAKEN-PRICE-HISTORY.md), immutable revisions, receipt-time availability, distinct public/synthetic sources and explicit missing slots preserve point-in-time evidence. Exclude Kraken's final uncommitted candle; unknown exchange publication stays unknown. This source slice grants no paid/live authority or acceptance transition. |

The 2026-09-29 USD10,000 update supersedes EUR100 as the operating example. EUR100 golden accounting fixtures and small-live-allocation sensitivity language are tests/examples only. See docs/10-PAPER-CAPITAL.md; no live allocation has been set.

## Remaining decisions

Owner/runtime: legal residence/entity and exchange eligibility; actual venue/pairs/fee tier; model accounts and measured quality; actual expense budget including taxes/FX/hosting; deployment availability; exposure/loss/experiment envelope; live permission; broader engineering classes. Keep private answers out of public Git.

Implemented local slices include the registered R1 artifact loop and the bounded preparation recorded in D68–D81. Actual Linux process isolation and immutable-container rehearsal are implemented; the historical D15 protocol fixture is not their proof. Credentialed model probes, downloaded provider bills, genuine forward economics, native venue conformance and owner-approved intended-host attestation remain open. Factual future venue partial-fill/fee bounds, authenticated external producers, complete public-data/funded routing, externally anchored host recovery, production application integration and owner grants of broader code classes remain required. See [implementation status](../IMPLEMENTATION-STATUS.md) for current evidence and scope.

None prevents offline development. Mark credentialed verification pending when credentials are absent; do not invent successful tests or treat implementation completion as permission to trade or spend.

| D90 | [A bounded Research subscription diagnostic](reviews/2026-10-06-research-subscription-preflight.md) collects public data but stops before inference when installed CLI constraints cannot meet owner authorization | Codex 0.125.0 has unconfigurable built-in retries under the documented route; Claude Code is logged out. Preserve unknown usage/cost, private evidence and financial state; no continuous graph, API funding or live authority. |

| D91 | Owner-triggered Optimisation supersedes weekly/default/artifact/Leader automatic Optimisation; one bounded cycle retains protected Engineer/activation controls | Owner instruction 2026-10-06. Ordinary Research, Trader, Learning and Leader scheduling remain automatic within permissions. Repeated starts attach to one service. The same cycle request replays its saved outcome; another request while a cycle is active or unresolved is rejected explicitly. |
| D92 | New startup controls use subscription-only AI admission, separate usage records and no retry/repair/fallback or direct model API billing | Official native CLI login and actual filesystem confinement must be verified; unsupported retry controls, unknown login/capacity or extra-usage permission block inference. Unknown subscription cost is not zero and paper capital cannot replenish it. |
| D93 | Protected live startup remains separately commissioned and disabled in this installation | Scripted broker/conformance and local controller tests establish implementation behavior only. This task forbids private Kraken requests and real orders; source completion does not grant allocation, key permissions, host admission or live authority. |

- **D94 (2026-10-06):** Owner-requested startup source can be implemented/tested while credentialed T17 acceptance is blocked. Retain acceptance states and all independent live/Engineer commissioning gates; synthetic tests do not satisfy missing dependencies.

- **D95 (2026-10-07):** The owner permits bounded documented CLI-internal retries
  within included subscription quota. Select the implemented native Claude route
  with explicit zero transport/timeout retries and one structured attempt; retain
  disabled application retries, fallback, extra usage and API billing. Earlier
  Codex zero-retry blockers are historical, not a present owner restriction.
  [Final-image evidence](reviews/2026-10-07-first-paper-cycle.md) gates this paper
  route separately from subscription login, financial acceptance and live authority.


- **D96 (2026-10-07):** Normal local Windows/WSL paper
  operation supersedes D92/D95 and the first-cycle diagnostic operating policy.
  Enable configured departmental schedules and recurring Optimisation, normal
  supported CLI retries, bounded configurable application attempts and compatible
  independently verified existing-subscription fallback. Remove a prerequisite
  Research diagnostic, mandatory first-cycle pause and one-generation policy.
  Reuse the existing portfolio/mandate without funding an API budget. Retain
  structured contracts, truthful unknown subscription costs, protected credential
  boundaries, persistent attempt receipts and order-state reconciliation. Real
  trading, API billing, paid extras and purchases remain unauthorized. Native
  Codex quota/credit metadata establishes actual included capacity; a
  Claude-specific extra-usage checkbox cannot substitute for that evidence.
  [Normal operating runbook](NORMAL-PAPER-OPERATION.md) records supported limits
  and genuine missing capabilities. Historical diagnostic evidence remains intact.

- **D97 (2026-10-07, owner model selection):** Use only GPT-6.1 Sol, exact model
  ID `gpt-6.1-sol`, through the existing Codex subscription route for every AI
  department and retry. This narrows D96: no alternate-model/provider fallback
  is authorized. The protected primary profile selects `codex_subscription` /
  `gpt-6.1-sol`, has `fallback_profiles: []`, and retains a catalog containing
  only that exact model. If this model is unavailable or its allowance is
  exhausted, pause affected AI work while retaining necessary paper-position
  management. Keep this personal choice in protected owner configuration,
  rather than hardcoding it into provider-neutral domain contracts. Preserve
  requested and actually reported model identities; an unreported identity
  remains unknown. Recording this choice does not establish a new inference
  result or prove that the private PC configuration has been updated.

- **D98 (2026-10-07, morning owner instruction):** Normal paper startup is
  authorized after installing and verifying fresh, serialized subscription quota
  admission. Preserve 30 percentage points of included quota plus 10 points of
  practical headroom: a fresh weekly reading is required, and every reported native
  window must have strictly more than 40% remaining before each dispatch or retry.
  Missing shorter windows are disclosed without independently blocking admission.
  Disclose unavailable windows,
  shared-account consumption and unbounded active-call usage; this threshold does
  not guarantee a 30% balance after a call or across unreported windows. Watch the
  first normal run and stop new AI at 40% or below, on metadata failure, or after an
  unexpected single-attempt drop greater than 10 points. Dashboard, deterministic
  reconciliation and paper-order/position management continue under MANAGE_ONLY.
  All six AI departments and their retries use **gpt-6.1-sol only**. The protected
  profile has `fallback_profiles: []`; the pinned catalog contains only that model
  slug. Unavailability pauses AI instead of selecting an alternate model, provider
  or separately billed route. This supersedes D96's fallback permission and the
  overnight start hold. Authorization is separate from actual execution evidence;
  live trading, paid extras, API billing and purchases remain unauthorized. See the
  [normal operating runbook](NORMAL-PAPER-OPERATION.md).


- **D99 (2026-10-08, owner-authorized repair):** Protected financial manifest
  upgrades use the offline [authenticated transition](FINANCIAL-TRANSITION.md),
  an exact root-distributed approval, original key/database inode, owner
  MANAGE_ONLY, exclusive service ownership, complete state commitments and all
  retained witness scopes. Preserve previous checkpoints and native/subscription
  attempt identities; revoke old capabilities rather than rewrite history.
  Interrupted publication can finish only the same already-authenticated operation
  against unchanged committed state. This is no general restore, key rotation,
  reseal or resume authority. Older checkpoints cannot retroactively prove identity
  commitments they never retained. Synthetic tests and independent review are
  separate from an actual installation transition.

- **D100 (2026-10-08, storage incident):** Preserve the unreadable current database,
  independent witness, protected owner settings, failed attempts and verified
  hold-time backup. Do not replace the authoritative database or call it recovered
  after a failed header-only forensic repair. Keep the worker/dashboard stopped
  pending a separately reviewed storage recovery and SQLite runtime. Retaining
  auxiliary database descriptors fixes a reproduced POSIX lock-loss defect;
  neither it nor the old runtime's SQLite version proves the corruption cause.
  Leader's original native CLI cause also remains unknown. No new AI, paid call,
  live order or quota admission is claimed while the installation is stopped.

- **D101 (2026-10-08, owner-authorized non-destructive recovery):** Preservation
  retains original damaged bytes/history and permits a separate verified recovery
  copy. A new inode requires the dedicated [authenticated recovery](FINANCIAL-RECOVERY.md),
  not the original-inode manifest transition or fresh witness initialization.
  Retain all witness scopes/checkpoints and recovered attempt prefixes, original
  unknown costs and owner MANAGE_ONLY. Missing newer financial payloads block
  promotion and restart. The repaired image uses hash-pinned official SQLite
  3.53.4; storage checks and a reproduced locking defect do not establish the
  original corruption cause.


- **D102 (2026-10-08, explicit owner reporting-gap acceptance):** Personal paper
  recovery may record proven missing historical public-price/reporting observations
  without manufacturing payloads or claiming complete history. Independently
  verified financial/AI state, unknown usage/costs, original witness commitments,
  old run and account totals remain mandatory. Bind exact gaps, conservative
  interval, remaining uncertainty and a fresh evaluation boundary into the
  authenticated identity-recovery receipt; then transition the manifest. The
  legacy witness's historical proof limits remain explicit. Run one MANAGE_ONLY
  worker and dashboard in the same protected application container to retain the
  strict dedicated-network invariant. Normal paper AI requires a later explicit
  owner instruction and management-to-normal handover before Owner Resume.


- **D103 (2026-10-09, owner-authorized resource repair):** Subscription deployments require finite 192 cgroup tasks and 1 GiB memory with equal memory-swap, retaining one CPU and all isolation. Limits count threads and processes. The supported create specification and exact independent verifier change together; other profiles retain existing bounds. Controller-launched workers retain only bounded rotated operational codes/classes/counters, never raw output or model conversations. Protected code upgrades preserve the original database/key/witness through the authenticated manifest transition. Observed native metadata pressure supports this headroom; worst-case inference capacity remains bounded by future evidence. Research timing/expiry alignment is a separate proposal and does not change schedules or strategy in this repair.

- **D104 (2026-10-09, live dashboard evidence):** Separate current operational
  health from inception-period economics and retained native/applied outcomes.
  Count reported attempts once and disclose missing token coverage; retained
  shared quota is separate. Owner bills are immutable evidence with explicit
  service-period graph proration and labelled department allocations. Whole bills
  and graph shares are separate; unknown usage is not priced into per-call fees.
  Economic conclusions require complete expense/valuation evidence and retain
  recovery limitations. A completed reporting cutoff never makes a past
  completeness declaration cover future expenses. Multiple accounts without
  explicit portfolio allocation retain unknown selected-account economics.
  Snapshot export and modest refresh are read-only and trigger no inference or
  native quota probe. Upgrade preserves original database/key/witness through
  supported continuity; schedules, strategy and routing remain unchanged.

- **D105 (2026-10-10, owner dashboard request):** [Trade Graph HQ](HQ-DASHBOARD.md)
  is the signed-in home at `/`: a plain-language, phone-first page built only from
  persisted records. The previous home moves unchanged to `/journal` (keeping
  `?end_at=`); Mission Control, trading, organisation, costs, improvements and
  owner pages are unchanged and listed under "Detailed views". `/journal` is
  allowlisted in redaction. Source integration is not deployment: shipping needs
  the owner-approved financial-manifest transition with every portfolio in
  MANAGE_ONLY and the service drained.

- **D106 (2026-10-10):** HQ AI and allowance state comes only from persisted
  evidence: provider pause state, quota-deferral events and the latest saved
  allowance reading against the configured stop line, always shown with its age.
  HQ never calls `ServiceController.status()`/`_readiness()` or reads the
  dashboard's cached `ai_available`/`admitted` verdicts, which are usually stale in
  browser reads. A reading at or below the line is "probably waiting", not a claim
  that work was refused. Unreported windows are disclosed, never treated as zero.

- **D107 (2026-10-10):** HQ owner actions reuse the existing endpoints with fixed
  bodies: start-trading, pause (`MANAGE_ONLY` only), resume, start-optimisation,
  stop-service (`manage-only`), expenses and check-ins. Pause and Resume send a
  `request_id` and an `expected_revision` read from `/api/v1/owner/config` at
  confirm time; Pause retries once with a new request id on a stale revision, and
  Resume re-opens its preview. Uncertain outcomes resend the same request. Other
  pause profiles, `flatten`, budgets, policy and live controls stay in Owner
  controls. HQ adds no new authority.

- **D108 (2026-10-10):** Owner review check-ins (migration `0025`,
  `owner_checkins`) are owner-only and append-only, with request-id replay and
  trigger-enforced plan/needs-plan/closed rules. They are not in the review
  export, the financial checkpoint, `activity_events`, Secretary routing or any
  agent context, and never bump the owner revision. A compromised process sharing
  the SQLite file is not stopped by a module boundary; that residual risk is
  accepted for these non-financial owner notes. Downgrade is not automatic.

- **D109 (2026-10-10):** HQ discloses existing behaviour: on an account holding
  nothing, Stop replaces an automatic safety stop with the owner's own Stop. HQ
  offers no guided "Stop, Start, Resume" recovery and no button that lifts a
  system pause until the owner decides (HQ open question Q1). The safety-stop card
  says it can't be lifted from the dashboard by design, puts it in the next review
  prompt and links Owner controls for the technical details.

- **D110 (2026-10-10, HQ review fixes):** Changes made after the HQ review
  (49 findings, triaged in one fix plan):
  - HQ never probes database ownership (deviation from HQ spec §B.4 R2), so a
    page read never takes the database lock. It reports the run as recorded and
    never says "attached". In-process ownership probes are now serialised.
  - Estimated department turns more than 48 hours ahead are summarised per
    schedule in the additive `calendar.estimates` key instead of being listed,
    so a busy week stays within the 80 KB payload budget.
  - Leftover schedule rows for roles that are not configured are ignored
    (§B.5.3); an `artifact-` schedule wins over the default one for its role.
  - A team started in management-only mode is turn gate GM (§B.5.4): its turns
    happen only if AI becomes available.
  - The review prompt's "since" counts use exact `COUNT(*)` queries (reader
    R30), an exception to the capped readers of §B.4.
  - Story pages: `window_start` is the oldest moment a page covers completely
    and can be later than 72 hours ago when a capped reader was full;
    `next_before` continues from there, pages may overlap by 1 ms and clients
    de-duplicate by id. Long allowance waits are counted in full, never cut.
  - "Pause AI" is never offered while the Leader has a stronger pause on
    (cancel all, flatten or stopped), because it would weaken it.

- **D111 (2026-10-10, HQ desktop check):** Close deadlines of one kind
  (research-note expiries, improvement-idea expiries, Engineer time limits)
  with the same department, status and texts within two hours of the group's
  earliest member are one calendar item with the additive `count` and `until`
  keys and a counted title ("18 research notes stop being used"). The ten
  research-expiry items are now the ten soonest groups of every note in use,
  not the ten newest notes. On desktop the two HQ columns flow independently
  (floats, no change to the page order), so a tall "Needs you" never opens a
  gap under the map.

- **D112 (2026-10-10, HQ visual check):** A group of deadlines (D111) now
  chains by gap: a deadline joins its group when it is at most one hour after
  the group's latest member, so one continuous run of research-note expiries
  is one row however long it is, instead of several identical two-hour rows.
  A group spans at most 12 hours (`CALENDAR_GROUP_SPAN_SECONDS`, which the
  contract checks for `until`): the server doesn't know the viewer's time
  zone, so 12 hours stands in for "the same day", and a range that ends on the
  next local day says so ("to 01:50 tomorrow"). One note reads "A research
  note stops being used", matching the week strip's "Note expires". On
  desktop, Prompt settings and Help & glossary are one full-width row below
  both columns (still part of Decide on phones and in the tab bar), so the
  right-hand column no longer runs up to 2 000 px below the left.

- **D113 (2026-10-10, HQ visual check round 2):** On desktops at least
  1 200 px wide, when the right-hand column would end more than 300 px below
  Story, Your bills (then, if still needed, Review check-ins) moves into the
  bottom row, stacked in its first third beside Prompt settings and Help &
  glossary. The page script does this after each render or size change, and
  never while the focus is inside the panel. The panels go just before Prompt
  settings, so the document order, phone and iPad layouts, keyboard order and
  anchors are unchanged; narrower than 1 200 px a third is too tight for the
  prompt fields. Opening or closing punctuation that touches a tappable time,
  term or amount stays on its line ("(tomorrow 3:00).").

- **D114 (2026-10-12, first-run diagnosis):** The 2026-10-07 to 2026-10-10
  paper run made 17 `no_action` decisions and no orders. Fix the causes
  before new features: give the Trader the latest completed close and a short
  candle series; emit features from the candles that are available instead of
  all-or-nothing; run decisions after the candle they need is stored; stop
  stale EUR reporting FX from blocking USD-native exposure; record a refused
  order as a visible rejection, not a task failure plus `MANAGE_ONLY` pause;
  drop the leftover `slow-trend`/`s` mandate IDs. Evidence and the full cause
  list: [day-trader engine](DAY-TRADER-ENGINE.md).

- **D115 (2026-10-12, owner direction):** The engine becomes an event-driven
  day trader with a daily cycle: scanner shortlist, Research focus coins, a
  Trader day plan (strategy, menu indicators, alarms, resting orders), then
  software watches every price update and wakes the Trader on alarms, fills,
  protective exits or Leader requests. This replaces the fixed 4-hour Trader
  timer as the main wake mechanism and implements the intent of D09.

- **D116 (2026-10-12, owner instruction):** No cap on Trader wake-ups or
  activity. The owner tunes the graph from measured behavior, which HQ shows
  per wake, reason and department allowance. An alarm firing once until
  re-armed, and batching alarms that fire during a wake, are alarm semantics,
  not caps. The existing subscription-allowance floor stays unless the owner
  changes it.

- **D117 (2026-10-12):** Resting limit entries are allowed. Every open
  position carries a protective exit while the Trader sleeps. Software keeps
  enforcing the exposure envelope. A pause cancels resting entries and keeps
  protective exits. The daily-loss value is set in the deferred coins, fees
  and risk session.

- **D118 (2026-10-12, owner question):** Indicators are computed by a proven
  open-source library (TA-Lib preferred) from Kraken 1/5/15/60-minute candles.
  The Trader picks from a menu and receives numbers, not chart images.
  Indicator values may be floats; money stays Decimal. No indicator formula is
  written in-house. Engine code supplies completed candles and compares results
  with alarm levels, and tests cross-check the library against a second
  independent one. TradingView is not the
  engine: it has no public alert-creation API, and webhooks need a paid plan
  and a public endpoint. HQ charts use TradingView Lightweight Charts
  (Apache-2.0, with an attribution link).

- **D119 (2026-10-12, owner priority):** A practice range replays proposed
  strategies on at least three months of Kraken history (15-minute bars or
  finer) with tiered fees and conservative limit fills. It reports trade
  count, win rate, average win and loss, after-fee result versus cash and
  BTC, and worst drawdown and losing streak. Tune on an earlier period and
  confirm on an untouched later one. Data comes from Kraken's downloadable
  OHLCVT archive, the public `Trades` endpoint for recent gaps, and the
  engine's own collector. Delisted pairs are absent, so coin-selection results
  are optimistic.

- **D120 (2026-10-12, owner approved):** Software, not a model, ranks all
  Kraken USD pairs hourly by volume, swing relative to cost, spread and trend,
  and excludes thin pairs. Research gets about 10 candidates, adds news
  context and picks the focus coins. Paper symbol scope widens through an
  owner policy revision when implemented.

- **D121 (2026-10-12, owner direction):** The Leader acts like a human
  manager. It makes rounds of broad, shallow news reading (crypto markets and
  AI trading agents) without rabbit holes, asks Research for deep dives, and
  may wake the Trader with a note. It never places or approves individual
  trades. Strategy-level ideas go through Research or Optimisation and the
  practice range; the system does not rewrite itself while it runs.

- **D122 (2026-10-12, pending owner):** Recommended Research web access is the
  Perplexity Search API. Software stores dated results as snapshot sources
  before the model reads them, which matches the persisted-source citation
  rule, and it spends no Codex allowance. Third-party pricing pages put it near
  $5 per 1,000 searches. It waits for the owner's decision and budget; paid
  calls stay disabled. The key belongs in private runtime configuration only.
  The free fallback is Codex live web search after the adapter captures result
  evidence; today it records only search IDs.

- **D123 (2026-10-12, owner decision):** Each department chooses its own next
  check-in time instead of a fixed timer. The day start always runs, and a
  department that names no time is checked in at the next day start. HQ shows
  each department's chosen next time. Together with D116, this replaces the
  fixed role schedules as the normal mechanism.

- **D124 (2026-10-12, owner decision):** Research and the Leader's news rounds
  use Claude Sonnet 5.5 (`anthropic/claude-sonnet-5-5`) through Perplexity's
  pay-as-you-go Agent API with Perplexity search; the Trader stays on Codex.
  A Perplexity Pro or Max subscription cannot run software. Every search
  result the model sees is recorded as dated evidence. Published 2026-10-12
  prices ($2 / $10 per million input / output tokens, $0.0025 per search,
  $0.0005 per page fetch) become a dated price card. The owner sets a monthly
  research budget, a money limit rather than an activity cap; paid calls stay
  disabled until it exists. The key is installed through a local private file,
  never chat, Git or model context.

- **D125 (2026-10-12, owner decision; amends D124):** During testing,
  Perplexity research calls are enabled without a spending limit, so natural
  costs can be observed. Every call still gets a reservation and receipt
  (unknown usage is unresolved, not free), and HQ shows the spend. The owner
  adds budgets afterwards. The owner knowingly supplied a temporary test key
  in chat. It is stored only in an owner-only private file outside Git, is
  never committed or logged, and the owner revokes it after testing. Later
  keys use the local private-file route.

- **D126 (2026-10-12, owner decision):** The practice range replays each
  strategy under these cost profiles: Kraken spot (current tier and after the
  volume discount), OKX Europe spot (0.10% / 0.20%, official 2026-09-25
  notice), and later US stocks at Interactive Brokers in a cash account. This
  follows an owner-run Perplexity venue study for an EU account, corrected
  against official pages (Bitstamp's standard fees are 0.30% / 0.40%; Nordnet
  has an order API). A new venue adapter is built only when a strategy's
  evidence depends on that venue's costs. Details:
  [day-trader engine](DAY-TRADER-ENGINE.md#venues-and-cost-profiles-d126).

- **D127 (2026-10-12, owner condition):** Nothing considered riba. Under
  mainstream standards (OIC International Islamic Fiqh Academy resolution
  63 (1/7); AAOIFI as reported), the engine excludes futures and perpetuals
  (including their funding payments), conventional short selling, margin and
  other interest-bearing borrowing, and lending, earn or interest on idle
  balances. Spot crypto and cash-account stocks remain, and the long-only and
  no-leverage rules stay. The owner may revise this with a scholar's opinion.

- **D128 (2026-10-12, owner decision):** No Shariah business-activity screen
  is applied to the stock scanner, because positions are intraday trades, not
  holdings. D127's product exclusions still apply.

- **D129 (2026-10-13, implements D114 in source; not deployed):** The fix list
  is built with these recommended defaults (build plan tasks T23 and T24):
  - Hourly features are `hourly-price-features-v2`. All windows end at the
    newest completed candle available at as-of when it is at most 2 hours
    behind the latest completed hour, otherwise at that hour (v1 behaviour).
    Each field keeps its exact lookback and formula, gets status `stale` while
    newer hours are pending, and lists its own missing hours. Snapshots also
    carry `latest_close`, up to 24 recent completed candles available at as-of
    and the missing slots. The D89 wording in KRAKEN-PRICE-HISTORY is updated.
  - Recurring schedules advance on their persisted grid (no "now + interval"
    drift; missed runs still coalesce into one task). In the normal
    subscription operation a fresh Trader task waits for the candle that
    closed at the last whole hour, rechecking every 5 s, for at most
    `decision_candle_wait_seconds` (default 300: in the first run Kraken
    committed each hour 2.6 to 3.3 minutes after it closed). Research is not
    gated. `history.expected_candle` says `present`, `missing_after_wait` or
    `missing` in one plain sentence.
  - Exposure checks compare the order's quote currency (USD) with native
    equity. Other-currency holdings use a direct rate or the inverse of the
    identified opposite reference rate, keeping its stale flag; missing or
    stale needed marks or FX still block. Stale EUR reporting FX only labels
    EUR views. D16 is unchanged. A Frankfurter outage still sets the feed
    failure that blocks buys; that coupling is a follow-up.
  - `AuthorityDenied`, `StaleState` or `ValidationFailure` raised by
    `Execution.authorize` for a Trader choice is recorded as a decision plus an
    `order_refused` event, shown to the next turn as
    `portfolio.recent_order_refusals` (newest 5) and in HQ, and the task
    succeeds without a pause. Model contract errors, research cited outside
    the snapshot, stale snapshots and role unavailability keep FAILED plus the
    system MANAGE_ONLY pause. A refused turn still counts as a failed
    version-trial sample, as before.
  - The default mandate names only `slow-trend-pullback`. An older database is
    revised only by the owner command `trade-graph mandate-cleanup --confirm`
    (new revision N+1, refused while the service holds its lease; old
    revisions and decisions stay untouched). Synthetic FLATTEN exits use
    strategy id `pause-flatten`, and the D18 named-strategy check applies to
    entries only, so owner FLATTEN works under a non-discretionary mandate.

- **D130 (2026-10-13, owner decisions; amends D129):**
  - A Trader answer that arrived but cannot be used fails only that turn, with
    `failure_scope: turn` and a `trader_turn_set_aside` event, and no system
    pause. This covers a reply that fails its schema or checks, cites research
    it was not given, names an unknown strategy, or comes back cut off,
    declined or with a temporary model error, and a state change during the
    turn. Lost AI credentials, an unknown outcome (WAITING_EXTERNAL), quota and
    budget stops, exhausted attempts and software exceptions still set the
    system MANAGE_ONLY pause, as genuine unavailability.
  - A euro reference-rate (Frankfurter) outage no longer fails the public
    poll. The paper and live feeds keep their quotes, retry the rate after 5
    minutes and record `reference_fx_health` when the outage starts and ends.
    Only EUR reporting labels are affected; buys are not blocked.
  - Provisional money-risk settings, to be revisited in a later bankroll
    management session and applied when T32 is built: daily loss limit 3% of
    the day's starting value (then no new buying and resting entries cancelled
    until the next day start, protective exits kept); at most 20% of the
    account per coin and 80% in total; the paper coin list is a rule (Kraken
    USD spot pairs passing the scanner's liquidity and exclusion checks) rather
    than a fixed list; the Perplexity budget is set after about two weeks of
    testing, at roughly 1.5 times the observed monthly cost. These are paper
    settings, not a real-money allocation.

- **D131 (2026-10-13, owner decisions):** The scanner and the paper coin list
  exclude stablecoins and yield-paying tokens such as staking or lending
  tokens (T31): stablecoins barely move, and a yield may count as riba under
  D127. During Perplexity testing, HQ shows a spending alert once the testing
  total passes USD20 (T40); it is information, not a stop, so D125's
  unlimited testing mode is unchanged.

- **D132 (2026-10-13, implementation default for T25; not an owner decision):**
  Candle history for many pairs and four intervals lives in its own private
  SQLite file (`runtime/market_data.sqlite`, Git-ignored, mode 600) with its
  own small migration list, not in the financial database. A full quarter of
  one-minute candles is about 130,000 rows per pair, and the measured cost is
  about 118 bytes per candle revision (100,000 rows: 11.8 MB, 32,000 rows per
  second). All pairs at one-minute detail would be tens of millions of rows,
  which would swell every financial backup and recovery, and a long bulk
  write would compete with trading writes. The file is rebuildable public
  data, so it is not part of the financial recovery unit. Rules (details in
  [CANDLE-STORE.md](CANDLE-STORE.md)): rows are append-only revisions keyed by
  series, open time and revision; availability is the receipt time and is
  never backdated; each source (collector, archive, Trades-built, synthetic)
  is its own series and sources are compared by reports, never merged in
  storage; a missing slot stays missing; `hourly_candles`, hourly feature
  snapshots and every financial table are unchanged. Reads have two explicit
  modes: `point_in_time` (receipt time at or before as-of) and `replay` (event
  time only, labelled `event_time_only`). CLI commands are added as modules in
  `trade_graph/market_data_cli/` so parallel work does not edit one list.

- **D133 (2026-10-15, implementation defaults for T25; details in
  [PAIR-CATALOG.md](PAIR-CATALOG.md) and
  [KRAKEN-CANDLE-COLLECTION.md](KRAKEN-CANDLE-COLLECTION.md)):**
  - *Pair catalog.* One public AssetPairs request returned 1,460 pairs on
    2026-10-13, 677 of them quoted in USD. Normal trading status means
    `online` only; other statuses stay in the dated catalog and are not
    collected. Dated, versioned rules (`pair-rules-2026-10-13.1`) mark
    stablecoins, yield-paying or liquid-staking tokens (D131) and fiat
    currencies listed against USD; native proof-of-stake coins (ETH, SOL, ADA,
    DOT, AVAX, ATOM) are not yield tokens. Marking is by asset name: 44 pairs
    are marked and 608 coins are "not individually reviewed", which every
    report lists, so an unmarked coin is never presented as verified clean. A
    price-peg check is left to the scanner (T31). Calls that need the owner's
    eye: U, STBL, UNITAS, CAP, DEBIT, RE, ST, and USDE (marked a stablecoin
    although its reserve is hedged with perpetual futures, D127).
  - *Quiet intervals.* Kraken's OHLC endpoint fills a minute or hour without
    trades with a flat row (previous close, vwap 0, volume 0, count 0), as
    recorded on 2026-10-13. The collector stores these as sent and counts them;
    a missing slot in the collected series therefore means "not collected" or
    "older than the 720-candle window". The archive source omits quiet
    intervals, so reconciliation will show that difference.
  - *Requests.* `since` is inclusive (observed), so each incremental request
    starts one interval before the newest stored candle and re-verifies two
    committed candles. The final row of every response is dropped unseen and
    the receipt time is taken after the network call. The limiter mirrors
    Kraken's documented counter conservatively (ceiling 10 of 15, drain 0.30
    per second instead of 0.33) and starts each command at the ceiling, so
    back-to-back runs never exceed Kraken's real counter; a rate-limit answer
    stops the run, one short "Throttled" answer is waited out once, ten failed
    series in a row stop the run, and a run whose clock is earlier than a
    recorded run is refused. Two collectors running at once are not
    coordinated; run one at a time.
  - *Scope.* Eligible USD pairs at 5, 15 and 60 minutes; 1-minute only for an
    explicit list (default BTC/USD and ETH/USD) because a full quarter at
    one-minute for all 591 eligible pairs would be about 77 million rows and
    9 GB. `--one-minute-pairs all` exists. Which pairs get 1-minute detail is an
    owner decision. Most perishable data goes first when a request limit stops
    a run, and the report lists exactly what was not reached.
  - *Safety.* All five candle commands refuse the financial database by name,
    path, link and content, and refuse any non-empty SQLite file that is not a
    candle-history file.

- **D134 (2026-10-15, implementation defaults for T26 importer; details in
  [KRAKEN-ARCHIVE-IMPORT.md](KRAKEN-ARCHIVE-IMPORT.md)):** The importer reads
  a local ZIP or folder only and has no network code (a test checks the import
  closure of its modules). It scans the whole file first, reads the clock once
  and uses that moment as the receipt time of every row (never the historical
  close); rows closing after it are dropped and counted. More than 1% rejected
  rows refuses the file. Member names must be plain flat file names; nothing
  is extracted; member count, directory size, sizes, rows and compression ratio
  are bounded. Default scope is USD pairs at 15 and 60 minutes (1 and 5 are
  opt-in). Re-importing the same file adds nothing; a changed file adds
  revisions only for changed rows, and stored candles that a corrected file no
  longer contains are counted, not retracted. Every report states that
  delisted pairs are absent from Kraken's data, so coin-selection tests on this
  history are optimistic. The real file's layout (timestamp unit, open or close
  convention, internal names, folders) is unconfirmed; the first real import is
  the first real evidence.

- **D135 (2026-10-15, implementation defaults for T26 backfill and
  reconciliation; details in
  [KRAKEN-TRADES-BACKFILL.md](KRAKEN-TRADES-BACKFILL.md)):** Public Trades are
  aggregated into completed candles only after a later trade proves the close
  passed; the receipt time is the completing page's. Kraken's `since` is
  inclusive (observed), so a repeated boundary trade is removed by trade id
  and must equal the stored row; a skipped trade id withholds the candles it
  could change, and that withholding survives a resume. The cursor
  (`next_open_unix` per pair and interval) lives in the run report, so a hard
  kill repeats at most one run's requests; Ctrl-C and SIGTERM record the run.
  Measured cost on 2026-10-13: BTC/USD made about 164,000 trades in a day
  (about 165 requests, 8 minutes at the safe rate); BAT/USD about 13,700.
  Reconciliation compares sources with no tolerance and reports every
  difference; `precedence_window` picks the first source in a stated order
  (default collector, archive, Trades-built) and labels the source and any
  disagreement per slot; sources are never blended. A live smoke on 2026-10-15
  compared one BTC/USD 15-minute slot built from 3,000 trades with Kraken's
  own candle: open, high, low, close, volume and trade count were identical and
  only vwap differed (Kraken rounds it to one decimal), by 0.00004%.

- **D136 (2026-10-15, implementation defaults for T27; details in
  [INDICATOR-MENU.md](INDICATOR-MENU.md)):**
  - *Libraries.* TA-Lib 0.8.2 (bundled C library 0.8.1, Python 3.12 manylinux
    wheel) and NumPy 2.5 are locked runtime dependencies, with TA-Lib capped
    below 0.9 so a new C library cannot change numbers unnoticed. They are
    imported only by the indicator wrapper, and a test keeps them out of
    `domain`, `contracts` and `kernel`. pandas-ta-classic 0.8.32 (covers every
    menu entry) and talipp 2.7 (its own conventions) are test-only
    cross-check libraries. The menu has 26 entries with bounded settings;
    warm-up comes only from TA-Lib's lookback. VWAP is not on the menu because
    TA-Lib has none.
  - *Differences.* Where libraries differ because of seeding (MACD, ADX and
    the directional indices, ATR, Parabolic SAR), the difference is written
    down, bounded and tested; nothing is patched with an in-house formula.
    The TradingView comparison column is an unverified recollection. No
    published numeric examples are in the tests (the StockCharts examples are
    spreadsheets that need a download); the reference values are hand-derived.
  - *Start-sensitive indicators.* RSI, MACD, ATR, ADX and similar give
    slightly different numbers depending on how much history precedes them.
    The engine reads the same span for every requested count, so one candle
    has one number; it needs more history than the bare lookback (RSI 14 reads
    794 candles, ADX 863, MACD 741). A coin younger than that reports
    `warming_up` longer.
  - *Gaps and alarms.* Any missing slot in the span withholds every value of
    that indicator and lists the exact missing close times; nothing is carried
    forward or partially computed. `compare_to_level` rounds the value and the
    level to the same 12 significant digits before an exact comparison; an
    unready indicator never satisfies a comparison. Undefined readings in a
    market with no movement and no volume (RSI, MFI, Stochastic, Williams %R,
    CCI, ADX read 0, Aroon reads 100) are still labelled `ready`; the Trader
    must read oscillators beside the candles.
  - *Hourly fields (D89 re-expressed, D118).* `hourly-price-features-v3`
    computes sma_20, sma_50, range_high, range_low, midpoint, hourly_return and
    four_hour_drift with TA-Lib (SMA, MAX, MIN, MIDPRICE, ROCP). New:
    `highest_high_20`. Retired: `pullback_from_high`, `pullback_depth`,
    `midpoint_distance`, because each is one division that no library function
    returns; their ingredients are supplied instead (also for databases that
    kept the old template, which asks only for the retired name). The
    slow-trend-pullback template now reads `highest_high_20` and `latest_close`.
    Values are exact decimal text, rounded half-even to 12 significant digits
    (selection fields are withheld above 11 digits); one-tick returns carry
    5e-12 relative plus 2.5e-16 absolute error. Stored v1 and v2 snapshots are
    never recomputed and stay readable (fixtures from the v2 code are tested).
    The hourly features still read `hourly_candles`; rewiring to the new store
    is later work.
  - *One in-house ratio remains,* in the owner dashboard's trend label
    (`hq_state.market()`, an SMA ratio compared with 0.001). It is display only
    and is not Trader input. Recommended reading of D118: display
    classification is exempt. The owner may say otherwise.
  - *Before deploying:* an existing database keeps its stored active baseline
    (`VersionController.ensure` returns early), so the new templates need an
    owner-approved baseline replacement; the protected image must be built with
    the NumPy and TA-Lib wheels (`scripts/build_protected_image.py` was not
    exercised); the kernel manifest is re-pinned as for any `.py` change.

- **D137 (2026-10-15, owner decision; defaults pending owner):** The next task
  is a news alarm built on Jev, TypeSafe AI's decision model (released
  2026-09-15). Jev answers typed questions with probabilities and writes no
  text. The owner's Perplexity report and TypeSafe's official pages (S40)
  show it is cheap and fast. The only public market test found it no better
  than a coin flip at price direction. So Jev only notices important headlines
  between Leader news rounds. It does not rank coins, label market types,
  check strategy rules or estimate prices; software already does the first
  three (D120), and the evidence does not support the last.
  - *Two steps.* T44 builds the connection and runs it in shadow mode: it
    stores headlines as dated sources, asks Jev fixed questions and logs which
    headlines would have woken someone, but wakes nobody. T45 turns the alarms
    into real wake-ups after the event engine (T35), Leader news rounds (T38)
    and HQ's wake log (T39) exist, once shadow evidence supports it. T43 does
    not wait for T45.
  - *Rules.* A news alarm only adds wake-ups (D116). It never touches orders,
    money limits or owner policy, and it is not a Trader confidence gate.
    Headline text is data, never instructions.
  - *Recommended defaults for the owner to confirm:* free public headline feeds
    that need no key (Kraken's status page and announcements, plus a few major
    crypto news outlets; exact feeds checked at build time); a check every 5
    minutes; the pinned version `jev-1.13.0`, because `jev-latest` moves; the
    alarm wakes the Leader, and also the Trader when the headline concerns a
    coin the paper account holds; once a key is installed, testing calls run
    with receipts and an HQ alert at USD5, which is an alert, not a stop.
  - *Access.* The owner signs up at console.typesafe.ai and installs the key
    through the private-file route. Paid Jev calls stay off until then.
    OpenRouter, Vercel and lookalike Jev sites are not used.
  - *Expected cost* at one check every 5 minutes: about USD0.05 a day (288
    checks × about 4,000 input tokens × USD0.042 per million). The receipts
    show the real figure.

- **D138 (2026-10-16, owner decision):** The owner accepted the recommendation
  for every decision in the build plan's "Decisions that need you" (numbers
  1–10). The provisional settings of D130 and the pending defaults of D137 are
  now decided. All values apply to the virtual USD10,000 paper account; none
  is a real-money allocation.
  1. *Daily loss limit:* 3% of the day-start value, fees included. At the limit,
     new entries stop and resting entries cancel until the next day start;
     protective exits and reconciliation continue. No forced sell-off.
  2. *Maximum per coin:* 20% of the account; 80% in total. Resting entries count
     as if filled (T32).
  3. *Paper coin list:* the rule. Any Kraken USD spot pair that passes the
     scanner's liquidity and exclusion checks at that moment, without
     stablecoins or yield-paying tokens (D131) or riba products (D127).
  4. *Perplexity budget:* no money limit during testing, with the USD20 HQ alert
     (D131). After about two weeks of testing seen in HQ (T40), a monthly limit
     of about 1.5 times the observed monthly cost; reaching it pauses Research
     and Leader news rounds only.
  5. *Practice-range pass mark:* none. A practice-range report must exist for a
     strategy version before it enters a day plan (T34); the Trader reads it and
     decides. This is not a confidence threshold.
  6. *Trading-day start:* 00:00 UTC, matching Kraken's daily candles. The day
     start, day end and the daily loss limit reset run then.
  7. *Scored test (T43):* at least four weeks and at least 30 completed trades,
     whichever takes longer. "Keep going" means profit after fees beats both
     holding cash and holding BTC over the same days, with AI spending shown
     beside it. A good result is not permission for live trading.
  8. *AI allowance floor:* keep 40% for the first week of the event engine,
     then review the wake counts in HQ (T39).
  9. *Stock price source:* decided only when crypto results justify stocks;
     T30 stays LATER.
  10. *News alarm (D137 defaults confirmed):* free public feeds that need no
      key (Kraken's status page and announcements plus a few major crypto news
      outlets, exact list checked when T44 is built); a software check every 5
      minutes; pinned `jev-1.13.0`; an alarm wakes the Leader, and the Trader
      when the headline concerns a held coin; testing calls carry receipts and
      an HQ alert at USD5, which pauses nothing.
  Applying these to the running installation still needs an owner policy
  revision at deployment, because agents cannot edit protected owner policy.
  Until then T32 and T43 keep refusing to install orders or start without
  owner-recorded values; tests use synthetic values.

- **D139 (2026-10-16, owner request; process):** The remaining day-trader work
  is built by cloud coding sessions, following
  [CLOUD-BUILD-PLAN.md](CLOUD-BUILD-PLAN.md). The branch
  `claude/day-trader-engine-20261012` is pushed to GitHub as the integration
  base. Each cloud session takes one work package, branches from the base,
  opens a pull request into it and writes its status to
  `planning/handoffs/<package>.md` instead of editing `planning/progress.json`
  or `IMPLEMENTATION-STATUS.md`; one integration session merges in dependency
  order and folds the handoffs into those files. Decision numbers and
  migration versions are reserved per package so parallel sessions do not
  collide. Cloud sessions have no private data, keys, WSL or Windows access:
  real-data runs, credentialed smoke tests, image builds, deployment and the
  scored paper run stay on the owner's PC, and nothing is deployed without the
  owner's approval.

Cloud wave 1 (D140–D164, 2026-10-16). The wave 1 integration copied these
implementation defaults from `planning/handoffs/W1-A.md` to `W1-E.md`, where
each package's evidence is kept. W1-B needed no state migration, so its
reserved 0027 stays unused: the state migrations run 0026 (W1-A), 0028 (W1-C)
and 0029 (W1-D), and Alembic runs 0025 → 0026 → 0028 → 0029. The gap is not
renumbered, because the migration runner applies the listed versions in order
and does not need contiguous numbers.

- **D140 (2026-10-16, implementation default for T44): Jev's own decision
  contract and wire reading.** Jev is not a model-provider adapter: it has its
  own contract (state and typed questions in, probabilities out) in
  `contracts/decisions.py`, with no SDK and no network import. The version is
  pinned to `jev-1.13.0`; any name that is not `jev-<major>.<minor>.<patch>` is
  refused before sending, so `jev-latest` and `jev-preview` can never be used.
  TypeSafe's documentation host did not resolve from the cloud on 2026-10-16,
  so the wire names beyond S40 (`question`, `options`, `levels`,
  `probabilities`, `confidence`, `id`) and the `Authorization: Bearer` header
  are this build's reading of the published API, kept in one adapter module;
  the first credentialed call (L2) confirms them. Replies are read strictly:
  the answers must name exactly the questions asked and exactly their options,
  every probability a number from 0 to 1, each answer's probabilities summing
  to between 0.99 and 1.01; `confidence` is optional and checked when present.
  Anything else is `malformed_answers`: kept as received, receipted, and given
  no verdict. The key is read only from
  `~/.local/share/trade-graph-owner/typesafe/typesafe.key` (folder private to
  its owner, file 0600, one link, no symlink, 16–4096 printable characters) and
  is sent only in the header.

- **D141 (2026-10-16, implementation default for T44): receipts.** Every call
  reserves through the existing `BudgetGateway` under role `news_alarm`,
  purpose `news_alarm:jev`, holding the largest request Jev accepts (64,000
  input tokens, S40) plus 1,024 output tokens (free on the dated card), with
  the owner's FX rate and buffer (defaults 1 and 1.02, as
  `RuntimeModelConfig`). The reservation and the attempt row commit together
  before the call. A reply is receipted only when both token counts are
  reported and the answering model is the priced one; an HTTP error is
  receipted from the usage its body reports. Missing usage, a reply from
  another model (its price is unknown), a timeout, a refused reply or an
  interrupted call stays `UNCERTAIN` at the full hold. A retry is a new
  reservation (`attempt_kind` `retry`). A money limit with no room sends
  nothing and is logged as `budget_refused`, which is not an attempt. Scripted
  transports are always synthetic and the runner refuses a binding that labels
  them otherwise. A receipt is allocated once, 100%, to the latest paper
  portfolio. Real calls also refuse a price card verified more than 30 days
  ago, as the model router does. The USD5 testing alert is reported from real
  committed spending only and pauses nothing.

- **D142 (2026-10-16, implementation default for T44): feeds and polling.** The
  five feeds in `RECOMMENDED_FEEDS` (Kraken status page Atom, Kraken blog RSS,
  CoinDesk, Cointelegraph and Decrypt RSS), all free, keyless and https. One
  plain GET per feed per poll with the previous ETag and Last-Modified, no
  redirects, a 2 MB bound, a descriptive User-Agent and no cookie or
  credential; DTDs and entity declarations are refused outright instead of
  adding an XML dependency. Repeats are detected by canonical link (lower-case
  scheme and host, no fragment, no `utm_` parameters). Titles lose control and
  text-direction characters (so a printed headline reads as stored) and links
  holding them are refused; the item's hash is taken from the element as
  received. Recommended interval 5 minutes (`--polls N` repeats at that
  interval; scheduling it is an installation step). Only headlines published
  within the last hour (receipt time when the feed gives none or a future one)
  go to Jev; older ones are stored and reported as not checked. Retryable
  failures (rate limit, overloaded, server error, timeout, credentials) are
  asked again on later polls up to 3 attempts per headline; a credentials
  failure or a budget refusal ends that poll's Jev checks. The cloud network
  refused every feed host, so the fixtures are synthetic and labelled; one real
  recording per feed is step L2.

- **D143 (2026-10-16, implementation default for T44): question set and
  would-wake rule.** Three fixed questions: `concerns` (choice of watched
  coins, `kraken`, `none`), `event_type` (hack or exploit, delisting, listing,
  exchange outage or maintenance, regulatory action, other) and
  `holder_wants_to_know` (yes/no, within the hour). The headline is only in the
  `state`, as quoted data. Watched coins: held coins, the active mandate's
  coins, the owner's `extra_watch_coins` and focus coins once T37 exists, at
  most 253 (a Jev choice holds 255 options). The rule: the subject is the most
  probable `concerns` option, ties all count and a coin tied with `none`
  counts; it would wake when the subject is not `none` and P(yes) is at least
  the alarm threshold (default 0.5, owner setting); it wakes the Leader, and
  the Trader too when a subject is a held coin or Kraken itself while anything
  is held. The event type is logged for T45's note and is not part of the rule.
  It is an alarm setting, not a confidence gate, and property tests show it can
  only add wake-ups.

- **D144 (2026-10-16, implementation default for T44): storage and commands.**
  Migration 0026 (W1-A's reserved version) adds five append-only tables to the
  state database; triggers refuse every update and delete, and money stays in
  the budget tables. The commands are discovered through `market_data_cli`, so
  `cli.py` is unchanged. `news-alarm-poll` needs an initialised paper database
  in a 0700 folder; with Jev switched on and no readable key it stops before
  reading anything; scripted replies and real calls cannot be mixed.
  `news-alarm-report` opens the database read-only, never creates or upgrades
  it, and never recomputes an answer or a verdict. News tables are not added to
  agent context, the financial checkpoint or the review export; how the Leader
  reads headlines is T38/T45.

- **D145 (2026-10-16, implementation default for T28):** One fill engine in the
  protected kernel (`kernel/fills.py`) serves the paper broker and the practice
  range. Entries: no event that began before the order (`eligible_after_utc`,
  `excluded_event_ref`) can fill it, so the bar a decision used never does. On
  candles a resting limit fills only when trades print beyond it (`low < limit`
  for a buy), at its limit (never a better gap price), as a maker; a limit at
  or through its placement price is marketable and fills as a taker at the next
  open plus slippage, never beyond its limit; market orders fill at the next
  open plus slippage. On quotes a resting limit fills when the opposite best
  price reaches it (a crossed book, not a touch; the T05 behaviour is kept) and
  market orders take the displayed size at the touch. Fills inside a bar are
  stamped at its close, fills at the open at its open. Reason: D119's "a touch
  is not a fill" and the T28 golden cases, without changing the T05 quote
  guarantees.

- **D146 (2026-10-16, implementation default for T28):** Cost profiles are
  dated, sourced, append-only data records in `kernel/cost_profiles.json`, each
  revision pinned by the SHA-256 of its canonical content in protected kernel
  source (`PUBLISHED_PINS`), because the protected package hash covers only the
  Python files and `runtime_artifacts.json`. A fee change is a new revision
  with a later `effective_from`; every fill records the profile reference and
  hash, so a profile change never rewrites an earlier result. The three
  profiles keep the build-plan figures (D126) because kraken.com and okx.com
  were unreachable from the cloud on 2026-10-16 (the network policy refused the
  connections); each source records `check_result:
  unreachable_from_build_environment`. The paper broker's default profile is
  `kraken-spot-starting-tier@1`, the same rates as D14. Rates passed in
  configuration are `explicit_configuration` profiles with no source.

- **D147 (2026-10-16, implementation default for T28):** Protective exits are a
  stop-loss and an optional take-profit on one long spot position, one
  cancelling the other, both filled as triggered taker sells of the whole
  quantity with slippage (build plan "Notes for implementers"). On candles the
  stop triggers when the low reaches it and fills from `min(open, stop)` (a gap
  gives the worse open); the take-profit needs a trade beyond it and fills from
  its own level, never a better gap open; a bar reaching both takes the stop;
  in the bar the position was entered only the stop can trigger. On quotes the
  stop triggers at `bid <= stop` and fills from the bid, the take-profit needs
  `bid > target`; no bid, no fill. Default exit slippage is 0.10% on both
  paths; slipped prices round to the price increment against the trader.
  Consequence: a paper stop gapped to a bid of 90 on a 0.1 tick now fills at
  89.9 instead of 90.

- **D148 (2026-10-16, implementation default for T28):** Fill policies are
  hashed records separate from fees. `bar-replay@1`: a fill takes at most 10%
  of the bar's volume (no fill in a bar without volume), 0.10% taker entry
  slippage and 0.10% exit slippage. `paper-quote@1`: the T05 shares (100% of
  displayed size or quote volume, 50% of the order when no volume is known), no
  entry slippage (real quotes already include the spread and buy reservations
  are sized at the quote, so the paper broker refuses entry slippage) and 0.10%
  exit slippage. The practice range may vary pessimism with a stricter,
  separately named policy.

- **D149 (2026-10-16, implementation default for T28):** The paper broker
  records each fill's terms in its own order document (`fill_terms`) rather
  than changing `FillRecord` or adding a migration, so ledger identities and
  old databases are untouched. Submitted take-profit and one-cancels-the-other
  orders in the paper broker are left to T34, which attaches exits to entries;
  the engine already supports them.

- **D150 (2026-10-16, implementation default for T36):** Search runs in
  software as the registered `web_search` function tool, on Perplexity's Search
  API. Perplexity's own `web_search` and `fetch_url` tools are never enabled
  (the adapter refuses them, and any reported use leaves the usage unresolved),
  because the model could otherwise read pages the software never stored
  (D124). Each result is stored before the model reads it; a finding must cite
  a `source_ref` stored for its own round, or the whole reply is rejected and
  nothing from it is kept. Endpoints follow Perplexity's published
  documentation as summarised on 2026-10-16: Agent API
  `https://api.perplexity.ai/v1/agent`, Search API
  `https://api.perplexity.ai/search`; L2 confirms them.

- **D151 (2026-10-16, implementation default for T36; deviation from the plan's
  "USD0.0025 per search"):** Two dated cards. The Agent API card keeps D124's
  published 2026-10-12 prices (USD2 / USD10 per million tokens; its USD0.0025
  search rate applies only to Perplexity's own `web_search` tool, which stays
  off; `fetch_url` USD0.0005 is recorded in the planning entry). Software-run
  searches use the Search API, a different product, priced at USD0.005 per
  request: the highest figure found (search-engine summaries of Perplexity's
  pricing page and third-party pages, USD5 per 1,000; a Fast Search tier at
  USD1 per 1,000 was also reported). The cloud session could not open
  docs.perplexity.ai, so this is unconfirmed; pricing high means a receipt can
  overstate but not understate. L2 re-reads the official page; a different
  price becomes a new card ID. The cost of choosing evidence capture over
  Perplexity's built-in search is about USD0.0025 more per search if D124's
  tool price is current.

- **D152 (2026-10-16, implementation default for T36):** Unknown usage is
  unresolved, never free. A reply whose usage is missing is still used (it is
  valid evidence) but the round reports `billing: unresolved` and the
  reservation stays held at its maximum. An answered search (results or a
  malformed body) is one billed request and gets a receipt; a timed-out or
  refused search stays unresolved. Unresolved calls count at their reserved
  maximum against the owner limit. There is no automatic retry in T36: a retry
  is a new round with its own reservations.

- **D153 (2026-10-16, implementation default for T36):** The owner spend policy
  has no default; without it nothing is reserved or sent. `unlimited_testing`
  is accepted only as D125. `monthly_limit` is in USD per deployment and UTC
  month. Reaching it (or the deployment allowance) gives the round the status
  `paused_at_limit`, which is not a system pause: the Trader, protection and
  records continue. Size bounds on a round (searches, tokens) are a reservation
  envelope for one call, not a cap on activity (D116).

- **D154 (2026-10-16, implementation default for T36):** The key is read only
  from the owner's private file (owner-only 600 or 400, one link, a regular
  file outside any Git checkout, in a folder others cannot write; see
  `application/private_key_file.py`), held as a masked `SecretStr`, sent only as the `Authorization` header, and never stored,
  logged or placed in model context. The credentialed smoke is opt-in
  (`--confirm-paid-call`), uses a separate smoke database, and labels its
  report `real` only when the pinned network transport ran; a scripted run is
  `synthetic` and never counts as a paid-provider test.

- **D155 (2026-10-16, implementation default for T33):** Alarms live in the
  state database (migration 0029) as non-financial tables. An alarm has one
  owning department (trader, research, leader, learning or optimisation), one
  condition (`quote_price`, `candle_price` or `indicator`), the indicator
  engine's four relations and an exact Decimal level, plus optional portfolio,
  day-plan and task references and a note of at most 280 characters. It fires
  at most once per arming, enforced by a unique constraint, so restarts and
  repeated ticks cannot fire twice. Only the owning department re-arms (after a
  fire) or cancels; a re-arm keeps the condition, and a new level means a new
  alarm. No field or setting limits how many alarms exist or how often they
  fire (D116). Reason: the task's one-shot rule as a store guarantee, with full
  history kept.

- **D156 (2026-10-16, implementation default for T33):** Candle and indicator
  alarms read candles only through `CandleStore.window`, point-in-time as of
  the clock, and evaluate every completed candle that closed strictly after
  arming (or after the cursor) in order, so candles completed during downtime
  are not missed. The watcher waits at a candle that has not been received and
  steps over a missing one, saying so, only once a later candle is present; a
  candle that arrives later than its successor is not evaluated (alarms watch
  the live edge). A crossing needs two consecutive candles. Candle alarms use
  the collected intervals 1, 5, 15 and 60 minutes. Level alarms on On-Balance
  Volume are refused (its level depends on where history starts, D136), and so
  are levels outside an output's declared range. One tick evaluates at most
  5,000 candles per alarm and continues next tick; a whole 5,000-slot step
  without any candle is stepped over (reported) once newer candles exist, so an
  outage that can never be backfilled does not freeze the alarm. Stored alarms
  are read back without re-checking today's menu, so a later menu change makes
  one alarm report an error instead of stopping the watcher.

- **D157 (2026-10-16, implementation default for T33):** Quote alarms use quote
  and trade observations received after arming, on `mid` (default, (bid + ask)
  / 2), `bid`, `ask` or `last`; a missing price is not guessed. A crossing
  alarm saves the side of its level whenever it changes, so a crossing across a
  restart or a feed gap still fires, and the fire reports the previous reading
  on the other side and the seconds since the watcher's previous quote of that
  symbol. A quote stamped in the future is refused; one older than the alarm's
  held reading is ignored.

- **D158 (2026-10-16, implementation default for T33):** A feed that an armed
  alarm depends on is lost after 120 seconds without a quote (the paper feed
  polls every 10 seconds) or when no candle has arrived for the slot due 10
  minutes ago. Loss and return are each recorded once and sent as a notice to
  every owning department (a return also to every department told about the
  loss); a department that arms on a feed while it is lost gets the same loss
  notice; after a restart a lost quote feed stays lost until a fresh update
  arrives (a backlog of old quotes is no recovery); a candle series the store
  refuses to read counts as lost. An alarm whose evaluation fails keeps its
  cursor, carries the failure as its reason, is reported in the tick, and does
  not stop the other alarms. These are health thresholds, not caps.

- **D159 (2026-10-16, implementation default for T33, contract for T35):**
  Fired alarms and feed notices go to a persisted per-department queue that the
  wake dispatcher claims one batch per wake (`claim`, `acknowledge`, `release`,
  one open claim per department, single-use wake ids). Items that arrive while
  a department is awake stay pending and are delivered together at its next
  wake. Nothing is dropped or merged, and nothing limits items or wakes.
  Whether a feed notice wakes a department is W5-A's decision.

- **D160 (2026-10-16, implementation default for C1):** Mission Control accepts
  the planning statuses `todo`, `in_progress`, `blocked` and `done` (plus the
  older `pending` and `not_started`) and an `updated_at` that is a date or a
  full UTC timestamp. The packaged snapshot is generated, not hand-edited: run
  `uv run python scripts/update_progress_catalog.py` after every change to the
  planning files, and
  `test_packaged_catalog_matches_current_planning_without_private_metadata`
  fails until it is run. Tests derive task counts from the planning files.
  Three hand-written evidence notes in the old snapshot (T17, T19, T21) were
  never shown, because the page uses the notes in `progress.py`; the
  regenerated snapshot carries the shown ones.

- **D161 (2026-10-16, implementation default for C1):** The overview again
  carries `degraded` and `degraded_reasons` copied from the health check, as
  before 2026-10-09. Acceptance case A34 requires visible degraded states.

- **D162 (2026-10-16, test policy for C1):** A test whose subject is not a time
  limit gives real child processes at least the production wall clock (or more,
  for the Ledger helper), so a slow or busy machine cannot turn it red. Each
  limit keeps its own deterministic test: the 0.3 s checker sleep case, the
  plugin parent-deadline case and `MAXIMUM_SECONDS = 0` in
  `test_financial_checkpoint`. No production limit was changed.

- **D163 (2026-10-16, implementation default for C1):** The GitHub test job may
  run 120 minutes. Revisit if the suite is made faster (see "Suite speed" in
  `planning/handoffs/W1-E.md`).

- **D164 (2026-10-16, implementation default for C1):** The subscription smoke
  cycle is checked against the profile's configured limits, following
  `80cdad6`; only the diagnostic call has the smaller fixed cap.
