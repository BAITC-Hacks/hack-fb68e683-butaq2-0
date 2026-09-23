# Synthetic Voice Router datasets

Three independent, authored insurance datasets for Case 2:

| Directory | Scope | Scenario IDs |
| --- | --- | --- |
| `auto/` | Motor insurance, accidents, repairs and roadside assistance | AUT01–AUT40 |
| `health/` | Health insurance administration, clinic approvals and reimbursements | MED01–MED40 |
| `travel/` | Travel insurance, baggage, cancellations and assistance abroad | TRV01–TRV40 |

Each contains `scenarios.json`, `dialogs_sample.json`, `knowledge_base.json`, `mock_backend.json`, `dev_utterances.json`, `evaluate.py`, and its own README. Each catalogue has 40 intents with neighboring boundaries, action parameters and Russian/Kazakh examples; each dialogue collection has 10 annotated conversations, including code-switching and interrupted topics. All companies, customers, prices and service terms are fictional. Medical examples concern insurance administration, not diagnosis or treatment.

All three datasets live under `app/data/prod/`. The name `prod` is organizational; these are still synthetic fixtures, not real production data. The existing `app/data/demo/` remains the application's default. Creating these files does **not** replace the running database. Choose one whole dataset; do not combine its knowledge or mock records with another catalogue. The different ID prefixes prevent accidental collisions.

## Evaluation

```bash
# Offline: inspect fixtures; no network, no API key required.
python app/data/prod/auto/evaluate.py --limit 48
python app/data/prod/health/evaluate.py --dialogs --limit 10
python app/data/prod/travel/evaluate.py --limit 48
```

For live evaluation, first import the chosen `scenarios.json`, `knowledge_base.json` and `mock_backend.json` together using the existing authenticated `/router/admin/catalog/import-files` endpoint. Import replaces the active catalogue and reference data: use a separate test instance or back up the current catalogue first. It requires `ROUTER_ADMIN_TOKEN`; the evaluator does not import data or change server configuration. Do not put `V2V_API_KEY` in browser code or dataset files.

Then add `--live --base-url http://localhost:8000` to an evaluation command. The script refuses to run against another active catalogue, creates new session IDs, sends only utterances (never expected labels), and disables speech synthesis. Model calls can incur costs. Reports separate primary routing accuracy, pending-topic checks, errors and observed routing latency. A live run adds test sessions; it never executes insurance operations.

These are development fixtures, not the organizer's original forty scenarios or hidden jury tests. No quality score is claimed without a live run. All responses use a fixed **2026-09-23 09:00 Asia/Almaty** snapshot, not today's date. Confirmation only previews a change: no payments, appointments, dispatch, cancellation, medical decisions or operator connections are performed.
