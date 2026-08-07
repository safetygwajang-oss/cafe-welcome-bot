# ============================================
# 👋 네이버 카페 가입인사 게시판 자동 댓글 봇 (v2)
# GitHub Actions - 하루 3회 (08:00 / 13:00 / 18:00 KST)
#
# 정책:
#  1) 봇이 처음 돌아간 시점(=first_run_time) 이전 글은 절대 건드리지 않음
#  2) 이미 누군가(=내 계정 포함) 댓글이 달린 글도 스킵
#  3) 첫 실행(state 파일 없음)에는 "가장 최근 글 1건"만 처리
#  4) 신규 글 없으면 아무것도 안 하고 종료
# ============================================

import os
import json
import time
import random
import requests
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

# --------------------------------------------
# ⚙️ 환경변수
# --------------------------------------------
CLIENT_ID       = os.environ.get("NAVER_CLIENT_ID", "")
CLIENT_SECRET   = os.environ.get("NAVER_CLIENT_SECRET", "")
REFRESH_TOKEN   = os.environ.get("NAVER_REFRESH_TOKEN", "")
CAFE_ID         = os.environ.get("CAFE_ID", "")
WELCOME_MENU_ID = os.environ.get("WELCOME_MENU_ID", "")

MAX_COMMENTS_PER_RUN = 20       # 한 번 실행 시 최대 댓글 수 (안전장치)
COMMENT_INTERVAL_SEC = 15       # 댓글 간 딜레이 (도배 방지)

# 중복 방지 저장소
STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "commented.json"

# --------------------------------------------
# 💬 랜덤 환영 댓글 풀
# --------------------------------------------
WELCOME_MESSAGES = [
    "반갑습니다 😊",
    "안녕하세요, 어서오세요~",
    "환영합니다! 반가워요 🙌",
    "어서오세요, 잘 오셨어요 😄",
    "반가워요! 앞으로 자주 뵈어요 🙂",
    "안녕하세요~ 반갑습니다 👋",
    "환영합니다, 좋은 시간 되세요!",
    "반갑습니다! 활발한 활동 기대할게요 🔥",
    "어서오세요~ 편하게 활동하세요 😊",
    "가입 축하드려요, 반갑습니다 🎉",
]

# --------------------------------------------
# 🔧 상태 저장/로드
# --------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def to_html_entity(text: str) -> str:
    """댓글 한글 깨짐 방지"""
    result = []
    for c in text:
        code = ord(c)
        if code > 127 or c in '%&=?#':
            result.append(f"&#{code};")
        else:
            result.append(c)
    return ''.join(result)

# --------------------------------------------
# 🔑 토큰 발급
# --------------------------------------------
def get_access_token() -> str:
    url = "https://nid.naver.com/oauth2.0/token"
    params = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {r.text}")
    return token

# --------------------------------------------
# 📥 가입인사 게시판 최근 글 조회
# --------------------------------------------
def fetch_recent_articles(token: str, per_page: int = 30) -> list:
    url = f"https://openapi.naver.com/v1/cafe/{CAFE_ID}/menu/{WELCOME_MENU_ID}/articles"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page": 1, "perPage": per_page}

    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    try:
        articles = data["message"]["result"]["articleList"]
    except (KeyError, TypeError):
        print(f"⚠️ 예상치 못한 응답 구조: {json.dumps(data, ensure_ascii=False)[:500]}")
        return []

    return articles

# --------------------------------------------
# 💬 이미 댓글이 달려 있는지 확인
# --------------------------------------------
def has_any_comment(token: str, article_id) -> bool:
    """
    해당 글에 댓글이 1개라도 있으면 True.
    → 내가 이미 달았든, 다른 사람이 달았든 스킵 대상.
    """
    url = f"https://openapi.naver.com/v1/cafe/{CAFE_ID}/articles/{article_id}/comments"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            # 댓글 조회 실패 시 안전하게 "이미 있다"고 간주 → 스킵
            print(f"      ⚠️ 댓글 조회 실패 HTTP {r.status_code} → 안전하게 스킵 처리")
            return True
        data = r.json()
        comments = data.get("message", {}).get("result", {}).get("commentList", [])
        return len(comments) > 0
    except Exception as e:
        print(f"      ⚠️ 댓글 조회 예외: {e} → 안전하게 스킵 처리")
        return True

# --------------------------------------------
# 💬 댓글 등록
# --------------------------------------------
def post_comment(token: str, article_id, content: str) -> requests.Response:
    url = f"https://openapi.naver.com/v1/cafe/{CAFE_ID}/articles/{article_id}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }
    content_html = to_html_entity(content)
    content_enc = quote(content_html)
    body = f"content={content_enc}"
    return requests.post(url, headers=headers, data=body, timeout=30)

