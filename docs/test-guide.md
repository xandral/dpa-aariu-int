# Web Page Integrity Monitor — Test Guide

Guida completa per testare lo stack via Docker, con flusso di defacement
end-to-end e sample call per ogni endpoint.

Tutti i comandi usano `jq` per formattare l'output JSON. Se non installato:
`sudo apt install jq` (Debian/Ubuntu) oppure `brew install jq` (macOS).

---

## 1. Prerequisiti

- Docker + Docker Compose
- File `.env` con `OPENAI_API_KEY` valida (`cp .env.example .env`)
- Porte libere: `8000`, `5432`, `5672`, `15672`, `5555`
- `jq` installato

---

## 2. Avvio stack

```bash
cd /home/aariu/repositories/web-page-integrity-monitor

docker compose down -v          # reset completo
docker compose build
docker compose up -d
```

Attendi ~15 secondi, poi verifica:

```bash
docker compose ps
docker compose logs app          | head -5
docker compose logs celery-worker | head -5
docker compose logs celery-beat   | head -5
```

---

## 3. Health check

```bash
curl -s http://localhost:8000/health | jq .
```

```json
{ "status": "ok" }
```

---

## 4. Flusso completo con simulazione defacement

### 4.1 Avvia un web server locale di test

```bash
mkdir -p /tmp/wpim-test

cat > /tmp/wpim-test/index.html << 'HTML'
<html>
<head><title>Azienda Example S.r.l.</title></head>
<body>
  <h1>Benvenuto nel sito ufficiale</h1>
  <p>Siamo un'azienda leader nel settore delle soluzioni digitali.
     Offriamo consulenza, sviluppo software e servizi cloud.</p>
  <p>Contatti: info@example.com | Tel: +39 02 1234567</p>
  <footer>© 2024 Azienda Example S.r.l. — P.IVA 01234567890</footer>
</body>
</html>
HTML

python3 -m http.server 9999 --directory /tmp/wpim-test &
WEB_PID=$!
echo "Web server PID: $WEB_PID"
```

### 4.2 Trova l'IP del bridge Docker

```bash
DOCKER_HOST_IP=$(ip addr show docker0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
echo "Docker bridge: $DOCKER_HOST_IP"
# solitamente 172.17.0.1
```

### 4.3 Registra l'URL (POST /urls/)

```bash
RESPONSE=$(curl -s -X POST http://localhost:8000/urls/ \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"http://$DOCKER_HOST_IP:9999/index.html\", \"frequency\": 60}")

echo "$RESPONSE" | jq .

URL_ID=$(echo "$RESPONSE" | jq -r '.id')
echo "URL_ID=$URL_ID"
```

Risposta attesa — **202 Accepted**:
```json
{ "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
```

### 4.4 Leggi la baseline (GET /urls/{id}/baseline)

Attendi 2-3 secondi per l'acquisizione, poi:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/baseline" | jq .
```

```json
{
  "id": "...",
  "url_id": "...",
  "text_clean": "Benvenuto nel sito ufficiale ...",
  "created_at": "..."
}
```

### 4.6 Aspetta il primo check automatico

Celery Beat lancia `poll_and_check` ogni `SCHEDULER_INTERVAL` secondi.
Aspetta ~90 secondi, poi:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" | jq .
```

```json
{
  "status": "OK",
  "diff_percentage": 0.0,
  "similarity_score": null,
  "llm_analysis": null
}
```

`similarity_score` e `llm_analysis` sono `null`: il diff è 0%, il funnel
si è fermato al livello 1 senza chiamare OpenAI.

### 4.7 Triggerare livello 2 — cosine similarity

Per attivare il livello 2 serve un diff ambiguo (tra 5% e 50%).
Modifichiamo una parte del testo mantenendo la struttura:

```bash
cat > /tmp/wpim-test/index.html << 'HTML'
<html>
<head><title>Azienda Example S.r.l.</title></head>
<body>
  <h1>Benvenuto nel sito ufficiale</h1>
  <p>Siamo un'azienda leader nel settore delle soluzioni digitali.
     Offriamo consulenza, sviluppo software e servizi cloud.</p>
  <p>Da oggi offriamo anche formazione professionale avanzata,
     certificazioni tecniche e workshop dedicati alle aziende.
     Scopri il nostro nuovo catalogo corsi sul portale formazione.</p>
  <p>Contatti: info@example.com | Tel: +39 02 1234567</p>
</body>
</html>
HTML

echo "Pagina modificata (aggiunta sezione). Attendi il prossimo check (~60s)..."
```

