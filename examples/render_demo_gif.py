"""Render the README terminal demo GIF from the verified dry-run flow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1100, 620
BACKGROUND = "#0d1117"
PANEL = "#161b22"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
GREEN = "#3fb950"
BLUE = "#58a6ff"
YELLOW = "#d29922"

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "assets" / "safepatch-demo.gif"


def _font(*candidates: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, 21)
    return ImageFont.load_default(size=21)


FONT = _font(
    r"C:\Windows\Fonts\consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)
BOLD = _font(
    r"C:\Windows\Fonts\consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
)

COMMAND = [
    "$ code-agent examples/buggy_calculator",
    '> "divide should raise ValueError when b is zero"',
    "> --dry-run-script examples/dry_run_fix_divide.json --yes",
]
EVENTS = [
    ("Imported isolated working_copy", BLUE),
    ("Baseline Docker pytest: FAILED (1 test)", YELLOW),
    ("Agent tools: repo_map -> read_file -> read_file", TEXT),
    ("Patch policy + exact preflight: PASSED", GREEN),
    ("Approval bound to patch_hash + working_tree_hash", BLUE),
    ("Docker pytest attempt 1: PASSED", GREEN),
    ("Session finished: SUCCEEDED", GREEN),
]


def frame(event_count: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((22, 22, WIDTH - 22, HEIGHT - 22), 16, fill=PANEL, outline="#30363d")
    for x, color in ((52, "#ff5f56"), (80, "#ffbd2e"), (108, "#27c93f")):
        draw.ellipse((x - 8, 45 - 8, x + 8, 45 + 8), fill=color)
    draw.text((WIDTH // 2, 45), "SafePatch deterministic demo", font=BOLD, fill=MUTED, anchor="mm")

    y = 90
    for line in COMMAND:
        draw.text((55, y), line, font=FONT, fill=TEXT)
        y += 31
    y += 22
    for message, color in EVENTS[:event_count]:
        prefix = "[ok]" if color == GREEN else "[->]"
        draw.text((55, y), prefix, font=BOLD, fill=color)
        draw.text((120, y), message, font=FONT, fill=color)
        y += 45
    if event_count == len(EVENTS):
        draw.text((55, HEIGHT - 62), "artifacts: final.diff | summary.json | trace.jsonl", font=FONT, fill=MUTED)
    return image


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [frame(i) for i in range(len(EVENTS) + 1)]
    durations = [900] * (len(frames) - 1) + [2600]
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
