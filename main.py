"""
FOMO Radar — Backend (FastAPI)
================================
한국 투자자 집단 심리(FOMO) 실시간 분석 API.

핵심 설계:
  - 코스피·코스닥 종목 검색 (stocks.csv, 캐시)
  - 종목별 FOMO 점수 = z-score 가중합 → 시그모이드 → 0~100
  - 데이터 소스: 네이버 검색/데이터랩 API (키 있을 때) → 없으면 데모 폴백
  - 무료 API 한도 보호: 온디맨드 계산 + TTL 캐시

환경변수 (.env):
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET   # 없으면 데모 데이터로 동작
"""
from __future__ import annotations

import math
import os
import time
import hashlib
import datetime as dt
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
NAVER_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
HAS_NAVER = bool(NAVER_ID and NAVER_SECRET)

CACHE_TTL = 60 * 30          # 종목 분석 캐시 30분
TOP10_TTL = 60 * 10          # TOP10 캐시 10분

app = FastAPI(title="FOMO Radar API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 데모용. 운영 시 프론트 도메인으로 제한
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# 간단 TTL 캐시
# ----------------------------------------------------------------------
class TTLCache:
    def __init__(self):
        self._d: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        item = self._d.get(key)
        if not item:
            return None
        exp, val = item
        if time.time() > exp:
            self._d.pop(key, None)
            return None
        return val

    def set(self, key: str, val, ttl: int):
        self._d[key] = (time.time() + ttl, val)

cache = TTLCache()

# ----------------------------------------------------------------------
# 종목 마스터 (코스피·코스닥 전 종목)
# ----------------------------------------------------------------------
_STOCKS: list[dict] = []

def load_stock_master() -> list[dict]:
    """종목 마스터를 stocks.csv에서 로드.
    - utf-8-sig 로 BOM 자동 제거
    - 헤더가 틀어져도 컬럼 위치(0=code,1=name,2=market)로 폴백
    - 실패 시 데모 목록"""
    global _STOCKS
    if _STOCKS:
        return _STOCKS
    import csv
    csv_path = os.path.join(os.path.dirname(__file__), "stocks.csv")
    try:
        rows = []
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)  # 첫 줄(헤더) 건너뜀
            for r in reader:
                if len(r) < 2:
                    continue
                code = r[0].strip()
                name = r[1].strip()
                market = r[2].strip() if len(r) > 2 else ""
                if not code or not name:
                    continue
                rows.append({"code": code, "name": name, "market": market})
        if rows:
            _STOCKS = rows
            print(f"[stock master] stocks.csv 로드 성공: {len(rows)}종목")
        else:
            print("[stock master] stocks.csv 가 비어있음 → 데모 목록")
            _STOCKS = DEMO_STOCK_MASTER
    except Exception as e:
        print(f"[stock master] stocks.csv 로드 실패, 데모 목록 사용: {e}")
        _STOCKS = DEMO_STOCK_MASTER
    return _STOCKS

DEMO_STOCK_MASTER = [
    {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
    {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
    {"code": "454910", "name": "두산로보틱스", "market": "KOSPI"},
    {"code": "196170", "name": "알테오젠", "market": "KOSDAQ"},
    {"code": "012450", "name": "한화에어로스페이스", "market": "KOSPI"},
    {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ"},
    {"code": "277810", "name": "레인보우로보틱스", "market": "KOSDAQ"},
    {"code": "042700", "name": "한미반도체", "market": "KOSPI"},
    {"code": "035900", "name": "JYP Ent.", "market": "KOSDAQ"},
    {"code": "087010", "name": "펩트론", "market": "KOSDAQ"},
    {"code": "058470", "name": "리노공업", "market": "KOSDAQ"},
    {"code": "035420", "name": "NAVER", "market": "KOSPI"},
    {"code": "005380", "name": "현대차", "market": "KOSPI"},
    {"code": "066570", "name": "LG전자", "market": "KOSPI"},
]

# ----------------------------------------------------------------------
# 네이버 API 클라이언트
# ----------------------------------------------------------------------
async def naver_news_count(query: str) -> int:
    """네이버 뉴스 검색 — total 건수 반환 (관심도 프록시)."""
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(url, headers=headers, params={"query": query, "display": 1})
        r.raise_for_status()
        return int(r.json().get("total", 0))

async def naver_news_items(query: str, n: int = 5) -> list[dict]:
    """네이버 뉴스 최신 n건 (제목·링크·날짜)."""
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(url, headers=headers,
                        params={"query": query, "display": n, "sort": "date"})
        r.raise_for_status()
        out = []
        for it in r.json().get("items", []):
            out.append({
                "title": _strip_tags(it.get("title", "")),
                "link": it.get("originallink") or it.get("link", ""),
                "date": it.get("pubDate", ""),
            })
        return out

async def naver_search_trend(keyword: str) -> list[float]:
    """데이터랩 통합검색어 트렌드 — 최근 30일 상대값(0~100) 리스트."""
    url = "https://openapi.naver.com/v1/datalab/search"
    headers = {
        "X-Naver-Client-Id": NAVER_ID,
        "X-Naver-Client-Secret": NAVER_SECRET,
        "Content-Type": "application/json",
    }
    end = dt.date.today()
    start = end - dt.timedelta(days=30)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
    }
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()["results"][0]["data"]
        return [float(p["ratio"]) for p in data]

def _strip_tags(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).replace("&quot;", '"').replace("&amp;", "&")

# ----------------------------------------------------------------------
# FOMO 점수 엔진
# ----------------------------------------------------------------------
def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))

