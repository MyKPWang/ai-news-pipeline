#!/usr/bin/env python3
"""
publish_news - 通用微信公众号HTML生成与上传工具
接收预处理好的数据，生成HTML并上传到微信公众号草稿箱
"""
import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional

# 微信公众号 API 配置


def get_config():
    """读取同目录下的配置文件"""
    config_path = Path(__file__).parent / "news-config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}，请创建并填写 app_id 和 app_secret")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_thumb_media_id(config: dict) -> str:
    """
    确保有 thumb_media_id：
    1. 如果配置里已有，直接返回
    2. 如果没有，读取同目录下的 cover.jpg 上传，并写回配置文件
    """
    if config.get("thumb_media_id"):
        return config["thumb_media_id"]

    # 上传封面图
    cover_path = Path(__file__).parent / "cover.jpg"
    if not cover_path.exists():
        raise FileNotFoundError(f"封面图不存在: {cover_path}，请放置 cover.jpg")

    print(f"   📤 首次运行，正在上传封面图...")
    app_id = config["app_id"]
    app_secret = config["app_secret"]

    # 获取 access_token
    import requests
    resp = requests.get(
        f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={app_id}&secret={app_secret}",
        timeout=10
    ).json()
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(f"获取 access_token 失败: {resp}")

    # 上传封面图
    with open(cover_path, "rb") as f:
        files = {"media": ("cover.jpg", f, "image/jpeg")}
        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=thumb"
        r = requests.post(url, files=files, timeout=30).json()

    media_id = r.get("media_id")
    if not media_id:
        raise RuntimeError(f"封面上传失败: {r}")

    print(f"   ✅ 封面上传成功: {media_id}")

    # 写回配置文件
    config["thumb_media_id"] = media_id
    config_path = Path(__file__).parent / "news-config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

    return media_id


def generate_html(
    data: dict,
    title: str,
    sources: List[str] = None,
    author: str = None,
    compliance_contact: str = None,
) -> str:
    """生成HTML文档"""
    from jinja2 import Environment, FileSystemLoader

    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("news.html")

    # 准备数据
    hot_items = data.get("hot_items", [])
    insight = data.get("insight", "")
    categories = data.get("categories", [])
    github_trending = data.get("github_trending")

    # 底部来源
    if sources:
        sources_str = "来源：" + "、".join(sources)
    else:
        sources_str = ""
    contact = compliance_contact or "公众号后台留言"

    html_content = template.render(
        title=title,
        hot_items=hot_items,
        insight=insight,
        categories=categories,
        github_trending=github_trending,
        sources_str=sources_str,
        author=author,
        date=datetime.now().strftime("%Y年%m月%d日"),
        compliance_contact=contact,
    )

    return html_content


def save_html(html_content: str, title: str) -> str:
    """保存HTML到本地"""
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"news_{date_str}.html"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"   💾 HTML已保存: {filepath}")
    return str(filepath)


def upload_to_wechat(html_content: str, title: str, thumb_media_id: str, config: dict, author: str = None) -> bool:
    """上传到微信公众号草稿箱"""
    import requests

    app_id = config["app_id"]
    app_secret = config["app_secret"]

    # 获取 access_token
    resp = requests.get(
        f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={app_id}&secret={app_secret}",
        timeout=10
    ).json()
    token = resp.get("access_token")
    if not token:
        print(f"   ❌ 获取 access_token 失败")
        return False

    # 构造草稿
    draft = {
        "articles": [{
            "title": title,
            "author": author or "Valkyrie",
            "digest": f"{title} | 今日移动端AI资讯速览",
            "content": html_content,
            "thumb_media_id": thumb_media_id,
        }]
    }

    post_data = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    resp = requests.post(url, data=post_data, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
    result = resp.json()

    if "media_id" in result:
        print(f"   ✅ 已上传到公众号草稿箱")
        return True
    else:
        print(f"   ❌ 上传失败: {result}")
        return False


def publish_news(
    data: dict,
    title: str,
    sources: List[str] = None,
    author: str = None,
    compliance_contact: str = None,
) -> bool:
    """
    通用微信公众号发布函数

    Args:
        data: 数据字典，结构如下：
            {
                "hot_items": ["热点1", "热点2"],  # 可选
                "insight": "洞察内容",              # 可选
                "categories": [
                    {
                        "name": "分类名",
                        "items": [
                            {
                                "title": "原文标题",
                                "desc": "原文摘要",
                                "summary": "LLM重写后的正文",
                                "rewritten_title": "LLM重写后的标题",
                                "source": "来源",
                                "link": "https://...",
                                "time_ago": "2小时前"
                            }
                        ]
                    }
                ]
            }
        title: 文章标题
        sources: 来源列表，如 ["东方财富", "华尔街见闻"]，可选
        author: 整理人，可选

    Returns:
        bool: 成功返回 True，失败返回 False
    """
    try:
        print(f"\n📰 开始生成: {title}")

        # 1. 读取配置
        config = get_config()

        # 2. 确保有 thumb_media_id
        thumb_media_id = ensure_thumb_media_id(config)
        print(f"   ✅ thumb_media_id: {thumb_media_id}")

        # 3. 生成HTML
        html_content = generate_html(
            data,
            title,
            sources,
            author,
            compliance_contact or config.get("compliance_contact", ""),
        )

        # 4. 保存本地
        save_html(html_content, title)

        # 5. 上传微信
        success = upload_to_wechat(html_content, title, thumb_media_id, config, author=author)

        if success:
            print(f"\n✅ 发布成功！")
            return True
        else:
            print(f"\n❌ 发布失败")
            return False

    except Exception as e:
        print(f"\n❌ 发生异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 示例用法
    sample_data = {
        "hot_items": ["央行宣布降准0.5个百分点", "A股三大指数集体收涨"],
        "insight": "今日市场情绪回暖，政策面利好频出，建议关注消费板块。",
        "categories": [
            {
                "name": "A股市场",
                "items": [
                    {
                        "title": "沪指收复3000点",
                        "desc": "今日A股三大指数集体收涨",
                        "summary": "今日A股市场表现强势，沪指成功收复3000点整数关口。板块方面，白酒、金融、科技股普遍走强，市场成交量有所放大。分析师表示，政策暖风频吹有望继续支撑市场。",
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
                        "summary": "中国人民银行今日宣布，下调金融机构存款准备金率0.5个百分点，预计释放长期资金约1万亿元。此举旨在保持流动性合理充裕，支持实体经济发展。",
                        "rewritten_title": "央行突袭降准！释放万亿流动性",
                        "source": "华尔街见闻",
                        "link": "https://example.com/news2",
                        "time_ago": "3小时前"
                    }
                ]
            }
        ]
    }

    # 测试调用（需要先配置 news-config.json）
    # result = publish_news(sample_data, "金融资讯早报（测试）", ["东方财富", "华尔街见闻"], "小明")
    # print(f"结果: {result}")
    print("请先配置 news-config.json，然后参考示例代码调用 publish_news()")
