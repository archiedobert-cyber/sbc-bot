"""Posts SBCs labelled "New" on fut.gg/sbc to a Discord channel via webhook.

Env vars:
  DISCORD_WEBHOOK_URL  webhook to post to (GitHub secret)
  DRY_RUN=1            print what would be posted instead of sending it
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.fut.gg"
LIST_URL = f"{BASE}/sbc/"
STATE_FILE = Path("posted.json")  # remembers what's already been posted
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TEST_MODE = os.environ.get("TEST_MODE") == "1"  # post every current "New" SBC, even if already posted

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Links to individual SBCs, e.g. /sbc/challenges/27-14-manchester-calling/
SBC_HREF = re.compile(
    r"^(?:https://www\.fut\.gg)?/sbc/(?:challenges|players|upgrades|foundations)/[^/]+/?$"
)
# The badge must be exactly "New" (so a title like "Newcastle Special" won't match)
NEW_BADGE = re.compile(r"^\s*new\s*$", re.I)
GENERIC_OG_IMAGE = "fut-social"  # the site-wide fallback image, not SBC-specific

# Title line shown above the SBC cards - edit the text/emojis however you like
HEADER = "## 🚨🆕 **NEW SBC ALERT** 🆕🚨\n *Hover Bot brought to you by 𝐒𝐊𝐄𝐋𝐄𝐓𝐎𝐑*""

# Message posted at the very bottom, after all the SBC cards.
# Edit the text/emojis/link however you like, or set it to "" for no footer.
FOOTER = ""

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def card_container(anchor):
    """Walk up from a link until the parent holds more than one SBC (= the card)."""
    node = anchor
    while node.parent is not None and node.parent.name not in ("body", "html"):
        hrefs = {a["href"] for a in node.parent.find_all("a", href=SBC_HREF)}
        if len(hrefs) > 1:
            break
        node = node.parent
    return node


def clean_title(anchors):
    for a in anchors:
        parts = [p for p in a.stripped_strings if not NEW_BADGE.match(p)]
        if parts:
            return re.sub(r"\s+[\d,]{3,}$", "", parts[0]).strip()
    return None


def find_description(anchors, title):
    texts = [a.get_text(" ", strip=True) for a in anchors]
    candidates = [t for t in texts if t.startswith(title) and len(t) > len(title) + 15]
    if not candidates:
        return ""
    return max(candidates, key=len)[len(title):].strip()


def find_rewards(card):
    """Reward lines (packs/coins) sit outside the links inside the card."""
    rewards = []
    for s in card.find_all(string=True):
        text = s.strip()
        if text and not s.find_parent("a") and re.search(r"pack|coins|pick|boost", text, re.I):
            rewards.append(text)
    return rewards


def find_image(card, url):
    # 1) an image on the card itself (ignoring pack/coin icons)
    for img in card.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src and "public-assets" not in src:
            return urljoin(BASE, src)
    # 2) the SBC page's own preview image
    try:
        page = BeautifulSoup(get(url), "html.parser")
        og = page.find("meta", attrs={"property": "og:image"})
        if og and og.get("content") and GENERIC_OG_IMAGE not in og["content"]:
            return og["content"]
    except requests.RequestException:
        pass
    return None


def find_new_sbcs(html):
    soup = BeautifulSoup(html, "html.parser")
    groups = {}
    for a in soup.find_all("a", href=SBC_HREF):
        groups.setdefault(urljoin(BASE, a["href"]), []).append(a)

    if not groups:
        sys.exit("No SBC cards found - the page layout may have changed.")
    print(f"Found {len(groups)} SBCs on the page")

    new = []
    for url, anchors in groups.items():
        if not any(a.find(string=NEW_BADGE) for a in anchors):
            continue
        title = clean_title(anchors) or url.rstrip("/").split("/")[-1]
        card = card_container(anchors[0])
        new.append(
            {
                "url": url,
                "title": title,
                "description": find_description(anchors, title),
                "rewards": find_rewards(card),
                "image": find_image(card, url),
            }
        )
    return new


def to_embed(sbc):
    description = sbc["description"]
    if sbc["rewards"]:
        description += "\n\n**Rewards:** " + ", ".join(sbc["rewards"])
    embed = {
        "title": f"🆕 {sbc['title']}"[:256],
        "url": sbc["url"],
        "description": description.strip()[:4000],
        "color": 0x2ECC71,
    }
    if sbc["image"]:
        embed["image"] = {"url": sbc["image"]}
    return embed


def post(embeds):
    for i in range(0, len(embeds), 10):  # Discord allows 10 embeds per message
        payload = {"embeds": embeds[i : i + 10]}
        if i == 0:
            payload["content"] = HEADER
        r = requests.post(WEBHOOK, json=payload, timeout=30)
        r.raise_for_status()
        time.sleep(1)

    if FOOTER:  # flags=4 stops Discord adding a big link preview under the footer
        r = requests.post(WEBHOOK, json={"content": FOOTER, "flags": 4}, timeout=30)
        r.raise_for_status()


def main():
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_WEBHOOK_URL is not set")

    posted = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    new = [s for s in find_new_sbcs(get(LIST_URL)) if TEST_MODE or s["url"] not in posted]
    print(f"{len(new)} new SBC(s) to post" + (" (test mode)" if TEST_MODE else ""))
    if not new:
        return

    embeds = [to_embed(s) for s in new]
    if DRY_RUN:
        print(json.dumps(embeds, indent=2, ensure_ascii=False))
        return

    post(embeds)
    STATE_FILE.write_text(json.dumps(sorted(posted | {s["url"] for s in new}), indent=2))


if __name__ == "__main__":
    main()