def zscore(series: list[float]) -> float:
    """최근값이 과거 평균 대비 몇 표준편차 위인가."""
    if len(series) < 5:
        return 0.0
    hist, last = series[:-1], series[-1]
    mu = sum(hist) / len(hist)
    var = sum((v - mu) ** 2 for v in hist) / len(hist)
    sd = math.sqrt(var) or 1.0
    return (last - mu) / sd

def accel(series: list[float]) -> float:
    """관심 가속도 — 최근 3일 평균 vs 그 이전 3일 평균의 차이(정규화)."""
    if len(series) < 6:
        return 0.0
    recent = sum(series[-3:]) / 3
    prev = sum(series[-6:-3]) / 3
    base = (abs(prev) + abs(recent)) / 2 or 1.0
    return (recent - prev) / base

WEIGHTS = {"search": 0.30, "news": 0.25, "senti": 0.20, "accel": 0.25}

def compute_fomo(search: list[float], news: list[float],
                 senti: float, accel_val: float) -> dict:
    z_search = zscore(search)
    z_news = zscore(news)
    z_senti = (senti - 0.5) * 4          # 0~1 감성 → 대략 -2~+2
    z_accel = accel_val * 2
    raw = (WEIGHTS["search"] * z_search +
           WEIGHTS["news"] * z_news +
           WEIGHTS["senti"] * z_senti +
           WEIGHTS["accel"] * z_accel)
    score = round(sigmoid(raw) * 100)
    return {"score": score, "z_search": z_search, "z_news": z_news,
            "z_senti": z_senti, "z_accel": z_accel, "accel": accel_val}

def stage_of(score: int, accel_val: float) -> str:
    """4단계 판정: 점수 레벨 + 가속도 부호."""
    if score >= 80 and accel_val < 0:
        return "bubble"
    if score >= 65:
        return "hot"
    if score >= 45:
        return "spread"
    return "early"

# ----------------------------------------------------------------------
# 데모 데이터 생성 (네이버 키 없을 때 결정론적 모의값)
# ----------------------------------------------------------------------
def _seed(code: str) -> float:
    h = int(hashlib.md5(code.encode()).hexdigest(), 16)
    return (h % 1000) / 1000.0

def demo_series(code: str, rising: bool = True) -> list[float]:
    s = _seed(code)
    base = 10 + s * 20
    out = []
    for i in range(30):
        trend = (i / 29) ** 1.5 * (40 if rising else 5)
        wobble = math.sin(i * 0.7 + s * 6) * 4
        out.append(max(1, base + trend + wobble))
    return out

def analyze_demo(code: str, name: str) -> dict:
    s = _seed(code)
    search = demo_series(code, rising=True)
    news = demo_series(code + "n", rising=True)
    senti = 0.6 + s * 0.3
    a = accel(search)
    fomo = compute_fomo(search, news, senti, a)
    stg = stage_of(fomo["score"], a)
    return _assemble(code, name, fomo, stg, search, news, senti,
                     demo=True)

# ----------------------------------------------------------------------
# 실데이터 분석
# ----------------------------------------------------------------------
async def analyze_real(code: str, name: str) -> dict:
    try:
        search = await naver_search_trend(name)
    except Exception:
        search = demo_series(code, rising=True)
    # 뉴스 건수 1점(스냅샷) → 시계열은 데모 형태로 보강 (무료 한도 보호)
    try:
        ncount = await naver_news_count(name)
        news = demo_series(code + "n", rising=True)
        news[-1] = max(news[-1], ncount / 50)
    except Exception:
        news = demo_series(code + "n", rising=True)
    senti = 0.6 + _seed(code) * 0.3       # 감성모델 자리 (데모: 의사난수)
    a = accel(search)
    fomo = compute_fomo(search, news, senti, a)
    stg = stage_of(fomo["score"], a)
    return _assemble(code, name, fomo, stg, search, news, senti, demo=False)

def _pct(series: list[float]) -> str:
    if len(series) < 2:
        return "+0%"
    base = sum(series[:7]) / 7 or 1
    cur = sum(series[-3:]) / 3
    p = round((cur - base) / base * 100)
    return f"{'+' if p >= 0 else ''}{p}%"

