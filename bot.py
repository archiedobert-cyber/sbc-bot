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
HEADER = "# 🚨🆕 **NEW SBC ALERT** 🆕🚨"

# Message posted at the very bottom, after all the SBC cards.
# Edit the text/emojis/link however you like, or set it to "" for no footer.
FOOTER = ""

# Role to ping in the footer (pings once per post). Paste the role's ID - numbers
# only, e.g. "123456789012345678" - or leave as "" for no ping.
PING_ROLE_ID = "1551540516238131270"


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
    """Find an SBC-specific image using several possible sources."""

    def is_generic(src):
        if not src:
            return True

        src = src.lower()

        return (
            "fut-social" in src
            or "favicon" in src
            or "logo" in src
            or "placeholder" in src
            or "default-image" in src
        )

    def clean(src):
        if not src or src.startswith("data:"):
            return None

        src = urljoin(BASE, src.strip())

        if is_generic(src):
            return None

        return src

    # 1) Images directly on the SBC card
    for img in card.find_all("img"):
        for attr in (
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-lazy",
            "data-image",
            "data-url",
        ):
            image = clean(img.get(attr))
            if image:
                return image

        # Check lazy-loaded srcset
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for item in srcset.split(","):
                image = clean(item.strip().split()[0])
                if image:
                    return image

    # 2) Fetch the SBC page once and check its metadata
    try:
        page = BeautifulSoup(get(url), "html.parser")

        # Open Graph
        for prop in ("og:image", "og:image:url"):
            meta = page.find("meta", attrs={"property": prop})

            if meta:
                image = clean(meta.get("content"))
                if image:
                    return image

        # Twitter/X image
        for name in ("twitter:image", "twitter:image:src"):
            meta = page.find("meta", attrs={"name": name})

            if meta:
                image = clean(meta.get("content"))
                if image:
                    return image

        # 3) Check every image on the SBC page
        for img in page.find_all("img"):
            for attr in (
                "src",
                "data-src",
                "data-original",
                "data-lazy-src",
                "data-lazy",
                "data-image",
                "data-url",
            ):
                image = clean(img.get(attr))

                if image:
                    return image

            srcset = img.get("srcset") or img.get("data-srcset")

            if srcset:
                for item in srcset.split(","):
                    image = clean(item.strip().split()[0])

                    if image:
                        return image

    except requests.RequestException as e:
        print(f"Could not fetch SBC page for image: {e}")

    return None

    def is_generic(src):
        if not src:
            return True

        src = src.lower()

        return (
            "fut-social" in src
            or "favicon" in src
            or "logo" in src
            or "placeholder" in src
            or "default-image" in src
        )

    def clean(src):
        if not src or src.startswith("data:"):
            return None

        src = urljoin(BASE, src.strip())

        if is_generic(src):
            return None

        return src

    # 1) Images directly on the SBC card
    for img in card.find_all("img"):
        for attr in (
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-lazy",
            "data-image",
            "data-url",
        ):
            image = clean(img.get(attr))
            if image:
                return image

        # Check lazy-loaded srcset
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for item in srcset.split(","):
                image = clean(item.strip().split()[0])
                if image:
                    return image

    # 2) Fetch the SBC page once and check its metadata
    try:
        page = BeautifulSoup(get(url), "html.parser")

        # Open Graph
        for prop in ("og:image", "og:image:url"):
            meta = page.find("meta", attrs={"property": prop})

            if meta:
                image = clean(meta.get("content"))
                if image:
                    return image

        # Twitter/X image
        for name in ("twitter:image", "twitter:image:src"):
            meta = page.find("meta", attrs={"name": name})

            if meta:
                image = clean(meta.get("content"))
                if image:
                    return image

        # 3) Check every image on the SBC page
        for img in page.find_all("img"):
            for attr in (
                "src",
                "data-src",
                "data-original",
                "data-lazy-src",
                "data-lazy",
                "data-image",
                "data-url",
            ):
                image = clean(img.get(attr))

                if image:
                    return image

            srcset = img.get("srcset") or img.get("data-srcset")

            if srcset:
                for item in srcset.split(","):
                    image = clean(item.strip().split()[0])

                    if image:
                        return image

    except requests.RequestException as e:
        print(f"Could not fetch SBC page for image: {e}")

    return None

def to_embed(sbc):
    description = f"## {sbc['title']}]({sbc['url']})"

    if sbc["description"]:
        description += "\n\n" + sbc["description"]

    if sbc["requirements"]:
        description += (
            "\n\n## 🧩 Requirements\n"
            + "\n".join(sbc["requirements"])
        )

    if sbc["rewards"]:
        description += (
            "\n\n## 🎁 Rewards\n"
            + "\n".join(sbc["rewards"])
        )

    embed = {
        "description": description.strip()[:4000],
        "color": 0x2ECC71,
    }

    if sbc["image"]:
        embed["image"] = {
            "url": sbc["image"]
        }

    print("DEBUG EMBED:")
    print(json.dumps(embed, indent=2, ensure_ascii=False))

    return embed

def find_requirements(page):
    """Extract SBC requirements while keeping their original wording."""

    requirements = []

    # Look at individual visible elements first.
    for element in page.find_all(["li", "p", "div", "span", "td"]):
        text = element.get_text(" ", strip=True)

        if not text or len(text) > 180:
            continue

        lower = text.lower()

        if any(term in lower for term in (
            "min.",
            "minimum",
            "max.",
            "maximum",
            "players from",
            "squad rating",
            "team chemistry",
            "number of players",
            "overall rating",
            "rating:",
            "chemistry:",
            "league:",
            "club:",
            "nation:",
            "country:",
            "position:",
            "positions:",
            "rare players",
            "gold players",
            "silver players",
            "bronze players",
        )):
            text = re.sub(r"\s+", " ", text).strip()

            if text not in requirements:
                requirements.append(text)

    # Remove duplicate entries caused by nested HTML elements.
    cleaned = []

    for requirement in requirements:
        if any(
            requirement != other
            and requirement in other
            for other in requirements
        ):
            continue

        cleaned.append(requirement)

    return cleaned
  

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

        # Fetch the SBC page once so we can use it for both
        # requirements and image detection.
        try:
            page = BeautifulSoup(get(url), "html.parser")
        except requests.RequestException as e:
            print(f"Could not fetch SBC page {url}: {e}")
            page = None

        requirements = find_requirements(page) if page else []

        new.append(
            {
                "url": url,
                "title": title,
                "description": find_description(anchors, title),
                "rewards": find_rewards(card),
                "requirements": requirements,
                "image": find_image(card, url),
            }
        )

    return new

def post(embeds):
    for i in range(0, len(embeds), 10):  # Discord allows 10 embeds per message
        payload = {"embeds": embeds[i : i + 10]}
        if i == 0:
            payload["content"] = HEADER
        r = requests.post(WEBHOOK, json=payload, timeout=30)
        r.raise_for_status()
        time.sleep(1)

    if FOOTER or PING_ROLE_ID:
        content = f"<@&{PING_ROLE_ID}> {FOOTER}".strip() if PING_ROLE_ID else FOOTER
        # flags=4 stops Discord adding a big link preview under the footer
        payload = {"content": content, "flags": 4}
        if PING_ROLE_ID:  # only this role can be pinged, nothing else
            payload["allowed_mentions"] = {"roles": [PING_ROLE_ID]}
        r = requests.post(WEBHOOK, json=payload, timeout=30)
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
