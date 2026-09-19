#!/usr/bin/env python3
"""
Build devanagari.json for DAS.

BG source:
  https://raw.githubusercontent.com/gita/gita/main/data/verse.json
  Repository: https://github.com/gita/gita (Unlicense)

SB source:
  https://github.com/gita/Datasets/tree/main/srimad-bhagavatam
  The script uses GitHub's public contents API to enumerate chapter JSON files and
  extracts ONLY the ancient Sanskrit Devanagari field, not translations/purports.

Output:
  devanagari.json

Structure:
{
  "meta": {...},
  "BG": {"1.1": {"devanagari": "..."} },
  "SB": {"1.1.1": {"devanagari": "..."} }
}
"""

import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

UA = "bot_DAS/1.0"
OUT = Path("devanagari.json")

BG_URL = "https://raw.githubusercontent.com/gita/gita/main/data/verse.json"
GH_API = "https://api.github.com/repos/gita/Datasets/contents"
SB_ROOT = "srimad-bhagavatam"

def get_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def clean_devanagari(text):
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def build_bg():
    rows = get_json(BG_URL)
    result = {}
    for row in rows:
        ch = int(row["chapter_number"])
        vs = int(row["verse_number"])
        result[f"{ch}.{vs}"] = {
            "devanagari": clean_devanagari(row["text"])
        }
    return result

def list_dir(path):
    url = f"{GH_API}/{quote(path, safe='/')}"
    return get_json(url)

def walk_json_files(path):
    for item in list_dir(path):
        typ = item.get("type")
        if typ == "dir":
            yield from walk_json_files(item["path"])
        elif typ == "file" and item["name"].lower().endswith(".json"):
            yield item

def extract_canto_chapter(path):
    # Expected paths contain "Canto N" and chapterN.json (or close variants).
    m_canto = re.search(r"Canto\s*[-_ ]*(\d+)", path, re.I)
    m_ch = re.search(r"chapter\s*[-_ ]*(\d+)\.json$", path, re.I)
    if not (m_canto and m_ch):
        return None
    return int(m_canto.group(1)), int(m_ch.group(1))

def build_sb():
    result = {}
    files = list(walk_json_files(SB_ROOT))
    print(f"SB JSON files found: {len(files)}")

    for i, item in enumerate(files, 1):
        cc = extract_canto_chapter(item["path"])
        if not cc:
            continue
        canto, chapter = cc
        download_url = item.get("download_url")
        if not download_url:
            continue

        try:
            rows = get_json(download_url)
        except Exception as e:
            print(f"Skip {item['path']}: {e}")
            continue

        if not isinstance(rows, list):
            continue

        for row in rows:
            verse_raw = str(row.get("verse", "")).strip()
            devanagari = clean_devanagari(row.get("devanagari", ""))
            if not verse_raw or not devanagari:
                continue

            # Usually "1"; tolerate "1-2" / "1,2" by preserving identifier.
            verse_id = re.sub(r"\s+", "", verse_raw)
            key = f"{canto}.{chapter}.{verse_id}"
            result[key] = {"devanagari": devanagari}

        if i % 20 == 0:
            print(f"Processed {i}/{len(files)} files; SB verses: {len(result)}")
        time.sleep(0.05)

    return result

def validate(data):
    required_bg = ["1.1", "2.47", "9.34", "18.65", "18.78"]
    required_sb = ["1.1.1", "10.14.8"]

    missing_bg = [x for x in required_bg if x not in data["BG"]]
    missing_sb = [x for x in required_sb if x not in data["SB"]]

    if missing_bg:
        raise RuntimeError(f"BG validation failed, missing: {missing_bg}")
    if missing_sb:
        raise RuntimeError(f"SB validation failed, missing: {missing_sb}")

def main():
    print("Downloading Bhagavad-gita...")
    bg = build_bg()
    print(f"BG verses: {len(bg)}")

    print("Downloading Srimad-Bhagavatam...")
    sb = build_sb()
    print(f"SB verses: {len(sb)}")

    data = {
        "meta": {
            "format_version": 1,
            "purpose": "Devanagari lookup for DAS / sridhar.guru verse citations",
            "keys": {
                "BG": "chapter.verse",
                "SB": "canto.chapter.verse"
            },
            "sources": {
                "BG": "https://github.com/gita/gita",
                "SB": "https://github.com/gita/Datasets"
            }
        },
        "BG": bg,
        "SB": sb
    }

    validate(data)

    OUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Created: {OUT.resolve()}")
    print(f"Total entries: {len(bg) + len(sb)}")

if __name__ == "__main__":
    main()