Dopo il check:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" \
  | jq '{status, diff_percentage, similarity_score, llm_analysis}'
```

Risultato atteso — il diff è ~15-30% (ambiguo), il funnel scala al
livello 2. La cosine similarity sarà alta (~0.90+) perché il significato
è simile → probabilmente **OK** senza LLM:

```json
{
  "status": "OK",
  "diff_percentage": 25.3,
  "similarity_score": 0.94,
  "llm_analysis": null
}
```

`similarity_score` ora ha un valore (embedding calcolato), `llm_analysis`
è ancora `null` (cosine era sufficiente).

### 4.8 Triggerare livello 3 — classificazione LLM

Per forzare il livello 3, serve un diff ambiguo E una cosine ambigua.
Sostituiamo il contenuto con testo che ha significato diverso ma lunghezza
simile. Per forzarlo, abbassiamo la soglia cosine_ok dell'URL:

```bash
# Abbassa cosine_threshold_ok a 0.99 così anche similarità 0.95 è "ambigua"
curl -s -X PUT "http://localhost:8000/urls/$URL_ID" \
  -H "Content-Type: application/json" \
  -d '{"cosine_threshold_ok": 0.99, "cosine_threshold_alert": 0.40}' | jq .
```

Poi modifica la pagina con contenuto diverso ma non un defacement ovvio:

```bash
cat > /tmp/wpim-test/index.html << 'HTML'
<html>
<head><title>Azienda Example S.r.l.</title></head>
<body>
  <h1>Benvenuto nel sito ufficiale</h1>
  <p>Siamo un'azienda specializzata in sicurezza informatica e
     protezione dei dati aziendali. Offriamo audit, penetration
     testing e incident response per organizzazioni enterprise.</p>
  <p>Contatti: security@example.com | Tel: +39 02 7654321</p>
</body>
</html>
HTML

echo "Pagina modificata (contenuto diverso). Attendi il prossimo check (~60s)..."
```

Dopo il check:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" \
  | jq '{status, diff_percentage, similarity_score, llm_analysis}'
```

Risultato atteso — diff ambiguo, cosine ambigua (sotto 0.99), LLM
chiamato:

```json
{
  "status": "CHANGED",
  "diff_percentage": 35.1,
  "similarity_score": 0.82,
  "llm_analysis": {
    "verdict": "OK",
    "reason": "The content appears to be a legitimate business update..."
  }
}
```

`llm_analysis` ora ha un valore — il modello ha analizzato i chunk
modificati. Lo status è **CHANGED** (non OK): arrivare al livello 3
implica un cambiamento significativo, ma l'LLM conferma che è legittimo.

Ripristina le soglie originali:

```bash
curl -s -X PUT "http://localhost:8000/urls/$URL_ID" \
  -H "Content-Type: application/json" \
  -d '{"cosine_threshold_ok": 0.95, "cosine_threshold_alert": 0.50}' | jq .
```

### 4.9 Simula defacement totale (livello 1 → ALERT)

```bash
cat > /tmp/wpim-test/index.html << 'HTML'
<html>
<head><title>HACKED</title></head>
<body>
  <h1>DEFACED BY CYBER CREW 2024</h1>
  <marquee>Your security is a joke. We own this server.</marquee>
  <p>All your data belongs to us. Contact darknet@onion for negotiations.</p>
  <img src="skull.gif" alt="skull">
</body>
</html>
HTML

echo "Pagina defaced. Attendi il prossimo check (~60s)..."
```

### 4.10 Verifica rilevamento ALERT

Dopo il prossimo ciclo di check:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" | jq .
```

```json
{
  "status": "ALERT",
  "diff_percentage": 97.2,
  "similarity_score": null,
  "llm_analysis": null
}
```

Il diff è ~97%: il funnel decide **ALERT** al livello 1, senza bisogno
di embedding o LLM. In caso di diff ambiguo (5%-50%), il sistema
escalerebbe automaticamente ai livelli 2 e 3.

### 4.11 Dashboard — stato attuale

```bash
curl -s http://localhost:8000/dashboard/ | jq .
```

```json
{
  "total_urls": 1,
  "ok": 0,
  "changed": 0,
  "alert": 1,
  "error": 0,
  "no_check_yet": 0
}
```

### 4.12 Ripristina la pagina e verifica recovery

```bash
cat > /tmp/wpim-test/index.html << 'HTML'
<html>
<head><title>Azienda Example S.r.l.</title></head>
<body>
  <h1>Benvenuto nel sito ufficiale</h1>
  <p>Siamo un'azienda leader nel settore delle soluzioni digitali.
     Offriamo consulenza, sviluppo software e servizi cloud.</p>
  <p>Contatti: info@example.com | Tel: +39 02 1234567</p>
  <footer>© 2024 Azienda Example S.r.l. — P.IVA 01234567890</footer>
