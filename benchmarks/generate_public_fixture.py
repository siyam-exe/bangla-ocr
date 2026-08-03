from __future__ import annotations

import argparse
import html
import io
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "fixture"
GROUND_TRUTH_ROOT = FIXTURE_ROOT / "ground-truth"
RENDER_ROOT = FIXTURE_ROOT / "renders"
PAGE_SIZE = (1240, 1754)


PAGES = [
    """নদীর ধারে পুরোনো পাঠাগার

ভোরের আলো ফুটতেই নীল নদীর পাশের সরু পথ ধরে হাঁটতে বের হলো। রাতের বৃষ্টিতে মাটির গন্ধ আরও স্পষ্ট হয়ে উঠেছে। দূরের তালগাছের পাতায় জমে থাকা পানি টুপটাপ শব্দে নিচে পড়ছে।

পথের শেষে বহু দিনের পুরোনো একটি পাঠাগার। কাঠের দরজায় রং নেই, জানালার কাচেও ধুলোর আস্তরণ। তবু প্রতিদিন ঠিক আটটায় দরজাটি খুলে দেন বৃদ্ধ গ্রন্থরক্ষক আজিজ সাহেব।

নীল দরজার সামনে দাঁড়িয়ে বলল, ‘আজ কি ভেতরের বন্ধ ঘরটি দেখা যাবে?’ আজিজ সাহেব মৃদু হেসে পকেট থেকে ছোট পিতলের চাবিটি বের করলেন।""",
    """এক

ঘরটির ভেতর বাতাস ভারী, কিন্তু বইগুলোর গন্ধ পরিচিত। দেয়ালজুড়ে উঁচু তাক; কোথাও ইতিহাস, কোথাও ভ্রমণকাহিনি, আবার কোথাও হাতে লেখা পুরোনো পুঁথি। জানালার ফাঁক দিয়ে আসা আলোয় ধুলোর কণা ভেসে বেড়াচ্ছে।

আজিজ সাহেব বললেন, ‘বই শুধু পড়ার জিনিস নয়। একটি বই কোথায় ছিল, কে পড়েছে এবং কীভাবে বেঁচে আছে—সেই ইতিহাসও রক্ষা করতে হয়।’

নীল একটি মোটা খাতা খুলল। প্রথম পাতায় কালো কালিতে লেখা: এই সংগ্রহ নদীর বন্যা, আগুন এবং মানুষের অবহেলা পেরিয়ে এখানে এসেছে। কোনো পৃষ্ঠা নষ্ট হলে অনুমান করে লেখা যাবে না; মূল কপির সাক্ষ্য রাখতে হবে।""",
    """দুই

দুপুরের দিকে আকাশ আবার অন্ধকার হয়ে এল। ঝড় শুরু হওয়ার আগে তারা নিচের ঘর থেকে কয়েকটি ভেজা বাক্স ওপরে তুলল। একটি বাক্সের গায়ে লেখা সাল প্রায় মুছে গেছে, শুধু ১৩৭২ সংখ্যাটি বোঝা যায়।

বাক্স খুলতেই দেখা গেল পাতাগুলো ঢেউ খেলানো এবং কোনো কোনো অক্ষর ঝাপসা। নীল তাড়াতাড়ি মোবাইল বের করলে আজিজ সাহেব তাকে থামালেন। ‘প্রথমে আলো ঠিক করো। তারপর প্রতিটি পৃষ্ঠা সোজা করে ছবি তুলবে। তাড়াহুড়ো করলে লেখা আরও হারিয়ে যাবে।’

তারা জানালার পাশে সাদা কাপড় টাঙিয়ে নরম আলো তৈরি করল। প্রতিটি ছবির সঙ্গে বইয়ের নাম, পৃষ্ঠার ক্রম এবং অবস্থার সংক্ষিপ্ত বিবরণ লিখে রাখা হলো।""",
    """তিন

সন্ধ্যায় বিদ্যুৎ চলে গেল। টেবিলের ওপর ছোট বাতি জ্বালিয়ে নীল দিনের ছবিগুলো পরীক্ষা করতে বসলো। বেশির ভাগ লেখা পরিষ্কার, কিন্তু কয়েকটি লাইনে কালি ছড়িয়ে অক্ষরের মাত্রা একসঙ্গে মিশে গেছে।

কম্পিউটার একটি শব্দকে তিনভাবে পড়েছে। নীল কোনোটি সঙ্গে সঙ্গে গ্রহণ করল না। সে ছবির সংশ্লিষ্ট অংশ বড় করে দেখল, পাশের বাক্য পড়ল এবং সন্দেহজনক শব্দটির পাশে একটি চিহ্ন রাখল।

আজিজ সাহেব বললেন, ‘যন্ত্র আমাদের দ্রুত পথ দেখাতে পারে, কিন্তু মূল পাতাটি শেষ সাক্ষী। সন্দেহ থাকলে তা লুকাবে না। পাঠক যেন বুঝতে পারে কোথায় মানুষ সিদ্ধান্ত নিয়েছে।’""",
    """চার

পরদিন সকালে কাজ শেষ হলে তারা তিনটি কপি তৈরি করল। একটি অপরিবর্তিত ছবি, একটি পড়ার উপযোগী প্রতিলিপি এবং একটি নথি—যেখানে প্রতিটি সংশোধনের কারণ লেখা আছে।

নীল খেয়াল করল, পুরোনো বইকে নতুন করে লেখা তাদের উদ্দেশ্য নয়। বানান অচেনা হলেও তা বদলানো যাবে না, অনুচ্ছেদ ছোট মনে হলেও জোড়া যাবে না, আর কোনো বাক্য সুন্দর করার জন্য নতুন শব্দ বসানো যাবে না।

পাঠাগারের দরজা বন্ধ করার সময় নদীর ওপরে রোদ উঠেছে। নীল বুঝল, সংরক্ষণ মানে শুধু অতীত জমিয়ে রাখা নয়; ভবিষ্যতের পাঠকের কাছে সৎ প্রমাণ পৌঁছে দেওয়া।""",
]


