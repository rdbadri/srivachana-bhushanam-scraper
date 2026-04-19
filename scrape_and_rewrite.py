#!/usr/bin/env python3
"""
Śrīvachana Bhūṣaṇam Scraper & Rewriter

Scrapes all 463 sūthrams from granthams.koyil.org/srivachana-bhushanam-english/,
rewrites each section via claude_agent_sdk (uses your enterprise Claude auth),
and outputs:
  - srivachana_bhushanam.csv  (pipe-delimited, 5 columns)
  - suthrams/suthram-NNN.md   (individual markdown files)

Run with the backend venv:
  ../backend/venv/bin/python scrape_and_rewrite.py --test 2
  ../backend/venv/bin/python scrape_and_rewrite.py
  ../backend/venv/bin/python scrape_and_rewrite.py --resume
"""

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

# ─── Configuration ─────────────────────────────────────────────────────────────
INDEX_URL = "https://granthams.koyil.org/srivachana-bhushanam-english/"
MODEL = "claude-sonnet-4-6"
INTER_SECTION_DELAY = 2.0       # seconds between API calls within one sūthram
INTER_SUTHRAM_DELAY = 3.0       # seconds between sūthrams
RATE_LIMIT_PAUSE = 90.0         # seconds to wait on a rate-limit error

BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "srivachana_bhushanam.csv"
PROGRESS_FILE = BASE_DIR / "progress.json"
SUTHRAMS_DIR = BASE_DIR / "suthrams"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SriBhushanam-Scraper/1.0)"}

SECTION_KEYS = ["avatharikai", "suthram_text", "simple_explanation", "vyakyaham"]
SECTION_LABELS = {
    "avatharikai":        "avathārikai (Introduction)",
    "suthram_text":       "Sūthram Statement",
    "simple_explanation": "Simple Explanation",
    "vyakyaham":          "vyākyānam (Commentary)",
}

SYSTEM_PROMPT = (
    "You are a helpful AI assistant specializing in Viśiṣṭādvaita philosophy and "
    "Śrī Vaiṣṇavism, acting as a theological editor grounded in Śrī Vaiṣṇava Sampradāya tradition."
)

COMBINED_TEMPLATE = """\
Rewrite all four sections of Sūthram {num} from Śrīvachana Bhūṣaṇam. \
Return ONLY a valid JSON object with exactly these four keys: \
"avatharikai", "suthram_text", "simple_explanation", "vyakyaham". \
No markdown fences, no commentary before or after — just the raw JSON.

RULES (apply to all sections except suthram_text):

1. **Style**: Formal, reverential Śrī Vaiṣṇava Sampradāya English. Clear and accessible — avoid unnecessarily complex words.
2. **No Summarization or Invention**: Retain ALL original information. Expand only what is explicitly stated. Do NOT introduce theological concepts absent from the source.
3. **Theological Accuracy**: Accurate per Viśiṣṭādvaita philosophy.
4. **Structure**: If a word is followed by its meaning, retain that word-meaning structure.
5. **Terminology & Transliteration** (all sections):
   - Always refer to Srīman Nārāyaṇa. Retain names like Namperumāḷ, Āzhvār, etc.
   - Convert ALL source-language words to Roman IAST.
   - Use 'd' instead of 'ṭ' (e.g., 'adiyēn' not 'aṭiyēn').
   - Use 'zh' instead of 'ḻ' (e.g., 'āzhvār' not 'āḻvār').
   - EXCEPTION — Piraṭṭi: always keep 'Piraṭṭi' with 'ṭ'. Never write 'Piraddi'.
   - Replace 'centum'→'decade', 'decad'→'chapter', 'decads'→'chapters'.
6. **Formatting**: Rich Markdown (bold, italics, paragraph breaks). Multiple paragraphs.
7. **Cadence**: Vary sentence length and structure. Avoid robotic repetition.

SPECIAL RULE for "suthram_text" ONLY:
- Convert the aphorism to IAST and wrap in italics.
- ONE line only. No explanation, no elaboration, no commentary whatsoever.

SOURCE SECTIONS:

### avatharikai
{avatharikai}

### suthram_text
{suthram_text}

### simple_explanation
{simple_explanation}

### vyakyaham
{vyakyaham}"""


