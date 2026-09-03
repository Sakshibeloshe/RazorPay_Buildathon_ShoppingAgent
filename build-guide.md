# Build Guide: GEO for Merchants — Discoverable, and Trusted
### A from-scratch, beginner-friendly walkthrough

This guide assumes you know how to write basic code but haven't built something like this before. I'll explain *what* each piece is, *why* it exists, and *how* to actually build it — in that order, every time.

---

## Part 0: The Mental Model (read this first)

Before touching code, understand what you're actually building in one sentence:

**"We built GEO — the AI-era version of SEO — for Razorpay merchants: we structure a merchant's messy catalog into something LLMs can find, trust, and recommend, and we prove it works by having an AI buyer agent actually complete a purchase through it."**

Keep that sentence visible somewhere your whole hackathon — it's the thing you say when someone asks "wait, what does this actually do?"

If you know traditional SEO, you already understand most of this project — you're just applying the same discipline to a new kind of "search engine": an LLM instead of Google.

| Traditional SEO | Your product's GEO equivalent |
|---|---|
| Crawling (search engines find pages) | **Ingest** — pull a merchant's raw, messy product data |
| Indexing (store with structured, relevant data) | **Structure** — convert raw listing into a clean record |
| Keyword research | **Intent mapping** — the natural-language ways real shoppers describe this need |
| No keyword stuffing | **No claim stuffing** — unsupported claims get penalized, not rewarded |
| Sitemap | **Agent-readable feed** — the structured catalog output, published in one place |
| Serving (best result for a query) | **Recommendation** — the product actually gets surfaced by a real LLM |
| Bounce rate (visitor leaves after one page) | **Answer-to-act gap** — product gets recommended but the agent still can't complete the purchase |

### The Core Loop (six verbs — this is the whole product)

Everything you build maps to one of these six steps. Whenever you feel lost or you're not sure what to work on next, come back to this list and ask "which verb am I building right now?"

1. **Ingest & structure it** — a merchant's raw, messy listing (from whatever custom site, app, or storefront they actually use) becomes a clean, agent-readable record: attributes, fulfillment terms, price, availability. (Part 3.)
2. **Map intent** — figure out the natural-language ways a real shopper (or their LLM) would actually ask for this product, and check whether your structured record answers those framings. (Part 4.)
3. **Score trust** — check whether the claims in that listing actually have evidence behind them, combined with a few hard deterministic checks (return policy present? price consistent?). This produces one field — `trust_score` — that gets attached to the product record itself, not used once and discarded. (Part 5.)
4. **Publish the feed** — combine the structured record, its intent coverage, and its trust score into one agent-readable catalog feed — your sitemap equivalent, and the actual artifact a merchant walks away with. (Part 6.)
5. **Prove discoverability** — test, against real LLMs, whether a product actually gets recommended more often after this treatment than before. This is your headline, hardest number. (Part 7.)
6. **Prove it works end to end** — a buyer agent picks a product from your feed, a trust-based gate decides approve/hold/block, an approved purchase actually fires through Razorpay test mode, and every step is logged. This isn't a second product — it's how you *prove* the feed and trust score are real and usable, not just plausible-sounding. (Part 8.)

That's the entire product. Nothing else you build should compete with these six steps for your time.

### Why the trust score isn't a separate feature

It would be easy to think of "structure and get discovered" and "score trust and gate a purchase" as two different projects sharing one demo. They're not — and this is worth understanding clearly, because it's the difference between a scattered pitch and a tight one.

The trust score is a **field that travels with the structured record**, the same way a page's SEO quality travels with it wherever it gets indexed. Once you've computed "this claim is evidence-backed, this one isn't," that fact is reusable everywhere the product shows up: a buyer agent completing a purchase, an LLM answering a shopper's question, a comparison agent evaluating several merchants, or an agent you never built for at all. You're not gatekeeping one transaction — you're publishing a trust signal that any agent, not just yours, can weigh.

