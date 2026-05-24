# publish_news 通用微信发布工具

## 目录结构

```
├── publish_news.py    # 主函数
├── news-config.json   # 配置文件（需要填写）
├── cover.jpg         # 封面图（需要提供）
├── templates/
│   └── news.html     # HTML模板
└── output/           # 生成的HTML保存目录
```

## 快速开始

### 1. 配置文件

编辑 `news-config.json`，填入你的微信公众号 app_id 和 app_secret：

```json
{
    "app_id": "wx your_app_id",
    "app_secret": "your_app_secret",
    "thumb_media_id": ""
}
```

### 2. 封面图

将封面图命名为 `cover.jpg` 放在同目录下。首次运行时会自动上传封面图，并把返回的 `thumb_media_id` 写入配置文件。

### 3. 调用示例

```python
from publish_news import publish_news

data = {
    "hot_items": ["央行宣布降准0.5个百分点", "A股三大指数集体收涨"],
    "insight": "今日市场情绪回暖，政策面利好频出。",
    "categories": [
        {
            "name": "A股市场",
            "items": [
                {
                    "title": "沪指收复3000点",
                    "desc": "今日A股三大指数集体收涨",
                    "summary": "今日A股市场表现强势，沪指成功收复3000点整数关口。板块方面，白酒、金融、科技股普遍走强。",
                    "rewritten_title": "沪指收复3000点！白酒金融科技集体爆发",
                    "source": "东方财富",
                    "link": "https://example.com/news1",
                    "time_ago": "2小时前"
                }
            ]
        },
        {
            "name": "宏观经济",
            "items": [
                {
                    "title": "央行宣布降准",
                    "desc": "央行宣布下调存款准备金率",
                    "summary": "中国人民银行今日宣布，下调金融机构存款准备金率0.5个百分点。",
                    "rewritten_title": "央行突袭降准！释放万亿流动性",
                    "source": "华尔街见闻",
                    "link": "https://example.com/news2",
                    "time_ago": "3小时前"
                }
            ]
        }
    ]
}

result = publish_news(
    data=data,
    title="金融资讯早报（5月1日）",
    sources=["东方财富", "华尔街见闻"],
    author="小明"
)

print(f"发布结果: {'成功' if result else '失败'}")
```

## 函数签名

```python
def publish_news(
    data: dict,
    title: str,
    sources: List[str] = None,
    author: str = None
) -> bool
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| data | dict | 是 | 数据字典，见下方详情 |
| title | str | 是 | 文章标题 |
| sources | List[str] | 否 | 来源列表，如 `["东方财富", "华尔街见闻"]` |
| author | str | 否 | 整理人姓名 |

### data 参数完整结构

```python
{
    "hot_items": [...],    # 可选，热点板块列表
    "insight": "...",      # 可选，洞察/导读内容
    "categories": [...]    # 必填，分类列表
}
```

#### 顶层字段

| 字段名 | 类型 | 必填 | 说明 | 示例 |
|--------|------|------|------|------|
| hot_items | List[str] | 否 | 热点资讯列表，渲染为文章开头的热点板块，为空或不传则不展示 | `["央行宣布降准0.5个百分点", "A股三大指数集体收涨"]` |
| insight | str | 否 | 洞察/导读内容，渲染在热点板块下方，为空或不传则不展示 | `"今日市场情绪回暖，政策面利好频出，建议关注消费板块。"` |
| categories | List[dict] | 是 | 分类列表，至少包含一个分类，每个分类下有多条资讯 | 见下方详情 |

---

#### categories（分类列表）

| 字段名 | 类型 | 必填 | 说明 | 示例 |
|--------|------|------|------|------|
| name | str | 是 | 分类名称，用于渲染分类标题 | `"A股市场"` |
| items | List[dict] | 是 | 该分类下的资讯列表，至少包含一条 | 见下方详情 |

---

#### categories[].items（资讯列表）

| 字段名 | 类型 | 必填 | 说明 | 示例 |
|--------|------|------|------|------|
| title | str | 是 | 原文标题，作为备标题展示 | `"沪指收复3000点"` |
| desc | str | 否 | 原文摘要/描述，作为备用内容展示 | `"今日A股三大指数集体收涨"` |
| summary | str | 是 | LLM重写后的正文摘要，作为文章主体内容展示 | `"今日A股市场表现强势，沪指成功收复3000点整数关口。板块方面，白酒、金融、科技股普遍走强。"` |
| rewritten_title | str | 是 | LLM重写后的标题，作为文章主标题展示 | `"沪指收复3000点！白酒金融科技集体爆发"` |
| source | str | 是 | 资讯来源 | `"东方财富"` |
| link | str | 是 | 原文链接，点击后跳转 | `"https://finance.eastmoney.com/a/20240101.html"` |
| time_ago | str | 否 | 相对时间，展示在标题旁边 | `"2小时前"` |

---

### data 完整示例

```python
data = {
    "hot_items": [
        "央行宣布降准0.5个百分点",
        "A股三大指数集体收涨"
    ],
    "insight": "今日市场情绪回暖，政策面利好频出，建议关注消费板块。",
    "categories": [
        {
            "name": "A股市场",
            "items": [
                {
                    "title": "沪指收复3000点",
                    "desc": "今日A股三大指数集体收涨",
                    "summary": "今日A股市场表现强势，沪指成功收复3000点整数关口。板块方面，白酒、金融、科技股普遍走强，市场成交量有所放大。分析师表示，政策暖风频吹有望继续支撑市场走势。",
                    "rewritten_title": "沪指收复3000点！白酒金融科技集体爆发",
                    "source": "东方财富",
                    "link": "https://finance.eastmoney.com/a/20240101.html",
                    "time_ago": "2小时前"
                },
                {
                    "title": "创业板指涨超2%",
                    "desc": "创业板指今日大幅上涨",
                    "summary": "创业板指今日大幅上涨，盘中涨幅超过2%。科技、新能源板块领涨，市场做多情绪明显回升。",
                    "rewritten_title": "创业板指涨超2%！科技新能源板块领涨",
                    "source": "同花顺",
                    "link": "https://www.10jqka.com.cn/a/20240101.html",
                    "time_ago": "3小时前"
                }
            ]
        },
        {
            "name": "宏观经济",
            "items": [
                {
                    "title": "央行宣布降准",
                    "desc": "央行宣布下调存款准备金率",
                    "summary": "中国人民银行今日宣布，下调金融机构存款准备金率0.5个百分点，预计释放长期资金约1万亿元。此举旨在保持流动性合理充裕，支持实体经济发展。",
                    "rewritten_title": "央行突袭降准！释放万亿流动性",
                    "source": "华尔街见闻",
                    "link": "https://wallstreetcn.com/a/20240101.html",
                    "time_ago": "5小时前"
                }
            ]
        }
    ]
}
```

### 返回值

- 成功: `True`
- 失败: `False`

## 依赖

- Python 3.7+
- requests
- jinja2

安装依赖：

```bash
pip install requests jinja2
```