# ─── Web Scraping ───────────────────────────────────────────────────────────────
def fetch_page(url: str, retries: int = 4) -> BeautifulSoup:
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            last_exc = e
            if attempt == retries - 1:
                break
            wait = 5 * (attempt + 1)
            print(f"  [web] Retry {attempt + 1} ({wait}s): {e}", flush=True)
            time.sleep(wait)
    raise last_exc


def fetch_all_suthram_urls(index_url: str) -> list[tuple[int, str]]:
    print("Fetching sūthram index…", flush=True)
    soup = fetch_page(index_url)
    seen: set[int] = set()
    result: list[tuple[int, str]] = []
    pat = re.compile(r"srivachana-bhushanam-suthram-(\d+)-english", re.I)
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/") + "/"
        m = pat.search(href)
        if m:
            num = int(m.group(1))
            if num not in seen:
                seen.add(num)
                result.append((num, href))
    result.sort()
    print(f"  Found {len(result)} sūthrams.", flush=True)
    return result


SKIP_PAT = re.compile(
    r"←|→|Full Series|Previous|Next|Visits:|Share this|Privacy"
    r"|adiy[eē]n\b|koyil\.org|archived in",
    re.I,
)
SECTION_PATS = [
    (re.compile(r"\bavath[Aa]r[Ii]kai\b|\bIntroduction\b", re.I), "avatharikai"),
    (re.compile(r"^\s*s[Uu]thram\s+\d+\s*$"), "suthram_text"),
    (re.compile(r"\bSimple\s+Explanation\b", re.I), "simple_explanation"),
    (re.compile(r"\bvy[Aa]ky[Aa][Nn]am\b|\bCommentary\b", re.I), "vyakyaham"),
]


def extract_sections(soup: BeautifulSoup) -> dict[str, str]:
    """Extract the four named sections from a sūthram page's entry-content."""
    content = soup.find("div", class_="entry-content")
    if not content:
        return {k: "" for k in SECTION_KEYS}

    buckets: dict[str, list[str]] = {k: [] for k in SECTION_KEYS}
    current: str | None = None

    for tag in content.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
        text = tag.get_text(" ", strip=True)
        if not text or SKIP_PAT.search(text):
            continue

        if len(text) < 120:
            matched = None
            for pat, name in SECTION_PATS:
                if pat.search(text):
                    matched = name
                    break
            if matched:
                current = matched
                continue

        if current:
            buckets[current].append(text)

    return {k: "\n\n".join(v) for k, v in buckets.items()}


FOOTER_PAT = re.compile(
    r"\n*[-—–\s]*\n(adiy[eē]n\b|koyil\.org|archived in|pram[eēē]yam).*$",
    re.I | re.S,
)


def strip_footer(text: str) -> str:
    """Cut everything from the site footer block onwards."""
    return FOOTER_PAT.sub("", text).rstrip()


# ─── Claude Rewrite via claude_agent_sdk ──────────────────────────────────────
async def rewrite_suthram_async(
    raw: dict[str, str],
    suthram_no: int,
    retries: int = 4,
) -> dict[str, str]:
    prompt = COMBINED_TEMPLATE.format(
        num=suthram_no,
        avatharikai=raw.get("avatharikai", "") or "(not present)",
        suthram_text=raw.get("suthram_text", "") or "(not present)",
        simple_explanation=raw.get("simple_explanation", "") or "(not present)",
        vyakyaham=raw.get("vyakyaham", "") or "(not present)",
    )

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[],
        max_turns=1,
    )

    for attempt in range(retries):
        try:
            result_text = ""
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result_text = getattr(message, "result", "") or ""

            # Strip any markdown fences Claude might add despite instructions
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", result_text.strip())
            parsed = json.loads(cleaned)
            return {k: strip_footer(parsed.get(k, "")) for k in SECTION_KEYS}

        except json.JSONDecodeError:
            print(f"  [warn] JSON parse failed (attempt {attempt+1}), retrying…", flush=True)
            await asyncio.sleep(10)
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err or "overloaded" in err:
                wait = RATE_LIMIT_PAUSE * (attempt + 1)
                print(f"  [rate] Rate limit — waiting {wait:.0f}s", flush=True)
                await asyncio.sleep(wait)
            elif attempt < retries - 1:
                print(f"  [retry] {e}", flush=True)
                await asyncio.sleep(10)
            else:
                print(f"  [error] Failed after {retries} attempts: {e}", flush=True)
                return {k: f"[Rewrite failed: {e}]" for k in SECTION_KEYS}

    return {k: "[Rewrite failed: max retries]" for k in SECTION_KEYS}