This also protects your GEO layer from repeating old-school SEO's biggest mistake: chasing maximum visibility regardless of quality (keyword stuffing, link farms — things that eventually got penalized once search engines got smarter about detecting them). Scoring evidence quality *as part of* optimization, not as an afterthought, is what keeps your version of GEO defensible instead of gameable.

Your Part 8 buyer-agent-and-gate flow is the demo mechanism that proves this trust field actually means something — not a rival pipeline to your discoverability work.

### Differentiators (only two — resist growing this list)

- **The trust score as a portable "Razorpay Verified" signal**, attached to every structured record (Part 5–6). This is your main one. Almost every competing team will build *some* version of "structure a catalog for AI." Very few will attach an evidence-based trust signal to that structured output that any agent can read and act on.
- **Cross-model disagreement as a secondary trust signal**, folded into Part 5: if you're already calling more than one LLM, check whether they agree on a claim. Different AI models genuinely cite very different sources from each other, so disagreement is a real, measurable signal. **This is a stretch feature.** If it isn't done by Saturday afternoon, cut it without guilt — it must never compete for time against Parts 6–8, which are what make your demo credible at all.

### Execution, not just reasoning

Razorpay integration and the audit trail (inside Part 8) don't differentiate you from other teams — plenty of teams will wire up Razorpay. What they do is make everything above them *believable*. A judge doesn't care that you have an audit trail; they care that your trust score isn't vibes. Treat this as the credibility layer under the real product, not a headline feature.

### Your one hard number

The **before/after discoverability test** (Part 7) is the thing you should never bury in your actual pitch: proof that your structuring work measurably increases how often a real AI model recommends the product. This is your only hard, non-cherry-picked number, and it deserves its own slide, not a bullet point.

---

## Part 1: The Tools You Need (and what each one actually does)

You don't need to be an expert in any of these — you need to know *what job each tool does* so you can look up "how do I do X in Python" as you go.

| Tool | What it actually is | Why you need it |
|---|---|---|
| **Python** (or Node.js) | A programming language | The language you'll write your logic in. Python is easier for beginners and has better AI/data libraries — I'd pick Python unless your team already knows Node well. |
| **FastAPI** (Python) or **Express** (Node) | A "web framework" — lets your code respond to requests like a website does | This turns your Python script into something a browser or another program can talk to |
| **An LLM API** (Anthropic Claude API, OpenAI API) | A way to send text to an AI model and get text back, over the internet | This is how you'll (a) extract structured data from product descriptions, and (b) run your discoverability test |
| **Groq API (Llama models)** | A fast, cheap way to run open-source models via API | Good for your internal pipeline calls (extraction, scoring, matching) where speed and cost matter more than using the exact model a shopper would use |
| **Razorpay Test Mode API** | Razorpay's real payment system, but using fake money — nothing actually charges | This is how you prove a "money action" actually happened, safely |
| **A simple database or even just a JSON file** | Somewhere to store your product catalog and results | For a hackathon, a JSON file is completely fine — don't overthink this |
| **A frontend** (a simple HTML page, or React if your team knows it) | The screen judges will actually look at | Keep this simple — one dashboard page, not a multi-page app |
| **Git + GitHub** | Version control — a history/backup of your code that your team can share | So your team doesn't overwrite each other's work |

**You do NOT need:** a real database server, authentication/login systems, a mobile app, Kubernetes, or anything "enterprise." Judges are scoring your idea and execution, not your DevOps setup.

---

## Part 2: Setting Up (Day 0, before the clock starts)

Do these one at a time. Each one should end with something small and visibly working — never move to the next step until the current one works.

