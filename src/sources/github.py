"""
GitHub Trending 数据源 - 通过 ossinsight-github skill 获取 AI 趋势项目
"""
import json
import os
import subprocess
from pathlib import Path
from typing import List

from .base import NewsItem, register_source


# ossinsight-github skill 脚本路径
SKILL_SCRIPT = os.path.expanduser(
    "~/.openclaw/workspace/skills/ossinsight-github/scripts/fetch_github.py"
)


@register_source
class GithubSource:
    """GitHub AI 趋势项目数据源"""

    name = "github"
    url = "https://github.com/trending"

    def collect(self) -> List[NewsItem]:
        """通过 OSSInsight API 获取 GitHub AI 趋势项目"""
        output_file = "/tmp/github_ai_pipeline.json"
        news_list = []

        try:
            result = subprocess.run(
                ["python3", SKILL_SCRIPT, "--max", "10", "--output", output_file],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if not Path(output_file).exists():
                print(f"   ⚠️ github: 输出文件不存在，stderr: {result.stderr}")
                return []

            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            news_list = self.parse(data)

        except Exception as e:
            print(f"   ❌ github: 获取失败: {e}")

        return news_list

    def parse(self, data: list) -> List[NewsItem]:
        """解析数据，转换为 NewsItem"""
        news_list = []

        for item in data:
            repo_name = item.get("repo_name", "")
            description = item.get("description", "") or "无描述"

            extra = {
                "stars": item.get("stars", 0),
                "forks": item.get("forks", 0),
                "total_score": item.get("total_score", 0),
                "language": item.get("primary_language", ""),
                "description": description,
            }

            news = NewsItem(
                title=repo_name,
                desc=description,
                source=self.name,
                link=item.get("url", ""),
                time_ago="过去一周",
                extra=extra,
            )
            news_list.append(news)

        return news_list