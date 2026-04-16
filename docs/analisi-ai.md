# Strategia di analisi AI — Dettaglio tecnico

## Obiettivo

Dato un URL monitorato, confrontare il contenuto corrente con la baseline
salvata e decidere se la pagina è intatta (`OK`), modificata legittimamente
(`CHANGED`) o potenzialmente compromessa (`ALERT`).

Il sistema deve minimizzare i costi API (embedding + LLM) risolvendo il
prima possibile, senza sacrificare la qualità della classificazione.

---

## Input dell'analisi

L'analyzer lavora sempre su **`text_clean`** — il testo visibile estratto
dalla pagina senza tag HTML, script, style, navigazione. Viene prodotto dal
modulo `fetcher` tramite trafilatura (estrazione main content) con fallback
BeautifulSoup (rimozione tag rumore).

La scelta di analizzare testo pulito e non HTML grezzo è intenzionale:
- Riduce drasticamente la dimensione dell'input
- Rende il diff e gli embedding più significativi (confrontano contenuto
  semantico, non markup)

---

## Architettura a funnel — 3 livelli

Il pipeline è un funnel fail-fast: ogni livello è più costoso del
precedente e viene invocato solo se il livello prima non ha raggiunto una
decisione.

```
   testo baseline    testo check
        │                 │
        └────────┬────────┘
                 ▼
     ┌───────────────────────┐
     │  Livello 1 — diff     │  costo: zero
     │  SequenceMatcher      │  latenza: < 1ms
     └───────┬───────────────┘
             │ ambiguo (5% < diff < 50%)
             ▼
     ┌───────────────────────┐
     │  Livello 2 — embedding│  costo: ~$0.0001 per check
     │  cosine similarity    │  latenza: ~200ms (1 API call)
     └───────┬───────────────┘
             │ ambiguo (0.50 < cosine < 0.95)
             ▼
     ┌───────────────────────┐
     │  Livello 3 — LLM      │  costo: ~$0.001-0.01 per check
     │  gpt-4o-mini          │  latenza: 1-5s (N API calls)
     └───────────────────────┘
```

---

## Livello 1 — Diff testuale

### Algoritmo

`difflib.SequenceMatcher` implementa l'algoritmo di Ratcliff/Obershelp:
trova ricorsivamente le longest common subsequences tra due stringhe e
calcola un ratio di similarità.

```python
ratio = SequenceMatcher(None, baseline_text, check_text).ratio()
diff_percentage = (1.0 - ratio) * 100
```

- `ratio = 1.0` → testi identici → diff = 0%
- `ratio = 0.0` → nulla in comune → diff = 100%

Il primo argomento `None` indica di non usare euristica junk (nessun
carattere viene ignorato nel confronto, es. per blank).

### Soglie (configurabili per URL)

| Condizione | Decisione | Motivazione |
|---|---|---|
| `diff <= diff_threshold_ok` (default 5%) | **OK** | Variazioni minime: timestamp, contatore visite, cookie banner. Non serve analisi ulteriore |
| `diff >= diff_threshold_alert` (default 50%) | **ALERT** | Oltre metà della pagina è cambiata. Un aggiornamento editoriale raramente supera il 30% — un defacement totale è tipicamente >80% |
| 5% < diff < 50% | **ambiguo** | Zona grigia: potrebbe essere un aggiornamento consistente o un defacement parziale. Serve analisi semantica |


### Riutilizzo degli opcodes

`SequenceMatcher` viene usato due volte:
1. Al livello 1 per calcolare il `ratio()` (decisione immediata)
2. Al livello 3 per estrarre gli `opcodes` (localizzazione delle
   modifiche per il chunking)



---

## Livello 2 — Embedding + Cosine Similarity

### Come funziona un embedding

Un embedding è una rappresentazione numerica del significato di un testo.
Il modello `text-embedding-3-small` di OpenAI trasforma una stringa in un
vettore di 1536 numeri a virgola mobile. Testi con significato simile
producono vettori che puntano nella stessa direzione nello spazio
1536-dimensionale.

### Cosine similarity

La cosine similarity misura l'angolo tra due vettori, ignorando la
magnitudine:

```
cos(θ) = (a · b) / (||a|| × ||b||)
```

- `1.0` → stessa direzione → significato identico
- `0.0` → ortogonali → nessuna relazione semantica
- `-1.0` → direzioni opposte (raro con embedding di testo)

L'implementazione usa numpy per efficienza:

```python
a = np.array(vec1, dtype=np.float64)
b = np.array(vec2, dtype=np.float64)
similarity = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
```

### Perché serve il livello 2

Il diff testuale è cieco alla semantica. Due frasi possono essere molto
diverse come testo ma dire la stessa cosa:

```
baseline: "L'azienda è stata fondata nel 2005 a Milano"
check:    "Fondata a Milano nel 2005, l'azienda..."
```

Diff testuale: ~60% (sembra un ALERT). Cosine similarity: ~0.98 (stessa
frase riscritta → OK).

