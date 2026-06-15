r"""Time each search backend directly to locate the latency."""
import statistics
import time

import httpx

s = httpx.Client(timeout=60)
SRV = "http://127.0.0.1:8100"


def med(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs)


# get a clip vector + text vector
ivec = s.post(f"{SRV}/embed/clip-text", json={"text": "a man in a blue shirt"}).json()["vector"]
tvec = s.post(f"{SRV}/embed/texts", json={"texts": ["a man in a blue shirt"]}).json()["vectors"][0]

# OpenSearch BM25
def os_bm25():
    s.post("http://localhost:9200/dam-assets/_search",
           json={"size": 50, "query": {"multi_match": {"query": "blue shirt",
                 "fields": ["title^2", "body", "labels^1.5"], "fuzziness": "AUTO", "prefix_length": 2}}})

# Qdrant image (200) and text
def qd_img():
    s.post("http://localhost:6333/collections/dam_image/points/query",
           json={"query": ivec, "limit": 200, "with_payload": True})

def qd_txt():
    s.post("http://localhost:6333/collections/dam_text/points/query",
           json={"query": tvec, "limit": 50, "with_payload": True})

# OpenSearch transcripts
def os_tr():
    s.post("http://localhost:9200/dam-transcripts/_search",
           json={"size": 50, "query": {"match": {"text": {"query": "blue shirt", "fuzziness": "AUTO"}}}})

# reranker with 20 passages
passages = [f"Document {i}. A person wearing clothing of various colors outdoors." for i in range(20)]
def rr():
    s.post(f"{SRV}/rerank", json={"query": "a man in a blue shirt", "passages": passages})

out = {
    "OpenSearch BM25 (assets)": med(os_bm25),
    "OpenSearch transcripts": med(os_tr),
    "Qdrant image (limit 200)": med(qd_img),
    "Qdrant text (limit 50)": med(qd_txt),
    "Reranker (20 passages)": med(rr),
}
with open(r"E:\dam-platform\.data\qa_backends_report.txt", "w", encoding="utf-8") as f:
    f.write("=== BACKEND LATENCY (median ms) ===\n")
    for k, v in out.items():
        f.write(f"{k:32s}: {v:.0f}\n")
print(" ".join(f"{k.split()[0]}={v:.0f}" for k, v in out.items()))