def find_browser(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    ]
    for command in ("msedge", "google-chrome", "chromium", "chrome"):
        discovered = shutil.which(command)
        if discovered:
            candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No Chromium browser found. Pass --browser with Edge/Chrome/Chromium."
    )


def browser_render(text: str, browser: Path, index: int) -> Image.Image:
    scratch = ROOT.parent / "tmp" / "pdfs" / "benchmark-fixture"
    scratch.mkdir(parents=True, exist_ok=True)
    html_path = scratch / f"page-{index + 1:04d}.html"
    screenshot_path = scratch / f"page-{index + 1:04d}.png"
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    body = "".join(
        (
            f"<h1>{html.escape(part)}</h1>"
            if position == 0 and len(part) <= 40
            else f"<p>{html.escape(part)}</p>"
        )
        for position, part in enumerate(paragraphs)
    )
    document = f"""<!doctype html>
<html lang="bn"><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
html, body {{ width: 1240px; height: 1754px; margin: 0; overflow: hidden; }}
body {{ padding: 105px 95px; background: #f7f5ef; color: #191919;
  font-family: "Nirmala UI", "Noto Serif Bengali", serif; }}
h1 {{ margin: 0 0 54px; font-size: 55px; line-height: 1.3; font-weight: 700; }}
p {{ margin: 0 0 34px; font-size: 42px; line-height: 1.62; text-align: left; }}
</style><body>{body}</body></html>"""
    html_path.write_text(document, encoding="utf-8", newline="\n")
    command = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={PAGE_SIZE[0]},{PAGE_SIZE[1]}",
        f"--screenshot={screenshot_path}",
        f"--user-data-dir={scratch / 'browser-profile'}",
        html_path.resolve().as_uri(),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if completed.returncode or not screenshot_path.exists():
        raise RuntimeError(
            f"Browser rendering failed ({completed.returncode}): {completed.stderr}"
        )
    return Image.open(screenshot_path).convert("L")


def render_page(text: str, browser: Path, index: int) -> Image.Image:
    image = browser_render(text, browser, index)
    rng = random.Random(2401 + index)
    if index == 1:
        image = ImageEnhance.Contrast(image).enhance(0.58)
        image = image.filter(ImageFilter.GaussianBlur(0.85))
    elif index == 2:
        image = image.rotate(1.25, resample=Image.Resampling.BICUBIC, fillcolor=247)
        pixels = np.asarray(image).copy()
        for _ in range(1550):
            x = rng.randrange(pixels.shape[1])
            y = rng.randrange(pixels.shape[0])
            pixels[y, x] = rng.choice((50, 90, 210))
        image = Image.fromarray(pixels)
    elif index == 3:
        gradient = np.tile(np.linspace(0.72, 1.0, PAGE_SIZE[0]), (PAGE_SIZE[1], 1))
        pixels = np.asarray(image, dtype=np.float32) * gradient
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=38)
        buffer.seek(0)
        image = Image.open(buffer).convert("L")
    elif index == 4:
        image = image.resize((930, 1316), Image.Resampling.LANCZOS).resize(
            PAGE_SIZE, Image.Resampling.BICUBIC
        )
        image = image.filter(ImageFilter.GaussianBlur(0.55))
    return image.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    browser = find_browser(args.browser)
    GROUND_TRUTH_ROOT.mkdir(parents=True, exist_ok=True)
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    images: list[Image.Image] = []
    for index, text in enumerate(PAGES):
        page_number = index + 1
        normalized = text.strip() + "\n"
        (GROUND_TRUTH_ROOT / f"page-{page_number:04d}.txt").write_text(
            normalized, encoding="utf-8", newline="\n"
        )
        image = render_page(text, browser, index)
        image.save(RENDER_ROOT / f"page-{page_number:04d}.png", optimize=True)
        images.append(image)
    output = FIXTURE_ROOT / "bangla-preservation-benchmark.pdf"
    images[0].save(
        output,
        save_all=True,
        append_images=images[1:],
        resolution=150,
        quality=92,
    )
    for image in images:
        image.close()
    shutil.rmtree(ROOT.parent / "tmp" / "pdfs" / "benchmark-fixture", ignore_errors=True)
    print(output)


if __name__ == "__main__":
    main()
