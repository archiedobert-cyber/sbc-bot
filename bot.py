"""Posts SBCs labelled "New" on fut.gg/sbc to a Discord channel via webhook.

Env vars:
  DISCORD_WEBHOOK_URL  webhook to post to (GitHub secret)
  DRY_RUN=1            print what would be posted instead of sending it
  TEST_MODE=1          post every current "New" SBC, even if already posted
  TEST_URL=<sbc link>  post just this one SBC page, skipping the site scan
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
TEST_MODE = os.environ.get("TEST_MODE") == "1"
TEST_URL = os.environ.get("TEST_URL", "").strip()

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
        if s.find_parent(("script", "style")):
            continue  # never scan embedded JS/CSS - it isn't visible reward text
        text = s.strip()
        if (
            text
            and len(text) <= 150
            and not s.find_parent("a")
            and re.search(r"pack|coins|pick|boost", text, re.I)
        ):
            rewards.append(text)

    # Drop description sentences (e.g. "Earn a pack containing 2 Gold Player
    # Items, rated 79 or higher.") and keep just the reward names. If that would
    # leave nothing, keep everything rather than post an empty section.
    names = [r for r in rewards if not r.lower().startswith("earn ") and not r.endswith(".")]
    return names or rewards


def find_player_award(page):
    """If the SBC's reward is a player card, return 'Name - OVR - Rarity'."""
    m = re.search(
        r'overall:(\d+),commonName:"([^"]+)",cardName:"[^"]+",rarityName:"([^"]+)"',
        page_text(page),
    )
    if not m:
        return None
    ovr, name, rarity = m.groups()
    return f"{name} - {ovr} - {rarity}"


def find_player_card_image(page):
    """The actual player card artwork, when the SBC's reward is a player."""
    m = re.search(r'cardImageUrl:"([^"]+)"', page_text(page))
    return m.group(1).replace("\\/", "/") if m else None


IMAGE_ATTRS = (
    "src",
    "data-src",
    "data-original",
    "data-lazy-src",
    "data-lazy",
    "data-image",
    "data-url",
)


def find_image(card, url, page=None):
    """Find an SBC-specific image, preferring fut.gg's own SBC artwork."""

    # A player-card reward has its own artwork - use that in preference to
    # any generic SBC icon/thumbnail if we can find it.
    if page is not None:
        player_image = find_player_card_image(page)
        if player_image:
            return player_image

    def is_generic(src):
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

    def sources(img):
        """Every image URL an <img> tag might carry (src, lazy-load attrs, srcset)."""
        for attr in IMAGE_ATTRS:
            yield img.get(attr)
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for item in srcset.split(","):
                parts = item.strip().split()
                if parts:
                    yield parts[0]

    def first_image(container, must_contain=None):
        for img in container.find_all("img"):
            for src in sources(img):
                image = clean(src)
                if image and (must_contain is None or must_contain in image):
                    return image
        return None

    # 1) fut.gg's own artwork for this SBC (game-assets.fut.gg/.../sbcs/...)
    image = first_image(card, "/sbcs/")
    if image:
        return image

    if page is None:
        try:
            page = BeautifulSoup(get(url), "html.parser")
        except requests.RequestException as e:
            print(f"Could not fetch SBC page for image: {e}")

    if page is not None:
        image = first_image(page, "/sbcs/")
        if image:
            return image

    # 2) Any other image on the card
    image = first_image(card)
    if image:
        return image

    if page is not None:
        # 3) Page metadata (Open Graph, then Twitter/X)
        for attr_name, names in (
            ("property", ("og:image", "og:image:url")),
            ("name", ("twitter:image", "twitter:image:src")),
        ):
            for name in names:
                meta = page.find("meta", attrs={attr_name: name})
                if meta:
                    image = clean(meta.get("content"))
                    if image:
                        return image

        # 4) Any image on the SBC page
        return first_image(page)

    return None


REQUIREMENT_TERMS = (
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
)


def find_requirements(page):
    """Extract SBC requirements while keeping their original wording."""

    def collect(elements):
        found = []
        for element in elements:
            text = element.get_text(" ", strip=True)
            if not text or len(text) > 180:
                continue
            if any(term in text.lower() for term in REQUIREMENT_TERMS):
                text = re.sub(r"\s+", " ", text).strip()
                if text not in found:
                    found.append(text)
        return found

    def dedupe(items):
        # Drop entries that are just part of a longer entry (nested HTML elements).
        return [r for r in items if not any(r != o and r in o for o in items)]

    # Requirements are bullet-list items on the SBC page, so try those first.
    requirements = dedupe(collect(page.find_all("li")))
    if requirements:
        return requirements

    # Only fall back to broader elements if there are no matching list items.
    return dedupe(collect(page.find_all(["p", "div", "span", "td"])))


