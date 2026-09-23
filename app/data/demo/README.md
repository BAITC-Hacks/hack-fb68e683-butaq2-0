# Butaq Demo Insurance — synthetic Case 2 dataset

This is an authored insurance simulation, **not the official HackAlem starter kit or jury test set**. Every customer, ID, address, policy, amount and deadline is fictional. Snapshot time: 2026-09-23 09:00 Asia/Almaty. Relative dates refer to this snapshot, not today's date. No real payments, document delivery, account changes or operator connection are executed.

- `scenarios.json`: 40 distinct intents with purpose, neighboring boundaries, action parameters, RU/KZ request-response examples, knowledge references and confirmation rules. S19 and S39 describe handoff cases: the router must return `action=handoff`, `scenario_id=null` as required by the existing API.
- `knowledge_base.json`: five demo insurance products, coverage/exclusions, illustrative prices, deductibles, service procedures and uncertainty rules. Sample premiums are fixed examples, not a tariff calculator.
- `mock_backend.json`: three synthetic clients, four policies, three applications, four payments, a refund, five claims, a payout, two deliveries and document metadata. All references are linked; unknown IDs must not inherit another customer's record.
- `dialogs_sample.json`: 10 annotated conversations covering topic switching, returning to pending topics, mixed speech, ambiguity, emergency prioritization, repeated uncertainty and confirmation without execution.
- `dev_utterances.json`: 48 independently phrased development cases, covering all 40 scenarios plus ambiguity, multiple intents and unsupported requests. Labels are expectations, not evidence of model accuracy.
- `evaluate.py`: optional live evaluation of real LLM routing. It never supplies labels to the router or synthesizes speech. Errors count as misses; pending-topic results are reported separately from scenario accuracy.

## Try speaking

1. «По заявке DEMO-A-2001 деньги списались, а полиса нет. Потом ещё адрес доставки поменяем».
2. «Алдымен жеткізу мекенжайын өзгертейік: Демо-улица, 8».
3. «Вернёмся к оплате».

Other records: DEMO-P-1001 (active auto policy), DEMO-C-5001 (missing photos), DEMO-C-5003 (approved claim, payout pending), DEMO-R-4001 (refund sent, credit unconfirmed). Say identifiers digit by digit if speech recognition struggles, or use text.

## Evaluate

From the repository root, with the backend running:

```bash
# Inspection only; no model calls
python app/data/demo/evaluate.py
# Billable LLM calls using the server's V2V_API_KEY
python app/data/demo/evaluate.py --live --limit 48
python app/data/demo/evaluate.py --live --dialogs --limit 10
```

The full catalogue is given to the LLM for every decision. There is no keyword router or hardcoded test-phrase mapping. Unit tests validate data integrity and seeding; only live evaluation measures routing quality. The official 40-scenario evaluation still requires importing the organizer's catalogue.

Local smoke check (2026-09-23, `gpt-4o-mini`): `--live --dialogs --limit 1` selected S11 → S33 → S11 with the expected pending topics on all three turns, including the Kazakh topic switch. Routing took 5,883 / 1,945 / 3,265 ms; total text-only responses took 8,525 / 3,644 / 5,421 ms. This single run is not a full accuracy benchmark, did not test STT/TTS, and does not meet the 500 ms routing target. Earlier smoke attempts exposed a provider JSON-mode input requirement and an invalid model response; an explicit JSON format/schema hint was added before this successful run. Model responses remain validated and can still fail.

## Bootstrap

The API seeds these three data files atomically on first service initialization **only if the scenario table is empty**. A PostgreSQL transaction lock prevents two workers seeding simultaneously. Restarts preserve existing catalogues, prompt settings and manual edits. This bootstrap requires no admin token. Authenticated admin API endpoints remain available for later official-catalogue imports; seeding never overwrites an imported catalogue.
