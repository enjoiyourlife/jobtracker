# jobtracker

A job search tool that polls companies' own ATS boards directly (Greenhouse, Lever, Ashby) instead of scraping aggregator sites, ranks postings against criteria you define, and tracks your applications through to an offer — all running locally on your own machine.

No account, no server, no subscription. Your search criteria, your database, your machine.

## Why direct-from-ATS

Indeed, LinkedIn, and similar aggregators are copies of listings, often stale, sometimes reposted by staffing agencies who don't actually have the req. Greenhouse, Lever, and Ashby are the employer's own posting — best signal-to-noise, earliest visibility, no ghost jobs. The tradeoff is you have to know which companies to poll; jobtracker ships with ~100 pre-resolved companies and a built-in resolver to add more by name.

## Install

**Download** the latest `.dmg` (macOS) or `.exe` (Windows) from [Releases](../../releases) — no Python required. On first launch it seeds a starter configuration and opens as a normal desktop app.

> macOS note: since this isn't signed with a paid Apple Developer certificate, Gatekeeper will flag it as from an unidentified developer on first launch. Right-click the app → **Open** to bypass that once.

**Or run from source**, if you want to hack on it:

```bash
git clone https://github.com/enjoiyourlife/jobtracker.git
cd jobtracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
jobtracker gui
```

## What it does

- **Polls** Greenhouse, Lever, and Ashby boards on a schedule (or on demand from the GUI), storing every posting it's ever seen with idempotent upserts — reruns never duplicate, and a posting silently missing from one run isn't assumed closed unless the run actually succeeded.
- **Filters and ranks** postings against criteria you control: job titles to match/exclude, target cities with priority weighting, remote/hybrid preference, experience level, minimum score to appear at all. Every dimension is independent and explains its own contribution to a posting's score — "why is this ranked here" always has an answer.
- **Tracks applications** through a pipeline (queued → submitted → screening → interview → offer/rejected) with validated status transitions and automatic "ghosted" detection for submissions with no response in 21+ days.
- **Never auto-submits anything.** Every ATS has its own application form, uploads, and anti-bot checks — "Apply" opens the real posting in your actual browser (with your saved passwords and autofill intact), you fill it out and click submit yourself.

Two interfaces, same underlying data:

- **GUI** (`jobtracker gui`, or the packaged app) — a queue you can work through with a couple of clicks, a status dashboard, and a Settings page for everything above in plain language, no YAML editing required.
- **CLI** (`jobtracker queue` / `apply` / `mark` / `status` / `browse` / ...) — for running headless, scripting, or an arrow-key interactive queue in the terminal.

## Configuration

Everything lives in one `config.yaml`, editable by hand or through the GUI's Settings page — both read and write the same file, so neither one is the "real" way to do it. See the comments in [`config.default.yaml`](src/jobtracker/config.default.yaml) for the full schema.

## Optional: AI-assisted answer tailoring

`jobtracker tailor` can retarget your own pre-written application answers to a specific posting using the Claude API — never inventing new claims or experience, only adapting wording and emphasis to what's already true. This needs an Anthropic API key (`ANTHROPIC_API_KEY` in a `.env` file) and is entirely optional; nothing else in the tool touches an LLM.

## Development

```bash
pip install -e .
python -m pytest      # full test suite
```

Building the desktop app locally:

```bash
pip install pyinstaller pillow
pyinstaller build_assets/jobtracker.spec
```

## License

MIT — see [LICENSE](LICENSE).