</body>
</html>
HTML

echo "Pagina ripristinata. Attendi il prossimo check (~60s)..."
```

Dopo il check:

```bash
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" | jq .
```

```json
{ "status": "OK", "diff_percentage": 0.0 }
```

Il sistema torna a **OK** automaticamente.

---

## 5. Reference — sample call per ogni endpoint

### URLs

```bash
# Registra URL (202 Accepted, baseline async)
curl -s -X POST http://localhost:8000/urls/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "frequency": 300}' | jq .

# Lista URL (paginata)
curl -s "http://localhost:8000/urls/?skip=0&limit=10" | jq .

# Dettaglio URL
curl -s "http://localhost:8000/urls/$URL_ID" | jq .

# Aggiorna frequency e soglie
curl -s -X PUT "http://localhost:8000/urls/$URL_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "frequency": 120,
    "status": "active",
    "diff_threshold_ok": 3.0,
    "diff_threshold_alert": 60.0,
    "cosine_threshold_ok": 0.97,
    "cosine_threshold_alert": 0.85
  }' | jq .

# Elimina URL + tutti gli snapshot (cascade)
curl -s -X DELETE "http://localhost:8000/urls/$URL_ID" -w "\nHTTP %{http_code}\n"
```

### Baselines

```bash
# Baseline corrente (404 se non ancora acquisita)
curl -s "http://localhost:8000/urls/$URL_ID/baseline" | jq .

# Refresh baseline (non distruttivo — crea nuova riga, sposta puntatore)
curl -s -X POST "http://localhost:8000/urls/$URL_ID/baseline/refresh" | jq .
```

### Checks

```bash
# Storia check (paginata)
curl -s "http://localhost:8000/urls/$URL_ID/checks?skip=0&limit=5" | jq .

# Solo l'ultimo check
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" | jq .

# Filtra solo i campi chiave dell'ultimo check
curl -s "http://localhost:8000/urls/$URL_ID/checks/latest" \
  | jq '{status, diff_percentage, similarity_score, llm_analysis}'
```

### Dashboard


 Distribuzione eventi in un periodo con breakdown per URL
```
curl -s "http://localhost:8000/dashboard/history?from_dt=2024-01-01T00:00:00&to_dt=2026-12-31T23:59:59" | jq .

# Filtra per URL specifici (comma-separated UUIDs)
curl -s "http://localhost:8000/dashboard/history?from_dt=2024-01-01T00:00:00&to_dt=2026-12-31T23:59:59&url_ids=$URL_ID" | jq .

# Solo i totali globali
curl -s "http://localhost:8000/dashboard/history?from_dt=2024-01-01T00:00:00&to_dt=2026-12-31T23:59:59" \
  | jq '{total_checks, ok, alert}'

# Breakdown per URL — quali URL sono più instabili
curl -s "http://localhost:8000/dashboard/history?from_dt=2024-01-01T00:00:00&to_dt=2026-12-31T23:59:59" \
  | jq '.urls[] | {url, total_checks, alert}'
```

---

## 6. Unit e integration tests (senza Docker)

I test usano SQLite in-memory — non servono PostgreSQL, RabbitMQ né OpenAI:

```bash
uv sync --extra dev

uv run pytest -v                              # tutti i test
uv run pytest tests/unit/test_analyzer.py -v  # singolo file
uv run ruff check .                           # lint
uv run ruff format .                          # format
uv run bandit -r app/ -c pyproject.toml       # security
```

---

## 7. Monitoraggio task con Flower

```bash
open http://localhost:5555
```

Task ID hanno prefissi per correlazione rapida:
- `baseline:<url_id>` — acquisizione baseline
- `check:<url_id>:<hex>` — check periodico

Per debugging via CLI:

```bash
# Lista task attivi
docker compose exec celery-worker celery -A app.celery_app inspect active | head -20

# Stato di un task specifico
docker compose exec celery-worker celery -A app.celery_app result "baseline:$URL_ID"
```

---

## 8. Pulizia

```bash
docker compose down -v     # stop + rimuovi volumi (reset DB)

kill $WEB_PID 2>/dev/null  # ferma il web server di test
rm -rf /tmp/wpim-test
```

---

