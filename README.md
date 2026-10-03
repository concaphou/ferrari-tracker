# Ferrari Tracker

Scrapes Ferrari Approved (pre-owned), Cars.com and CarGurus at 11am and 5pm Central,
keeps every snapshot in `data/history.csv`, and publishes a phone-friendly dashboard.

**Models tracked:** 488 GTB · 488 GTS/Spider · 296 GTB · 296 GTS/Spider · F8 Coupe (Tributo) · F8 Spider
(GTS, Spider and Spyder are combined. 488 Pista and Challenge/Assetto Fiorano cars are excluded.)

**Excluded colors:** any exterior or interior matching red, Rosso (Corsa, Scuderia, Fuoco, Mugello,
Dino, Imola, Magma, Ferrari leather…), Bordeaux, burgundy, maroon, crimson and similar.
Edit `RED_WORDS` in `scraper/config.py` to change the list.

**Captured per car:** model, year, price, mileage, exterior + interior color, dealer city/state,
dealer, VIN, link, and which site(s) listed it.

**Dashboard shows, per model:** median/lowest price and listing count with changes; a trend chart
of median price and availability (filterable by year); low/avg/high price by year and exterior
color; cities ranked by how far below market they price (mileage-adjusted, and how consistently);
and every current listing with days listed and price changes.

---

## Setup (about 15 minutes, on a computer)

1. **Create a GitHub account** at github.com (free).
2. **Create a repository:** click **+ → New repository**, name it `ferrari-tracker`,
   choose **Public** (free website hosting requires public), and click **Create repository**.
3. **Upload the files:** on the new repo page click **uploading an existing file**, drag in
   everything from the unzipped folder, and click **Commit changes**.
   The `.github` folder is hidden on a Mac, so add the workflow by hand:
   **Add file → Create new file**, type the name `.github/workflows/scrape.yml`,
   paste the contents of that file, and click **Commit changes**.
4. **Allow the robot to save data:** **Settings → Actions → General → Workflow permissions →
   Read and write permissions → Save.**
5. **Turn on the website:** **Settings → Pages → Build and deployment → Source: Deploy from a branch →
   Branch: `main`, folder: `/docs` → Save.** Your URL will be
   `https://YOUR-USERNAME.github.io/ferrari-tracker/`.
6. **(Recommended) Add CarGurus searches.** CarGurus URLs use internal model IDs, so:
   on cargurus.com search a used Ferrari 488 GTB, set distance to **Nationwide**, copy the address
   bar URL. Repeat for each model. Then in GitHub open `scraper/config.py`, click the pencil icon,
   paste the URLs into `CARGURUS_SEARCH_URLS` (each in quotes, followed by a comma), and commit.
7. **Run it once now:** **Actions** tab → enable workflows if asked → **Scrape Ferrari listings →
   Run workflow → Run workflow.** It takes 20–40 minutes. A green check means success.
8. **On your iPhone:** open your URL in Safari, tap **Share → Add to Home Screen**.

After that it runs on its own at 11am and 5pm Central (GitHub can start scheduled jobs a
few minutes late).

## If a site returns nothing

Open the latest run in the **Actions** tab and read the log; each site prints how many records it found.

- **Cars.com or CarGurus finds 0 records:** these sites sometimes block cloud servers. Use Plan B below.
- **Cars.com finds cars but not your models:** the model slugs in `CARSCOM_MODEL_SLUGS` may be off.
  Search on cars.com, filter to the model, and copy the `models[]=` value from the URL.
- **Ferrari Approved finds 0:** do a filtered search on the site and paste that URL into
  `FERRARI_SEARCH_URLS`.
- **Colors or city show "Unknown":** these are filled from detail pages, up to 120 per run, and
  cached, so gaps close over the first few runs.

## Plan B: run from your Mac

Home internet is blocked far less often than cloud servers. The website stays on GitHub.

```bash
# one-time setup (Terminal)
brew install python git gh
gh auth login
git clone https://github.com/YOUR-USERNAME/ferrari-tracker.git
cd ferrari-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python -m playwright install chromium
./run_on_mac.sh --force        # test run
```

Schedule it (assumes your Mac's clock is on Central time): run `crontab -e` and add

```
0 11,17 * * * /Users/YOU/ferrari-tracker/run_on_mac.sh >> /Users/YOU/ferrari-tracker/cron.log 2>&1
```

The Mac must be awake at those times. Then disable the GitHub schedule
(**Actions → Scrape Ferrari listings → ⋯ → Disable workflow**) so the two don't overlap.

## Files

| Path | What it does |
|---|---|
| `scraper/config.py` | Models, colors, search URLs, limits (edit this) |
| `scraper/scrape.py` | Browser automation for the three sites |
| `scraper/normalize.py` | Model grouping, red filter, city parsing |
| `scraper/analyze.py` | Builds `docs/data.json` for the dashboard |
| `run.py` | Runs one snapshot; `--force` to run now, `--analyze-only` to rebuild the page |
| `data/history.csv` | Every snapshot ever taken (open it in Excel anytime) |
| `docs/index.html` | The dashboard |

Please respect each site's Terms of Service; keep the schedule and page limits modest.
