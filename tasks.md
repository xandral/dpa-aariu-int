### Per il momento non imlementare test automatici l'allineamento dei test arrivera' successivamente


# 1
## Problema

`GET /dashboard` mescola due semantiche in un unico endpoint — stato corrente vs storia degli eventi — scelte implicitamente dalla presenza di parametri opzionali.

## Soluzione

Separare in due endpoint con semantiche esplicite.

---

## `GET /dashboard`

Stato corrente: _"come stanno i miei URL adesso?"_

- Un voto per URL, basato sull'ultimo check snapshot
- Calcola `no_check_yet` per URL senza alcun check

```json
{
  "total_urls": 20,
  "ok": 10,
  "changed": 3,
  "alert": 2,
  "error": 1,
  "no_check_yet": 4
}
```

---

## `GET /dashboard/history`

Distribuzione eventi: _"quanti problemi sono successi in questo periodo?"_

- `from_dt` e `to_dt` entrambi obbligatori (ISO 8601)
- Conta ogni evento di check nella finestra, non gli URL
- Nessun campo `no_check_yet`

```json
{
  "from_dt": "2024-01-01T00:00:00Z",
  "to_dt":   "2024-01-31T23:59:59Z",
  "total_checks": 138,
  "ok": 120,
  "changed": 10,
  "alert": 5,
  "error": 3
}
```

---

## Cambiamenti

- Dividere `get_dashboard` in `get_dashboard_current` e `get_dashboard_history`
- Due schema Pydantic separati: `DashboardCurrentResponse`, `DashboardHistoryResponse`
- Sostituire il dict `counts` con `{status: 0 for status in CheckStatus}`
- `get_dashboard_history` ritorna `422` se manca uno dei due parametri temporali

## Out of scope

Nuove metriche, filtri per URL, modifiche ai modelli.


# 2
per la parte llm vorrei che venisse fatto un chunking anche per l'embedding tramite un file di configurazione interno per adesso in cui hai max lenght o tenks vedi te , per llm idem, modello di embedding max tokens ecc. ma se usi per la parte embedding e per la parte llm lo stesso modellodi embedding (cioe' usi quellouato dal modello llm) allora non fai il chunk due volte. Inoltre ho un dubbio. Non teniamo conto dei tag in questa fase masolo del teso libero ma se si iniettano tgag malevoli e' un problema. Inoltre non capiscola parte di chunking vedo che spezzi in pezzi piccoli e poi ricomponi, none' ridondante? Inoltre non potremmo seprare in base ai tag html il testo senza usare il testo pulito per il confronto?

# 3 filtro direttamente nella uqery?
def poll_and_check(enqueue_check: Callable[[str], None]) -> None:
    """Find all active URLs whose check interval has elapsed and enqueue checks."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        rows = db.execute(
            select(Url.id, Url.last_checked_at, Url.frequency)
            .where(Url.status == UrlStatus.active)
        ).all()

        due_ids = [
            row.id for row in rows
            if row.last_checked_at is None
            or now >= row.last_checked_at + timedelta(seconds=row.frequency)
        ]

        if not due_ids:
            logger.debug("No URLs due for checking")
            return

        logger.info("Enqueuing checks for %d URL(s)", len(due_ids))

        for url_id in due_ids:
            enqueue_check(str(url_id))

        db.execute(update(Url).where(Url.id.in_(due_ids)).values(last_checked_at=now))
        db.commit()

# 4 come mai usi i task celery usi quelel funzioni annidate? come hai solo 1 ha strategi adi retry

# 6 ma il task che vedo su celery coem si lega al nostro? per facilitare debug e' l'id dello snapshot?

# 7 per l apulizia dei tag funziona anche per pagine dinamiche? riusciamo a fare qualcosa di semplice anche epr quelle se torppo complicato lascia stare

# 8 step semplice per mettere i file di testo su filesystem (in prod su obj storage) compresso (parquet?) e tenere sulle tabelle solo i puntatori ai file e quindia ogni step leggerli