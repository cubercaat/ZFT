"""Savollar bazasi: data/questions.json (saytdagi 1276 ta savolni shu formatda eksport qiling)."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Question:
    id: str
    question: str
    options: list[str]
    correct: int
    topic: str = "umumiy"
    explanation: str = ""
    image: str | None = None   # rasm fayli yo'li (data/images/...) yoki None

    @property
    def correct_text(self) -> str:
        return self.options[self.correct]


class QuestionBank:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.questions: list[Question] = []
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.questions = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw["questions"] if isinstance(raw, dict) else raw
        qs = []
        for i, it in enumerate(items):
            opts = [str(o) for o in it["options"]]
            correct = int(it["correct"])
            if not 2 <= len(opts) <= 10 or not 0 <= correct < len(opts):
                raise ValueError(f"Savol #{i} ({it.get('id')}) noto'g'ri: variantlar yoki javob indeksi")
            image = it.get("image")
            if image and not Path(image).is_absolute():
                image = str((self.path.parent / image).resolve())
            qs.append(Question(
                id=str(it.get("id", f"q{i + 1:04d}")), question=str(it["question"]).strip(),
                options=opts, correct=correct, topic=str(it.get("topic", "umumiy")),
                explanation=str(it.get("explanation", "")), image=image,
            ))
        self.questions = qs

    def __len__(self) -> int:
        return len(self.questions)

    def by_id(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)

    def topics(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for q in self.questions:
            out[q.topic] = out.get(q.topic, 0) + 1
        return out

    def pick(self, exclude: set[str] | None = None, topic: str | None = None,
             rng: random.Random | None = None, video_friendly: bool = False) -> Question:
        if not self.questions:
            raise RuntimeError("Savollar bazasi bo'sh: data/questions.json ni to'ldiring")
        rng = rng or random.Random()
        exclude = exclude or set()
        pool = [q for q in self.questions if q.id not in exclude]
        if topic:
            narrowed = [q for q in pool if q.topic == topic]
            pool = narrowed or pool
        if video_friendly:  # qisqa variantlar videoda yaxshi o'qiladi
            short = [q for q in pool if len(q.options) <= 4 and all(len(o) <= 70 for o in q.options)]
            pool = short or pool
        pool = pool or self.questions
        return rng.choice(pool)
