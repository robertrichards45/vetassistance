from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1] / 'content' / 'articles'


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9\s-]', '', value)
    value = re.sub(r'\s+', '-', value)
    value = re.sub(r'-+', '-', value)
    return value.strip('-') or 'article'


def list_articles() -> list[dict]:
    if not ROOT.exists():
        return []
    items = []
    for path in sorted(ROOT.glob('*.md')):
        item = load_article(path.stem)
        if item:
            items.append(item)
    return items


def load_article(slug: str) -> dict | None:
    path = ROOT / f"{slug}.md"
    if not path.exists():
        return None
    updated_at = datetime.utcfromtimestamp(path.stat().st_mtime)
    raw = path.read_text(encoding='utf-8')
    lines = raw.splitlines()
    title = ''
    summary = ''
    body_lines = []
    for i, line in enumerate(lines):
        if i == 0 and line.startswith('TITLE:'):
            title = line.replace('TITLE:', '', 1).strip()
            continue
        if i == 1 and line.startswith('SUMMARY:'):
            summary = line.replace('SUMMARY:', '', 1).strip()
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    return {
        'slug': slug,
        'title': title,
        'summary': summary,
        'body': body,
        'updated_at': updated_at,
    }


def save_article(slug: str, title: str, summary: str, body: str) -> str:
    ROOT.mkdir(parents=True, exist_ok=True)
    safe_slug = _slugify(slug)
    content = "\n".join([
        f"TITLE: {title.strip()}",
        f"SUMMARY: {summary.strip()}",
        "",
        body.strip(),
        "",
    ])
    (ROOT / f"{safe_slug}.md").write_text(content, encoding='utf-8')
    return safe_slug


def delete_article(slug: str) -> None:
    path = ROOT / f"{slug}.md"
    if path.exists():
        path.unlink()
