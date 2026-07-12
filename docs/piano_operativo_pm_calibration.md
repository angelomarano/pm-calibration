# Piano operativo — Prediction Market Calibration & Tradability
**Polymarket 2024–2026 · calibrazione, inferenza event-clustered, persistenza out-of-sample al netto dei costi**

Versione 1.0 — 9 luglio 2026 · Budget: 42–54 ore su ~6 settimane part-time · Freeze: 23 agosto 2026

---

## 0. Scheda progetto

**Domanda di ricerca.** I prezzi di Polymarket sono probabilità ben calibrate? Dove non lo sono (favorite–longshot bias, compressione a orizzonti lunghi), la miscalibrazione è (a) robusta a un'inferenza che rispetta la dipendenza tra mercati, (b) misurata su un orologio non-anticipativo, (c) persistente out-of-sample nel 2026, e (d) monetizzabile al netto di fee, spread e costo del capitale?

**Contributo (le 4 corsie).**
1. **Inferenza dependence-aware**: pannello a livello mercato con bootstrap clusterizzato per evento, contro le analisi trade-level pubblicate (milioni di trade ≠ osservazioni indipendenti: condividono lo stesso esito).
2. **Timing non-anticipativo**: snapshot a date di calendario e a deadline programmata, contro l'orizzonte "alla risoluzione effettiva" (stopping time dipendente dall'esito).
3. **Persistenza OOS**: la letteratura si ferma al 31/12/2025 → H1-2026 è terreno vergine; test di decay post-pubblicazione (framing McLean–Pontiff).
4. **Tradabilità netta**: fee esatte (flag per-mercato), haircut di spread con bande di sensibilità, carry da lockup del capitale.

**Configurazione bloccata.** Solo Polymarket · pannello mensile a calendario (primario) + pannello ancorato alla deadline (companion) · split OOS al 31/12/2025 · costi via fee esatte + haircut + carry · output = repo GitHub + README con figura-titolo + research note 4–6 pp.

**Esiti possibili — tutti difendibili a CV.** Edge persistente netto → anomalia documentata OOS. Edge lordo ma non netto → premio per frizioni/carry (limits to arbitrage). Edge decaduto nel 2026 → anomaly decay post-pubblicazione. Nessun effetto con inferenza onesta → gli effetti pubblicati sono artefatti di ponderazione/selezione. Il deliverable è il metodo; il finding è quello che è.

---

## 1. Specifica primaria (pre-registrazione)

> ⚠️ **Questa sezione va copiata nel README e committata PRIMA di lanciare il run sul campione completo.** L'hash del commit è la prova. Tutto ciò che non è qui dentro è etichettato *esplorativo* nella nota finale.

### 1.1 Popolazione e unità di analisi
- Mercati binari Polymarket **risolti**, creati o attivi tra 2024-01-01 e 2026-06-30.
- Unità = **mercato × snapshot**, sola gamba **Yes** (il No è 1−Yes per costruzione).
- Cluster di dipendenza = **evento** (event id da Gamma; fallback: il mercato stesso se orfano).

### 1.2 I due design di campionamento (entrambi condizionano solo su stati osservabili ex ante)
- **P1 — Pannello a calendario (primario):** snapshot alle 00:00 UTC del 1° di ogni mese, 2024-01-01 → 2026-06-01 (30 date). Un mercato entra allo snapshot t se: creato prima di t, non ancora chiuso a t, prezzo disponibile. *Estimando: calibrazione dello stock di mercati aperti a inizio mese.*
- **P2 — Pannello a deadline (companion):** snapshot a T−7g e T−30g dalla **deadline programmata** (endDate), condizionato a mercato ancora aperto allo snapshot. *Estimando: calibrazione dei mercati aperti a orizzonte fisso dalla scadenza.* Copre i mercati a vita breve che P1 struttura­lmente manca.
- **Prezzo allo snapshot:** ultimo bucket di prices-history con timestamp ≤ t, fidelity=1440 (fallback 720 — regola delle 12h per i risolti), staleness massima 72h, altrimenti missing.

