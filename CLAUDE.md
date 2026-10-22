# CLAUDE.md

## Despre proiect

Aplicație personală (un singur utilizator, rulează local pe Windows) care:

1. **Extrage joburi** de pe principalele platforme de anunțuri din România, le salvează local și le ordonează după cât de bine se potrivesc cu profilul meu.
2. **Generează un CV adaptat** unui anunț anume, pornind de la experiența mea reală (din profil) și de la cerințele din anunț.

Comunică cu utilizatorul în limba română. Codul, numele de variabile, comentariile și mesajele de log și de commit sunt în engleză; textele afișate utilizatorului (UI, mesajele de eroare din UI) sunt în română.

**Repository public pe GitHub** (`origin`, branch `main`), predat la un curs; documentația pentru utilizatori e în `README.md`. Reguli:
- Commit-uri locale da; **push doar cu acordul utilizatorului**, de fiecare dată.
- **Nimic personal în git:** nici numele utilizatorului, nici date reale de profil, chei, baza de joburi sau CV-uri (`data/` și `.env` sunt în `.gitignore`). Exemplele folosesc doar persoana fictivă din `profile.example.yaml`. Înainte de un commit, verifică diff-ul pentru date personale.
- Autorul commit-urilor publicate e neutru: `Job Scraper <job-scraper@example.invalid>` (configurat local în repo). Istoricul vechi, cu autorul real, e doar în branch-ul local `istoric-local`, care nu se publică niciodată (nu folosi `git push --all`).

Stare (2026-10-22): toți cei 7 pași din „Ordinea de implementare” sunt gata: BYOK, LinkedIn prin SearchApi.io (Google Jobs e implementat, dar dezactivat: nu suportă România), scrapere directe BestJobs și Hipo, dedup între surse, UI-ul Streamlit (Joburi, Căutare, Generează CV, CV-urile mele, Profil, Chei API), profil, CV adaptat cu Claude (validare, export PDF/DOCX) și scorul de potrivire cu explicații. Peste ele: design-ul din `design.md`, preferințele din profil folosite ca filtre, descrierile BestJobs/Hipo aduse automat la căutare, importul profilului din PDF-ul LinkedIn, ștergerea profilului și CV-ul pe tip de job (QA, dezvoltare...). Apelurile la Claude (CV, import PDF) au fost testate doar cu un client fals: la primul apel real verifică răspunsul.

## Stack

