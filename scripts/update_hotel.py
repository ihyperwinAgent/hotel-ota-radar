"""酒店 / OTA 行业信源雷达抓取入口。

复用 update_news.py 的通用零件(RawItem / RSS 抓取 / session / 去重 / 时间解析),
读 config/hotel_sources.yaml 驱动,产出对齐前端 schema 的 data/*.json。
不碰主脚本的 AI 过滤逻辑——这里全行业放行,不做 AI 关键词筛选。
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
import update_news as u  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "hotel_sources.yaml"
DATA = ROOT / "data"
MAX_AGE_DAYS = 45  # 行业财报/政策更新慢,放宽窗口

# 导航/UI 噪词表(移植自 hotel-radar/fetch.py,用于过滤非文章链接)
_NAV_EXACT = {
    "首页", "home", "关于我们", "about", "about us", "联系我们", "contact",
    "更多", "more", "查看更多", "read more", "详情", "点击查看", "点击阅读",
    "登录", "注册", "login", "sign in", "sign up", "搜索", "search",
    "返回", "back", "上一页", "下一页", "prev", "next",
    "English", "中文", "繁體", "新闻", "资讯", "文章", "报告",
    "产品与服务", "Products and services", "友情链接",
}
_NAV_KEYWORDS = [
    "cookie", "privacy", "隐私政策", "服务条款", "免责声明",
    "广告", "招聘", "合作", "APP下载", "下载APP",
]
_NAV_CONTAINER_RE = re.compile(
    r"nav|menu|footer|header|sidebar|breadcrumb|pagination|social|share|tag|copyright", re.I
)


def _is_nav_title(title: str) -> bool:
    """判断是否是导航/UI 文字而非文章标题(移植自 fetch.py)"""
    t = title.strip()
    if t.lower() in {n.lower() for n in _NAV_EXACT}:
        return True
    has_cjk = any("一" <= c <= "鿿" for c in t)
    if has_cjk and len(t) < 10:
        return True
    if not has_cjk and len(t) < 20:
        return True
    tl = t.lower()
    if len(t) < 20 and any(kw.lower() in tl for kw in _NAV_KEYWORDS):
        return True
    return False


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def fetch_rss(session, src: dict, now: datetime) -> list[u.RawItem]:
    feed = {
        "title": src["name"],
        "xml_url": src["url"],
        "html_url": src.get("url", ""),
        "include_keywords": src.get("include_keywords", ""),
    }
    items = u.fetch_feed_as_official_items(session, feed, now)
    for it in items:
        it.site_id = src["id"]
        it.site_name = src["name"]
        it.meta["category"] = src["category"]
        it.meta["credibility"] = src.get("credibility", "")
    return items


def fetch_html(session, src: dict, now: datetime) -> list[u.RawItem]:
    """通用启发式 HTML 抓取:抓页面里带 <time> 或日期的内容链接。

    国内 IR / 协会 / 媒体页面结构各异,不逐站写死,用通用规则:
    取正文区可见的、文字够长的链接,优先有 <time> 标签的。
    """
    resp = session.get(
        src["url"],
        timeout=(8, 12),  # (连接, 读取):境外IR连不上时8秒快速失败,不拖垮全流程
        headers={"User-Agent": u.BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    # 编码修正:政府站(文旅部等)常被误判为 ISO-8859-1 导致中文乱码
    if resp.encoding and resp.encoding.upper() in ("ISO-8859-1", "LATIN-1"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    # 移除导航/页脚区域减噪
    for tag in soup.find_all(["nav", "footer", "header"]):
        tag.decompose()
    for tag in soup.find_all(True, class_=_NAV_CONTAINER_RE):
        tag.decompose()

    base = src["url"]
    base_host = urlparse(base).netloc
    out: list[u.RawItem] = []
    seen: set[str] = set()

    # link_pattern:按文章 URL 正则抓(国内源列表页日期不在链接附近时的可靠方式)
    link_re = re.compile(src["link_pattern"]) if src.get("link_pattern") else None
    list_limit = int(src.get("list_limit", 15))  # 列表页按序取前 N 条

    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        title = u.maybe_fix_mojibake(title)
        href = str(a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if link_re and not link_re.search(href):
            continue
        url = urljoin(base, href)
        # 只保留同域链接(跨域多为外链/广告);IR 子域已在 link_pattern 放行
        if not link_re and urlparse(url).netloc and urlparse(url).netloc != base_host:
            continue
        if _is_nav_title(title):  # 过滤导航/招聘/UI 噪音
            continue
        if url in seen:
            continue

        # 找时间:<time> > 链接文本日期。link_pattern 模式下列表有序,无日期则用抓取时刻近似
        published = None
        t = a.find("time") or (a.parent.find("time") if a.parent else None)
        if t:
            published = u.parse_date_any(t.get("datetime") or t.get_text(" ", strip=True), now)
        if not published:
            published = u.parse_date_any(title, now)
        approx = False
        if not published:
            if link_re:
                published = now  # 列表页有序,前 N 条视为近期
                approx = True
            else:
                continue
        if published < now - timedelta(days=MAX_AGE_DAYS):
            continue

        seen.add(url)
        out.append(
            u.RawItem(
                site_id=src["id"], site_name=src["name"], source=src["name"],
                title=title, url=url, published_at=published,
                meta={
                    "category": src["category"],
                    "credibility": src.get("credibility", ""),
                    "approx_time": approx,
                },
            )
        )
        if link_re and len(out) >= list_limit:
            break
    return out


def collect(cfg: dict, session, now: datetime):
    items: list[u.RawItem] = []
    statuses: list[dict] = []
    for src in cfg["sources"]:
        start = time.perf_counter()
        err, got = None, []
        try:
            got = fetch_rss(session, src, now) if src["type"] == "rss" else fetch_html(session, src, now)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            # RSS 失败且有兜底 URL → 试兜底
            if src.get("fallback_url"):
                try:
                    fb = dict(src, url=src["fallback_url"])
                    got = fetch_rss(session, fb, now)
                    err = None
                except Exception as exc2:  # noqa: BLE001
                    err = f"{err} | fallback: {exc2}"
        items.extend(got)
        statuses.append({
            "site_id": src["id"], "site_name": src["name"], "category": src["category"],
            "ok": err is None, "item_count": len(got),
            "duration_ms": int((time.perf_counter() - start) * 1000), "error": err,
        })
    return items, statuses


def to_record(it: u.RawItem, cfg: dict) -> dict:
    cat = it.meta.get("category", "")
    cat_label = cfg["categories"].get(cat, {}).get("label", cat)
    return {
        "site_id": it.site_id, "site_name": it.site_name, "source": it.source,
        "title": it.title, "title_zh": it.title, "url": it.url,
        "published_at": u.iso(it.published_at), "event_time": u.iso(it.published_at),
        "category": cat, "category_label": cat_label,
        "credibility": it.meta.get("credibility", ""),
        "ai_is_related": True,  # 全行业放行,绕过前端 AI 过滤
    }


def main() -> int:
    cfg = load_config()
    now = u.utc_now()
    session = u.create_session()
    raw, statuses = collect(cfg, session, now)
    records = [to_record(it, cfg) for it in raw if it.published_at]
    records = u.dedupe_items_by_title_url(records, random_pick=False)
    records.sort(key=lambda r: r.get("event_time") or "", reverse=True)

    generated_at = u.iso(now)
    ok_sites = sum(1 for s in statuses if s["ok"])
    latest = {
        "generated_at": generated_at, "window_hours": MAX_AGE_DAYS * 24,
        "total_items": len(records), "total_items_raw": len(records),
        "total_items_all_mode": len(records),
        "source_count": len({r["site_id"] for r in records}),
        "topic_filter": "hotel_ota_all_pass",
        "items": records, "items_ai": records,
        "items_all": records, "items_all_raw": records,
    }
    status_payload = {
        "generated_at": generated_at,
        "successful_sites": ok_sites, "total_sites": len(statuses),
        "items_before_topic_filter": len(records),
        "sites": statuses,
    }

    # 伯乐精选区读 daily-brief.json:把行业条目包成 story 结构(一条=一个story,不做合并)
    stories = []
    for r in records[:30]:
        stories.append({
            "story_id": "story_" + r["url"][-12:],
            "title": r["title"], "url": r["url"], "primary_url": r["url"],
            "source": r["source"], "source_name": r["site_name"],
            "source_count": 1, "item_count": 1, "duplicate_count": 0,
            "score": 1.0, "importance": "normal",
            "importance_label": r.get("credibility", ""),
            "category": r["category"], "category_label": r.get("category_label", ""),
            "earliest_at": r["published_at"], "latest_at": r["published_at"],
            "reasons": [f"{r['site_name']} · {r.get('category_label','')}"],
            "primary_item": r,
        })
    brief_payload = {
        "generated_at": generated_at, "window_hours": MAX_AGE_DAYS * 24,
        "total_items": len(stories), "items": stories,
    }
    stories_payload = {
        "generated_at": generated_at, "window_hours": MAX_AGE_DAYS * 24,
        "total_stories": len(stories), "stories": stories,
    }

    DATA.mkdir(exist_ok=True)
    (DATA / "latest-24h.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "latest-24h-all.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "source-status.json").write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "daily-brief.json").write_text(json.dumps(brief_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "stories-merged.json").write_text(json.dumps(stories_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"抓取完成: {len(records)} 条 / {ok_sites}/{len(statuses)} 源成功")
    for s in statuses:
        flag = "OK" if s["ok"] else "FAIL"
        print(f"  [{flag}] {s['site_name']}: {s['item_count']} 条" + (f" — {s['error'][:60]}" if s['error'] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