Caso opposto — il testo è simile ma il significato è cambiato:

```
baseline: "Contatti: info@azienda.it"
check:    "Contatti: hacker@darknet.onion"
```

Diff testuale: ~30% (ambiguo). Cosine similarity: ~0.6 (significato
diverso → più vicino ad ALERT).

### Soglie

| Condizione | Decisione |
|---|---|
| `similarity >= cosine_threshold_ok` (default 0.95) | **OK** — semanticamente identico |
| `similarity <= cosine_threshold_alert` (default 0.50) | **ALERT** — contenuto radicalmente diverso |
| 0.50 < similarity < 0.95 | **ambiguo** → livello 3 |

### Gestione embedding

- L'embedding della **baseline** viene calcolato una volta sola durante
  l'acquisizione e salvato nel DB come JSON array
- L'embedding del **check** viene calcolato al livello 2 e salvato
  anch'esso nel DB (`check_embedding` in `AnalysisResult`) per analisi
  storiche — nessun costo extra, è già stato calcolato
- Se la baseline non ha embedding (caso legacy), il livello 2 viene
  saltato e si passa direttamente al livello 3

### Perché non pgvector

Gli embedding sono salvati come `JSONB` in PostgreSQL, non come tipo
`vector` di pgvector. Il motivo: il sistema fa solo confronti 1:1
(baseline vs check corrente). Non serve similarity search su N vettori,
che è il caso d'uso di pgvector. JSONB è sufficiente e non aggiunge
un'estensione al database.

---

## Livello 3 — Classificazione LLM

Raggiunto solo quando né il diff né la cosine similarity sono conclusivi.
Casi tipici: defacement parziale (una sezione sostituita), inserimento di
link malevoli in un testo altrimenti intatto, cambio sottile di contenuti
critici (coordinate bancarie, email di contatto).

### Il problema del testo intero

Mandare l'intero `text_clean` della baseline e del check al modello LLM
sarebbe:
- **Costoso**: un testo di 20K caratteri × 2 (baseline + check) = ~10K
  token, al costo di ~$0.01 per check
- **Rumoroso**: il 90% del testo è identico. L'LLM deve trovare l'ago nel
  pagliaio
- **Lento**: più token = più latenza

La soluzione: mandare solo le **regioni effettivamente modificate** con
un margine di contesto.

### Diff-guided chunking

Il chunking è guidato dagli opcodes di `SequenceMatcher` — le stesse
strutture dati usate al livello 1 per calcolare il ratio.

#### Passo 1 — Localizzazione delle modifiche

```python
opcodes = SequenceMatcher(None, baseline, check).get_opcodes()
```

Restituisce una lista di operazioni:

```python
[('equal',   0,    500,  0,    500),   # primi 500 char identici
 ('replace', 500,  600,  500,  650),   # modifica: 100 char → 150 char
 ('equal',   600,  1800, 650,  1850),  # 1200 char identici
 ('insert',  1800, 1800, 1850, 1900),  # 50 char inseriti nel check
 ('equal',   1800, 3000, 1900, 3100)]  # resto identico
```

Ogni opcode ha 5 elementi: `(tag, i1, i2, j1, j2)` dove:
- `tag`: tipo di operazione (`equal`, `replace`, `insert`, `delete`)
- `i1:i2`: range nella baseline
- `j1:j2`: range nel check

Vengono selezionati solo i blocchi non-equal: le zone dove qualcosa è
cambiato.

#### Passo 2 — Context padding

Ogni regione modificata viene espansa di `llm_context_chars` (default 300)
caratteri in entrambe le direzioni. L'LLM ha bisogno di contesto per
giudicare se una modifica è legittima:

```
baseline: "...articolo precedente. [MODIFICA] Prossimo articolo..."
                                 ←300→           ←300→
```

Senza contesto, l'LLM vede solo la modifica isolata e non può giudicare.
Con il padding vede la frase prima e dopo, e capisce se ha senso.

#### Passo 3 — Merge di blocchi vicini

Se due regioni modificate (dopo il padding) distano meno di
`llm_merge_gap_chars` (default 500) caratteri, vengono fuse in un unico
blocco. La fusione mantiene gli estremi: start del primo blocco, end del
secondo — **incluso il testo uguale in mezzo**.

```
Blocco A: [100, 400]    Blocco B: [600, 900]    gap = 200 < 500
→ Blocco fuso: [100, 900] (include il testo identico 400-600)
```

Motivazione: chunk troppo piccoli danno poco contesto all'LLM. Meglio un
chunk medio con tutto il contesto che due frammenti staccati.

#### Passo 4 — Parallel slicing

Se un blocco fuso è ancora troppo grande (> `chunk_size_chars`, default
6000 per gpt-4o-mini), viene suddiviso con una sliding window.

**Parametri:**
- `half = max_chars // 2` = 3000 — budget per lato (baseline + check)
- `overlap = llm_chunk_overlap_chars` (default 200) — sovrapposizione
  tra finestre consecutive