- **Python 3.11+** (SDK-ul `anthropic` 1.x cere minimum 3.10)
- **UI:** Streamlit, multipage cu `st.navigation` (paginile în `app_pages/`, nu în `pages/`, ca Streamlit să nu le descopere automat a doua oară)
- **HTTP:** `httpx`; **parsare HTML:** `beautifulsoup4` + `lxml`; **robots.txt:** `protego` (înțelege `*` și `$`; `urllib.robotparser` nu le înțelege)
- **Playwright (Chromium):** doar pentru surse care randează conținutul din JavaScript, și pentru exportul CV-ului în PDF
- **Stocare:** SQLite (`sqlite3` din stdlib), fișierul `data/jobs.db`
- **Modele de date:** Pydantic v2
- **LLM:** SDK-ul oficial `anthropic` (Claude API)
- **Search API:** SearchApi.io, motoarele [`google_jobs`](https://www.searchapi.io/docs/google-jobs), [`linkedin_jobs`](https://www.searchapi.io/docs/linkedin-jobs-api) și [`linkedin_job_details`](https://www.searchapi.io/docs/linkedin-job-details-api), apelate cu `httpx`
- **Export CV:** șablon Jinja2 → HTML → PDF (Playwright `page.pdf()`); DOCX cu `python-docx`
- **Teste:** pytest; HTTP-ul se simulează cu `httpx.MockTransport`
- **Configurare:** `config.yaml` (surse, filtre, model LLM) + `.env` (`ANTHROPIC_API_KEY`, `SEARCHAPI_API_KEY`)

## Comenzi

Pe acest calculator `python` nu e în PATH; Python 3.13 se apelează prin launcher-ul `py`. După crearea venv-ului folosește direct `.venv\Scripts\python.exe` (nu depinde de execution policy-ul PowerShell pentru `Activate.ps1`).

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium   # necesar pentru exportul PDF (instalat pe acest calculator)

.venv\Scripts\python.exe -m pytest                         # toate testele
.venv\Scripts\python.exe -m pytest tests/test_db.py        # un singur fișier
.venv\Scripts\python.exe -m jobscraper.cli status          # creează DB-ul dacă lipsește și afișează un rezumat
.venv\Scripts\python.exe -m jobscraper.cli demo            # date de probă offline: joburi din tests/fixtures, profilul de exemplu, 2 CV-uri
.venv\Scripts\python.exe -m jobscraper.cli scrape --source bestjobs --query "python" --city Cluj-Napoca --pages 1 --no-details
.venv\Scripts\python.exe -m streamlit run app.py           # UI-ul
```

`scrape --source google_jobs|linkedin` și butonul „Încarcă detaliile de pe LinkedIn” consumă credite reale din contul SearchApi.io (1 per pagină de rezultate, 1 per job la detalii): nu le rula fără acordul utilizatorului. Butoanele „Generează CV” și „Completează din PDF” consumă tokeni Anthropic: la fel. BestJobs și Hipo nu costă nimic, dar sunt site-uri reale: rulările live doar rar și mici (o căutare, câteva detalii; `--no-details` ca să nu se încarce automat până la `max_auto_details` anunțuri). Testele nu fac niciun request real.

`JOBSCRAPER_CONFIG=<cale>` alege alt fișier de config (de exemplu unul cu propria bază de date). Folosește-l pentru verificări manuale în UI cu date demo, ca să nu atingi `data/jobs.db`.

## Surse de joburi (România)

Două tipuri de surse, complementare, cu dedup între ele:

1. **Prin SearchApi.io** (`jobscraper/sources/searchapi.py` are `SearchApiClient` comun). Cheia se trimite în header-ul `Authorization: Bearer`, niciodată în URL. Fiecare request costă un credit.
   - **`google_jobs`** — **dezactivat: Google Jobs nu suportă România.** SearchApi.io răspunde 400 „Unsupported value `ro` in gl parameter”; țările suportate sunt în https://www.searchapi.io/docs/parameters/google-jobs/gl. Codul rămâne pentru țările suportate: Google Jobs adună anunțuri de pe mai multe site-uri, site-ul original ajunge în `via`; orașul și „remote” se adaugă în `q`; paginare cu `next_page_token`.
   - **`linkedin`** (`sources/linkedin.py`) — motorul `linkedin_jobs`: orașul merge în `location` („Cluj, Romania”), paginare cu `page` cât timp există `pagination.next`, filtru opțional `searchapi.linkedin_time_period`. Lista **nu are descriere**. Descrierea, salariul și nivelul vin din `linkedin_job_details`, doar la cerere (vezi „Detalii la cerere” mai jos).
2. **Scrapere directe** — activate în `config.yaml` pe măsură ce sunt implementate. Accesul verificat pe 2026-10-19 cu User-Agent-ul nostru:

| Sursă | Domeniu | Stare |
|---|---|---|
| BestJobs | bestjobs.eu | **implementat** (`sources/bestjobs.py`). robots.txt permite tot. Rezultate din endpoint-ul JSON folosit de scroll-ul site-ului (`/api/proxy/v2/jobs?limit=24&keyword=…&cursor=…`); `keyword` e text liber, BestJobs îl descompune singur în cuvânt cheie + oraș. Salariile din listă sunt în €/lună; `estimatedSalary` e doar estimarea BestJobs (intră în `salary_text`, nu în `salary_min/max`). Lista nu are dată de publicare și nici descriere; descrierea vine din `__NEXT_DATA__` al paginii `/loc-de-munca/<slug>`, la cerere. |
| Hipo | hipo.ro | **implementat** (`sources/hipo.py`), HTML randat pe server. robots.txt interzice doar `/public/prvfiles/`, `/objects/`, `/angajator/`. Căutare: `GET /locuri-de-munca/cautajob/Toate-Domeniile/<Oraș>/<cuvinte-cheie>[/<pagina>]`, aceleași URL-uri pe care le leagă site-ul (formularul lor face POST cu nonce; nu-l folosim). Orașul are forma Hipo (`BUCURESTI`, `Cluj-Napoca`, `Iasi`); pagina următoare există doar dacă pagina curentă o leagă. Cardurile (`div.job-item`) au titlu, firmă, dată (zz-ll-aaaa) și „locații” (`i.fa-map-marker-alt`), care pot fi și `Hybrid`/`Remote`. Detalii din `/locuri_de_munca/<id>/…`: JSON-LD `JobPosting` (dată, firmă, salariu din `baseSalary`; textul lui e dublu-codat chiar de Hipo, deci nu se folosește), tabelul `.companie-info` (Job type/Tip job, Job level/Nivel job, Location/Orase, Remote work) și anunțul: în `#anunt-content` sau, în formatul mai nou (cam 60% din anunțuri, octombrie 2026), în secțiuni cu titlu (descrierea firmei, Cerinte, Responsabilitati, Salariu) din `.content-block-content > .content`. Câmpul `Remote work` are prioritate față de titlu. |
| eJobs | ejobs.ro | **blocat**: căutarea și paginile de job dau 403 Cloudflare. Accesibile doar `rss-listings.xml` și `sitemap-listings-<categorie>.xml` (titlu din URL, link, dată; fără firmă, oraș sau descriere). Deocamdată eJobs nu e acoperit de nicio sursă. |
| OLX Locuri de muncă | olx.ro | **blocat**: 403 chiar și la robots.txt. |

**Un 403 / Cloudflare / CAPTCHA înseamnă „nu”.** Nu încerca alt User-Agent, browser headless, cookie-uri copiate sau proxy-uri ca să treci de el; spune-i utilizatorului și propune alternative (altă sursă, un motor SearchApi.io, sitemap/RSS dacă robots.txt le oferă).

**Fără scraper direct:** LinkedIn (vine doar prin SearchApi.io) și Indeed. Termenii lor interzic scraping-ul și au protecție anti-bot puternică. Nu le adăuga fără acordul explicit al utilizatorului.

Înainte de a scrie un scraper nou, verifică în ordinea asta:
1. `robots.txt` al site-ului.
2. Dacă pagina conține JSON-LD `application/ld+json` de tip `JobPosting` (schema.org). Multe site-uri de joburi îl includ și e mult mai stabil decât selectorii CSS.
3. Dacă site-ul încarcă datele dintr-un endpoint JSON intern sau le include în pagină (ex. `__NEXT_DATA__`).
4. Abia apoi selectori CSS pe HTML.

## Reguli de scraping (obligatorii)

Toate sunt implementate în `sources/http.py` (`PoliteClient`); scraperele nu fac request-uri pe lângă el.

- Respectă `robots.txt` (`protego`, parsat o dată per client; inclusiv `Crawl-delay`). Fără robots.txt (404) = totul permis; robots.txt blocat (401/403) = site blocat.
- **Rate limit:** minimum `scraping.min_delay_seconds` (≥ 2 s, implicit 2,5 s) între request-uri pe același domeniu, comun pentru tot procesul (`DomainRateLimiter`), fără request-uri paralele pe același domeniu.
- User-Agent fix și descriptiv. Fără rotație de User-Agent sau proxy-uri.
- **Nu ocoli CAPTCHA, login, paywall sau 401/403.** `BlockedError` oprește sursa, iar eroarea apare în UI.
- Cache pe disc (`data/cache/`, TTL `scraping.cache_ttl_hours`) pentru paginile de detaliu și robots.txt; paginile de rezultate nu se cache-uiesc (`use_cache=False`), ca o căutare nouă să aducă anunțuri noi.
- Retry cu backoff exponențial doar la 429, 5xx și erori de rețea; respectă header-ul `Retry-After` (maxim 60 s).
- Doar uz personal: anunțurile nu se republică nicăieri.

## Arhitectură

```
.
├── app.py                     # entry point Streamlit: tema (theme.apply), logo-ul (st.logo) + st.navigation
├── design.md                  # sistemul de design „Linear / Modern” (sursa pentru tema de mai jos)
├── .streamlit/config.toml     # culori, fonturi, raze: ce poate exprima tema Streamlit
├── app_pages/
│   ├── jobs.py                # Joburi (implicit): contoare, filtre în bara laterală, tabel, panou de detalii
│   ├── search.py              # Căutare: formular (completat din preferințe), progres descrieri, ultimele rulări
│   ├── profile.py             # Profil: import PDF LinkedIn, tab-uri pe secțiuni, editor YAML, ștergere
│   ├── cv.py                  # CV: pentru un anunț (cu anunțul afișat) sau un tip de job → versiune → Editează / Previzualizare / Export
│   ├── cvs.py                 # CV-urile mele: toate CV-urile generate, filtre (tip de job, anunț/tip, limbă, text), Deschide / Șterge
│   └── keys.py                # Chei API (BYOK)
├── jobscraper/
│   ├── config.py              # încarcă config.yaml + .env
│   ├── keys.py                # BYOK: KeyStore (sesiune → .env / mediu), mask()
│   ├── models.py              # JobPosting, SearchQuery, Profile, TailoredCV (Pydantic)
│   ├── profile.py             # data/profile.yaml (load/save, YAML), PDF-ul LinkedIn de lângă el, delete_profile()
│   ├── profile_form.py        # Profile ↔ starea widget-urilor paginii Profil, erori în română (pur)
│   ├── linkedin_import.py     # PDF LinkedIn → ImportedProfile (Claude) → merge_into_profile() → formular
│   ├── llm.py                 # make_client(keys), parse(): un apel Claude cu output Pydantic, erori → LLMError (RO)
│   ├── theme.py               # CSS-ul temei (sticlă, lumini, glow, animații), spotlight JS și LOGO (SVG „Job Scraper”)
│   ├── db.py                  # SQLite: migrări append-only (PRAGMA user_version), upsert, interogări, scrape_runs
│   ├── normalize.py           # diacritice, date relative, mod de lucru, id LinkedIn din URL (gata); salarii, orașe (de făcut)
│   ├── scrape.py              # run_source() (+ descrieri automate), load_missing_details(), load_details()
│   ├── ui.py                  # helpers Streamlit: app_context(), etichete RO, page_header/card/stat_tiles/pills, chei
│   ├── filters.py             # JobFilter, filter_jobs(), sort_jobs() pentru pagina Joburi (pure)
│   ├── matching.py            # MatchSettings, score_jobs()/score_job() → MatchResult, hard_filter() (pur)
│   ├── dedupe.py              # dedup_key() din titlu + firmă + oraș, group_duplicates() (pur)
│   ├── cli.py                 # status, scrape, demo
│   ├── demo.py                # seed_demo(): joburi parsate offline din tests/fixtures, profilul de exemplu, 2 CV-uri scrise de mână
│   ├── sources/
│   │   ├── __init__.py        # JobSource/DetailsSource, create_source(), create_details_source(), details_source_name()
│   │   ├── errors.py          # SourceUnavailable, SourceError → FetchError (BlockedError, DisallowedByRobots), ParseError
│   │   ├── searchapi.py       # SearchApiClient + GoogleJobsSource
│   │   ├── linkedin.py        # LinkedInSource (listă + detalii la cerere)
│   │   ├── http.py            # PoliteClient pentru scrapere: robots.txt, rate limit, cache, retry
│   │   ├── base_scraper.py    # BaseScraper, ListingPage
│   │   ├── bestjobs.py
│   │   └── hipo.py
│   └── cv/
│       ├── prompts.py         # SYSTEM_PROMPT, profilul cu indici, anunțul și/sau tipul de job (FOCUS_PRESETS), limba
│       ├── generator.py       # generate_cv(client, profile, job | None, focus=…) → GeneratedCV (prin llm.parse)
│       ├── library.py         # pagina CV-urile mele: tipul fiecărui CV (focus sau din titlul anunțului), filter_entries() (pur)
│       ├── validate.py        # check_cv(): erori (blochează exportul) și avertismente
│       ├── document.py        # build_document(profile, cv) → CVDocument (faptele vin din profil, după index)
│       ├── render.py          # render_html, write_pdf (proces separat), write_docx, file_stem
│       ├── pdf_worker.py      # python -m jobscraper.cv.pdf_worker in.html out.pdf (Playwright Chromium)
│       └── templates/cv.html.j2   # o coloană, A4, text real (bun pentru ATS)
├── data/                      # gitignored: jobs.db, profile.yaml, profile_linkedin.pdf/.json, cache/, output/
├── tests/
│   └── fixtures/<sursa>/      # HTML salvat (scrapere) sau JSON (searchapi)
├── README.md                  # documentația pentru utilizatori (instalare, demo, chei, arhitectură)
├── config.yaml
├── profile.example.yaml
├── .env.example
└── requirements.txt
```

### Contractul unei surse

Orice sursă respectă `JobSource` din `jobscraper/sources/__init__.py`: `name: str` și `fetch_jobs(query: SearchQuery) -> list[JobPosting]`. `create_source(name, config, keys)` construiește sursa (și verifică cheia, dacă are nevoie) sau ridică `SourceUnavailable` cu un mesaj pentru utilizator. O sursă nouă se adaugă acolo.

Scraperele directe moștenesc `BaseScraper` (primesc un `PoliteClient`), care implementează `fetch_jobs` și `fetch_details` din:

- `search_url(query) -> str` — prima pagină de rezultate
- `parse_listing(body, url) -> ListingPage(jobs, next_url)` — joburile de pe o pagină (fără descriere) și URL-ul paginii următoare
- `parse_detail(body, job, *, now) -> JobPosting` — `job` completat din pagina lui, cu `details_fetched_at = now`

Funcțiile de parsare (`parse_*`, `parse_google_jobs`, `parse_linkedin_jobs`, `apply_linkedin_details`) sunt pure, deci se testează offline pe fixture-uri. Când pagina nu mai are structura așteptată, ridică `ParseError`, nu întoarce date goale. `scrape.run_source()` prinde orice excepție a unei surse, o salvează în `scrape_runs` și continuă cu celelalte; UI-ul o afișează.

### Rezultate fără legătură și „remote”

- **„remote” nu e un cuvânt de căutare** la LinkedIn și Hipo: ambele potrivesc oricare dintre cuvinte, deci „ospatar remote” aducea toate joburile remote (iar motorul `linkedin_jobs` nu are parametru de mod de lucru). „Doar remote” e un filtru în `scrape.run_source`: pleacă joburile cu mod de lucru cunoscut și diferit de remote; cele necunoscute rămân. BestJobs (care înțelege „remote” ca locație) și Google Jobs îl primesc în continuare în text.
- **O sursă fără potriviri întoarce joburi generale** (LinkedIn: 60 de anunțuri oarecare la „ospatar”). `matching.unrelated_results()`: dacă niciun rezultat nu menționează vreun cuvânt al căutării (`search_words`, fără cuvintele de nivel și cele generice; contează și o formă mai lungă, „ospatarita”, și sinonimele din `_ROLE_SYNONYMS`, de ex. ospătar = waiter), rularea nu păstrează nimic, iar UI-ul spune „niciun job pentru …” și câte anunțuri generale a ignorat.
  - LinkedIn și Google Jobs se judecă pe listă (titlu + descriere, când există).
  - Scraperele (`BaseScraper.searches_ad_text`) se judecă **după** încărcarea descrierilor complete, pentru că BestJobs caută și în textul anunțului sau pe domenii („python Cluj” întoarce joburi IT fără „python” în titlu). Dacă nici anunțurile complete nu menționează cuvintele, se șterg (`db.delete_jobs`) joburile adăugate de rularea respectivă; cele salvate dinainte rămân. Fără descrieri încărcate, scraperele nu se judecă.
- `found` / `jobs_found` numără joburile unice găsite (paginile se pot repeta).

### Detalii la cerere

LinkedIn, BestJobs și Hipo nu dau descrierea în rezultate. `sources.details_source_name(job)` spune de unde se pot lua detaliile (`linkedin` dacă jobul are `linkedin_job_id`, sursa însăși pentru scraperele din `SCRAPERS`, `None` dacă au fost deja luate). Un scraper nou se adaugă în `SCRAPERS` (`sources/__init__.py`) și primește automat căutare și detalii. `PAID_DETAILS` marchează sursele care costă credite, ca UI-ul să spună asta.

- **Scraperele (gratuite): automat după căutare.** `run_source(…, details=source, max_details=…)` încarcă descrierea completă pentru joburile găsite care nu o au, în ordinea din listă, cel mult `scraping.max_auto_details` per sursă și căutare (implicit 40, ~2,5 s fiecare), cu progres (`on_progress`). O pagină care eșuează e sărită și numărată; un 401/403 sau robots.txt oprește bucla. Pornit de `scraping.auto_details`, de bifa din formularul de căutare și oprit în CLI cu `--no-details`.
- **LinkedIn: doar la cerere**, din panoul jobului (un credit SearchApi per job): butonul apelează `scrape.load_details(conn, create_details_source(job, …), job)`. Pagina Generează CV folosește același drum când jobul nu are descriere.

## UI (Streamlit)

- Fiecare pagină din `app_pages/` începe cu `config, keys = ui.app_context()` și deschide baza cu `db.open_db()` doar cât îi trebuie. Etichetele în română (status, mod de lucru, nivel, surse) vin din `ui.py`, nu se scriu din nou în pagini.
- **Design** (`design.md`, „Linear / Modern”, temă închisă): la cererea utilizatorului, negrurile din `design.md` sunt ridicate cu o treaptă (fundal `#0d0d12`, lumini ambientale mai puternice, suprafețe și text secundar mai deschise), cu aceleași nuanțe; nu le închide la loc. Luminile sunt forme pline estompate cu `filter: blur()`, nu gradiente radiale simple (acelea ating opacitatea doar în centru și par negre). Culorile și fonturile în `.streamlit/config.toml`; restul (fundal cu lumini și grilă, carduri de sticlă, glow pe butonul principal, etichete mono, animații, `prefers-reduced-motion`) în `jobscraper/theme.py`, care țintește `data-testid`-urile Streamlit și clasele `st-key-<cheie>`. Paginile folosesc blocurile din `ui.py`: `page_header()` (titlu cu accent), `card(name)` (container cu cheia `card-<name>`, stilat ca sticlă), `card_title()`, `stat_tiles()`, `pills()`, `plain_text()` (text ca atare, de ex. descrierea unui anunț: nu `st.text`, care e monospace, și nu Markdown, care interpretează „-”, „*”, „#”). Nu scrie CSS în pagini.
  - CSS-ul se injectează cu un `st.html` care conține **doar** `<style>` (JS-ul spotlight separat, cu `unsafe_allow_javascript=True`). DOMPurify aruncă un `<style>` care conține `<` urmat de literă, deci SVG-urile din CSS (zgomotul) sunt percent-encoded.
  - După ce modifici `theme.py` sau alte module din `jobscraper/`, repornește serverul Streamlit: pe acest calculator watcher-ul nu reîncarcă modulele importate. Repornirea pierde formularele nesalvate din sesiunile deschise, deci spune-i utilizatorului înainte.
  - `theme.py` e un șablon formatat cu `%`: orice `%` din CSS (inclusiv din comentarii) se scrie `%%`.
- **Joburi:** filtrele (`f_*` în session state) stau în bara laterală. Statusul se filtrează în SQL, restul în `filters.py`, iar preferințele din profil prin `matching.hard_filter` (comutatorul „Aplică preferințele din profil”, cheia `f_hide_excluded`, implicit pornit, cu „N ascunse de preferințe” și butonul „Arată-le”). `f_search` restrânge lista la rezultatele unei căutări („Arată toate joburile” îl scoate). Valorile implicite ale widget-urilor pe care alte pagini le pot seta (`f_hide_excluded`, `cv_mode`) se dau cu `st.session_state.setdefault`, nu cu `value=`/`default=` (Streamlit avertizează când apar ambele). Deschiderea unui job `new` îl marchează `seen`.
- **Căutare:** după o căutare, cardul „Rezultate pentru …” (din `st.session_state.last_search`: cuvintele și id-urile tuturor joburilor găsite, noi sau deja salvate) spune câte se potrivesc preferințelor și de ce sunt ascunse celelalte (`matching.exclusion_kind`: rol, nivel, oraș...), cu „Vezi rezultatele în Joburi” și „Vezi toate, fără preferințe”: ele setează `f_search` și `f_hide_excluded` și deschid Joburi. Formularul pornește cu primul rol și primul oraș din preferințele profilului (rezervă: `config.search`) și cu „Doar remote” bifat dacă singurul mod de lucru preferat e remote. Dacă orașul căutat nu e printre preferințe, pagina spune că rezultatele vor fi ascunse în Joburi.
- **Selecția din tabel e după poziție.** După orice schimbare de date (status, detalii) apelează `refresh()`: crește `jobs_table_version` (cheia tabelului, deci selecția se resetează), iar jobul deschis rămâne în `st.session_state.selected_job_id`. Altfel selecția ar indica alt job.
- Textele care costă bani o spun explicit (`PAID_SEARCH`, `PAID_DETAILS`).
- **Teste UI:** `tests/test_ui.py` rulează paginile cu `streamlit.testing.v1.AppTest` pe o bază temporară (`JOBSCRAPER_CONFIG`). Setează `SEARCHAPI_API_KEY=""` și `ANTHROPIC_API_KEY=""` în mediu, ca o cheie reală din `.env` să nu fie folosită niciodată de teste. Rândurile din tabel nu se pot selecta în AppTest; setează `at.session_state["selected_job_id"]` înainte de `run()` (la fel `cv_mode = "focus"` pentru CV-ul pe tip de job). După un `st.switch_page` declanșat de un buton, AppTest rulează din nou pagina pe care a fost setat ultima dată: apelează `at.switch_page(<pagina nouă>)` înainte de următorul clic. Încărcarea de fișiere nu se poate simula în AppTest: importul PDF se testează în `tests/test_linkedin_import.py`.

## BYOK (chei proprii)

Utilizatorul își aduce propriile chei: `ANTHROPIC_API_KEY` și `SEARCHAPI_API_KEY`. Tot accesul la chei trece prin `KeyStore` (`jobscraper/keys.py`), niciodată direct prin `os.environ`:

- Ordinea: cheia introdusă în UI pentru sesiunea curentă (`st.session_state`, doar în memorie) → `.env` / variabile de mediu.
- Butonul „Salvează” scrie cheia în `.env` (gitignored) cu `python-dotenv`; „Șterge din .env” o scoate.
- În CLI se folosesc doar cheile din `.env` / mediu.
- Cheile nu apar niciodată în loguri, mesaje de eroare, URL-uri sau commit-uri; în UI se afișează doar mascate (`mask()`).
- Clientul Anthropic se construiește cu `anthropic.Anthropic(api_key=keys.get(ANTHROPIC_API_KEY))`.

### Model de date

`JobPosting`: `id`, `source`, `url`, `title`, `company`, `city`, `via` (site-ul original când jobul vine printr-un agregator), `work_mode` (on-site / hybrid / remote), `salary_text` (salariul exact cum apare în anunț), `salary_min`, `salary_max`, `currency`, `salary_is_net`, `employment_type`, `seniority`, `description`, `requirements`, `language` (ro/en), `posted_at`, `scraped_at`, `status`, `linkedin_job_id`, `details_fetched_at`.

**`id`:** sha1 din `linkedin:<linkedin_job_id>` când jobul are un link LinkedIn (din motorul LinkedIn sau din orice link al unui rezultat Google Jobs), altfel din `source` + id-ul extern sau URL. Așa, același anunț LinkedIn găsit de ambele surse ajunge într-un singur rând.

**Upsert:** re-scrapingul nu suprascrie statusul setat de utilizator și nu șterge date cunoscute: o valoare nouă `NULL`, `''` sau `'[]'` păstrează valoarea existentă (`COALESCE` în `db._UPSERT_SQL`). Coloanele noi se adaugă printr-o migrare nouă în `db._MIGRATIONS`. O migrare poate fi un script SQL sau o funcție Python (ex. `_add_dedup_keys`, care adaugă coloana și o completează pentru rândurile existente).

**Dedup între surse** (`dedupe.py`, coloana `dedup_key`, migrarea 5):
- `dedup_key = titlu|firmă|oraș`, normalizate: titlul fără diacritice, punctuație și etichete de gen („(m/f/d)”, „(all genders)”); firma fără forme juridice și fără „Romania” („S.R.L.”, „GmbH”); orașul = primul oraș cunoscut găsit în text (BestJobs dă adrese întregi), „bucharest” → „bucuresti”, remote = "". **Fără firmă nu există cheie**: titluri generice de la firme diferite nu se unesc.
- Un job nou cu aceeași cheie ca unul deja marcat (salvat/aplicat/respins) primește acel status la inserare; `set_status` schimbă statusul tuturor copiilor; `duplicates_of(job_id)` întoarce celelalte copii.
- Pagina Joburi arată o singură linie per anunț (`group_duplicates`, se alege copia cea mai completă: descriere, detalii, salariu, dată), cu „Prin” = toate sursele („BestJobs + Hipo”) și „N duplicate unite” în rezumat; panoul jobului are linkuri „Același anunț și pe: …”.
- Dacă schimbi `dedup_key()`, adaugă o migrare care apelează `db.refresh_dedup_keys()`.

Fluxul de status: `new` → `seen` → `saved` → `applied` / `rejected`.

### Normalizare (`normalize.py`)

- **Diacritice:** site-urile amestecă ş/ţ cu sedilă (U+015F, U+0163) cu ș/ț cu virgulă (U+0219, U+021B). La salvare normalizează la varianta cu virgulă; pentru căutare și potrivire compară și fără diacritice.
- **Date relative:** „acum 2 zile”, „ieri”, „azi”, „12 octombrie” → `datetime` cu fus orar `Europe/Bucharest`.
- **Salarii:** „4.000 - 6.000 RON net” → min/max/monedă/net-brut. Punctul e separator de mii.
- **HTML → text** (`html_to_text`): un rând per bloc, „- ” pentru liste; se elimină `<script>`/`<style>`, caracterele invizibile (zero-width) și liniile de încărcare („Please wait...”) din anunțurile copiate de angajatori de pe site-ul lor.
- **Ani de experiență ceruți** (`matching.years_asked`): „1 - 5 ani” înseamnă minim 1 an (limita de jos), „3+ years” minim 3; „an” doar ca cuvânt întreg (nu în „angajați”).
- **Dedup:** LinkedIn prin `id`; restul prin `dedup_key` (vezi „Model de date”).

## Profilul utilizatorului

`data/profile.yaml` e **sursa unică de adevăr** despre experiența mea: date de contact, rezumat, experiență (rol, firmă, perioadă, realizări), educație, cursuri (ex. StepIT), proiecte, skill-uri cu nivel, limbi străine, preferințe de căutare (roluri, orașe, mod de lucru, nivel de experiență, cuvinte excluse din titlu, salariu minim).

E gitignored pentru că are date personale; `profile.example.yaml` e șablonul care se poate pune în git. Se editează din pagina „Profil” sau manual. Dacă profilul lipsește sau e incomplet, întreabă utilizatorul. Nu completa niciodată din presupuneri.

**Import din LinkedIn** (`linkedin_import.py`): utilizatorul încarcă PDF-ul „Salvează ca PDF” de pe LinkedIn; Claude îl primește ca bloc `document` (base64, `application/pdf`) și întoarce `ImportedProfile`, un model permisiv (șiruri simple, fără format de dată), ca nimic să nu fie ghicit: o dată dată doar ca an rămâne „2021”. **Toate câmpurile `ImportedProfile` (și `TailoredCV`) sunt obligatorii, de tip șir sau listă, fără `| None`** („” / [] = lipsă): structured outputs refuză scheme cu peste 24 de câmpuri opționale sau 16 uniuni („compiled grammar is too large”); `tests/test_linkedin_import.py` verifică asta. `merge_into_profile(imported, current)` combină cu profilul curent (formularul nesalvat, altfel cel de pe disc): valorile din PDF câștigă, câmpurile goale se iau din profil, intrările care lipsesc din PDF (un curs StepIT, nivelul skill-urilor) rămân, preferințele nu se ating. Rezultatul doar completează formularul; utilizatorul verifică și salvează. PDF-ul se păstrează lângă profil (`profile.linkedin_pdf_path()` → `data/profile_linkedin.pdf`) și se poate descărca; ce a citit Claude din el se salvează în `data/profile_linkedin.json` (`profile.linkedin_import_path()`), iar butonul „Refă din ultimul import” reface formularul fără un apel nou (de ex. după o repornire a serverului).

**Ștergerea profilului** („Șterge profilul”, cu confirmare): `profile.delete_profile()` șterge `profile.yaml`, PDF-ul atașat și importul salvat (JSON); opțional și toate CV-urile generate (`db.delete_all_cvs`), care conțin date din profil.

Validare (modelele din `models.py`, `ProfilePart`): textul se curăță de spații; câmpurile obligatorii (`Required`) nu pot fi goale; datele sunt `AAAA-LL` sau doar `AAAA` când luna nu se știe (LinkedIn dă studiile ca „2015 - 2019”; nu ghici luna). CV-ul afișează „2015 – 2019”; la anii de experiență o lună lipsă contează ca mijlocul anului. Intrările complet goale din formular se ignoră la salvare.

**Pagina Profil (`app_pages/profile.py` + `jobscraper/profile_form.py`):**
- Fiecare widget are o cheie `p.…` în session state; intrările din liste au un uid stabil (`p.experience.<uid>.role`), ca ștergerea unei intrări să nu mute valorile celorlalte.
- Valorile widget-urilor se modifică **doar în callback-uri** (`on_click`): Streamlit nu permite schimbarea unei chei după ce widget-ul ei a fost desenat în același run. Salvarea, „Renunță”, exemplul și YAML-ul trec toate prin `reset_form()`.
- Skill-urile și limbile sunt în `st.data_editor`; în session state stau doar modificările, iar `apply_editor_delta()` le aplică peste rândurile inițiale.
- Mesajele stau mereu în aceleași containere: dacă structura paginii se schimbă între rerun-uri, `st.tabs` revine la primul tab.
- La schimbarea paginii, Streamlit șterge valorile widget-urilor; pagina detectează asta și reîncarcă profilul de pe disc, deci modificările nesalvate se pierd (UI-ul o spune).

## Potrivire (`matching.py`)

Scor determinist 0–100, pur (fără LLM, fără rețea), calculat la fiecare afișare a paginii Joburi pentru joburile filtrate (`score_jobs`: faptele din profil se calculează o dată per lot).

| Componentă | Puncte | Cum |
|---|---|---|
| Skill-uri | 50 | termenii pe care îi cere anunțul (vocabular `_TECH_TERMS` cu alias-uri + tot ce e în profil) acoperiți de skill-urile/tehnologiile din profil; fără termeni recunoscuți = jumătate |
| Rol | 20 | cuvintele (> 2 litere) din rolurile preferate găsite în titlu |
| Nivel | 15 | junior/mid/senior din titlu, `seniority` sau „X+ ani/years”, față de anii de experiență din profil (< 2 junior, < 5 mid); necunoscut = 10 |
| Locație | 15 | remote (dacă e acceptat), oraș preferat, mod de lucru exclus = 0; necunoscut = 7 |

- **Setări:** `MatchSettings.from_sources(profile, config.search)`: roluri, orașe, mod de lucru și salariul minim din preferințele profilului, cu `config.search` ca rezervă; nivelurile doar din profil; cuvintele excluse = `config.search` + profil; `keywords_required` doar din `config.search`. Rolurile filtrează doar când vin din profil (`filter_by_role`): `config.search.queries` sunt termeni de căutare, nu filtre.
- **Filtre dure** (`hard_filter`), care nu schimbă scorul, ci marchează jobul ca exclus, cu motiv (UI-ul le ascunde cât timp e pornit „Aplică preferințele din profil”). **O valoare necunoscută nu exclude niciodată** (job fără oraș, fără mod de lucru, fără nivel recunoscut, fără monedă):
  - cuvânt exclus **în titlu** (nu în descriere: „lucrezi cu colegi senior” nu exclude un job junior); cuvânt obligatoriu lipsă din titlu + descriere;
  - mod de lucru în afara preferințelor; oraș în afara preferințelor, doar pentru joburile care nu sunt remote (`city_matches`: „Bucharest” = „București”, adrese întregi, liste de orașe; „Romania” singur = necunoscut);
  - nivel (`job_level`) în afara nivelurilor alese;
  - titlu fără niciun cuvânt dintr-un rol (cuvintele de nivel nu contează, iar `_ROLE_SYNONYMS` echivalează developer = engineer = programator = dezvoltator, analyst = analist, tester = QA; sinonimele contează și la scor);
  - `salary_max` sub minim. Salariul se compară doar cu monedă cunoscută (`RON_PER_CURRENCY`, curs aproximativ EUR = 5 RON).
- **Potrivirea termenilor** se face pe cuvinte întregi, fără diacritice: `java` nu se potrivește în `javascript`, `sql` nu în `postgresql`, `js` nu în `node.js`. Termenii care sunt și cuvinte obișnuite (`go`, `r`, `c`, `rest`) nu sunt în vocabular. Un termen nou se adaugă în `_TECH_TERMS`, cu test.
- **UI:** coloana „Potrivire” (bară), ordinea implicită după potrivire, comutatorul „Ascunde joburile excluse” (implicit pornit, cu numărul celor ascunse) și, în panoul jobului, „Potrivire X% · de ce?” cu motivele (✓/✗/•). Fără profil nu există scor, iar pagina trimite la Profil. Joburile fără descriere au un scor mai puțin precis; panoul o spune.
- **Încă neimplementat:** scor LLM la cerere pentru joburile selectate (niciodată automat pe toate, din cauza costului).

## Generare CV (`cv/`)

Pagina Generează CV are două moduri (`cv_mode`):

- **Pentru un anunț:** alegi jobul (implicit cel deschis în Joburi, butonul „CV pentru acest job”) → dacă nu are descriere, o încarci → opțional „Accent pe” un tip de job → `generate_cv(client, profile, job, focus=…)`. Export în `data/output/<data>_<firma>_<rol>.*`.
- **Pentru un tip de job** (CV refolosibil pentru orice anunț de acel tip): alegi un tip din `prompts.FOCUS_PRESETS` (Testare / QA, Dezvoltare software, Date / BI, DevOps / Cloud, Suport IT; fiecare are o descriere în engleză pentru model), unul generat deja sau „Altul” (text liber) → `generate_cv(client, profile, None, focus=…)`. Experiența și studiile sunt aceleași; se schimbă cum se prezintă candidatul și ce pune în față din fiecare loc, **doar din ce e adevărat**; `gaps` = ce se cere de obicei la acel tip de job și lipsește din profil. Fără anunț, limba „auto” înseamnă română. Export în `data/output/<data>_cv_<tip>.*`.

`TailoredCV` se salvează în tabela `cvs` (fiecare generare = rând nou, pentru că a costat bani; editările actualizează rândul): `job_id` pentru un anunț, `focus` pentru tipul de job (migrarea 6 a făcut `job_id` opțional). `cv_versions(job_id=… | focus=…)`, `all_cvs()`, `delete_cv()`, `cv_focuses()`. Când un anunț sau un tip are mai multe generări, pagina Generează CV arată selectorul „Versiune”; versiunea deschisă e `st.session_state.cv_open_id` (o setează o generare nouă și butonul „Deschide” din CV-urile mele). Pagina Generează CV arată și anunțul (cerințe, descriere, „Vezi în Joburi”, care deschide jobul în panou chiar dacă filtrele îl ascund).

**Pagina CV-urile mele** (`app_pages/cvs.py` + `cv/library.py`): toate CV-urile, cele mai noi primele, cu plăci pe tipuri și filtre în bara laterală (`l_*`): tip de job, „Pentru un anunț” / „Pe tip de job”, limbă, text (titlu, rezumat, anunț, firmă). Tipul unui CV = focusul ales la generare; altfel se deduce din titlul anunțului (`type_from_title`: QA și DevOps înaintea lui „engineer”), „Altele” dacă nu se recunoaște. „Deschide” setează `cv_mode`, `cv_job_id` / `cv_focus_choice` și `cv_open_id`, apoi `st.switch_page`. Apoi tab-ul **Editează** (titlu, rezumat, competențe, ce experiențe/proiecte intră și cu ce bullet-uri, educație, cursuri), **Previzualizare** (HTML-ul exact care devine PDF), **Export** (erori/avertismente din `check_cv`, `gaps`, PDF/DOCX + buton de descărcare).

Previzualizarea și exportul folosesc versiunea editată, chiar nesalvată. Exportul e blocat cât timp `check_cv` are erori.

### Reguli de conținut (critice)

- **Nu inventa nimic.** CV-ul poate doar selecta, reordona, reformula și pune în evidență ce există în `profile.yaml`. Fără skill-uri, firme, titluri, date sau cifre noi.
- Cerințele din anunț pe care profilul nu le acoperă merg într-o listă separată `gaps`, afișată utilizatorului, **nu** în CV.
- `TailoredCV` referă experiența, proiectele, educația și cursurile **prin index** în profil (`profile_index`, `education_indices`, `course_indices`). Firmele, rolurile, perioadele și școlile se iau din profil la randare, deci modelul nu le poate inventa. Modelul scrie doar textul (headline, rezumat, bullet-uri, descrieri) și alege skill-urile.
- `validate.check_cv` → **erori** (blochează exportul): indici inexistenți sau duplicați; skill-uri care nu sunt în profil (se acceptă și tehnologiile din experiență/proiecte, fără diferențe de majuscule/diacritice). **Avertismente**: cifre din CV care nu apar în partea profesională a profilului (contactul, de ex. prefixul +40, și preferințele nu contează), experiență lipsă sau fără bullet-uri.
- Limba CV-ului = limba anunțului (RO sau EN), dacă utilizatorul nu alege altfel.
- Format compatibil ATS: o singură coloană, titluri de secțiune standard, fără tabele sau imagini în DOCX. Cuvintele cheie din anunț se folosesc natural, doar acolo unde sunt adevărate.
- Maximum 2 pagini.

### Claude API

- Încarcă skill-ul `claude-api` înainte de a scrie sau modifica cod care apelează Claude.
- SDK-ul oficial `anthropic`; cheia vine din `KeyStore` (vezi BYOK), niciodată din cod sau din git.
- Model implicit `claude-opus-5-5`, configurabil în `config.yaml` (`llm.model`, `llm.effort`, `llm.max_tokens`).
- Un singur drum pentru toate apelurile: `llm.parse(client, llm=…, output_format=<model Pydantic>, system=…, content=…, what=…)` → `client.beta.messages.parse(model, max_tokens, output_config={"effort": …}, output_format=…, system=…, messages=…)` → `Parsed(output, model, tokeni)`. `effort` și `output_format` merg împreună; nu pune `format` în `output_config`. Îl folosesc `cv/generator.py` (`TailoredCV`) și `linkedin_import.py` (`ImportedProfile`, PDF ca bloc `document`). `CVGenerationError` e un alias pentru `LLMError`.
- **Fallback la refuz:** pentru modelele din `FALLBACK_MODELS` se trimit `betas=["server-side-fallback-2026-07-01"]` și `fallbacks="default"` (doar pe Claude API, nu Bedrock/Vertex). `response.model` spune ce model a răspuns efectiv.
- Pe Opus 5.5 thinking-ul nu se poate dezactiva, iar `temperature`/`thinking` nu se trimit. Calitatea și costul se controlează prin `effort` (implicit `medium`).
- Verifică `stop_reason` (`refusal`, `max_tokens`) și `parsed_output is None` înainte de a folosi rezultatul. Erorile SDK (`AuthenticationError`, `RateLimitError`, `BadRequestError`, `APIStatusError`, `APIConnectionError`) devin `LLMError` cu mesaj în română.
- Prompt caching: instrucțiunile fixe (system), apoi profilul (JSON determinist, cu `"index"`, fără contact și preferințe; `cache_control` pe acest bloc), apoi anunțul (descriere tăiată la 20.000 de caractere) și limba, la final.
- Testele folosesc `FakeClaude` (`tests/test_cv.py`, `tests/test_linkedin_import.py`), care înregistrează cererea; nu face apeluri reale la Claude din teste.

### Export

- PDF: HTML-ul din `templates/cv.html.j2` printat cu Chromium headless (Playwright) **într-un proces separat** (`pdf_worker`), pentru că bucla asyncio a Playwright și cea a Streamlit se ciocnesc pe Windows. Dacă lipsește Chromium, mesajul de eroare spune comanda `playwright install chromium`.
- DOCX: `python-docx`, o coloană, paragrafe „List Bullet”, fără tabele; data e aliniată la dreapta cu un tab stop.

## Convenții

- Type hints peste tot; modele Pydantic la granițe (output de scraper, output LLM, config).
- `logging`, nu `print`.
- **Testele nu fac request-uri reale.** Parserele se testează pe HTML/JSON salvat în `tests/fixtures/<sursa>/`; request-urile HTTP se simulează cu `httpx.MockTransport`. Când un site își schimbă structura, actualizează fixture-ul și parserul împreună. Fixture-urile din `tests/fixtures/searchapi/` sunt scrise de mână după documentație; înlocuiește-le cu răspunsuri reale salvate după primele căutări ale utilizatorului. Cele din `tests/fixtures/bestjobs/` sunt răspunsuri reale (2026-10-19), scurtate: din paginile de job s-a păstrat doar `props.pageProps.job` din `__NEXT_DATA__`. Cele din `tests/fixtures/hipo/` sunt pagini reale (2026-10-19; `job_structured.html` 2026-10-22), scurtate: din căutare doar cardurile `div.job-item` (plus un card-reclamă) și linkurile de paginare, din paginile de job doar blocul principal și JSON-LD-ul.
- Fișierele se citesc și se scriu explicit cu `encoding="utf-8"` (pe Windows implicitul nu e UTF-8).
- `.env`, `data/` și `.venv/` sunt în `.gitignore`.

## Ordinea de implementare

1. ✅ Schelet: structura de foldere, modele Pydantic, SQLite, config. Plus BYOK și sursa SearchApi.io.
2. ✅ Un scraper complet + teste cu fixture-uri: BestJobs (eJobs e blocat, vezi „Surse”), plus `PoliteClient` și `BaseScraper`.
3. ✅ UI Streamlit: căutare, listă cu filtre, detaliu job, schimbare status.
4. ✅ Scraperul Hipo + dedup general (titlu + firmă + oraș) între surse.
5. ✅ Profil: `profile.example.yaml` + pagina de editare.
6. ✅ Generator CV + validare + export PDF/DOCX.
7. ✅ Scorul de potrivire cu explicații.
