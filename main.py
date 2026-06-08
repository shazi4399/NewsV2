"""
RSS 订阅源抓取 & 飞书群机器人推送脚本

功能：
1. 抓取指定 RSS 源的最新文章
2. 与本地状态文件去重
3. 通过飞书 Webhook 推送富文本消息
4. 更新本地状态文件以供下次去重
"""

import os
import sys

import feedparser
import requests
from bs4 import BeautifulSoup

# ============ 配置区 ============
# RSS 订阅源地址（可按需替换）
RSS_URL = "https://kejikuaixun.blogspot.com/feeds/posts/default?alt=rss"

# 本地状态文件路径
STATE_FILE = "last_rss_link.txt"


def get_webhook_url():
    """从环境变量获取飞书 Webhook 地址"""
    url = os.environ.get("FEISHU_WEBHOOK")
    if not url:
        print("[错误] 未设置环境变量 FEISHU_WEBHOOK，程序退出")
        sys.exit(1)
    return url


def parse_article_html(html):
    """
    从博文 HTML 中解析出结构化内容：标题、正文、来源名、来源链接。
    博文格式通常为：标题 + 正文 + "——" + 来源名 + 原文链接
    """
    soup = BeautifulSoup(html, "html.parser")

    # 提取来源链接（最后一个非 blogspot 外链）
    source_link = ""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "blogspot.com" not in href:
            source_link = href

    # 提取纯文本
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    # 分离正文与来源：以"——"行作为分隔
    body_lines = []
    source_name = ""
    separator_idx = None
    for i, line in enumerate(lines):
        if line.strip("—-─━") == "":
            separator_idx = i
            break
    if separator_idx is not None:
        body_lines = lines[:separator_idx]
        remaining = lines[separator_idx + 1:]
        # 来源名通常紧跟分隔线之后
        for r in remaining:
            if r and not r.startswith("http"):
                source_name = r
                break
    else:
        body_lines = lines

    # 第一行通常是重复的标题，与 RSS title 相同，去掉
    title = body_lines[0] if body_lines else ""
    body = "\n".join(body_lines[1:]) if len(body_lines) > 1 else "\n".join(body_lines)

    return {
        "title": title,
        "body": body,
        "source_name": source_name,
        "source_link": source_link,
    }


def fetch_latest_entry(rss_url):
    """抓取 RSS 源并返回最新一篇文章的 title、link 和正文内容"""
    try:
        print(f"[信息] 正在请求 RSS 源: {rss_url}")
        resp = requests.get(rss_url, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except requests.exceptions.Timeout:
        print("[错误] RSS 源请求超时（15秒），请检查网络或是否需要代理")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("[错误] RSS 源连接失败，可能被墙或地址无效")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"[错误] RSS 源请求失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[错误] RSS 解析异常: {e}")
        sys.exit(1)

    if feed.bozo and not feed.entries:
        print(f"[错误] RSS 源无法解析: {feed.bozo_exception}")
        sys.exit(1)

    if not feed.entries:
        print("[错误] RSS 源中没有任何文章条目")
        sys.exit(1)

    latest = feed.entries[0]
    title = latest.get("title", "无标题")
    link = latest.get("link", "")

    content_html = ""
    if "content" in latest and latest.content:
        content_html = latest.content[0].get("value", "")
    elif "summary" in latest:
        content_html = latest.get("summary", "")
    elif "description" in latest:
        content_html = latest.get("description", "")

    article = parse_article_html(content_html) if content_html else {
        "title": "", "body": "", "source_name": "", "source_link": ""
    }

    if not link:
        print("[错误] 最新文章缺少 link 字段")
        sys.exit(1)

    print(f"[信息] 最新文章: {title}")
    print(f"[信息] 链接: {link}")
    if article["source_link"]:
        print(f"[信息] 原文链接: {article['source_link']}")
    if article["source_name"]:
        print(f"[信息] 来源: {article['source_name']}")
    print(f"[信息] 正文长度: {len(article['body'])} 字符")
    return title, link, article


def read_last_link():
    """读取上次推送的链接，文件不存在则返回空字符串"""
    if not os.path.exists(STATE_FILE):
        return ""
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def save_last_link(link):
    """将最新链接写入状态文件"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(link)
    print(f"[信息] 状态文件已更新: {STATE_FILE}")


def build_feishu_payload(title, article):
    """构造飞书 interactive 卡片消息（与 daily_news_titles 风格一致）"""
    body = article["body"]
    source_name = article["source_name"]
    source_link = article["source_link"]

    max_len = 4000
    if len(body) > max_len:
        body = body[:max_len] + "\n\n... (内容过长，已截断)"

    # 正文区 markdown
    md_body = body

    # 底部：来源 + 原文链接
    footer_parts = []
    if source_name:
        footer_parts.append(f"——{source_name}")
    if source_link:
        footer_parts.append(f"🔗 [阅读原文]({source_link})")
    footer_text = "  ".join(footer_parts) if footer_parts else ""

    card = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": f"📰 {title}"}
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": md_body}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": footer_text}},
        ],
    }

    payload = {
        "msg_type": "interactive",
        "card": card
    }
    return payload


def send_to_feishu(webhook_url, payload):
    """发送消息到飞书 Webhook"""
    headers = {"Content-Type": "application/json"}
    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
    except requests.exceptions.Timeout:
        print("[错误] 飞书 Webhook 请求超时")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("[错误] 飞书 Webhook 连接失败，请检查网络或 URL")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"[错误] 飞书 Webhook 请求异常: {e}")
        sys.exit(1)

    if resp.status_code == 200:
        print(f"[成功] 消息推送成功，HTTP {resp.status_code}")
        print(f"[信息] 响应内容: {resp.text}")
    else:
        print(f"[错误] 消息推送失败，HTTP {resp.status_code}")
        print(f"[信息] 响应内容: {resp.text}")
        sys.exit(1)


def main():
    print("=" * 50)
    print("RSS 飞书推送机器人 启动")
    print("=" * 50)

    # 1. 获取飞书 Webhook 地址
    webhook_url = get_webhook_url()

    # 2. 抓取 RSS 最新文章
    title, link, article = fetch_latest_entry(RSS_URL)

    # 3. 去重拦截
    last_link = read_last_link()
    if last_link == link:
        print("[拦截] 最新文章与上次相同，无需推送，程序退出")
        sys.exit(0)

    print("[信息] 检测到新文章，准备推送...")

    # 4. 构造消息并推送
    payload = build_feishu_payload(title, article)
    send_to_feishu(webhook_url, payload)

    # 5. 更新状态文件
    save_last_link(link)

    print("=" * 50)
    print("RSS 飞书推送机器人 运行完毕")
    print("=" * 50)


if __name__ == "__main__":
    main()