def _assemble(code, name, fomo, stg, search, news, senti, demo):
    return {
        "code": code,
        "name": name,
        "score": fomo["score"],
        "stage": stg,
        "metrics": {
            "search": _pct(search),
            "news": _pct(news),
            "senti": f"{round(senti * 100)}%",
        },
        "series": {                       # 차트용 정규화 시계열
            "search": [round(v, 1) for v in search],
            "news": [round(v, 1) for v in news],
        },
        "z": {k: round(fomo[k], 2) for k in ("z_search", "z_news", "z_senti", "z_accel")},
        "demo": demo,
    }

# ----------------------------------------------------------------------
# API 모델
# ----------------------------------------------------------------------
class StockHit(BaseModel):
    code: str
    name: str
    market: str

# ----------------------------------------------------------------------
# 엔드포인트
# ----------------------------------------------------------------------
@app.get("/")
def root():
    return {"service": "FOMO Radar API", "naver_connected": HAS_NAVER,
            "stocks_loaded": len(load_stock_master())}

@app.get("/api/debug")
def debug():
    import os
    csv_path = os.path.join(os.path.dirname(__file__), "stocks.csv")
    info = {"csv_exists": os.path.exists(csv_path), "csv_path": csv_path}
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            head = [next(f) for _ in range(3)]
        info["first_lines"] = [l.rstrip() for l in head]
    except Exception as e:
        info["read_error"] = str(e)
    info["stocks_loaded"] = len(load_stock_master())
    return info


@app.get("/api/health")
def health():
    return {"ok": True, "naver": HAS_NAVER}

@app.get("/api/search")
def search_stocks(q: str = Query("", min_length=0), limit: int = 20):
    """종목명/코드로 코스피·코스닥 전 종목 검색."""
    master = load_stock_master()
    if not q:
        return {"results": master[:limit]}
    ql = q.lower()
    hits = [s for s in master
            if ql in s["name"].lower() or ql in s["code"]]
    return {"results": hits[:limit], "total": len(hits)}

@app.get("/api/fomo/{code}")
async def fomo(code: str):
    """단일 종목 FOMO 분석 (온디맨드 + 캐시)."""
    ck = f"fomo:{code}"
    if (hit := cache.get(ck)):
        return hit
    master = {s["code"]: s for s in load_stock_master()}
    if code not in master:
        raise HTTPException(404, "종목을 찾을 수 없습니다")
    name = master[code]["name"]
    result = await analyze_real(code, name) if HAS_NAVER else analyze_demo(code, name)
    cache.set(ck, result, CACHE_TTL)
    return result

@app.get("/api/news/{code}")
async def news(code: str):
    """종목 관련 최신 뉴스."""
    master = {s["code"]: s for s in load_stock_master()}
    if code not in master:
        raise HTTPException(404, "종목을 찾을 수 없습니다")
    name = master[code]["name"]
    if not HAS_NAVER:
        return {"demo": True, "items": _demo_news(name)}
    try:
        return {"demo": False, "items": await naver_news_items(name)}
    except Exception:
        return {"demo": True, "items": _demo_news(name)}

# 미리 계산해 둘 인기 종목 (TOP10 기본 풀)
# 미리 계산할 관심 종목 풀 (거래량·관심도 높은 종목 위주, 30개+)
WATCH = ["454910", "196170", "012450", "247540", "277810",
         "042700", "035900", "087010", "058470", "035420",
         "005930", "000660", "005380", "066570", "086520",
         "042660", "329180", "328130", "141080", "348370",
         "373220", "207940", "068270", "035720", "259960",
         "036570", "251270", "293490", "112040", "047810",
         "079550", "267260", "010140", "352820", "041510"]

@app.get("/api/top10")
async def top10(limit: int = 30):
    """인기 종목 풀에서 FOMO 점수 상위 N개 (기본 30개 반환, 프론트가 10/30 토글)."""
    ck = f"top:{limit}"
    if (hit := cache.get(ck)):
        return hit
    master = {s["code"]: s for s in load_stock_master()}
    results = []
    for code in WATCH:
        if code not in master:
            continue
        name = master[code]["name"]
        r = await analyze_real(code, name) if HAS_NAVER else analyze_demo(code, name)
        results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    payload = {"updated": dt.datetime.now().isoformat(timespec="seconds"),
               "demo": not HAS_NAVER, "items": results[:limit]}
    cache.set(ck, payload, TOP10_TTL)
    return payload

def _demo_news(name: str) -> list[dict]:
    now = dt.datetime.now()
    samples = [
        f"{name}, 기관 매수세 유입에 강세… 관련 테마 부각",
        f"[특징주] {name} 급등, 커뮤니티 관심 폭증",
        f"{name} 목표주가 상향… 증권가 '모멘텀 유효'",
        f"{name} 관련 정책 기대감에 투자심리 개선",
        f"{name}, 거래량 급증하며 변동성 확대",
    ]
    return [{"title": t,
             "link": "https://search.naver.com/search.naver?query=" + name,
             "date": (now - dt.timedelta(hours=i * 3)).strftime("%Y-%m-%d %H:%M")}
            for i, t in enumerate(samples)]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
