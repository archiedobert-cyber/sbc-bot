```python
"""Posts SBCs labelled "New" on fut.gg/sbc to a Discord channel via webhook.

Env vars:
  DISCORD_WEBHOOK_URL  webhook to post to (GitHub secret)
  DRY_RUN=1            print what would be posted instead of sending it
  TEST_MODE=1          post every current "New" SBC, even if already posted
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
STATE_FILE = Path("posted.json")

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TEST_MODE = os.environ.get("TEST_MODE") == "1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SBC_HREF = re.compile(
    r"^(?:https://www\.fut\.gg)?/sbc/"
    r"(?:challenges|players|upgrades|foundations)/[^/]+/?$"
)

NEW_BADGE = re.compile(r"^\s*new\s*$", re.I)

# Generic/site-wide images that should never be used as an SBC image.
GENERIC_IMAGE_MARKERS = (
    "fut-social",
    "futgg",
    "fut.gg/logo",
    "logo.png",
    "logo.webp",
    "favicon",
    "default-image",
    "default.jpg",
    "default.webp",
    "placeholder",
)

HEADER = "## 🚨🆕 **NEW SBC ALERT** 🆕🚨"

FOOTER = ""

PING_ROLE_ID = "1551540516238131270"


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def is_generic_image(url):
    """Return True when an image appears to be a generic/site-wide image."""
    if not url:
        return True

    value = url.lower()

    return any(marker in value for marker in GENERIC_IMAGE_MARKERS)


def clean_image_url(url, base_url=BASE):
    """Turn an image URL into an absolute URL and reject generic images."""
    if not url:
        return None

    url = url.strip()

    if not url or url.startswith("data:"):
        return None

    url = urljoin(base_url, url)

    if is_generic_image(url):
        return None

    return url


def card_container(anchor):
    """Walk up from a link until the parent holds more than one SBC."""
    node = anchor

    while node.parent is not None and node.parent.name not in ("body", "html"):
        hrefs = {
            a["href"]
            for a in node.parent.find_all("a", href=SBC_HREF)
        }

        if len(hrefs) > 1:
            break

        node = node.parent

    return node


def clean_title(anchors):
    for a in anchors:
        parts = [
            p for p in a.stripped_strings
            if not NEW_BADGE.match(p)
        ]

        if parts:
            return re.sub(
                r"\s+[\d,]{3,}$",
                "",
                parts[0]
            ).strip()

    return None


def find_description(anchors, title):
    texts = [
        a.get_text(" ", strip=True)
        for a in anchors
    ]

    candidates = [
        t for t in texts
        if t.startswith(title)
        and len(t) > len(title) + 15
    ]

    if not candidates:
        return ""

    return max(
        candidates,
        key=len
    )[len(title):].strip()


def find_rewards(card):
    """Reward lines such as packs/coins."""
    rewards = []

    for s in card.find_all(string=True):
        text = s.strip()

        if (
            text
            and not s.find_parent("a")
            and re.search(r"pack|coins|pick|boost", text, re.I)
        ):
            if text not in rewards:
                rewards.append(text)

    return rewards


def get_image_candidates_from_img(img):
    """Return all possible image URLs from an <img> element."""
    candidates = []

    for attr in (
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-lazy",
        "data-image",
        "data-url",
        "data-fallback-src",
    ):
        value = img.get(attr)

        if value:
            candidates.append(value)

    # srcset may contain several image sizes.
    srcset = img.get("srcset") or img.get("data-srcset")

    if srcset:
        for item in srcset.split(","):
            item = item.strip()

            if item:
                candidates.append(item.split()[0])

    return candidates


def find_image(card, page):
    """
    Find the best SBC-specific image.

    Checks:
      1. Images on the SBC card.
      2. Lazy-loaded image attributes.
      3. srcset.
      4. Open Graph.
      5. Twitter card image.
      6. JSON-LD.
      7. Embedded page data.
    """

    # ---------------------------------------------------------
    # 1. Images directly on the SBC card
    # ---------------------------------------------------------

    for img in card.find_all("img"):
        for candidate in get_image_candidates_from_img(img):
            image = clean_image_url(candidate)

            if image:
                return image

    # ---------------------------------------------------------
    # 2. Open Graph / Twitter metadata
    # ---------------------------------------------------------

    meta_selectors = [
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"},
    ]

    for selector in meta_selectors:
        meta = page.find("meta", attrs=selector)

        if meta and meta.get("content"):
            image = clean_image_url(meta["content"])

            if image:
                return image

    # ---------------------------------------------------------
    # 3. JSON-LD structured data
    # ---------------------------------------------------------

    for script in page.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())

        except (json.JSONDecodeError, TypeError):
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            image = obj.get("image")

            if isinstance(image, str):
                image = clean_image_url(image)

                if image:
                    return image

            if isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        image = clean_image_url(item)

                        if image:
                            return image

                    elif isinstance(item, dict):
                        image = clean_image_url(
                            item.get("url")
                        )

                        if image:
                            return image

            if isinstance(image, dict):
                image = clean_image_url(
                    image.get("url")
                )

                if image:
                    return image

    # ---------------------------------------------------------
    # 4. Search embedded page data for image URLs
    # ---------------------------------------------------------

    page_text = str(page)

    image_patterns = [
        r'"image"\s*:\s*"([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
        r'"image_url"\s*:\s*"([^"]+)"',
        r'"thumbnailUrl"\s*:\s*"([^"]+)"',
        r'"thumbnail"\s*:\s*"([^"]+)"',
    ]

    for pattern in image_patterns:
        for match in re.findall(pattern, page_text, re.I):
            image = clean_image_url(
                match.replace("\\/", "/")
            )

            if image:
                return image

    # ---------------------------------------------------------
    # 5. Last resort: any reasonable image on the page
    # ---------------------------------------------------------

    for img in page.find_all("img"):
        for candidate in get_image_candidates_from_img(img):
            image = clean_image_url(candidate)

            if image:
                return image

    return None


def clean_requirement(text):
    """Clean one requirement while keeping its original wording."""
    text = re.sub(r"\s+", " ", text).strip()

    # Remove obvious decorative bullets.
    text = re.sub(r"^[•·▪●◦\-]+\s*", "", text)

    return text.strip()


def looks_like_requirement(text):
    """
    Decide whether a line looks like an SBC requirement.

    This deliberately looks for common FUT/SBC requirement wording
    rather than trying to shorten or rewrite the requirement.
    """

    if not text:
        return False

    lower = text.lower()

    requirement_terms = (
        "min.",
        "minimum",
        "max.",
        "maximum",
        "players from",
        "players:",
        "player:",
        "squad rating",
        "team chemistry",
        "chemistry",
        "number of players",
        "number of",
        "rating:",
        "overall rating",
        "overall:",
        "rare players",
        "gold players",
        "silver players",
        "bronze players",
        "league:",
        "club:",
        "nation:",
        "country:",
        "position:",
        "positions:",
        "playstyle",
        "playstyles",
        "tots",
        "totw",
        "if players",
        "inform players",
    )

    return any(term in lower for term in requirement_terms)


def find_requirements(page):
    """
    Extract SBC requirements and keep each requirement on its own line.
    """

    candidates = []

    # First look through normal visible text.
    for element in page.find_all(
        ["li", "p", "div", "span", "td"]
    ):
        text = element.get_text(" ", strip=True)

        if not text:
            continue

        if len(text) > 180:
            continue

        if looks_like_requirement(text):
            candidates.append(clean_requirement(text))

    # Also inspect individual lines from the page text.
    for line in page.get_text("\n").splitlines():
        line = clean_requirement(line)

        if not line or len(line) > 180:
            continue

        if looks_like_requirement(line):
            candidates.append(line)

    # Remove duplicates while preserving order.
    requirements = []

    for item in candidates:
        if item not in requirements:
            requirements.append(item)

    # Remove parent/container duplicates where a longer string contains
    # a shorter requirement verbatim.
    filtered = []

    for item in requirements:
        if any(
            item != other
            and item in other
            and len(other) > len(item)
            for other in requirements
        ):
            continue

        filtered.append(item)

    return filtered


def get_sbc_page(url):
    """Fetch and parse an individual SBC page once."""
    try:
        return BeautifulSoup(
            get(url),
            "html.parser"
        )
    except requests.RequestException as e:
        print(f"Could not fetch SBC page {url}: {e}")
        return None


def find_new_sbcs(html):
    soup = BeautifulSoup(html, "html.parser")

    groups = {}

    for a in soup.find_all("a", href=SBC_HREF):
        groups.setdefault(
            urljoin(BASE, a["href"]),
            []
        ).append(a)

    if not groups:
        sys.exit(
            "No SBC cards found - the page layout may have changed."
        )

    print(f"Found {len(groups)} SBCs on the page")

    new = []

    for url, anchors in groups.items():

        if not any(
            a.find(string=NEW_BADGE)
            for a in anchors
        ):
            continue

        title = (
            clean_title(anchors)
            or url.rstrip("/").split("/")[-1]
        )

        card = card_container(anchors[0])

        # Fetch the SBC page once.
        page = get_sbc_page(url)

        requirements = []
        image = None

        if page:
            requirements = find_requirements(page)
            image = find_image(card, page)

        new.append(
            {
                "url": url,
                "title": title,
                "description": find_description(
                    anchors,
                    title
                ),
                "rewards": find_rewards(card),
                "requirements": requirements,
                "image": image,
            }
        )

    return new


def to_embed(sbc):

    description = sbc["description"]

    if sbc["requirements"]:
        description += (
            "\n\n**Requirements:**\n"
            + "\n".join(sbc["requirements"])
        )

    if sbc["rewards"]:
        description += (
            "\n\n**Rewards:** "
            + ", ".join(sbc["rewards"])
        )

    embed = {
        "title": f"🆕 {sbc['title']}"[:256],
        "url": sbc["url"],
        "description": description.strip()[:4000],
        "color": 0x2ECC71,
    }

    if sbc["image"]:
        embed["image"] = {
            "url": sbc["image"]
        }

    return embed


def post(embeds):

    for i in range(0, len(embeds), 10):

        payload = {
            "embeds": embeds[i:i + 10]
        }

        if i == 0:
            payload["content"] = HEADER

        r = requests.post(
            WEBHOOK,
            json=payload,
            timeout=30
        )

        r.raise_for_status()

        time.sleep(1)

    if FOOTER or PING_ROLE_ID:

        content = (
            f"<@&{PING_ROLE_ID}> {FOOTER}".strip()
            if PING_ROLE_ID
            else FOOTER
        )

        payload = {
            "content": content,
            "flags": 4
        }

        if PING_ROLE_ID:
            payload["allowed_mentions"] = {
                "roles": [PING_ROLE_ID]
            }

        r = requests.post(
            WEBHOOK,
            json=payload,
            timeout=30
        )

        r.raise_for_status()


def main():

    if not WEBHOOK and not DRY_RUN:
        sys.exit(
            "DISCORD_WEBHOOK_URL is not set"
        )

    posted = (
        set(json.loads(STATE_FILE.read_text()))
        if STATE_FILE.exists()
        else set()
    )

    new = [
        s
        for s in find_new_sbcs(get(LIST_URL))
        if TEST_MODE or s["url"] not in posted
    ]

    print(
        f"{len(new)} new SBC(s) to post"
        + (" (test mode)" if TEST_MODE else "")
    )

    if not new:
        return

    embeds = [
        to_embed(s)
        for s in new
    ]

    if DRY_RUN:
        print(
            json.dumps(
                embeds,
                indent=2,
                ensure_ascii=False
            )
        )
        return

    post(embeds)

    STATE_FILE.write_text(
        json.dumps(
            sorted(
                posted
                | {s["url"] for s in new}
            ),
            indent=2
        )
    )


if __name__ == "__main__":
    main()
```
