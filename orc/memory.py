import os
import json
import re
from typing import Optional, List
from orc.logger import get_logger

logger = get_logger('memory')

BASE = os.path.join(os.path.dirname(__file__), '..', 'data')
STM_FILE = os.path.join(BASE, 'stm.txt')
LTM_FILE = os.path.join(BASE, 'ltm.json')

STM_CAP = 80
STM_COMPRESS_TRIGGER = 60


def _word_count(text):
    return len(text.split())


def _ensure_files():
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(STM_FILE):
        open(STM_FILE, 'w').close()
    if not os.path.exists(LTM_FILE):
        with open(LTM_FILE, 'w') as f:
            json.dump([], f)


# ── Core STM ────────────────────────────────────────────────────────────

def load_stm() -> str:
    _ensure_files()
    with open(STM_FILE, 'r') as f:
        return f.read().strip()


def save_stm(content):
    _ensure_files()
    with open(STM_FILE, 'w') as f:
        f.write(content.strip())


def append_stm(phrase) -> Optional[int]:
    phrase = phrase.strip()
    if not phrase:
        return None
    current = load_stm()
    updated = (current + '. ' + phrase).strip('. ')
    save_stm(updated)
    return _word_count(updated)


def needs_compression() -> bool:
    stm = load_stm()
    return _word_count(stm) >= STM_COMPRESS_TRIGGER


def compress_stm(providers):
    stm = load_stm()
    if not stm:
        return None
    from orc.providers import ChatMessage
    messages = [
        ChatMessage(role="system", content="You are a memory compressor. Given a list of memory phrases, compress them into the most essential facts only. Output must be under 15 words total. Plain text only, no bullets, no punctuation except periods."),
        ChatMessage(role="user", content=f"Compress this:\n{stm}")
    ]
    try:
        response = providers.chat(messages, max_tokens=60)
        compressed = response.content.strip()
        save_stm(compressed)
        logger.info(f"STM compressed: {_word_count(stm)} -> {_word_count(compressed)} words")
        return compressed
    except Exception as e:
        logger.error(f"STM compression failed: {e}")
        return None


# ── Core LTM ────────────────────────────────────────────────────────────

def load_ltm() -> List[dict]:
    _ensure_files()
    with open(LTM_FILE, 'r') as f:
        return json.load(f)


def save_ltm_entry(note):
    note = note.strip()
    if ':' in note:
        tag, content = note.split(':', 1)
        tag = tag.strip().lower()
        content = content.strip()
    else:
        tag = 'general'
        content = note
    entries = load_ltm()
    entries.append({"tag": tag, "note": content})
    _ensure_files()
    with open(LTM_FILE, 'w') as f:
        json.dump(entries, f, indent=2)


def fetch_ltm(topic) -> Optional[str]:
    topic = topic.strip().lower()
    keywords = set(topic.split())
    entries = load_ltm()
    matched = []
    for entry in entries:
        entry_text = (entry.get('tag', '') + ' ' + entry.get('note', '')).lower()
        if any(kw in entry_text for kw in keywords):
            matched.append(entry)
    if not matched:
        return None
    return '\n'.join(f"[{e['tag']}] {e['note']}" for e in matched)


# ── Paging / Indexing ────────────────────────────────────────────────────

def list_tags() -> List[str]:
    entries = load_ltm()
    tags = set()
    for e in entries:
        if e.get('tag'):
            tags.add(e['tag'])
    return sorted(tags)


def count_entries() -> dict:
    entries = load_ltm()
    counts = {}
    for e in entries:
        tag = e.get('tag', 'general')
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def search_ltm(query: str, limit: int = 5) -> Optional[str]:
    query_lower = query.lower()
    keywords = set(query_lower.split())
    entries = load_ltm()
    scored = []
    for e in entries:
        text = (e.get('tag', '') + ' ' + e.get('note', '')).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]
    if not top:
        return None
    return '\n'.join(f"[{e['tag']}] {e['note']}" for _, e in top)


# ── Legacy signal parser (deprecated, use tag_dispatch instead) ──────────

def parse_signals(reply):
    signals = {
        'fetch_ltm': None,
        'save_ltm': None,
        'save_stm': None,
        'run_cmd': None,
        'file_op': None,
        'ui': None,
    }
    lines = reply.split('\n')
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('FETCH_LTM:'):
            signals['fetch_ltm'] = stripped[len('FETCH_LTM:'):].strip()
        elif stripped.startswith('SAVE_LTM:'):
            signals['save_ltm'] = stripped[len('SAVE_LTM:'):].strip()
            save_ltm_entry(signals['save_ltm'])
        elif stripped.startswith('SAVE_STM:'):
            signals['save_stm'] = stripped[len('SAVE_STM:'):].strip()
            append_stm(signals['save_stm'])
        elif stripped.startswith('RUN_CMD:'):
            signals['run_cmd'] = stripped[len('RUN_CMD:'):].strip()
        elif stripped.startswith('FILE_OP:'):
            signals['file_op'] = stripped[len('FILE_OP:'):].strip()
        elif stripped.startswith('UI:'):
            signals['ui'] = stripped[len('UI:'):].strip()
        else:
            clean_lines.append(line)
    clean_reply = '\n'.join(clean_lines).strip()
    return clean_reply, signals
