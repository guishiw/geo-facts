"""`--corpus` 既有稿件库与近似重复的相似度。

目录格式（只看目录第一层，不递归）：

    CORPUS/
    ├── a.md            既有稿件（.md / .markdown / .txt / .html / .htm）
    ├── a.meta.json     该稿件的 meta：至少含 brand_entity_id 与 topic（同 --meta 的格式）
    ├── b.html
    └── b.meta.json

稿件与 meta 按文件名主干对应（`a.md` ↔ `a.meta.json`）；同一主干有多份稿件时无法对应，按输入错误处理。
没有 meta、或 meta 缺 brand_entity_id / topic 的稿件不参与比较，只计数。

相似度：取各稿件的可见正文，NFKC + 小写后只保留字母、数字与汉字，切成长度 SHINGLE_SIZE 的
字符片段（shingle），两篇的片段集合算 Jaccard 系数；≥ NEAR_DUPLICATE_THRESHOLD 判为近似重复。
任一方片段数少于 MIN_SHINGLES 时不比较（太短的文本相似度不稳定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from geo_facts.errors import InputError
from geo_facts.gate.document import FORMATS, VISIBLE, Draft, format_of, parse_draft, read_text
from geo_facts.gate.meta import Meta, load_meta
from geo_facts.normalize.text import compact, fold

SHINGLE_SIZE = 4
NEAR_DUPLICATE_THRESHOLD = 0.5
MIN_SHINGLES = 10
META_SUFFIX = ".meta.json"


def shingles(text: str, size: int = SHINGLE_SIZE) -> frozenset[str]:
    kept = "".join(char for char in fold(text) if char.isalnum())
    return frozenset(kept[i : i + size] for i in range(len(kept) - size + 1))


def jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    union = first | second
    if not union:
        return 0.0
    return len(first & second) / len(union)


def draft_shingles(draft: Draft) -> frozenset[str]:
    return shingles(draft.text_of(VISIBLE))


def topic_key(topic: str) -> str:
    """主题比较键：忽略全半角、大小写、空白与连字符。"""
    return compact(topic)


@dataclass(frozen=True)
class CorpusDoc:
    name: str
    path: Path
    brand_entity_id: str | None
    topic: str | None
    shingles: frozenset[str]


@dataclass(frozen=True)
class Corpus:
    docs: tuple[CorpusDoc, ...]

    @property
    def without_scope(self) -> int:
        return sum(1 for doc in self.docs if not (doc.brand_entity_id and doc.topic))


def _meta_for(path: Path, timezone: str) -> Meta | None:
    meta_path = path.with_name(path.name[: -len(path.suffix)] + META_SUFFIX)
    return load_meta(meta_path, timezone) if meta_path.is_file() else None


def load_corpus(directory: Path, timezone: str) -> Corpus:
    if not directory.is_dir():
        raise InputError(f"--corpus 目录不存在：{directory.name}", details={"dir": directory.name})
    files = sorted(
        path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in FORMATS
    )
    stems: dict[str, str] = {}
    docs: list[CorpusDoc] = []
    for path in files:
        stem = path.name[: -len(path.suffix)]
        if stem in stems:
            raise InputError(
                f"--corpus 中 {stems[stem]} 与 {path.name} 的文件名主干相同，无法对应 meta",
                details={"files": [stems[stem], path.name]},
            )
        stems[stem] = path.name
        meta = _meta_for(path, timezone)
        draft = parse_draft(read_text(path, "既有稿件"), format_of(path))
        docs.append(
            CorpusDoc(
                name=path.name,
                path=path,
                brand_entity_id=meta.brand_entity_id if meta else None,
                topic=meta.topic if meta else None,
                shingles=draft_shingles(draft),
            )
        )
    return Corpus(tuple(docs))