### Step 2.1 — Get your accounts and keys
1. Sign up for Anthropic API access (console.anthropic.com) or OpenAI API access — get an API key. Sign up for Groq API access too if you're using it for internal calls. **Treat these keys like passwords — never put them directly in code that gets shared publicly.**
2. Sign up for a Razorpay account and switch to **Test Mode** (there's a toggle in their dashboard). Get your test API key and secret.
3. Store all keys in a file called `.env` (a standard convention — this file holds secrets and is never uploaded to GitHub).

### Step 2.2 — Prove each piece works in isolation, before connecting anything
This is the single most important habit for a hackathon: **never build two new things at once.** Test each tool alone first.

- Write a 5-line script that sends "Hello" to the LLM API and prints the response. Confirm it works.
- Follow Razorpay's test-mode "create an order" quickstart and confirm you can create a fake order and see it in their test dashboard.
- Confirm you can read/write a simple JSON file from your code.

Once all three of these work independently, you're ready to actually build the product — because now you know none of your tools are going to surprise you later.

---

## Part 3: Component 1 — Ingest & Structure

**What it does in plain terms:** takes a merchant's raw, messy product data and turns it into a clean, structured, agent-readable record.

**Why this is needed — and why it's harder than it sounds:** most Razorpay merchants aren't on one standardized platform with a clean built-in product feed. They have their own custom-built websites, mobile apps, or storefronts, each formatted differently. That means this component can't assume tidy input the way "convert a Shopify product to JSON" would — it has to handle arbitrary, inconsistent text and still produce something usable. This is genuinely your most defensible technical claim: you're normalizing chaos, not just reformatting clean data.

**How to build it:**
1. Write out 15–20 fake products by hand, as plain text, deliberately in a few *different* formats and styles — some like a formal e-commerce listing, some like a rough WhatsApp-catalog description, some with inconsistent structure. This mimics the real heterogeneity your ingestion has to survive. Save these in a file — this is your **catalog**.
2. Write a prompt for the LLM that says, roughly: *"Extract the following fields from this product listing as JSON: category, target audience, key attributes, claims made, price, availability, fulfillment terms (delivery window, return policy). If a claim has no supporting evidence in the text, mark it as unsupported. If a field is missing from the source text, mark it as missing rather than guessing."*
3. Send each product through this prompt, one at a time, and save the structured JSON output.

**What you'll learn here:** how to write a good "extraction prompt" — this is a real, transferable skill called *prompt engineering*. The trick is being extremely specific about the exact output format you want (ask for JSON, give an example of the shape you expect), and being explicit about what to do when data is missing rather than letting the model quietly invent it.

**Checkpoint:** you should now have a folder of 15–20 JSON files, each one a clean, structured version of a messy, inconsistently-formatted product listing.

---

## Part 4: Component 2 — Map Intent

**What it does in plain terms:** this is your "keyword research" equivalent — instead of guessing exact search terms, you figure out the natural-language ways a real shopper (or their LLM) would actually describe needing this product, and check whether your structured record can answer those framings.

**Why this matters:** LLMs match on meaning, not exact keyword strings — a shopper might say "gentle deodorant, no harsh chemicals" while your listing says "fragrance-free, alcohol-free." Traditional SEO would need the exact keyword; GEO needs the *concept* covered.

**How to build it:**
1. For each product, write 2–3 natural-language ways a shopper might ask for something like it (e.g., for a sensitive-skin deodorant: "gentle on sensitive skin," "no harsh chemicals," "safe for daily use").
2. Ask the LLM: *"Given this structured product record and this shopper phrasing, does the record contain information that answers this framing? If not, what's missing?"*
3. Store the result as a simple `intent_coverage` field: which framings are covered, which aren't, and what's missing for the ones that fail.

**What you'll learn here:** the difference between keyword matching (exact string) and semantic matching (meaning) — this is a core concept in modern search and a good thing to be able to explain if a judge asks how GEO differs from SEO.

**Checkpoint:** every product now has an `intent_coverage` field showing which realistic shopper phrasings it can and can't currently answer.

---

## Part 5: Component 3 — Score Trust

**What it does in plain terms:** looks at each structured product and asks "is this claim actually backed by anything, or is it just asserted?" — your "no keyword stuffing" rule, applied to claims instead of keywords.

**Why this matters:** This is the part that catches a "Morrowen" — a claim like "clinically proven" is meaningless unless something in the listing backs it up (a certification, a study reference, a specific ingredient explanation). Just like Google eventually penalized stuffed, low-quality pages, an LLM (or a careful agent) should discount unsupported claims rather than repeat them uncritically.

**How to build it, step by step:**
1. For each claim your Component 1 extracted, write another LLM prompt: *"Here is a claim: '{claim}'. Here is the full product listing: '{listing}'. Does the listing provide actual supporting evidence for this claim, or is it an unsupported assertion? Answer with a score from 0-100 and a one-sentence reason."*
2. Also check simpler, non-LLM signals you can compute directly in code (no AI needed for these — this makes your system more robust and less dependent on the LLM getting things right):
   - Is a return policy mentioned anywhere? (yes/no)
   - Is there a consistent price across the listing? (yes/no)
   - Is there any contact/business info? (yes/no)
3. Combine the claim-evidence score with these simpler checks into one overall **`trust_score`** per product (e.g., average them, or weight the claim-evidence score more heavily since it's the core signal). Attach this score, plus a one-line reason, directly onto the product's structured record — it's a field on the record, not a separate database.
4. **Optional stretch (cut first if short on time):** also call a second LLM provider on the same claim and note whether the two models agree. Store this as `cross_model_agreement` on the same record.

**What you'll learn here:** how to combine "AI judgment" with plain old rule-based logic (the yes/no checks) — a real technique used in production ML systems, called a **hybrid scoring system**.

**Checkpoint:** every product in your catalog now has a `trust_score` (0–100), a plain-English reason, and (optionally) a cross-model agreement flag, all attached to the same structured record from Part 3.

---

## Part 6: Component 4 — Publish the Feed

**What it does in plain terms:** combines everything so far — the structured record (Part 3), its intent coverage (Part 4), and its trust score (Part 5) — into one clean, agent-readable catalog feed. This is your sitemap equivalent, and it's the literal artifact a merchant walks away with at the end of the hackathon.

**How to build it:**
1. For each product, merge the outputs of Parts 3–5 into a single JSON object.
2. Write all of these objects into one file, `feed.json` — an array of fully-enriched product records.
3. That's it. This file is what Parts 7 and 8 both consume, and it's the thing you'd show a merchant and say "this is what your catalog looks like to an AI now."

**Checkpoint:** one `feed.json` file containing your whole catalog, each entry structured, intent-mapped, and trust-scored.

---

## Part 7: Component 5 — Prove Discoverability

**What it does in plain terms:** proves that your structuring work (Parts 3–6) actually makes a product more likely to be recommended by a real AI. This is your headline number.

**How to build it:**
1. Write 8–10 realistic shopping questions a person might type into ChatGPT or Claude (e.g., "what's a good fragrance-free deodorant for sensitive skin under ₹1000?").
2. For each question, send it to the LLM **twice**:
   - Once giving the AI only the raw, original (unstructured) product text
   - Once giving the AI your `feed.json` entry for that product
3. Check whether the product gets mentioned/recommended in each case, and log it.
4. Count up: "before optimization, mentioned in X out of 10 queries. After, mentioned in Y out of 10."
5. **If you have time:** segment this result by trust score — do high-trust products see a bigger discoverability lift than low-trust ones? That's a genuinely interesting second finding, not just a nice-to-have.

**What you'll learn here:** this is your first real **experiment with a control group** — "before" is your control, "after" is your treatment. This is basic scientific method applied to AI, and it's exactly the kind of rigor that makes judges trust your numbers instead of your claims.

**Checkpoint:** you have one clean, honest, headline statistic for your demo — and possibly a second one connecting trust score to discoverability.

---

## Part 8: Component 6 — Prove It Works End to End

This part has four small pieces that build on each other. Together, they're the proof that your feed isn't just theoretically useful — an agent can actually act on it safely, including completing a real (test-mode) payment.

### 8a. The Buyer Agent (kept simple on purpose)

**What it does:** takes a shopping request and picks the best matching product from your `feed.json`.

**How to build it:**
1. Take a shopping query (e.g., "sensitive skin deodorant under ₹1000").
2. Send it to the LLM along with your full `feed.json`, and ask: *"Which product best matches this request? Return the product ID."*
3. You now have a "purchase intent": `{query: "...", matched_product: "SkinCalm Deodorant", requested_price: 699}`

**Important teaching point:** resist the urge to make this agent "smarter" (negotiation, multi-turn conversation, memory, etc.). A basic, working agent that reads your feed correctly is more valuable than an impressive-sounding agent that eats your whole weekend.

### 8b. The Trust-Based Gate

**What it does:** the decision-maker. Takes the `trust_score` already sitting on the matched product and decides: approve, hold, or block.

**How to build it — this is mostly plain code logic, not AI:**
```
if trust_score >= 80 and price <= spend_limit:
    decision = "APPROVE"
elif trust_score >= 50:
    decision = "HOLD_FOR_APPROVAL"
else:
    decision = "BLOCK"
```
This is intentionally simple — a set of `if/else` rules based on the trust score you already calculated in Part 5. **Don't make this an AI call.** A hackathon judge will actually be *more* impressed that your money-decision logic is simple, deterministic, and auditable — that's a real best practice in fintech, not a shortcut.

**For the BLOCK case (your graceful failure):**
- Write a message template: *"This product was not approved for automated purchase. Reason: {trust_score reason}. Here is a verified alternative that meets similar criteria: {alternative_product}."*
- Have your system automatically find the next-best product above the trust threshold, from the same `feed.json`, and suggest it.

### 8c. Razorpay Execution (the APPROVE path)

**What it does:** when a purchase is approved, actually create a real (test-mode) payment.

**How to build it:**
1. Follow Razorpay's test-mode "Orders API" documentation — well-documented with copy-pasteable code samples.
2. When your gate outputs `APPROVE`, call Razorpay's API to create an order for that product's price.
3. Log the response (order ID, status) back into your system.

**Teaching point:** this is the step that turns your project from "a cool AI demo" into "a fintech product." Don't skip it or fake it — actually calling the real (test-mode) API is what makes your audit trail credible to judges who know the difference between a real API call and a hardcoded fake response.

### 8d. The Audit Trail

**What it does:** a running log of every decision your system ever made, in one place.

**How to build it:** every time a purchase intent goes through 8a–8c, append one record to a JSON file (or a simple table) with these fields:
```
timestamp, product, claim, evidence_check_result, trust_score,
decision, reason, payment_status
```
No fancy database needed. This single log file is what makes your final metrics ("we approved X, held Y, blocked Z, and here's why") trustworthy and demoable.

**Checkpoint for all of Part 8:** given a purchase intent, your system matches a product from the feed, gates it using the trust score already attached to that product, fires a real test-mode payment when approved, and logs every step.

---

## Part 9: The Dashboard (what judges actually see)

Keep this to **one screen**. Don't build multiple pages/tabs — a single scrolling page is easier to demo and looks more polished under time pressure than a half-finished multi-page app.

Show, top to bottom:
1. A visual of the pipeline (ingest → structure → score → feed → discover → transact) — even a simple diagram image is fine
2. Your discoverability before/after numbers (Part 7) — your headline stat, given real visual weight
3. A sample entry from `feed.json`, showing the structured record, intent coverage, and trust score together — this is "what a merchant gets"
4. A table of your test batch: product, trust score, decision, reason
5. One expanded example of a full audit trail entry
6. A live "try it" box where you can type a shopping query during the demo and watch it flow through

**Teaching point:** a good dashboard tells the story *without you needing to narrate every line* — judges should be able to glance at it and understand what happened. Build this last, once everything underneath it actually works — never build UI around functionality that doesn't exist yet.

**Optional bonus, cheap to add if you have time:** a simple upsell/cross-sell suggestion — once every product has consistent structured attributes, "customers who need X also often need Y" can mostly be a lookup over your own `feed.json` (same category, complementary use-case) rather than new infrastructure. Nice to mention, not worth building if you're behind schedule.

---

## Part 10: How Your Team Should Split This Work

If you have 3–4 people, here's a sensible split that avoids people blocking each other:

- **Person A:** Parts 3 + 4 (ingest/structure + intent mapping) — this is the "AI/prompting" role
- **Person B:** Part 5 (trust scoring, including the deterministic checks) — this can start as soon as Person A has structured even one product
- **Person C:** Part 8a + 8b (buyer agent + gate) — this is the "logic/backend" role, and depends on Part 6's feed existing
- **Person D:** Part 8c + 8d (Razorpay integration + audit trail) — this is the "integration" role, and can be built and tested against a fake/dummy trust score while waiting for the real pipeline

**Part 6 (publish the feed) and Part 7 (discoverability test)** are small, fast steps that whoever finishes Parts 3–5 first should pick up — don't assign them to a person from day one.

**A scheduling trap to watch for:** Part 7 (discoverability test) and Part 8a (buyer agent) both consume the same `feed.json` and can look like "whoever's free" work — but don't let the same person try to do both back-to-back under time pressure. Part 7 is your headline number (don't rush it); Part 8a feeds the gate everything else depends on (don't delay it). If you're short a person, prioritize Part 8 first and treat Part 7 as something you return to once the core loop works end to end — a late but careful discoverability number beats an early but sloppy one.

Everyone should agree on the **shape of `feed.json`** (exactly what fields each product record has) *before* splitting up — this is the single most common way hackathon teams waste time, reconciling mismatched data formats at 2am.

---

## Part 11: A Realistic Order of Operations (don't skip steps)

**Tier 1 — the core loop (this must be rock-solid before anything else gets attention):**
1. Fake catalog written by hand, deliberately messy/inconsistent ✅ before you write any code
2. Prove each API works alone (Part 2.2)
3. Part 3, ingest & structure — one product, then all 20
4. Part 4, intent mapping
5. Part 5, trust scoring
6. Part 6, publish `feed.json`
7. Part 8a + 8b, buyer agent + trust-based gate
8. Part 8c + 8d, Razorpay execution + audit trail

**Tier 2 — proof (start once Tier 1 works end to end for at least one product):**
9. Part 7, the discoverability test — your one hard number. Give this real time; don't squeeze it into leftover hours.
10. Cross-model agreement signal (Part 5, optional), only if everything above is done. This is the first thing to cut if you're behind.

**Tier 3 — presentation (build last, on top of a working system):**
11. Dashboard (Part 9)
12. Rehearse, find bugs, fix, rehearse again

A note on why the discoverability test sits in Tier 2 even though it's your best number: it's genuinely valuable, but it doesn't feed into the gate or the payment — the demo still works end to end without it. If you're on schedule, do it early and give it real care. If you're behind, Tier 1 is what has to work no matter what; a missing discoverability number is a smaller loss than a payment flow that doesn't fire on stage.

Resist doing these out of order — especially don't build the dashboard early "to see progress." It's tempting but it always ends up rebuilt anyway once the real data shapes are finalized.

---

## A note on how to actually learn this as you go

You don't need to understand every concept deeply before starting — you need to understand it well enough to get the next small piece working. When you get stuck, the two most useful questions to search are usually:
- "[tool name] + quickstart" (e.g., "Razorpay test mode orders API quickstart")
- "[tool name] + [exact error message]" if something breaks

Build in small, working increments — get one product through one component before trying all 20 products through the full pipeline. Every checkpoint in this guide is designed to be a small, real, working thing you can show someone — use them as your pacing.
