# i1 · Manual QA checklist

Cover sheet for verifying a fresh `git clone` produces a working read-only bank
browser. Run through this before tagging an i1 milestone or merging to main.

Full-stack browser automation (Playwright) is intentionally deferred to i2 —
the UI surface expands materially once anchor editing ships, and retrofitting
selectors then is cheaper than shipping now.

## 1. Prerequisites

- [ ] Python 3.11+ available (`python --version`)
- [ ] Node 18+ available (`node --version`)
- [ ] `.venv` created and deps installed (`python -m venv .venv && source .venv/Scripts/activate && pip install -e packages/piano_core -e "apps/api[dev]" numpy scipy pytest`)
- [ ] `apps/web/node_modules` populated (`cd apps/web && npm install`)

## 2. Python test suite

- [ ] `pytest` from repo root → **118 passed** (piano_core + piano_web)
- [ ] No warnings about missing fixtures or skipped real-bank tests

## 3. Seed the dev DB

```bash
python scripts/ingest_idea_banks.py
```

- [ ] Prints `[ok]` for every bank file in `idea/`
- [ ] `data/dev.sqlite` file exists, ~30 MB total after ingest

## 4. Backend launcher

```bash
python run-backend.py --port 8000
```

- [ ] Startup banner prints: URL, Docs, API index, Health, DB path, Log file
- [ ] Log file written under `logs/backend-*.log` (tee works)
- [ ] Second attempt with port in use → clear error with PID hint + `taskkill /F /PID …`
- [ ] Ctrl+C → graceful shutdown (`app.shutdown` log line appears)

## 5. HTTP endpoints

With backend running on `http://127.0.0.1:8000`:

- [ ] `curl /api/health` → `{"status":"ok"}`
- [ ] `curl /api/banks` → 5 ICR banks from `idea/` (ks-grand, as-blackgrand, icr-bank-sample1/2, ks-grand-raw)
- [ ] `curl /api/banks/ks-grand-2604161547-icr` → summary with 704 notes, k_max=80, instrument="ks-grand"
- [ ] `curl /api/banks/ks-grand-2604161547-icr/notes/60/5` → note detail with populated `partials[]`, every partial has `origin: "measured"` and `sigma: null`
- [ ] `curl "/api/banks/ks-grand-2604161547-icr/notes/60/5/curves?parameters=f_coef"` → points with `value ≈ few thousandths` (dimensionless residual)
- [ ] Browser: http://127.0.0.1:8000/docs loads Swagger UI with all 6 routes + "Try it out" working

## 6. Frontend

In a second shell:

```bash
cd apps/web && npm run dev -- --port 3000
```

- [ ] Vite starts on port 3000, no TypeScript errors
- [ ] Browser: http://localhost:3000 loads header "ICR Piano Spectral Editor · i1 · read-only preview"
- [ ] BankSwitcher dropdown lists 5 banks (instrument name shown when set)
- [ ] Selecting a bank populates BankMetaPanel with n_notes / midi_range / velocities / k_max / source
- [ ] Piano keyboard renders: white + black keys, disabled keys greyed out for missing MIDI positions
- [ ] Auto-selection lands on MIDI 60 / vel 5 when bank is first loaded (if present)
- [ ] Clicking a white key highlights it blue; clicking a black key highlights it blue-700
- [ ] Velocity buttons 0..7 highlight in blue when clicked
- [ ] ParameterPlot renders:
  - Default visible: tau1, tau2, A0 (all log-y)
  - Each sub-chart shows one parameter's curve with dots + line + tooltip
  - Toggle buttons add/remove charts live
  - f_coef values are ~0.003 (expected residual magnitude)
- [ ] PartialTable renders:
  - 60-128 rows (per-bank k_max)
  - Sigma column shows `-` for legacy-v1 partials
  - Origin column shows `measured` with zinc badge
  - Scroll works within 320px

## 7. Network & latency

- [ ] First `/api/banks` hit after backend restart: < 100ms (cache miss into empty DB)
- [ ] First `/api/banks/:id/notes/60/5/curves` on the `as-blackgrand` (700 notes): curve JSON returned in < 500ms (spec target for cold cache on 88×8 bank)
- [ ] Second identical curve request: < 50ms (cache hit — see `repo.load.cache_hit` DEBUG log)

## 8. Log quality

- [ ] Each request produces at least one `INFO` log line (repo.load, api.curves, etc.)
- [ ] `extra` fields appear as `[key=value key=value]` suffix — greppable
- [ ] `ICR_VIZ_LOG_LEVEL=DEBUG` reveals cache hit/miss DEBUG lines
- [ ] `ICR_VIZ_LOG_JSON=1` switches output to JSON Lines

## 9. Error paths

- [ ] `curl /api/banks/no-such` → HTTP 404, JSON body `{"detail": "bank 'no-such' not found"}`
- [ ] `curl /api/banks/valid-id/notes/200/0` → 404 for missing note
- [ ] `curl "/api/.../curves?parameters=foo"` → 400 for unknown param
- [ ] FE shows friendly error bar instead of crashing when bank 404 happens mid-load

## 10. Known-deferred items (NOT to verify in i1)

- Anchor add/remove UI (i2)
- SplineTransfer / ToneIdentifyAndCorrect operators (i3)
- 3D surface plot / BankIntegrity (i5)
- MIDI/SysEx bridge (separate work-stream, TBD)
- Playwright automation (deferred to i2 when mutations land)

---

Sign-off: date / tester / outcome of every section above.
