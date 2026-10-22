# Job Scraper

Aplicație locală pentru căutarea unui loc de muncă în România:

- **adună anunțuri** de pe BestJobs, Hipo și LinkedIn, le salvează local (SQLite) și unește dublurile aceluiași anunț de pe site-uri diferite;
- **le ordonează după potrivirea cu profilul tău** (skill-uri, rol, nivel, oraș, mod de lucru), cu explicații pentru fiecare scor, și ascunde ce nu se potrivește preferințelor;
- **generează CV-uri adaptate** cu Claude (Anthropic), fie pentru un anunț anume, fie pentru un tip de job (QA, dezvoltare, date...), folosind **doar** experiența reală din profil; CV-ul se poate edita și exporta în PDF sau DOCX;
- **importă profilul din PDF-ul exportat de pe LinkedIn**, ca să nu-l completezi de mână.

Interfața (Streamlit) și mesajele sunt în limba română.

## Cuprins

- [Funcționalități](#funcționalități)
- [Instalare](#instalare)
- [Testare rapidă, fără chei API](#testare-rapidă-fără-chei-api)
- [Chei API (opționale)](#chei-api-opționale)
- [Configurare](#configurare)
- [Teste automate](#teste-automate)
- [Linia de comandă](#linia-de-comandă)
- [Cum funcționează](#cum-funcționează)
- [Reguli de scraping](#reguli-de-scraping)
- [Date personale](#date-personale)
- [Limitări cunoscute](#limitări-cunoscute)

## Funcționalități

| Pagina | Ce face |
|---|---|
| **Joburi** | Toate anunțurile salvate, cu filtre (text, status, sursă, mod de lucru, dată, salariu) și scorul de potrivire 0–100. Panoul unui job arată descrierea completă, de ce are scorul acela, linkurile spre aceeași ofertă pe alte site-uri și statusul (nou, văzut, salvat, am aplicat, respins). |
| **Căutare** | Caută pe sursele alese; pentru BestJobs și Hipo încarcă automat și descrierile complete. Un card arată câte rezultate se potrivesc preferințelor și de ce sunt ascunse celelalte. |
| **Generează CV** | CV pentru un anunț (cu anunțul afișat alături) sau pentru un tip de job. Fiecare generare se păstrează ca versiune; tab-urile Editează, Previzualizare (exact ce devine PDF) și Export (PDF/DOCX). Cerințele pe care profilul nu le acoperă apar separat, nu în CV. |
| **CV-urile mele** | Toate CV-urile generate, cu filtre după tipul de job, limbă și text. |
| **Profil** | Experiență, studii, cursuri, proiecte, skill-uri, limbi și preferințe de căutare (roluri, orașe, mod de lucru, nivel, cuvinte excluse, salariu minim). Import din PDF-ul LinkedIn, editor YAML, ștergerea profilului. |
| **Chei API** | Cheile tale (Anthropic, SearchApi.io), doar pentru sesiune sau salvate în `.env`. |

CV-urile nu inventează nimic: modelul primește profilul și referă experiența, studiile și proiectele **prin index**, deci firmele, rolurile și perioadele vin mereu din profil. O validare blochează exportul dacă apar skill-uri care nu sunt în profil și avertizează la cifre care nu apar în el.

## Instalare

Cerințe: **Python 3.11+** (testat cu 3.13) și, pentru exportul PDF, Chromium prin Playwright.

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Pornirea interfeței (se deschide în browser, la http://localhost:8501):

```powershell
.venv\Scripts\python.exe -m streamlit run app.py
```

(Pe macOS / Linux, `.venv/bin/python` în loc de `.venv\Scripts\python.exe`, la toate comenzile.)

## Testare rapidă, fără chei API

Comanda `demo` umple aplicația cu date de probă, fără rețea și fără chei:

```powershell
.venv\Scripts\python.exe -m jobscraper.cli demo
.venv\Scripts\python.exe -m streamlit run app.py
```

Ea adaugă:

- **28 de anunțuri reale** de pe BestJobs, Hipo și LinkedIn, salvate în `tests/fixtures/` (o parte cu descrierea completă);
- **profilul de exemplu** (`profile.example.yaml`, o persoană fictivă), doar dacă nu ai deja un profil;
- **două CV-uri scrise de mână** din acel profil: unul pentru un anunț, unul pentru tipul „Testare / QA”.

Ce poți încerca apoi:

1. **Joburi**: ordinea după potrivire, filtrele, comutatorul „Aplică preferințele din profil”, panoul unui job și „Potrivire X% · de ce?”.
2. **Profil → Preferințe**: schimbă rolurile sau nivelul și vezi cum se schimbă lista din Joburi.
3. **CV-urile mele → Deschide**: editează un CV, vezi previzualizarea și exportă-l în PDF sau DOCX.

Căutarea pe BestJobs și Hipo funcționează și ea fără chei (sunt site-uri reale, deci fă căutări mici). Generarea de CV-uri noi, importul din LinkedIn și căutarea pe LinkedIn au nevoie de chei.

## Chei API (opționale)

| Cheie | Pentru ce | Cost |
|---|---|---|
| `ANTHROPIC_API_KEY` ([console.anthropic.com](https://console.anthropic.com/)) | generarea CV-urilor, importul profilului din PDF-ul LinkedIn | tokeni Claude per generare / import |
| `SEARCHAPI_API_KEY` ([searchapi.io](https://www.searchapi.io/)) | căutarea pe LinkedIn și descrierea unui anunț LinkedIn | un credit per pagină de rezultate și unul per descriere |

Cheile se introduc pe pagina **Chei API** (doar pentru sesiunea curentă sau salvate în `.env`) sau direct în `.env`, pornind de la `.env.example`. Nu apar niciodată în loguri, în URL-uri sau în interfață (acolo se afișează mascate).

## Configurare

`config.yaml` (comentat în fișier):

- `sources`: ce surse sunt active (BestJobs, Hipo, LinkedIn; Google Jobs e implementat, dar nu suportă România);
- `search`: căutările și orașele implicite, cuvinte obligatorii sau excluse, salariul minim (preferințele din profil au prioritate);
- `scraping`: pauza dintre cereri (minimum 2 s), cache, câte pagini și câte descrieri se încarcă automat;
- `llm`: modelul Claude (`claude-opus-5-5` implicit), nivelul de efort, numărul maxim de tokeni;
- `paths`: unde stau baza de date, profilul, cache-ul și CV-urile exportate (implicit în `data/`).

Variabila de mediu `JOBSCRAPER_CONFIG` alege alt fișier de configurare (de exemplu unul cu propria bază de date, pentru probe).

## Teste automate

```powershell
.venv\Scripts\python.exe -m pytest
```

Testele nu fac nicio cerere reală: parserele rulează pe pagini salvate (`tests/fixtures/`), HTTP-ul e simulat cu `httpx.MockTransport`, Claude cu un client fals, iar paginile Streamlit cu `AppTest`, pe o bază de date temporară.

## Linia de comandă

```powershell
.venv\Scripts\python.exe -m jobscraper.cli status        # creează baza de date dacă lipsește și afișează un rezumat
.venv\Scripts\python.exe -m jobscraper.cli demo          # date de probă (vezi mai sus)
.venv\Scripts\python.exe -m jobscraper.cli scrape --source hipo --query "python" --city Cluj-Napoca --pages 1
```

`scrape` are și `--no-details`, ca să nu încarce automat descrierile complete.

## Cum funcționează

```
app.py                  Streamlit: tema, logo-ul, navigarea între pagini
app_pages/              paginile: Joburi, Căutare, Generează CV, CV-urile mele, Profil, Chei API
jobscraper/
  sources/              sursele de joburi
    http.py             clientul HTTP politicos: robots.txt, pauze, cache, reîncercări
    bestjobs.py         BestJobs: endpoint-ul JSON al site-ului + pagina anunțului
    hipo.py             Hipo: HTML randat pe server + JSON-LD JobPosting
    linkedin.py         LinkedIn prin SearchApi.io
  scrape.py             o căutare pe o sursă: filtre, rezultate fără legătură, descrieri automate
  db.py                 SQLite cu migrări; upsert care nu șterge date cunoscute
  dedupe.py             același anunț pe mai multe site-uri (titlu + firmă + oraș)
  matching.py           scorul de potrivire și filtrele din preferințe (fără LLM)
  profile*.py           profilul (YAML) și formularul lui
  linkedin_import.py    PDF LinkedIn -> profil, cu Claude
  llm.py                apelul Claude cu răspuns structurat (Pydantic)
  cv/                   prompt, generare, validare, randare HTML / PDF / DOCX, biblioteca de CV-uri
  demo.py               datele de probă
tests/                  teste pytest + paginile salvate folosite de ele
```

- **Surse:** BestJobs și Hipo sunt citite direct (gratuit); LinkedIn doar prin SearchApi.io. Unde se poate, datele vin din formate stabile (JSON intern, JSON-LD `JobPosting`), nu din selectori CSS.
- **Rezultate fără legătură:** un site fără potriviri întoarce adesea anunțuri generale; dacă niciun rezultat nu menționează cuvintele căutate, nu se salvează nimic.
- **Potrivire:** skill-uri (50 de puncte), rol (20), nivel (15), locație (15), plus filtre din preferințe, care ascund, dar nu schimbă scorul. O informație necunoscută (oraș, nivel) nu exclude niciodată un job.
- **CV:** Claude primește instrucțiunile, profilul (fără date de contact) și anunțul sau tipul de job și întoarce un CV structurat, validat apoi local.

Tehnologii: Python, Streamlit, SQLite, Pydantic, httpx, BeautifulSoup + lxml, Protego (robots.txt), SDK-ul Anthropic, Jinja2, Playwright (PDF), python-docx, pytest.

## Reguli de scraping

- Se respectă `robots.txt` (inclusiv `Crawl-delay`) și o pauză de cel puțin 2 secunde între cereri către același site, fără cereri în paralel.
- User-Agent fix și descriptiv; fără rotație de User-Agent, proxy-uri sau browser headless pentru ocolirea protecțiilor.
- Un răspuns 401/403, CAPTCHA sau Cloudflare înseamnă „nu”: sursa se oprește și eroarea apare în interfață (de aceea eJobs și OLX nu sunt incluse).
- Paginile de detaliu se păstrează într-un cache local, ca să nu fie cerute din nou.
- Anunțurile sunt doar pentru uz personal și nu se republică.

## Date personale

Tot ce e personal rămâne pe calculatorul tău, în folderul `data/` (exclus din git prin `.gitignore`): baza de joburi, profilul, PDF-ul LinkedIn, cache-ul și CV-urile exportate. Cheile stau doar în `.env` (tot exclus) sau în memorie, pe durata sesiunii. Spre Claude pleacă doar profilul (din datele de contact, numai numele), pentru CV, și PDF-ul pe care îl imporți.

## Limitări cunoscute

- eJobs și OLX blochează accesul automat (403), deci nu sunt surse.
- Google Jobs (prin SearchApi.io) nu suportă România; codul rămâne pentru alte țări.
- Lista LinkedIn nu are descrieri; descrierea unui anunț se încarcă la cerere și costă un credit SearchApi.io.
- Scorul se bazează pe cuvinte (vocabular de tehnologii, sinonime de roluri), nu pe înțelegerea textului; un anunț fără descriere are un scor mai puțin precis.
- Site-urile își pot schimba structura; testele pe paginile salvate arată ce parser trebuie actualizat.
