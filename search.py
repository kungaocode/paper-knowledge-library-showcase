"""Build and search a local sparse vector index over the public concept records."""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata


DATA = Path(__file__).resolve().parent / "data"
INDEX = DATA / "index.sqlite3"
INDEX_VERSION = "tfidf-1"
HAN = r"\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af"
TOKENS = re.compile(rf"[{HAN}]+|[^\W{HAN}_]+")
HAN_RUN = re.compile(rf"[{HAN}]")
DIGEST_FILES = ("papers.jsonl", "concepts.jsonl")


def records(filename):
    with (DATA / filename).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def fingerprint():
    digest = sha256()
    for filename in DIGEST_FILES:
        with (DATA / filename).open("rb") as source:
            digest.update(filename.encode("ascii"))
            digest.update(b"\0")
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def counts(text):
    result = Counter()
    for token in TOKENS.findall(unicodedata.normalize("NFKC", text).casefold()):
        if HAN_RUN.fullmatch(token[0]):
            result.update(token)
            result.update(token[i : i + 2] for i in range(len(token) - 1))
        else:
            result[token] += 1
    return result


def vector_counts(concept, title):
    pieces = [
        concept["name"], concept["name"], concept["name"],
        *concept["aliases"], *concept["aliases"],
        concept["definition"] or "", title,
    ]
    for attribute in concept.get("attributes", []):
        pieces.extend([attribute["name"], *attribute["aliases"], attribute["definition"] or ""])
    return counts(" ".join(pieces))


def unit_vector(features, idf):
    vector = {
        term: (1 + math.log(freq)) * idf[term] * (0.3 if HAN_RUN.fullmatch(term) else 1)
        for term, freq in features.items()
        if term in idf
    }
    norm = math.sqrt(math.fsum(weight * weight for weight in vector.values()))
    return {term: weight / norm for term, weight in vector.items()} if norm else {}


def build():
    source_hash = fingerprint()
    titles = {paper["id"]: paper["title"] for paper in records("papers.jsonl")}
    concepts = records("concepts.jsonl")
    vectors = [vector_counts(concept, titles[concept["paper_id"]]) for concept in concepts]
    document_frequency = Counter()
    for features in vectors:
        document_frequency.update(features.keys())
    idf = {
        term: 1 + math.log((1 + len(vectors)) / (1 + frequency))
        for term, frequency in document_frequency.items()
    }

    descriptor, name = tempfile.mkstemp(prefix="index-", suffix=".sqlite3", dir=DATA)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection, connection:
            connection.executescript("""
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE features(term TEXT PRIMARY KEY, idf REAL NOT NULL) WITHOUT ROWID;
                CREATE TABLE postings(
                    term TEXT NOT NULL,
                    concept INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    PRIMARY KEY(term, concept)
                ) WITHOUT ROWID;
            """)
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [("version", INDEX_VERSION), ("source_hash", source_hash)],
            )
            connection.executemany("INSERT INTO features VALUES (?, ?)", sorted(idf.items()))
            connection.executemany(
                "INSERT INTO postings VALUES (?, ?, ?)",
                (
                    (term, position, weight)
                    for position, features in enumerate(vectors)
                    for term, weight in sorted(unit_vector(features, idf).items())
                ),
            )
        if fingerprint() != source_hash:
            raise ValueError("数据在构建过程中发生变化，请重新运行 build。")
        temporary.replace(INDEX)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"已构建 {len(vectors):,} 条概念的离线向量索引。")