def page_text(page):
    """Raw page HTML with escaped JSON quotes un-escaped, for regex searching."""
    return str(page).replace('\\"', '"')


def find_expires_in(page):
    """fut.gg's own 'expires in' text (e.g. '6 days'), or '' if it isn't there."""
    m = re.search(r'expiresIn:"([^"]+)"', page_text(page))
    return m.group(1).strip() if m else ""


def find_repeatable(page):
    """How many times the SBC can be repeated (e.g. '3x'), or '' if not repeatable."""
    html = page_text(page)
    if "isRepeatable:!1" in html:
        return ""
    m = re.search(r"numberOfRepeats:(\d+)", html)
    if m and int(m.group(1)) > 0:
        return f"{m.group(1)}x"
    return ""


def find_score(page):
    """fut.gg's SBC score/points value (the number next to the diamond icon on
    the site), e.g. '2,500', or '' if not found."""
    html = page_text(page)
    m = re.search(r'scoreRequirement:(\d+)', html)
    if m:
        return f"{int(m.group(1)):,}"
    return ""


def to_embed(sbc):
    description = f"## 🆕 {sbc['title']}\n[More details]({sbc['url']})"

    if sbc["description"]:
        description += "\n" + sbc["description"]

    if sbc["requirements"]:
        description += (
            "\n## 🧩 Requirements\n"
            + "\n".join(sbc["requirements"])
        )

    if sbc["rewards"]:
        description += (
            "\n## 🎁 Rewards\n"
            + "\n".join(sbc["rewards"])
        )

    if sbc["repeatable"]:
        description += f"\n## 🔁 Repeatable\n{sbc['repeatable']}"

    if sbc["expires"]:
        description += f"\n## ⏰ Available for\n{sbc['expires']}"

    embed = {
        "description": description.strip()[:4000],
        "color": 0x2ECC71,
    }

    if sbc["image"]:
        embed["thumbnail"] = {"url": sbc["image"]}

    print("DEBUG EMBED:")
    print(json.dumps(embed, indent=2, ensure_ascii=False))

    return embed


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

        # Fetch the SBC page once so we can use it for requirements,
        # expiry and repeat count.
        try:
            page = BeautifulSoup(get(url), "html.parser")
        except requests.RequestException as e:
            print(f"Could not fetch SBC page {url}: {e}")
            page = None

        requirements = find_requirements(page) if page else []
        expires = find_expires_in(page) if page else ""
        repeatable = find_repeatable(page) if page else ""
        score = find_score(page) if page else ""
        if score:
            requirements.append(f"💎 Score: {score}")

        new.append(
            {
                "url": url,
                "title": title,
                "description": find_description(anchors, title),
                "rewards": find_rewards(card) or ([find_player_award(page)] if page and find_player_award(page) else []),
                "requirements": requirements,
                "expires": expires,
                "repeatable": repeatable,
                "score": score,
                "image": find_image(card, url, page),
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


def build_sbc(url, page):
    """Build an sbc dict straight from one SBC page (used by TEST_URL)."""
    title_tag = page.find("h1") or page.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else url.rstrip("/").split("/")[-1]
    title = re.sub(r"\s*-\s*EA SPORTS FC.*$", "", title).strip()

    requirements = find_requirements(page)
    score = find_score(page)
    if score:
        requirements.append(f"💎 Score: {score}")

    return {
        "url": url,
        "title": title,
        "description": "",
        "rewards": find_rewards(page) or ([find_player_award(page)] if find_player_award(page) else []),
        "requirements": requirements,
        "expires": find_expires_in(page),
        "repeatable": find_repeatable(page),
        "score": score,
        "image": find_image(page, url, page),
    }


def main():
    if not WEBHOOK and not DRY_RUN:
        sys.exit("DISCORD_WEBHOOK_URL is not set")

    if TEST_URL:
        print(f"TEST_URL set - posting just this one SBC: {TEST_URL}")
        page = BeautifulSoup(get(TEST_URL), "html.parser")
        embed = to_embed(build_sbc(TEST_URL, page))
        if DRY_RUN:
            print(json.dumps([embed], indent=2, ensure_ascii=False))
        else:
            post([embed])
        return

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
