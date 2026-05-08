"""
Step 2: For each unique file in inventory.json, extract plain text.
Output: profile_build/staging/<hash>.txt — keyed by content hash so dedup is automatic.
       profile_build/output/extracted.json — manifest of {hash → {path, class, text_path, char_count}}
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

BUILD = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build")
OUT = BUILD / "output"
STAGING = BUILD / "staging"
STAGING.mkdir(exist_ok=True, parents=True)

inventory = json.loads((OUT / "inventory.json").read_text())


def extract_pdf(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-nopgbrk", str(path), "-"],
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout
    except Exception as e:
        return f"[EXTRACT ERROR: {e}]"


def extract_docx(path: Path) -> str:
    """Extract text from .docx by reading word/document.xml and stripping tags."""
    try:
        result = subprocess.run(
            ["unzip", "-p", str(path), "word/document.xml"],
            capture_output=True, text=False, timeout=30,
        )
        xml = result.stdout.decode("utf-8", errors="replace")
        # Replace paragraph + line-break tags with newlines BEFORE stripping
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:br[^/]*/>", "\n", xml)
        xml = re.sub(r"<w:tab[^/]*/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        # Decode common XML entities
        text = (text.replace("&amp;", "&")
                    .replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&apos;", "'"))
        # Collapse 3+ blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception as e:
        return f"[EXTRACT ERROR: {e}]"


def extract_html(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip script/style first
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
    except Exception as e:
        return f"[EXTRACT ERROR: {e}]"


def extract(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext == ".docx":
        return extract_docx(path)
    if ext in {".md", ".txt"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"[EXTRACT ERROR: {e}]"
    if ext == ".html":
        return extract_html(path)
    return ""


def main():
    extracted: dict = {}
    seen_hashes: set[str] = set()
    skipped_classes = {"job_search_report", "simpaisa_tech_doc"}  # keep these out of profile

    for entry in inventory:
        h = entry["hash"]
        if h in seen_hashes:
            continue
        if entry["class"] in skipped_classes:
            continue
        if h == "ERR":
            continue
        seen_hashes.add(h)

        path = Path(entry["path"])
        text = extract(path)
        if not text or text.startswith("[EXTRACT ERROR"):
            print(f"⚠️  {entry['class']:30s} {path.name[:60]:60s} {text[:60]}")
            continue
        text_path = STAGING / f"{h}.txt"
        text_path.write_text(text)

        extracted[h] = {
            "path": str(path),
            "name": path.name,
            "class": entry["class"],
            "text_path": str(text_path),
            "char_count": len(text),
            "size": entry["size"],
            "mtime": entry["mtime"],
        }

    print(f"\n✅ Extracted {len(extracted)} unique files")
    by_cls: dict[str, int] = {}
    for v in extracted.values():
        by_cls[v["class"]] = by_cls.get(v["class"], 0) + 1
    for c, n in sorted(by_cls.items(), key=lambda x: -x[1]):
        print(f"   {n:3d}  {c}")

    (OUT / "extracted.json").write_text(json.dumps(extracted, indent=2, default=str))
    print(f"\n✅ Wrote {OUT / 'extracted.json'}")


if __name__ == "__main__":
    main()
