# 酒店 / OTA 行业雷达

> 实时汇集酒店产业链与 OTA 行业动态的零成本静态雷达。自动抓取 CR9 酒管公司、OTA 平台、政府政策与 AI 趋势的权威信源,每 3 小时更新,公开可访问。

**线上地址**:https://ihyperwinagent.github.io/hotel-ota-radar/

---

## 这是什么

一个聚焦酒店 / OTA 行业的信息雷达。它把分散在各处的行业信源(华住等酒管公司财报、携程美团等 OTA 动态、文旅部政策、Skift 等 AI 趋势媒体)自动抓取、去重、分类,汇成一个 5 分钟可读的行业动态流。

所有信源都经过权威性与时效性验证,并在每条动态上标注可信度。

## 关注维度(五大分类)

| 分类 | 覆盖内容 | 代表信源 |
| --- | --- | --- |
| CR9 财报动态 | 头部酒管公司财报、季报、经营数据 | 华住 H World、亚朵 Atour |
| 政府政策 | 酒店旅游政策、行业法规、官方数据 | 文化和旅游部、中国旅游研究院 |
| 产业链/酒管 | 行业报告、酒管公司动态、产业链分析 | 中国饭店协会、迈点、执惠、品橙 |
| AI × 酒店 | 全球酒店科技与 AI 应用趋势 | Skift、Hotel Management |
| OTA 行业 | 在线旅游平台动态 | 携程 Trip.com、同程、美团 |

## 能做什么

- **行业动态流**:按时间倒序浏览全部动态,支持按五大分类 Tab 筛选、按信源筛选、关键词搜索
- **可信度标注**:每条动态显示信源可信度(高 / 中),沿用信源验证报告的结论
- **零成本自动更新**:GitHub Actions 每 3 小时抓取一次,无需服务器、无需人工维护

## 工作原理

```
信源配置(YAML) → 抓取(三路) → 去重/分类 → JSON → GitHub Pages 网页
```

抓取分三路,按信源特性自动选择:

1. **RSS**:有标准 RSS 的源直接解析(亚朵、Skift 酒店分类 feed、品橙旅游、Hotel Management)
2. **HTML link_pattern**:国内源无 RSS 且页面结构各异,按文章 URL 正则模式抓取 + 导航减噪 + 标题过滤(中国饭店协会、中国旅游研究院、文旅部、迈点、执惠)
3. **GNW 兜底**:境外 IR(华住/携程官网)直连超时,改走 GlobeNewswire 关键词搜索拿官方财报披露

**全程纯规则,不调用任何大模型,运行零 token 成本。**

## 数据产物

| 文件 | 用途 |
| --- | --- |
| `data/latest-24h.json` | 主数据:全部动态条目 |
| `data/daily-brief.json` | 精选区数据 |
| `data/source-status.json` | 各信源抓取状态(成功/失败/条数) |

## 添加信源

只需编辑一个文件 `config/hotel_sources.yaml`,加一条配置即可:

```yaml
- id: 信源唯一标识
  name: "信源显示名"
  category: cr9_finance   # 五大分类之一
  type: rss               # rss / html / gnw
  url: "信源地址"
  link_pattern: "/article/\\d+"   # html 类型:文章 URL 正则(可选)
  credibility: "高"        # 高 / 中
```

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install requests beautifulsoup4 feedparser python-dateutil pyyaml
.venv/bin/python scripts/update_hotel.py   # 抓取并生成 data/*.json
python3 -m http.server 8766                 # 本地预览,打开 http://localhost:8766/
```

## GitHub 自动更新

`.github/workflows/update-news.yml` 配置了:

- **定时**:每 3 小时抓取一次
- **push 触发**:改动抓取脚本/信源配置时自动跑一次
- **自动提交**:抓取后把新数据 commit 回仓库,GitHub Pages 自动重新部署

GitHub Actions 跑在境外(美国 Azure)服务器上,因此能稳定访问大陆直连超时的境外信源。公开仓库的 Actions 与 Pages 均免费。

## 信源验证

所有信源经过并行抓取 + 对抗性可信度评级(权威性 / 时效性 / 红旗检查),结论:绝大多数为真权威、真实时的一手或垂直媒体源。

## 致谢

抓取框架结构借鉴自 [ai-news-radar](https://github.com/LearnPrompt/ai-news-radar),针对酒店/OTA 行业重写了信源配置、抓取逻辑与前端。

## License

[MIT](LICENSE)
