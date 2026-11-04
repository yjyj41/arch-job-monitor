# Arch Job Monitor

건축 채용공고를 매일 자동으로 모아 표 하나로 보여주는 사이트. 지금은 UK / London.

- **수집**: GitHub Actions가 매일 06:17 UTC에 `scraper/scrape.py` 실행 → `data/jobs.json` 갱신 → 커밋
- **사이트**: `index.html` (정적, GitHub Pages)이 `data/jobs.json`을 읽어 표로 표시

## 출처

| 출처 | 방식 | 비고 |
|---|---|---|
| Dezeen Jobs | WP Job Manager AJAX (`/jm-ajax/get_listings/`), 실패 시 RSS | London + UK 검색, 회사명·연봉·태그 |
| RIBA Jobs (jobs.architecture.com) | London (Greater) 목록 페이지 | 리크루터 공고 많음 |
| Bespoke Careers | London 보드 → 각 공고의 JobPosting JSON-LD | 클라이언트명 비공개, 연봉 대부분 공개 |
| UK 스폰서 명단 (gov.uk) | Register of licensed sponsors CSV | 회사명 매칭 → "비자 스폰서" 열 |

## 설정 (한 번만)

1. GitHub에서 새 repo 만들기 (예: `arch-job-monitor`, Public)
2. 이 폴더 내용을 그대로 업로드 (`.github` 폴더 포함 — 웹 업로드 시 숨김폴더가 빠지지 않게 주의)
3. **Settings → Actions → General → Workflow permissions → Read and write** 체크
4. **Settings → Pages → Source: Deploy from a branch → `main` / `/ (root)`**
5. **Actions → Scrape jobs → Run workflow** 로 첫 수집 실행 (1–2분)
6. `https://<username>.github.io/arch-job-monitor/` 접속

## 기능

- 열 클릭 정렬, 검색, 국가/출처/직급/최소연봉 필터
- 건축만 · 연봉 공개만 · 리크루터 제외 · 마감 포함 토글
- **NEW**: 지난 방문 이후 새로 올라온 공고 (브라우저별로 기억)
- ★ 즐겨찾기 (브라우저에 저장)
- 사라진 공고는 "마감"으로 60일간 보관

## 나라/출처 추가

`scraper/scrape.py`에 함수 하나 추가 → `main()`의 소스 목록에 등록. 각 job은 `mk(source, id, title=, company=, location=, country=, salary_text=, posted=, url=)`로 만들면 됨. `country` 값이 사이트의 국가 필터에 자동으로 생김.

## 로컬 실행

```
pip install -r scraper/requirements.txt
python scraper/scrape.py
python -m http.server   # → http://localhost:8000
```
