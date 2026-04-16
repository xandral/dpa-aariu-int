# Web Page Integrity Monitor

Sistema di monitoraggio per rilevare modifiche sospette (defacement) su
pagine web. Per ogni URL registrato acquisisce una baseline, esegue check
periodici e applica un pipeline di analisi ibrida **diff → embedding → LLM**
che minimizza chiamate API restituendo lo stato `OK / CHANGED / ALERT`.

## Quick start

```bash
# 1. Dipendenze (Python 3.12 + uv)
uv sync --extra dev

# 2. Configura la API key OpenAI
cp .env.example .env
# poi edita OPENAI_API_KEY=sk-...

# 3. Avvia lo stack (db, rabbitmq, app, celery-worker, celery-beat, flower)
docker compose up -d

# 4. Registra un URL
curl -X POST http://localhost:8000/urls/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "frequency": 300}'

# 5. API interattiva
open http://localhost:8000/docs
# Flower (task monitoring)
open http://localhost:5555
```

## Stack

- **Python 3.12** + **FastAPI** (sync handlers)
- **SQLAlchemy 2.0** + **PostgreSQL 16** (storage)
- **Celery 5** + **RabbitMQ** (task queue + Beat scheduler)
- **OpenAI** `text-embedding-3-small` + `gpt-4o-mini`
- **trafilatura** + **BeautifulSoup4** (HTML cleaning)
- **pytest** + **ruff** + **bandit** + **pre-commit**
- **Flower** (monitoring Celery)

## Flusso operativo

### 1. Registrazione e acquisizione baseline

```
Client ──POST /urls/──► FastAPI ──INSERT──► PostgreSQL
                           │
                           └──publish wpim.acquire_baseline──► RabbitMQ
                                                                  │
          ┌───────────────────────────────────────────────────────┘
          ▼
    Celery Worker
      ├── fetch pagina (httpx)
      ├── estrai testo pulito (trafilatura + BS4)
      ├── calcola embedding (OpenAI)
      └── salva snapshot kind='baseline' ──► PostgreSQL
```

Il client riceve `202 Accepted` immediatamente — l'acquisizione avviene in
background. Se fallisce, il task viene ritentato fino a 3 volte con backoff
esponenziale; esauriti i retry il messaggio finisce nella Dead Letter Queue
per debug manuale.

### 2. Monitoraggio periodico

Celery Beat pubblica un task `poll_and_check` a intervalli configurabili.
Il worker carica gli URL attivi, calcola quelli scaduti in base a
`frequency` e `last_checked_at`, e pubblica un `run_check` per ognuno.
Per ogni URL scaduto il worker esegue fetch, confronto con la baseline
tramite il funnel di analisi (vedi sotto), e persiste il risultato come
snapshot `kind='check'`. Se il verdetto finale è `ALERT`, viene pubblicato
un task di notifica sulla coda dedicata.

### 3. Refresh baseline

Il refresh è **manuale** (`POST /urls/{id}/baseline/refresh`) ed è **non
distruttivo**: inserisce una nuova riga `kind='baseline'` e sposta il
puntatore `current_baseline_id`. Le baseline precedenti restano nel database
per audit. In un sistema anti-defacement il refresh automatico sarebbe un
rischio: consoliderebbe una pagina compromessa come nuova verità.

---

## Estrazione contenuto web

Il modulo `fetcher` scarica la pagina con **httpx** (sync, timeout
configurabile) e applica una pipeline di pulizia a due stadi:

1. **trafilatura**: specializzato nell'estrarre il main content di articoli
   e pagine editoriali, rimuovendo automaticamente navigazione, header,
   footer, script e style. Produce testo pulito ottimale per il confronto.
2. **BeautifulSoup4 (fallback)**: per pagine non-article (landing page,
   form, documentazione) dove trafilatura restituisce poco testo, si ricade
   su BS4 con rimozione esplicita dei tag rumore (`script`, `style`, `nav`,
   `header`, `footer`, `aside`, `form`).

---



## API

| Metodo | Path                                | Descrizione                           |
|--------|-------------------------------------|---------------------------------------|
| POST   | `/urls/`                            | Registra URL, triggera baseline async |
| GET    | `/urls/`                            | Lista URL (summary)                   |
| GET    | `/urls/{id}`                        | Dettaglio URL                         |
| PUT    | `/urls/{id}`                        | Aggiorna frequency/status/soglie      |
| DELETE | `/urls/{id}`                        | Rimuove URL + snapshots (cascade)     |
| GET    | `/urls/{id}/baseline`               | Baseline corrente (404 se non pronta) |
| POST   | `/urls/{id}/baseline/refresh`       | Rigenera baseline (non distruttivo)   |
| GET    | `/urls/{id}/checks`                 | Storia check (paginata)               |
| GET    | `/urls/{id}/checks/latest`          | Ultimo check                          |
| GET    | `/dashboard/`                       | Stato corrente per URL                |
| GET    | `/dashboard/history?from_dt&to_dt&url_ids`  | Distribuzione eventi con breakdown per URL |

## Development

```bash
uv run pytest -v                              # 58 test
uv run pytest tests/unit/test_analyzer.py -v  # singolo file
uv run ruff check .                           # lint
uv run ruff format .                          # format
uv run bandit -r app/ -c pyproject.toml       # security
```

Usa **sempre** `uv run` invece di `pip` / `.venv/bin/*`.

## Struttura

```
app/
├── main.py              FastAPI lifespan
├── celery_app.py        Celery config + beat_schedule
├── config.py            pydantic Settings + model registry
├── database.py          SQLAlchemy engine/sessionmaker
├── models.py            Url, Snapshot, enum
├── schemas.py           Pydantic request/response
├── tasks.py             acquire_baseline, run_check, poll_and_check, notify_alert
├── routers/             urls, baselines, checks, dashboard
├── services/            fetcher, analyzer, baseline, scheduler
└── utils/openai_client  sync OpenAI client
docs/
├── architettura.md      Descrizione componenti e scelte tech
├── diagrammi.md         Diagrammi (architettura, sequenze, DB schema)
├── schema.sql           DDL PostgreSQL idempotente
├── flusso.md            Dettaglio flusso sync + Celery Beat
├── test-guide.md        Guida manuale end-to-end
└── images/              PNG dei diagrammi
tests/
├── unit/                30 test (analyzer, fetcher, schemas)
└── integration/         28 test (endpoint + conftest SQLite StaticPool)
```

## Documentazione

- [docs/architettura.md](docs/architettura.md) — componenti, interazioni, scelte tecnologiche
- [docs/analisi-ai.md](docs/analisi-ai.md) — dettaglio tecnico pipeline AI: diff, embedding, chunking, LLM
- [docs/diagrammi.md](docs/diagrammi.md) — diagramma architettura, sequence diagram, schema ER
- [docs/flusso.md](docs/flusso.md) — dettaglio flusso sincrono e Celery Beat
- [docs/schema.sql](docs/schema.sql) — DDL PostgreSQL idempotente
- [docs/test-guide.md](docs/test-guide.md) — guida test end-to-end con stack Docker

