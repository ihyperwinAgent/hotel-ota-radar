"""酒店 / OTA 行业信源雷达抓取入口。

复用 update_news.py 的通用零件(RawItem / RSS 抓取 / session / 去重 / 时间解析),
读 config/hotel_sources.yaml 驱动,产出对齐前端 schema 的 data/*.json。
不碰主脚本的 AI 过滤逻辑——这里全行业放行,不做 AI 关键词筛选。
"""
from __future__ import annotations

import json
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
    resp.encoding = resp.apparent_encoding or resp.encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    base = src["url"]
    host = urlparse(base).netloc
    out: list[u.RawItem] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        title = u.maybe_fix_mojibake(title)
        if not title or len(title) < 8:  # 滤掉导航/按钮等短文本
            continue
        href = str(a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base, href)
        if urlparse(url).netloc and host not in urlparse(url).netloc and host not in base:
            pass  # 允许跨子域(IR 站常用独立子域)
        if url in seen:
            continue

        # 找时间:同级或父级的 <time>,否则从链接文本/周边找日期
        published = None
        t = a.find("time") or (a.parent.find("time") if a.parent else None)
        if t:
            published = u.parse_date_any(t.get("datetime") or t.get_text(" ", strip=True), now)
        if not published:
            published = u.parse_date_any(title, now)
        if not published:
            continue
        if published < now - timedelta(days=MAX_AGE_DAYS):
            continue

        seen.add(url)
        out.append(
            u.RawItem(
                site_id=src["id"],
                site_name=src["name"],
                source=src["name"],
                title=title,
                url=url,
                published_at=published,
                meta={"category": src["category"], "credibility": src.get("credibility", "")},
            )
        )
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
