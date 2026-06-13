from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.update_hotel import hotel_entities, hotel_story_score
from scripts.update_news import merge_story_items


NOW = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


def make_record(
    idx: int,
    *,
    title: str,
    url: str = "",
    category: str = "supply_chain",
    credibility: str = "高",
    hours_ago: int = 1,
    site_id: str = "chinahotel",
) -> dict:
    published = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": f"hotelitem-{idx}",
        "site_id": site_id,
        "site_name": site_id,
        "source": site_id,
        "title": title,
        "url": url,
        "published_at": published,
        "event_time": published,
        "category": category,
        "credibility": credibility,
        "ai_is_related": True,
    }


# --- hotel_entities: 品牌互斥,防止跨品牌财报误并 ---


def test_different_hotel_brands_extracted_distinctly():
    huazhu, _ = hotel_entities("华住集团发布2026年第一季度财报")
    atour, _ = hotel_entities("亚朵集团发布2026年第一季度财报")
    assert huazhu == {"huazhu"}
    assert atour == {"atour"}
    assert huazhu.isdisjoint(atour)  # 不同品牌 → 合并逻辑会拒绝


def test_hotel_entities_has_no_model_concept():
    brands, models = hotel_entities("携程发布暑期旅游趋势报告")
    assert brands == {"ctrip"}
    assert models == set()


# --- hotel_story_score: 分类主导的区分度 ---


def test_score_ranks_finance_above_ota():
    finance = make_record(1, title="华住财报", category="cr9_finance", credibility="高")
    ota = make_record(2, title="某OTA小促销", category="ota", credibility="中")
    assert hotel_story_score(finance, 1, NOW) > hotel_story_score(ota, 1, NOW)


def test_score_has_discrimination_across_categories():
    cats = ["cr9_finance", "policy", "ai_hotel", "supply_chain", "ota"]
    scores = {
        c: hotel_story_score(make_record(i, title=f"t{i}", category=c), 1, NOW)
        for i, c in enumerate(cats)
    }
    assert len(set(scores.values())) >= 4  # 不是全相等,有真区分度


# --- 端到端:品牌互斥在 merge 中生效 + 同源近似标题合并 ---


def test_merge_keeps_different_brands_separate():
    items = [
        make_record(1, title="华住集团发布2026年第一季度业绩报告数据", url="https://h.com/a", site_id="huazhu"),
        make_record(2, title="亚朵集团发布2026年第一季度业绩报告数据", url="https://atour.com/b", site_id="atour"),
    ]
    stories, _ = merge_story_items(
        items, NOW, window_hours=45 * 24, title_window_hours=72, entity_extractor=hotel_entities
    )
    assert len(stories) == 2  # 不同品牌不合并


def test_merge_collapses_same_url_across_query_params():
    # 中文标题分词后 token 数常 < 4,过不了 title_is_mergeable 门槛,
    # 所以中文场景真正生效的合并路径是 canonical_url(规整掉 utm/ref 等参数后相同)。
    items = [
        make_record(1, title="中国饭店协会发布绿色饭店公示名单", url="https://cha.org/articles/17825?utm_source=rss"),
        make_record(2, title="中国饭店协会发布绿色饭店公示名单", url="https://cha.org/articles/17825?ref=feed", hours_ago=3),
    ]
    stories, events = merge_story_items(
        items, NOW, window_hours=45 * 24, title_window_hours=72, entity_extractor=hotel_entities
    )
    assert len(stories) == 1
    assert stories[0]["source_count"] == 2
    assert events[0]["reason"] == "canonical_url"
