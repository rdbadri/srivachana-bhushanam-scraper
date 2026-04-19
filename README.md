# Śrīvachana Bhūṣaṇam Scraper & Rewriter

Scrapes all 463 sūthrams from [granthams.koyil.org](https://granthams.koyil.org/srivachana-bhushanam-english/), rewrites each section in formal Śrī Vaiṣṇava Sampradāya English using Claude AI, and saves output as:

- `srivachana_bhushanam.csv` — pipe-delimited, 5 columns
- `suthrams/suthram-NNN.md` — individual Markdown file per sūthram

**CSV columns:** `sUthram | avathārikai | sUthram Text | Simple Explanation | vyākyānam`

---

## Setup

### Requirements
- Python 3.10+
- Claude Code CLI installed and logged in (handles authentication — no API key needed)

### Install

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/srivachana-bhushanam-scraper.git
cd srivachana-bhushanam-scraper

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Mac/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Usage

```bash
# Test with first 2 sūthrams (recommended first run)
python scrape_and_rewrite.py --test 2

# Full run — all 463 sūthrams
python scrape_and_rewrite.py

# Resume after interruption (skips already-completed sūthrams)
python scrape_and_rewrite.py --resume
```

---

## How it works

1. Fetches the index page to discover all 463 sūthram URLs
2. For each sūthram, scrapes 4 sections: avathārikai, sūthram statement, simple explanation, vyākyānam
3. Rewrites each section using `claude-sonnet-4-6` via `claude_agent_sdk`
4. Saves to CSV (pipe-delimited) and individual Markdown files
5. Tracks progress in `progress.json` so interrupted runs can resume

---

## Rewriting rules applied

- Formal, reverential **Śrī Vaiṣṇava Sampradāya English**
- Full IAST transliteration (with `d` for `ṭ`, `zh` for `ḻ`; `Piraṭṭi` preserved as-is)
- Rich Markdown formatting (bold, italics, multiple paragraphs)
- No content omitted — all original information retained and elaborated
- Terminology: centum→decade, decad→chapter, decads→chapters

---

## Rate limiting

The script self-throttles to ~60% of the Claude API rate limit. It will pause automatically if limits are hit. A full run of 463 sūthrams takes approximately 45–90 minutes depending on your plan tier.
