#!/usr/bin/env python3
"""Build demo-video.mp4 from REAL command output (no staged footage).

Frames are rendered from:
  - demo/forge-test-output.txt  (actual `forge test` run)
  - demo/demo-transcript.txt    (actual end-to-end demo run on local anvil)

Then assembled with ffmpeg into a <=3 minute 1280x720 video.

Usage: .venv/bin/python demo/make_video.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "demo"
FRAMES = DEMO / "frames"
W, H = 1280, 720
BG = (13, 16, 22)
FG = (232, 235, 240)
ACCENT = (64, 199, 168)
MUTED = (150, 158, 170)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
TITLE_FONT = ImageFont.truetype(str(FONT_DIR / "DejaVuSans-Bold.ttf"), 44)
SUB_FONT = ImageFont.truetype(str(FONT_DIR / "DejaVuSans.ttf"), 24)
HEAD_FONT = ImageFont.truetype(str(FONT_DIR / "DejaVuSans-Bold.ttf"), 26)
MONO = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 17)


def wrap(line: str, width: int = 104) -> list[str]:
    if len(line) <= width:
        return [line]
    out = []
    while len(line) > width:
        out.append(line[:width])
        line = "    " + line[width:]
    out.append(line)
    return out


def text_frame(path: Path, header: str, lines: list[str], max_lines: int = 30) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill=(22, 27, 36))
    d.text((28, 16), header, font=HEAD_FONT, fill=ACCENT)
    tag = "Emilio Tecnologías · Aevum Builder #1"
    d.text((W - 28 - d.textlength(tag, font=SUB_FONT), 20), tag, font=SUB_FONT, fill=MUTED)
    y = 84
    flat: list[str] = []
    for line in lines:
        flat.extend(wrap(line.rstrip()))
    for line in flat[:max_lines]:
        color = ACCENT if line.startswith(("$", "===", "DEMO COMPLETE", "Suite result")) else FG
        if "[PASS]" in line:
            color = (140, 230, 170)
        d.text((28, y), line, font=MONO, fill=color)
        y += 21
    img.save(path)


def title_frame(path: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((90, 210), "Aevum tUSD8 Invoicing", font=TITLE_FONT, fill=FG)
    d.text((90, 280), "Gasless stablecoin invoices + Python reconciliation agent", font=SUB_FONT, fill=ACCENT)
    lines = [
        "Emilio Tecnologías — Aevum Builder Challenge #1",
        "Tracks: Invoice Payments + Native AI Assistants",
        "Solidity (Foundry, Shanghai EVM) · Python agent · EIP-3009 gasless payments",
        "",
        "Every frame in this video is real terminal output from the actual",
        "test suite and end-to-end demo in this repository.",
    ]
    y = 350
    for line in lines:
        d.text((90, y), line, font=SUB_FONT, fill=MUTED)
        y += 36
    img.save(path)


def closing_frame(path: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((90, 170), "What was proven", font=TITLE_FONT, fill=FG)
    lines = [
        "19/19 Foundry tests pass (incl. fuzz + EIP-3009 attack cases)",
        "Exact-amount on-chain invoices, direct settlement, zero custody",
        "Gasless client payment: one signature, relayer pays gas",
        "Agent reconciles events, issues receipts, drafts reminders",
        "",
        "Repo: github.com/emiliotecnologias/aevum-tusd8-invoicing",
        "Aevum testnet (44005) deployment: scripts ready, pending owner wallet",
        "Testnet only · MIT · AI-assisted development (disclosed)",
    ]
    y = 260
    for line in lines:
        d.text((90, y), line, font=SUB_FONT, fill=MUTED)
        y += 40
    img.save(path)


def section(text: str, start_marker: str, end_marker: str | None) -> list[str]:
    lines = text.splitlines()
    out, capture = [], False
    for line in lines:
        if start_marker in line:
            capture = True
        if capture:
            out.append(line)
        if capture and end_marker and end_marker in line:
            break
    return out


def main() -> int:
    FRAMES.mkdir(exist_ok=True)
    test_out = (DEMO / "forge-test-output.txt").read_text()
    transcript = (DEMO / "demo-transcript.txt").read_text()

    title_frame(FRAMES / "f0.png")
    test_lines = [l for l in test_out.splitlines() if l.strip()]
    text_frame(FRAMES / "f1.png", "1 · Foundry test suite — 19 tests, all passing", test_lines)

    deploy_create = section(transcript, "1. Deploy MockUSD8", "created invoice #3")
    text_frame(FRAMES / "f2.png", "2 · Deploy + create 3 invoices (local anvil, Shanghai)", deploy_create)

    pay = section(transcript, "3. Client pays invoice #1", "relayer submitted gasless payment")
    text_frame(FRAMES / "f3.png", "3 · Standard payment + GASLESS EIP-3009 payment", pay)

    watch = section(transcript, "5. Agent reconciles", "receipts: 2 total")
    text_frame(FRAMES / "f4.png", "4 · Agent reconciles events and generates receipts", watch, max_lines=32)

    remind = section(transcript, "6. Agent drafts", "DEMO COMPLETE")
    text_frame(FRAMES / "f5.png", "5 · Overdue reminder draft, dashboard, final balances", remind, max_lines=32)

    closing_frame(FRAMES / "f6.png")

    durations = [6, 24, 28, 26, 34, 30, 10]  # total 158s <= 180s
    concat = DEMO / "frames" / "concat.txt"
    with concat.open("w") as f:
        for i, dur in enumerate(durations):
            f.write(f"file 'f{i}.png'\nduration {dur}\n")
        f.write("file 'f6.png'\n")

    out = DEMO / "demo-video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
            "-crf", "20", str(out),
        ],
        check=True, capture_output=True, text=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    )
    print(f"wrote {out} (duration: {float(probe.stdout.strip()):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
