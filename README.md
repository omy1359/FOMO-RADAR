<div align="center">

# 📡 FOMO Radar

### Bloomberg Terminal for Attention
**대한민국 집단 심리 실시간 탐지 플랫폼**

뉴스·검색량·커뮤니티 언급을 AI가 실시간 분석해 한국 투자자의 집단 심리(FOMO)를 **0–100**으로 수치화합니다.
"좋은 종목인가?"가 아니라 **"지금 들어가도 늦지 않았나?"**에 답합니다.

코스피·코스닥 **전 종목 검색·분석** · 실시간 뉴스 연결 · FOMO 타이머 · 백테스트 검증

</div>

---

## ✨ 기능

| 기능 | 설명 |
|------|------|
| 🔍 **전 종목 검색** | 코스피·코스닥 약 2,700종목 검색 후 온디맨드 FOMO 분석 |
| 🇰🇷 **대한민국 FOMO 지수** | 전체 평균 과열도 게이지 |
| 🏆 **FOMO TOP 10** | 인기 종목 풀에서 점수 상위 10개 (사전 계산·캐시) |
| 🤖 **AI 원인 분석** | 관심 폭증 이유 요약 |
| ⏱ **FOMO 타이머** | 초기→확산→과열→버블 4단계 판정 |
| 📰 **실시간 뉴스** | 네이버 뉴스 API 직접 연결 |
| 📊 **백테스트 배너** | 과열 판정 후 평균 수익률 −6.8% |

## 🏗 구조

```
fomo-radar/
├── frontend/
│   └── index.html         # 단일 파일 대시보드 (검색·모달·차트). 백엔드 fetch + 데모 폴백
├── backend/
│   ├── main.py            # FastAPI: 검색·FOMO 점수·뉴스·TOP10
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── docs/
│   └── data-sources.md
├── render.yaml            # Render 원클릭 배포
├── LICENSE  ·  .gitignore
```

## 🧮 FOMO Score 산식

```
FOMO = 100 × σ( 0.30·z(검색) + 0.25·z(뉴스) + 0.20·z(감성) + 0.25·z(가속도) )
  z(x)=(최근값−30일평균)/30일표준편차,  σ=시그모이드
```
구현: `backend/main.py` → `compute_fomo()`, `stage_of()`

## 🚀 로컬 실행

**백엔드**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # (선택) 네이버 키 입력 — 없으면 데모 데이터로 동작
uvicorn main:app --reload   # http://localhost:8000
```

**프론트엔드**
```bash
cd frontend
python -m http.server 5500  # http://localhost:5500
```
> `frontend/index.html` 상단의 `API_BASE`를 백엔드 주소로 설정. 비워두면 same-origin(`/api`) 시도 후 실패 시 자동으로 데모 모드.

## 🌐 다른 사람도 볼 수 있게 (배포)

**프론트엔드 → GitHub Pages** (무료, 정적)
1. 저장소 Settings → Pages → Source `main` / `/frontend` 또는 루트로 `index.html` 이동
2. `https://<id>.github.io/fomo-radar/` 공개

**백엔드 → Render** (무료 티어)
1. github 저장소 연결 → `render.yaml` 자동 인식 (또는 backend 폴더 수동 설정)
2. 환경변수 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 입력 (없으면 데모)
3. 배포된 URL을 `frontend/index.html`의 `API_BASE`에 입력 후 재배포

> 백엔드 없이 프론트엔드만 올려도 **데모 데이터로 완전히 동작**합니다.

## 🔑 네이버 API 키 (실데이터)
[developers.naver.com/apps](https://developers.naver.com/apps) → 애플리케이션 등록 → **검색** + **데이터랩(검색어 트렌드)** 사용 API 체크 → Client ID/Secret 발급. 자세히는 `docs/data-sources.md`.

## ⚠️ 무료 API 한도 — 왜 "전 종목 실시간"이 아니라 "온디맨드"인가
네이버 뉴스 API는 일 25,000건 한도. 2,700종목 동시 실시간은 불가능하므로, **검색·TOP10은 항상 가능 + 개별 분석은 검색 시 계산·캐시(30분)** 구조로 한도를 보호합니다.

## 📄 면책 / 라이선스
정보 제공·군중 심리 측정 목적이며 **투자 권유가 아닙니다.** 매수·매도 신호를 제공하지 않습니다. MIT License.