# --------------------------------------------
# 🚀 메인
# --------------------------------------------
def run():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    now_ts = int(now.timestamp())
    print("=" * 60)
    print("👋 가입인사 자동 댓글 봇 (v2)")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M KST')}")
    print("=" * 60)

    # 환경변수 체크
    missing = [k for k, v in {
        "NAVER_CLIENT_ID": CLIENT_ID,
        "NAVER_CLIENT_SECRET": CLIENT_SECRET,
        "NAVER_REFRESH_TOKEN": REFRESH_TOKEN,
        "CAFE_ID": CAFE_ID,
        "WELCOME_MENU_ID": WELCOME_MENU_ID,
    }.items() if not v]
    if missing:
        print(f"❌ 환경변수 누락: {missing}")
        exit(1)

    # 상태 로드
    state = load_state()
    is_first_run = "first_run_ts" not in state
    if is_first_run:
        state["first_run_ts"] = now_ts
        print("🌱 최초 실행 감지 → 이 시점 이후 글만 대상으로 삼습니다.")
    else:
        first_dt = datetime.fromtimestamp(state["first_run_ts"], tz=kst)
        print(f"📌 봇 최초 실행 시점: {first_dt.strftime('%Y-%m-%d %H:%M KST')}")

    first_run_ts = state["first_run_ts"]
    commented_ids = set(str(x) for x in state.get("commented", []))
    print(f"📚 기존 댓글 이력: {len(commented_ids)}건")

    # 토큰
    print("\n🔑 액세스 토큰 발급...")
    token = get_access_token()
    print("   ✅ 토큰 OK")

    # 목록 조회
    print(f"\n📥 가입인사 게시판(메뉴ID={WELCOME_MENU_ID}) 최근 글 조회...")
    articles = fetch_recent_articles(token, per_page=30)
    print(f"   ✅ {len(articles)}건 조회")

    # 각 글에 writeDate(작성시각, ms 또는 s) 필드가 있음 → 정규화
    def get_write_ts(a: dict) -> int:
        v = a.get("writeDate") or a.get("regDate") or 0
        try:
            v = int(v)
        except Exception:
            return 0
        # 13자리면 밀리초 → 초로 변환
        if v > 10_000_000_000:
            v = v // 1000
        return v

    # 1차 필터: 봇 최초 실행 시점 이후에 작성된 글 + state에 없는 글
    candidates = []
    for a in articles:
        aid = str(a.get("articleId") or a.get("refArticleId") or "")
        if not aid:
            continue
        if aid in commented_ids:
            continue
        wts = get_write_ts(a)
        if wts == 0:
            # 시간 정보 없으면 안전하게 스킵
            continue
        if wts < first_run_ts:
            # 봇 시작 이전 글 → 무조건 스킵
            continue
        candidates.append((wts, a))

    # 최신 → 과거 순 정렬
    candidates.sort(key=lambda x: x[0], reverse=True)
    print(f"   🆕 시점 필터 통과: {len(candidates)}건")

    # ===== 첫 실행 특례 =====
    # state 파일이 방금 생긴 첫 실행이면 → "가장 최근 글 1건"만 시험 삼아 처리
    if is_first_run:
        if candidates:
            candidates = candidates[:1]
            print("   🧪 첫 실행 → 가장 최근 글 1건만 시험 댓글 시도")
        else:
            print("   🧪 첫 실행이지만 대상 글 없음 → 상태만 저장하고 종료")
            save_state(state)
            return

    if not candidates:
        print("\n✅ 처리할 신규 가입인사가 없습니다. 종료.")
        save_state(state)   # first_run_ts 저장은 유지
        return

    # 최대 개수 제한
    candidates = candidates[:MAX_COMMENTS_PER_RUN]

    # 오래된 것부터 자연스럽게 달리도록 역순 처리
    candidates.reverse()

    ok, fail, skip = 0, 0, 0
    for i, (wts, art) in enumerate(candidates, 1):
        article_id = art.get("articleId") or art.get("refArticleId")
        subject    = art.get("subject", "")
        nickname   = art.get("writerNickname", "")
        write_dt   = datetime.fromtimestamp(wts, tz=kst).strftime("%m-%d %H:%M")

        print(f"\n   ▶ [{i}/{len(candidates)}] {write_dt} · {nickname} · {subject[:30]}")
        print(f"      articleId={article_id}")

        # 이미 댓글 달린 글이면 스킵 (내가 달았든 남이 달았든)
        if has_any_comment(token, article_id):
            print(f"      ⏭️  이미 댓글이 있는 글 → 스킵")
            commented_ids.add(str(article_id))   # 다음 실행 때 재조회 안 하도록 캐시
            state["commented"] = list(commented_ids)
            save_state(state)
            skip += 1
            time.sleep(2)
            continue

        # 댓글 등록
        message = random.choice(WELCOME_MESSAGES)
        print(f"      💬 댓글: {message}")
        try:
            r = post_comment(token, article_id, message)
            if r.status_code in (200, 201):
                resp = r.json()
                status = str(resp.get("message", {}).get("status", "200"))
                if status in ("200", ""):
                    print(f"      ✅ 성공")
                    commented_ids.add(str(article_id))
                    state["commented"] = list(commented_ids)
                    save_state(state)
                    ok += 1
                else:
                    print(f"      ❌ API 오류: {resp}")
                    fail += 1
            else:
                print(f"      ❌ HTTP {r.status_code}: {r.text[:200]}")
                fail += 1
        except Exception as e:
            print(f"      ❌ 예외: {e}")
            fail += 1

        if i < len(candidates):
            time.sleep(COMMENT_INTERVAL_SEC)

    # 최종 저장
    save_state(state)

    print("\n" + "=" * 60)
    print(f"🎉 완료 — 성공: {ok} · 스킵: {skip} · 실패: {fail}")
    print("=" * 60)

if __name__ == "__main__":
    run()
