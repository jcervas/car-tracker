# Moses Car Tracker

Tracks the Moses Toyota of Morgantown inventory and each car's price over time.

## On GitHub
A GitHub Action (`.github/workflows/sync.yml`) pulls the dealer's inventory feed
every hour from 8 a.m. to 10 p.m. Eastern. It commits `docs/data/inventory.json`
only when something changed (new car, price change, car sold/removed), and
GitHub Pages serves the site from `docs/`.

To sync right away: Actions tab → "Sync inventory" → "Run workflow."

## Locally
Run in terminal:
`python3 ~/Downloads/car-tracker/server.py`

Then load in browser:
http://localhost:8777

Locally, every page load also pulls the live feed. Run `git pull` first so
your local copy has the latest hourly data from GitHub.
