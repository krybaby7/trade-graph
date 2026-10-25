# Owner turns

Provider: claude-cloud
Session: session_01A19Gf5FdrwqpogoP6mQXmJ
Title: Jev benefits for trading graph
Assistant: Claude Code (cloud)
Created: 2026-10-15T18:38:04.267685Z
Source: krybaby7/trade-graph@f1a46de959df4bdeee7736ea7733b261b8d70baf:planning/handoffs/owner-turns/SOURCE-claude-cloud-sessions-2026-10-24.md

## 2026-10-15T18:38:05.169717Z
```text
Could you tell me if we can benefit from Jev in our trading graph ? and if so how ?
```

## 2026-10-15T18:44:53.371486Z
```text
No, I'm talking about the new AI model Jev. I have access to Perplexity Deep Research. Could you provide me with a prompt I'll take there to research about Jev to see if it could help us with trading somehow? And I will share the result with you.
```

## 2026-10-15T18:51:49.956693Z
```text
### Summary for the owner

Jev is a new kind of AI model from TypeSafe AI. It answers multiple-choice, yes/no and rating questions with probabilities and writes no text. Our system could use it as a very cheap sorting and labelling tool. It does not look like a market predictor. The one public crypto and stock test I found had Jev doing no better than simple formulas, and its probabilities were overconfident. All seven uses, at the volumes you gave, cost about 5 cents a day or less in model fees, so cost is not the deciding factor. The strongest fits are labelling past decisions and filtering news. Weaker fits are event triage, regime labels, screening and probability estimates, because our own software already does the number-based work better. I recommend applying for access now, but only for a shadow-mode trial of text-sorting jobs. Nothing Jev says should be able to place or size a trade. Treat "calibrated" as the company's training goal until we measure it on our own data.

Labels used below: **[Official]** is vendor documentation or blog. **[Company claim]** is the vendor's own performance claim. **[Third party]** is independent writing or tests, most from small or unrated sites. **[Inference]** is my own reasoning.

### Part 1: What Jev is

**1. Maker, date, access**
- TypeSafe AI released Jev on 15 September 2026, announced by founder Diogo Almeida, a former OpenAI researcher. The launch was early access with a waitlist. [Official, blog dated 15 Sep 2026] The company calls it a "System One" model. [Official][1][2]
- Access has been unstable. Direct signup opened to everyone on 20 September with $5 of credit, then paused on 22 September because of demand. [Third party, Firecrawl, 23 Sep 2026] A later guide says the door "has opened and paused more than once". [Third party, 11 Oct 2026] I could not confirm whether signups are open today, so **current status is unknown**. Check console.typesafe.ai.[3][4]
- Two routes avoid TypeSafe's signup: Vercel AI Gateway and OpenRouter. OpenRouter lists `typesafe/jev-1.13` through a "Decisions" endpoint, not the chat endpoint. [Third party, 18 Sep 2026][3][5]
- EU individuals: I found no geographic restriction or individual-versus-company rule. That is "not stated", not "confirmed allowed". Services are hosted in the US. The DPA reportedly covers EU and UK transfers. [Third party, NeuralTrust, 6 Oct 2026] Server regions you can choose: **unknown**.[6]
- Versions: Jev 1.13 is the first release and 1.14 was released 14 October. `jev-latest` pointed to 1.13.0 when I fetched the docs. [Official, undated, retrieved 15 Oct 2026] Jev 1.14 adds experimental image input, a bounding-box question type and long context, none of which we need. [Third party, 14 Oct 2026][7][8]

**2. How it works**
- Each request to `POST /v1/systemone` has a `model`, a `state` (a string, a JSON object or an array) and a map of named `questions`. [Official][9]
- Three answer types exist:
  - Noul is yes/no and returns one probability.
  - Choice picks one of up to 255 options. It returns a probability for each option plus a separate confidence value.
  - Score rates against 2 to 10 ordered levels. It returns a probability-weighted value, the level probabilities and a confidence value.
- There is no native free number answer and no native ranking. [Official] [Inference] To rank, ask one Score or Noul per item and sort in code.[9]
- Any number of questions can go in one call, limited only by tokens. Every question is answered against the same state in parallel. [Official][7]
- Context is 64K tokens per request, and the state plus the longest single question must fit in 32K. A header raises both to 262K, but the docs say that is slower and less accurate. [Official][7]

**3. Inputs**
- Jev reads natural-language text, JSON or arrays of text. English is best. [Official][7]
- The documented weak spots are: literal reading, unreliable arithmetic, counting, date ordering, multi-hop questions, distraction by irrelevant text, contradictory criteria, and being steered by hostile text. It cannot write useful text. [Third party summarising the vendor's jagged-edges page, 22 Sep 2026][10]
- There is no documented guidance on price time series or numeric tables. [Inference] Treat that as a weak area.

**4. Pricing, limits, latency, cost reporting**
- Price is $0.042 per million input tokens, with free output. [Official][7]
- Rate limits are per organisation and shown in the console. A request over the limit gets HTTP 429. [Official] Secondary sources quote conflicting numbers (100K tokens/s and 80 requests/s, versus 250K tokens/s and 1,200 requests/min), and TypeSafe says limits can change without notice. [Third party] This is far above our volume.[7][11][12]
- Latency of 70–500 ms is a [Company claim]. Independent measurements: about 0.35 s median per passage [Third party, Firecrawl, 23 Sep 2026], and median under 1 s on classification sets [Third party, BenchLM, 22 Sep 2026].[3][10][13]
- No uptime figure or S

[... clipped ...]

 Copes https://flaviocopes.com/jev-api-key/
[45] Deterministic vs Non-Deterministic AI Outputs - LinkedIn https://www.linkedin.com/top-content/artificial-intelligence/understanding-ai-systems/deterministic-vs-non-deterministic-ai-outputs/
[46] Creating Deterministic, Consistent and Reproducible text in LLMs https://pub.aimind.so/creating-deterministic-consistent-and-reproducible-text-in-llms-e589ba230d44
[47] It's worth noting that LLMs are non-deterministic, This is probably ... https://news.ycombinator.com/item?id=44527256
[48] JEV assisted LLM Trading - DEV Community https://dev.to/nodefiend/jev-assisted-llm-trading-ofa
[49] Why is deterministic output from LLMs nearly impossible? - Unstract https://unstract.com/blog/understanding-why-deterministic-output-from-llms-is-nearly-impossible/
```

## 2026-10-15T18:57:35.600484Z
```text
Okay, let's add the Jev alarm thing as the next task.
```