# ─── Progress & Output ──────────────────────────────────────────────────────────
def load_progress() -> set[int]:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("completed", []))
    return set()


def save_progress(completed: set[int]):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"completed": sorted(completed), "updated": datetime.now().isoformat()},
            f,
            indent=2,
        )


def init_csv():
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_ALL)
            w.writerow(["sUthram", "avathArikai", "sUthram Text", "Simple Explanation", "vyAkyAnam"])


def append_csv(num: int, sections: dict[str, str]):
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_ALL)
        w.writerow([
            num,
            sections.get("avatharikai", ""),
            sections.get("suthram_text", ""),
            sections.get("simple_explanation", ""),
            sections.get("vyakyaham", ""),
        ])


def write_markdown(num: int, sections: dict[str, str]):
    path = SUTHRAMS_DIR / f"suthram-{num:03d}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Sūthram {num}\n\n")
        for key, heading in [
            ("avatharikai", "avathārikai"),
            ("suthram_text", "Sūthram"),
            ("simple_explanation", "Simple Explanation"),
            ("vyakyaham", "vyākyānam"),
        ]:
            content = sections.get(key, "").strip()
            if content:
                f.write(f"## {heading}\n\n{content}\n\n")


# ─── Core Processing ────────────────────────────────────────────────────────────
async def process_suthram(num: int, url: str) -> dict[str, str] | None:
    soup = fetch_page(url)
    raw = extract_sections(soup)

    if not any(v.strip() for v in raw.values()):
        print(f"  [warn] No content extracted for sūthram {num}", flush=True)
        return None

    print(f"    Rewriting all sections in one call…", flush=True)
    return await rewrite_suthram_async(raw, num)


# ─── Main ───────────────────────────────────────────────────────────────────────
async def run(args):
    urls = fetch_all_suthram_urls(INDEX_URL)
    if args.test:
        urls = urls[: args.test]
        print(f"Test mode: first {args.test} sūthrams only.\n", flush=True)

    completed = load_progress() if args.resume else set()
    if args.resume and completed:
        print(f"Resuming — {len(completed)} already done.\n", flush=True)

    init_csv()
    SUTHRAMS_DIR.mkdir(exist_ok=True)

    total = len(urls)
    success = 0

    for i, (num, url) in enumerate(urls, 1):
        if num in completed:
            print(f"[{i}/{total}] Sūthram {num} — skip (done)", flush=True)
            continue

        print(f"\n[{i}/{total}] Sūthram {num}", flush=True)
        result = await process_suthram(num, url)
        if result is not None:
            append_csv(num, result)
            write_markdown(num, result)
            completed.add(num)
            save_progress(completed)
            success += 1
            print(f"  ✓ Saved.", flush=True)

        if i < total:
            await asyncio.sleep(INTER_SUTHRAM_DELAY)

    print(f"\n{'─'*60}", flush=True)
    print(f"Complete: {success}/{total} sūthrams processed.", flush=True)
    print(f"CSV:      {CSV_FILE}", flush=True)
    print(f"Markdown: {SUTHRAMS_DIR}/", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Śrīvachana Bhūṣaṇam scraper & rewriter")
    parser.add_argument("--test", type=int, metavar="N",
                        help="Process only the first N sūthrams")
    parser.add_argument("--resume", action="store_true",
                        help="Skip sūthrams already in progress.json")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