def ranked(query):
    tokens = counts(query)
    if not tokens:
        return []
    if len(tokens) > 300:
        raise ValueError("查询包含过多术语，请缩短后重试。")
    if not INDEX.is_file():
        raise ValueError("索引文件缺失，请先运行 python search.py build。")

    with closing(sqlite3.connect(INDEX.as_uri() + "?mode=ro", uri=True)) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata.get("version") != INDEX_VERSION or metadata.get("source_hash") != fingerprint():
            raise ValueError("索引与数据版本不匹配，请运行 python search.py build。")
        marks = ", ".join("?" for _ in tokens)
        idf = dict(connection.execute(f"SELECT term, idf FROM features WHERE term IN ({marks})", tuple(tokens)))
        vector = unit_vector(tokens, idf)
        if not vector:
            return []
        values = ", ".join("(?, ?)" for _ in vector)
        parameters = tuple(value for pair in vector.items() for value in pair)
        return connection.execute(
            f"""
            WITH query_vector(term, weight) AS (VALUES {values})
            SELECT p.concept, SUM(q.weight * p.weight) AS similarity
            FROM query_vector q JOIN postings p ON p.term = q.term
            GROUP BY p.concept
            ORDER BY similarity DESC, p.concept
            """,
            parameters,
        ).fetchall()


def search(arguments):
    papers = {paper["id"]: paper for paper in records("papers.jsonl")}
    concepts = records("concepts.jsonl")
    seen = set()
    results = []
    query = " ".join(arguments.query)
    for position, score in ranked(query):
        concept = concepts[position]
        paper = papers[concept["paper_id"]]
        if (paper["id"] in seen
            or (arguments.topic and arguments.topic not in paper["topics"])
            or (arguments.collection and paper["collection"] != arguments.collection)
            or (not arguments.include_legacy and paper["concept_review"] != "reviewed")):
            continue
        seen.add(paper["id"])
        results.append((concept, paper, score))
        if len(results) == arguments.limit:
            break
    for concept, paper, score in results:
        print(f"{concept['name']}  [{score:.3f}]")
        if concept["definition"]:
            print(concept["definition"])
        print(f"{paper['title']}  |  PDF 第 {concept['page']} 页  |  {paper['source_url']}\n")
    if not results:
        print("当前范围内没有匹配条目。")


def outline(paper_id):
    papers = {paper["id"]: paper for paper in records("papers.jsonl")}
    if paper_id not in papers:
        raise ValueError(f"展示数据中找不到论文 {paper_id}。")
    paper = papers[paper_id]
    children = defaultdict(list)
    for concept in records("concepts.jsonl"):
        if concept["paper_id"] == paper_id:
            children[concept["parent_id"]].append(concept)

    roles = {"core": "主概念", "subconcept": "子概念", "attribute": "属性"}

    def show(node, level, role):
        indent = "  " * level
        print(f"{indent}- {role} · {node['name']}（PDF 第 {node['page']} 页）")
        if node["definition"]:
            print(f"{indent}  {node['definition']}")
        for child in children[node["id"]]:
            show(child, level + 1, roles[child["role"]])
        for attribute in node.get("attributes", []):
            print(f"{indent}  - 属性 · {attribute['name']}（PDF 第 {attribute['page']} 页）")
            if attribute["definition"]:
                print(f"{indent}    {attribute['definition']}")

    print(paper["title"])
    print(paper["source_url"])
    for root in children[None]:
        show(root, 0, roles[root["role"]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="从 JSONL 重建本地索引")
    tree = commands.add_parser("outline", help="展开单篇论文的概念层级和出处")
    tree.add_argument("paper_id")
    lookup = commands.add_parser("search", help="按概念和出处查论文")
    lookup.add_argument("query", nargs="+")
    lookup.add_argument("--topic", help="匹配一个完整主题标签")
    lookup.add_argument("--collection", choices=("recent_ai", "earlier_collection"))
    lookup.add_argument("--limit", type=int, default=5)
    lookup.add_argument("--include-legacy", action="store_true", help="搜索历史或未复核的条目")
    arguments = parser.parse_args()
    try:
        if arguments.command == "build":
            build()
        elif arguments.command == "outline":
            outline(arguments.paper_id)
        else:
            if arguments.limit < 1:
                parser.error("--limit 必须大于零")
            search(arguments)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

