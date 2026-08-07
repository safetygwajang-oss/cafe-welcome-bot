# ============================================
# 👋 네이버 카페 가입인사 게시판 자동 댓글 봇
# GitHub Actions - 하루 3회 (08:00 / 13:00 / 18:00 KST)
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

# 한 번 실행 시 최대 몇 건까지 댓글 달지 (안전장치)
MAX_COMMENTS_PER_RUN = 20
COMMENT_INTERVAL_SEC = 15   # 댓글 간 딜레이 (도배 방지)

# 중복 방지 저장소
STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "commented.json"

# --------------------------------------------
# 💬 랜덤 환영 댓글 풀
# --------------------------------------------
WELCOME_MESSAGES = [
    "반갑습니다",
    "안녕하세요, 어서오세요~",
    "환영합니다! 반가워요",
    "어서오세요",
    "반가워요! 앞으로 자주 뵈어요",
    "안녕하세요~ 반갑습니다",
    "환영합니다!",
    "반갑습니다! 활발한 활동 기대할게요",
    "어서오세요~ 잘 부탁드립니다",
    "가입 축하드려요, 반갑습니다",
]

# --------------------------------------------
# 🔧 유틸
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
    """댓글 한글 깨짐 방지용 - 특수문자/한글 HTML 엔티티 변환"""
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
    print("=" * 60)
    print("👋 가입인사 자동 댓글 봇")
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

    state = load_state()
    commented_ids = set(str(x) for x in state.get("commented", []))
    print(f"📚 기존 댓글 이력: {len(commented_ids)}건")

    print("\n🔑 액세스 토큰 발급...")
    token = get_access_token()
    print("   ✅ 토큰 OK")

    print(f"\n📥 가입인사 게시판(메뉴ID={WELCOME_MENU_ID}) 최근 글 조회...")
    articles = fetch_recent_articles(token, per_page=30)
    print(f"   ✅ {len(articles)}건 조회")

    # 아직 댓글 안 단 글만 필터
    new_articles = [
        a for a in articles
        if str(a.get("articleId") or a.get("refArticleId") or "") not in commented_ids
    ]
    print(f"   🆕 신규 대상: {len(new_articles)}건")

    if not new_articles:
        print("\n✅ 처리할 신규 가입인사가 없습니다.")
        return

    # 오래된 글부터 처리 (목록이 최신순이라 뒤집기)
    new_articles = list(reversed(new_articles))[:MAX_COMMENTS_PER_RUN]

    ok, fail = 0, 0
    for i, art in enumerate(new_articles, 1):
        article_id = art.get("articleId") or art.get("refArticleId")
        subject    = art.get("subject", "")
        nickname   = art.get("writerNickname", "")

        message = random.choice(WELCOME_MESSAGES)
        print(f"\n   ▶ [{i}/{len(new_articles)}] articleId={article_id} · {nickname} · {subject[:30]}")
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

        # 다음 댓글까지 딜레이 (도배/차단 방지)
        if i < len(new_articles):
            time.sleep(COMMENT_INTERVAL_SEC)

    print("\n" + "=" * 60)
    print(f"🎉 완료 — 성공: {ok} · 실패: {fail}")
    print("=" * 60)

if __name__ == "__main__":
    run()
