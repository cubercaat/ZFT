"""Savollarni import qilish.

CSV ustunlari (birinchi qator — sarlavha):
  id, topic, question, option1, option2, option3, option4, ..., correct, explanation, image
`correct` — to'g'ri variant RAQAMI, 1 dan boshlab (1 = option1). image ixtiyoriy.
JSON bo'lsa: [{"id","topic","question","options":[...],"correct":0-dan,"explanation","image"}].
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def import_questions(src: Path, dst: Path) -> int:
    if src.suffix.lower() == ".json":
        raw = json.loads(src.read_text(encoding="utf-8"))
        items = raw["questions"] if isinstance(raw, dict) else raw
    else:
        items = []
        with src.open(encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            for i, row in enumerate(csv.DictReader(fh, dialect=dialect), start=1):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                opts = [row[k] for k in sorted((k for k in row if k.startswith("option")),
                                               key=lambda k: int(k[6:] or 0)) if row[k]]
                if not row.get("question") or len(opts) < 2:
                    continue
                correct = int(row.get("correct") or 1) - 1
                if not 0 <= correct < len(opts):
                    raise ValueError(f"{i}-qator: correct={row.get('correct')} variantlar soniga mos emas")
                items.append({"id": row.get("id") or f"q{i:04d}", "topic": row.get("topic") or "umumiy",
                              "question": row["question"], "options": opts, "correct": correct,
                              "explanation": row.get("explanation", ""), "image": row.get("image") or None})
    ids = [str(it.get("id")) for it in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Takrorlangan id bor")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.with_suffix(".json.bak").write_text(dst.read_text(encoding="utf-8"), encoding="utf-8")
    dst.write_text(json.dumps({"questions": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    from .quiz import QuestionBank
    QuestionBank(dst)  # tekshiruv: format xato bo'lsa shu yerda to'xtaydi
    return len(items)
