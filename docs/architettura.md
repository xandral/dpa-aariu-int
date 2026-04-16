# Architettura — Web Page Integrity Monitor

## Scopo del sistema

Monitorare un elenco di pagine web per rilevare modifiche sospette (potenziale
defacement). Per ogni URL registrato il sistema acquisisce una **baseline**,
esegue **check periodici** e confronta il contenuto con un pipeline di analisi
ibrida (diff testuale → similarità semantica → classificazione LLM). Gli
alert sono esposti via API e propagati a un task di notifica.

## Componenti principali

| Componente      | Ruolo                                                                  | Tecnologia                  |
|-----------------|------------------------------------------------------------------------|-----------------------------|
| API layer       | CRUD URL, lettura baseline / check / dashboard                         | FastAPI (sync handlers)     |
| Scheduler       | Tick periodico che lancia `poll_and_check`                             | Celery Beat                 |
| Task worker     | Esecuzione async di acquisizione, check, notifiche                     | Celery (concurrency=4)      |
| Message broker  | Routing dei task e DLQ per i job falliti                               | RabbitMQ                    |
| Storage         | Tabelle `urls` e `snapshots` (unified baseline + check)                | PostgreSQL 16               |
| Fetcher         | Download HTTP + estrazione testo pulito                                | httpx + trafilatura + BS4   |
| Analyzer        | Pipeline a 3 livelli: diff → cosine → LLM                              | difflib + numpy + OpenAI    |
| Modulo AI       | Embedding + classificazione semantica                                  | OpenAI API (text-embedding-3-small, gpt-4o-mini) |
| Monitoring      | UI per ispezionare task in corso, falliti, history                     | Flower                      |

![Architettura](images/architecture.png)

## Interazione tra i componenti

1. **Registrazione URL** — il client chiama `POST /urls/`. L'API persiste la
   riga in Postgres e pubblica un task `wpim.acquire_baseline` su RabbitMQ con
   `task_id = baseline:{url_id}`. La risposta è `202 Accepted` con il solo
   `id` — l'acquisizione avviene in background.
2. **Acquisizione baseline** — il worker consuma il task, chiama
   `fetch_and_clean`, calcola l'embedding con OpenAI, scrive lo snapshot
   (`kind='baseline'`) e aggiorna `urls.current_baseline_id`. In caso di
   fallimento viene ritentato fino a 3 volte con backoff esponenziale; poi
   il messaggio finisce in `dead_letters`.
3. **Monitoraggio periodico** — Celery Beat pubblica `wpim.poll_and_check`
   ogni `scheduler_interval` secondi. Il task carica gli URL attivi, calcola
   quelli "scaduti" (in base a `frequency` e `last_checked_at`) e pubblica
   un `wpim.run_check` per ognuno, con `task_id = check:{url_id}:{hex}`.
4. **Check e analisi** — il worker esegue il funnel a 3 livelli
   (vedi `docs/diagrammi.md`). Persiste uno snapshot `kind='check'` con lo
   stato finale (`OK`, `CHANGED`, `ALERT`, `ERROR`) e, solo in caso di ALERT,
   pubblica un task `wpim.notify_alert` sulla coda dedicata.

## Scelte tecnologiche e motivazioni

### Python 3.12 + FastAPI sync
FastAPI supporta sia handler sync che async. Tutti i service
(`fetcher`, `analyzer`, `baseline`, `scheduler`) sono **puro sync** per
eliminare l'impedance mismatch con i worker Celery, che sono anch'essi
sincroni. FastAPI esegue automaticamente gli handler sync in un threadpool
— nessun `asyncio.run()` sparso nel codice.

### Celery + RabbitMQ
Un custom scheduler asyncio era possibile ma Celery offre out-of-the-box:
retry con backoff, dead letter queue, monitoring via Flower, task routing per
coda, e scaling orizzontale del worker. Celery Beat sostituisce
completamente il nostro scheduler custom.

### Unified snapshots con discriminatore `kind`
`baseline` e `check` vivono nella stessa tabella, distinti da
`kind ENUM('baseline','check')`. Questo evita la duplicazione di schema
(entrambi hanno html, text, embedding, timestamp) e semplifica le query di
storia. `urls.current_baseline_id` è un head pointer che permette refresh
non distruttivi: un refresh inserisce una nuova riga e sposta il puntatore,
la baseline precedente resta a scopo storico.

### Pipeline ibrida a 3 livelli (fail-fast)
- **Livello 1 — diff testuale**: `difflib.SequenceMatcher.ratio()`.
  Se `diff < diff_threshold_ok` → `OK`. Se `diff > diff_threshold_alert` →
  `ALERT`. Zero costo, zero latenza, risolve la maggior parte dei casi.
- **Livello 2 — embedding cosine**: solo per i casi ambigui, chiamata a
  `text-embedding-3-small`. Se la cosine è sopra/sotto le soglie, decisione
  immediata senza LLM.
- **Livello 3 — LLM classification**: solo se anche il coseno è ambiguo.
  Il testo viene **tagliato sulle sole aree modificate** (diff-guided
  chunking con parallel slicing) per ridurre i token del 60–80%.

### Diff-guided chunking con parallel slicing
`_build_llm_chunks` usa gli opcodes di `SequenceMatcher` per localizzare le
regioni modificate, aggiunge contesto, fonde blocchi vicini e applica
**parallel slicing**: lo stesso offset applicato a baseline e check
simultaneamente. Questo garantisce che l'LLM confronti sempre la stessa
posizione nelle due versioni, invece di finestre scelte in modo indipendente
(che sarebbe un bug semantico).

### PostgreSQL relazionale (no pgvector)
`pgvector` sarebbe utile per similarity search su N vettori. Qui facciamo
solo confronti 1:1 (baseline vs check), quindi l'embedding è salvato come
`JSONB` (array di float). Decisamente più semplice.

### Trafilatura + BS4 fallback per estrazione testo
Trafilatura è specializzato nell'estrarre il main da pagine web
(pulendo nav/header/footer/script). Per pagine non-article (landing, form,
docs) ricadiamo su BeautifulSoup con una lista di tag-rumore. Se il testo
estratto è troppo corto e la pagina ha molti `<script>`, emettiamo un WARNING
che suggerisce di usare un headless browser (out of scope).

### Modelli configurabili per URL
`urls.embedding_model` e `urls.llm_model` sono immutabili dopo la creazione:
cambiare modello invaliderebbe gli embedding storici rendendoli non
confrontabili. Il chunking adatta automaticamente la dimensione delle
finestre usando `LLM_MODELS` in `app/config.py`.

## Qualità e operatività

- **Test**: 58 test (30 unit + 28 integration) con SQLite in-memory
  (`StaticPool`) per massima velocità — nessuna dipendenza da Docker per
  la suite.
- **Lint / security**: `ruff` + `bandit` via `pre-commit`.
- **Retry strategy**: `acquire_baseline` è l'unico task con retry
  automatico (3 tentativi, backoff esponenziale) perché è one-shot
  user-facing. `run_check` non ha retry perché il Beat lo riprova al tick
  successivo, evitando duplicati in coda.
- **Dead Letter Queue**: i task `default` e `notifications` hanno
  `x-dead-letter-exchange` → `dead_letters`. I messaggi rigettati finiscono
  lì per debug manuale senza perdere lavoro.
- **Observability**: Flower su `:5555` per ispezionare task attivi, falliti,
  storia. I task ID sono prefissati (`baseline:{url_id}`,
  `check:{url_id}:{hex}`) per correlare rapidamente task e entità
  applicative.