- `step = half - overlap` = 2800 — avanzamento della finestra

**Il meccanismo:**

```python
offset = 0
while offset < max_len:
    b_win = b_sec[offset : offset + half]   # finestra sulla baseline
    c_win = c_sec[offset : offset + half]   # stessa finestra sul check
    chunks.append((b_win, c_win))
    offset += step
```

Lo **stesso offset** viene applicato a entrambi i lati. Questo è il punto
critico del design.

```
baseline:  |AAAA|BBBB|CCCC|DDDD|EEEE|
check:     |AAAA|XXXX|YYYY|DDDD|EEEE|

offset=0:     baseline[0:3000]    check[0:3000]      ← stessa zona
offset=2800:  baseline[2800:5800] check[2800:5800]   ← stessa zona
offset=5600:  baseline[5600:8600] check[5600:8600]   ← stessa zona
```

L'LLM confronta sempre la stessa posizione logica nel documento.

### Perché non RecursiveCharacterTextSplitter

L'alternativa classica (usata da LangChain e framework RAG) è splittare
ogni testo indipendentemente, cercando boundary "naturali" come `\n\n`,
`\n`, spazi. Produce chunk più leggibili, ma per un confronto 1:1 tra
due versioni ha un problema fondamentale: **l'allineamento si perde**.

Esempio con un insert di 500 caratteri a metà testo:

```
baseline chunk 3: "...fine paragrafo 4. Inizio paragrafo 5..."
check chunk 3:    "...fine paragrafo 3. Testo inserito che..."
```

Il recursive splitter taglia in punti diversi perché il check è più
lungo. L'LLM confronta il paragrafo 5 della baseline con il paragrafo 3
del check → falso positivo.

Con il parallel slicing, entrambi i chunk 3 partono dallo stesso offset
nel blocco modificato → confronto posizionalmente corretto.

Il trade-off: il parallel slicing può tagliare a metà parola. Ma l'LLM
gestisce bene i frammenti — è un problema estetico, non funzionale.

### Overlap tra finestre

L'overlap di 200 caratteri serve a coprire il caso in cui una modifica
cada esattamente al confine tra due finestre. Senza overlap:

```
finestra 1: baseline[0:3000]     check[0:3000]
finestra 2: baseline[3000:6000]  check[3000:6000]
                     ↑ se la modifica è qui, viene tagliata a metà
```

Con overlap di 200:

```
finestra 1: baseline[0:3000]     check[0:3000]
finestra 2: baseline[2800:5800]  check[2800:5800]
                     ↑ la modifica è coperta da entrambe le finestre
```

### Classificazione e fail-fast

Ogni coppia `(baseline_chunk, check_chunk)` viene inviata a `gpt-4o-mini`
con un prompt che richiede:
- `verdict`: `"OK"` (aggiornamento legittimo) o `"ALERT"` (sospetto
  defacement)
- `reason`: spiegazione in una frase

Il modello è configurato con `temperature=0` (output deterministico) e
`response_format={"type": "json_object"}` (output strutturato garantito).

**Fail-fast**: al primo chunk classificato ALERT, l'analisi si interrompe
immediatamente. Non serve controllare gli altri — un solo chunk sospetto
è sufficiente per segnalare l'anomalia. Questo riduce ulteriormente il
numero di chiamate API.

### Stato finale del livello 3

Se il livello 3 viene raggiunto, lo status non è mai `OK`:

| Verdetto LLM | Status finale | Motivazione |
|---|---|---|
| Tutti i chunk OK | **CHANGED** | Il contenuto è cambiato (altrimenti non saremmo al livello 3) ma l'LLM conferma che è legittimo |
| Almeno un chunk ALERT | **ALERT** | Defacement sospetto |

La distinzione `CHANGED` vs `OK` è voluta: arrivare al livello 3 implica
un cambiamento significativo (diff 5-50%, cosine 0.50-0.95). Anche se
l'LLM lo giudica legittimo, viene segnalato come `CHANGED` per
trasparenza — l'utente può investigare.
 


---

## Configurabilità

Tutte le soglie e i parametri sono configurabili:

**Per URL** (via `PUT /urls/{id}`):
- `diff_threshold_ok`, `diff_threshold_alert`
- `cosine_threshold_ok`, `cosine_threshold_alert`

**Globali** (via environment / `app/config.py`):
- `llm_chunk_max_chars` — budget massimo per chunk (default 6000)
- `llm_context_chars` — padding di contesto (default 300)
- `llm_chunk_overlap_chars` — overlap sliding window (default 200)
- `llm_merge_gap_chars` — distanza massima per merge (default 500)

**Per modello** (registry `LLM_MODELS` in `app/config.py`):
- `chunk_size_chars` — override del budget per modello specifico (gpt-4o-mini:
  6000, gpt-4-turbo: 4000, claude-3-haiku: 5000). I modelli con context
  window più piccola hanno chunk più corti per stare nel limite di token.