### 1.3 Filtri (fissati ora, sensibilità dopo)
- Volume lifetime ≥ 10.000 USDC (terzili di volume per l'eterogeneità).
- Prezzo allo snapshot in [0.01, 0.99]; replica del filtro [0.05, 0.95] come spec di confrontabilità con la letteratura.
- Esclusi: risoluzioni disputate/ambigue/annullate, outcomePrices non degeneri, mercati con esito non ricostruibile.
- Esito Y: da campi di risoluzione Gamma, cross-check con outcomePrices degeneri (concordanza misurata al Gate A; discrepanze escluse e conteggiate).
- Categorie: mapping da tag Gamma → {Politics, Sports, Crypto, Econ/Finance, Culture, Geopolitics, Other}; quota di non mappati riportata.
- **Headline sempre ex-Sports** (pooled non-sports + per categoria); Sports riportato separatamente.

### 1.4 Stime e test primari
- **Regressione di calibrazione:** logit(P(Y=1)) = α + β·logit(p) per design × categoria. H₀: (α, β) = (0, 1). β>1 ⇔ compressione verso 0.5 ⇔ favorite–longshot bias.
- **Inferenza:** bootstrap a blocchi per **evento**, B = 2.000, CI percentile. Stesso motore per: (α,β), Brier + decomposizione di Murphy (REL − RES + UNC) + skill score vs base rate, gap per bin.
- **Diagrammi:** reliability diagram con CI di Wilson per bin (decili) + curva isotonica in overlay (indipendenza dal binning). Log-loss come robustezza.
- **Test complementari:** Z di Spiegelhalter; binomiali per bin con correzione Benjamini–Hochberg.

### 1.5 Strategia OOS (regola dichiarata ora, valutata solo in W4)
- **Stima in-sample:** mappa di calibrazione su dati ≤ 2025-12-31. **Quarantena OOS:** nessuna analisi tocca snapshot 2026 prima della W4 (flag in config che lo impedisce).
- **R1 (primaria) — fade dei tail:** allo snapshot, se la mappa in-sample indica longshot sovraprezzati → compra No con prezzo Yes ∈ [0.02, 0.10]; se indica favoriti sottoprezzati → compra Yes ∈ [0.90, 0.98]. Size uguale per trade, hold a risoluzione, ingresso taker.
- **R2 (esplorativa) — trade the map:** posizione su ogni bucket con |gap in-sample| > costo round-trip stimato.
- **Censoring:** gambe OOS solo su mercati con deadline programmata ≤ 2026-06-30; quota esclusi riportata; mark-to-market all'ultimo prezzo come robustezza dichiarata.
- **Costi:** (i) fee = c·shares·P·(1−P) **solo se feesEnabled** sul mercato — coefficienti: Sports 0.03, Finance/Politics/Tech/Mentions 0.04, Econ/Culture/Weather/Other 0.05, Crypto ≈0.072, Geopolitics 0; (ii) haircut di half-spread per terzile di liquidità, calibrato su order book storici (/orderbook-history, disponibili fino a ~20/02/2026) + book live, presentato in bande **0.5× / 1× / 2×** con break-even esplicito; (iii) carry = giorni di lockup × risk-free 3M (FRED DGS3MO).
- **Metriche:** edge medio netto per trade con CI bootstrap a evento; distribuzione per-trade (skew!); PnL cronologico per drawdown; rendimento annualizzato sul capitale vs risk-free. Niente Sharpe su payoff binari.

---

## 2. Repo e tooling

```
pm-calibration/
├── README.md              # spec primaria (pre-registrata) + figura-titolo + risultati
├── DECISIONS.md           # log deviazioni dalla spec: data, cosa, perché
├── config/spec.yaml       # date snapshot, filtri, soglie, flag quarantena-OOS
├── data/raw/              # cache JSON Gamma+CLOB (gitignored, rigenerabile da script)
├── data/panel/            # parquet
├── src/ingest/            # gamma_events.py · clob_prices.py · orderbooks.py
├── src/panel/             # build_panel.py · resolution.py · categories.py · filters.py
├── src/calibration/       # reliability.py · murphy.py · calib_regression.py · tests_stat.py
├── src/inference/         # bootstrap.py (unico motore, resampling per evento)
├── src/strategy/          # rules.py · costs.py · oos_eval.py
├── notebooks/             # solo esplorazione; la logica vive in src/
├── tests/                 # pytest sui casi limite
└── note/                  # research note (LaTeX o md→pdf)
```

Python 3.12, requests+tenacity (backoff, resumable), polars + DuckDB, statsmodels/scipy, matplotlib. Dipendenze pinnate (uv/pip). Test minimi da scrivere: parsing evento multi-outcome, Y da outcomePrices degeneri, esclusione disputati, logica staleness snapshot, formula fee, filtro censoring.

---

## 3. Cronoprogramma con gate

### W0 · Gate A — Feasibility spike · **entro dom 12/7 · 2–3 h**
Campione ~200 mercati risolti stratificato: 3 anni × 5 categorie × 3 terzili di volume.
- [ ] **A1 Copertura prezzi:** prices-history a fidelity 1440/720 su tutti; misura quota con storico utilizzabile e span (primo/ultimo timestamp vs vita del mercato).
- [ ] **A2 Concordanza esiti:** campi risoluzione Gamma vs outcomePrices degeneri; conta mismatch.
- [ ] **A3 Affidabilità endDate:** prevalenza risoluzioni anticipate e endDate incoerenti (ultimo prezzo >> o << endDate).
- [ ] **A4 Operatività:** rate limit empirico prices-history (ramp gentile); presenza flag feesEnabled; parametri fee nell'oggetto CLOB.

**Exit criteria:** copertura ≥90% sui mercati filtrati → **GO**. 70–90% → GO con finestra spostata (inizio 2024-H2), annotato in DECISIONS.md. <70% → **STOP e decisione**: fallback trade-tape Becker (+10–15 h, re-scope del piano).

### W1 · Ingestion + pannelli · **13–19/7 · 10–14 h**
- [ ] Pull universo via `/events?closed=true`, paginazione per **finestre di data** (ordinamento offset instabile), limit 500, cache JSON grezzi, script resumable.
- [ ] Estrazione: market/event id, tag→categoria, clobTokenId Yes, volume, liquidity, endDate, campi risoluzione, feesEnabled.
- [ ] Pull prices-history (Yes, fidelity 1440) per i mercati filtrati — batch notturni, backoff, ripresa da cache. Preventivo 1–2 notti.
- [ ] Costruzione P1 (30 date) e P2 (T−7g, T−30g); tabella parquet: una riga per mercato × snapshot.
- [ ] **Gate B — Panel sanity:** conteggi per categoria × periodo × design; base rate; zero duplicati; distribuzione staleness; % missing; quota eventi multi-mercato; spot-check su mercati noti (elezioni 2024 presenti con prezzi sensati). **Exit:** numeri negli ordini attesi, nessun red flag.

### W2 · Calibrazione core (solo ≤2025) · **20–26/7 · 8–12 h**
- [ ] Reliability diagram + isotonica, per design × categoria; figura-titolo v1.
- [ ] Brier, Murphy, BSS, log-loss con CI bootstrap.
- [ ] (α, β) con bootstrap a evento; Spiegelhalter; per-bin + BH.
- [ ] **Gate C — Sanity vs letteratura:** slope nel ballpark dei risultati pubblicati a orizzonti comparabili (compressione crescente con l'orizzonte, intercetta politica positiva). Se fuori scala → caccia al bug prima di procedere.

### W3 · Riconciliazione + eterogeneità (solo ≤2025) · **27/7–2/8 · 8–12 h**
- [ ] **Griglia di design:** ponderazione {equal, volume-weighted} × orologio {P1, P2, τ-alla-risoluzione (spec di confrontabilità)} × campione {tutti, ex-sports, terzile liquido} × periodo {2024, 2025}.
- [ ] **Figura design-sensitivity** (una sola): segno/entità del FLB attraverso la griglia → dove e perché i paper divergono.
- [ ] Quantificazione della selezione da stopping time: mercati a chiusura fissa vs early-resolvable sotto l'orologio τ.
- [ ] Robustezza: bin alternativi, filtro 5–95, esclusione crypto "entro data".

### W4 · OOS + costi + strategia · **3–9/8 · 8–12 h**
- [ ] Congela mappa in-sample; sblocca quarantena 2026.
- [ ] Calibrazione OOS H1-2026: stessi (α, β) e gap per bin; confronto formale in/out (test di persistenza).
- [ ] R1 su snapshot gen–giu 2026 con censoring; costi per trade (fee via flag, haircut in bande, carry); edge netto con CI; PnL cronologico; break-even half-spread.
- [ ] Sotto-taglio esplorativo pre/post 30 marzo (fee V2) sulle categorie interessate.
- [ ] R2 esplorativa se resta tempo.

### W5 · Research note + packaging · **10–16/8 · 6–8 h**
- [ ] Nota 4–6 pp: domanda → design (tre orologi, clustering) → risultati → riconciliazione → verdetto economico. Una figura-titolo, la design-sensitivity, tabella costi, tabella persistenza.
- [ ] README finale: spec pre-registrata (già committata), risultati, riproducibilità con un comando, limitations & future work (replica Kalshi, depth del book).
- [ ] Igiene: test verdi, lint, LICENSE, dati non committati ma rigenerabili.
- [ ] Prep colloquio: walkthrough da 10 minuti + risposte alle domande prevedibili (perché β>1 = FLB; perché cluster per evento; trade-weighted vs market-level; cosa cambia post-fee; perché niente Sharpe).

**Buffer 17–23/8** per slittamenti (trasloco, ETH). **Freeze il 23/8**, prima dell'apertura delle application.

---

## 4. Budget ore

| Fase | Ore | Cumulato |
|---|---|---|
| W0 Gate A | 2–3 | 3 |
| W1 Ingestion + pannelli | 10–14 | 17 |
| W2 Calibrazione core | 8–12 | 29 |
| W3 Riconciliazione | 8–12 | 41 |
| W4 OOS + costi | 8–12 | 53 |
| W5 Nota + packaging | 6–8 | 61 max |

Range realistico 42–54 h; il massimo teorico (61) rientra nel buffer.

---

## 5. Disciplina e ordine di taglio

**Mai tagliare:** pre-registrazione della spec · clustering per evento · quarantena OOS · la W4 (è ciò che rende il progetto alpha-research e non data analysis).

**Ordine di taglio se il tempo stringe:**
1. Spread storici da /orderbook-history → solo book live + bande di sensibilità.
2. Spec τ-alla-risoluzione (resta un paragrafo di discussione, senza numeri).
3. Griglia W3 ridotta a ponderazione × campione.
4. P2 solo a T−30g.

**Regole:** ogni deviazione dalla spec → riga in DECISIONS.md (data, cosa, perché). Se una settimana slitta oltre 1.5× il budget → applica il taglio successivo, non rimandare. La nota si scrive comunque, anche con risultati parziali: W0–W2 completate sono già un deliverable difendibile.

---

## 6. Rischi residui → mitigazioni

| Rischio | Mitigazione |
|---|---|
| Copertura prices-history < attesa su annate vecchie | Decisione al Gate A: finestra spostata o fallback Becker |
| Rate limiting aggressivo su CLOB | Batch notturni resumable; 2 notti max preventivate |
| Sport inonda il campione | Headline ex-Sports by design; stratificazione sempre |
| endDate inaffidabili per P2 | Prevalenza misurata in A3; se >10–15%, P2 declassato a esplorativo e annotato |
| Tempo personale (trasloco, inizio ETH) | Gate = punti di uscita puliti; ordine di taglio §5; freeze 23/8 |
| Risultato "nullo" | Non è un rischio: ogni ramo dell'albero degli esiti è una conclusione pubblicabile (§0) |

---

## 7. Definition of Done + materiale colloquio

**DoD checklist**
- [ ] Repo pubblico, test verdi, risultati riproducibili con un comando da cache o da API.
- [ ] README con spec pre-registrata (hash del commit antecedente al full run) e figura-titolo.
- [ ] Research note PDF 4–6 pp in `note/`.
- [ ] Numeri finali inseriti nella riga CV.

**Riga CV (placeholder da riempire)**
> *Prediction-market calibration & tradability (Polymarket 2024–26): end-to-end pipeline over ~XXk resolved markets; horizon-stratified calibration with event-clustered bootstrap inference; out-of-sample test (H1-2026) of published miscalibration net of fees, spread and carry — [finding in una riga].*

**Narrativa in 4 battute**
1. Tre paper 2025–26 sugli stessi dati si contraddicono sul favorite–longshot bias.
2. Mostro che la divergenza è un artefatto di design (ponderazione trade-level, orologio anticipativo, dipendenza ignorata) e propongo la specifica corretta.
3. Quantifico il bias residuo per categoria e orizzonte con inferenza event-clustered.
4. Verifico se sopravvive nel 2026, al netto di fee, spread e carry: **[verdetto]** — coerente con [frizioni / inefficienza / decay post-pubblicazione].
