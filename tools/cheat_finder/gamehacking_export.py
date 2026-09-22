"""Export PCSX2 .pnach cheat files from gamehacking.org for a list of games.

    python gamehacking_export.py targets.txt [--out DIR] [--pace 10]

targets.txt: one game per line, `SERIAL|Title` (a third `|query` field overrides the search
text). For each game: one search, one game page, one export - three requests - with a random
pause of `--pace` seconds (+/-40%) between them. Never retries a failure; rerun with the
leftover lines instead. gamehacking.org is a volunteer site: keep the pace slow.

Needs playwright + playwright-stealth and a headed Chromium (the site's Cloudflare rules pass
a real browser window and block headless/plain fetches): the /search endpoint is what trips
the WAF for scripted clients, and stealth + typing into the form is what gets through.

Output per game: raw export in <out>/raw/<SERIAL>.pnach, and <out>/<SERIAL>_00000000.pnach
holding only the sections whose every patch line targets EE RAM with a legal pnach type -
the export still carries un-decrypted GameShark v1 codes (addresses like 91F68566), which
would write garbage if applied. The _00000000 suffix matches the app's serial-scoped cheat
glob (SERIAL_*.pnach); drop the file into <DataRoot>/cheats/ on the Thor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from pathlib import Path

REGION = {"SLUS": "NTSC-U", "SCUS": "NTSC-U", "SLES": "PAL", "SCES": "PAL", "SLPM": "NTSC-J", "SLPS": "NTSC-J",
          "SCPS": "NTSC-J", "SCAJ": "NTSC-J", "SLKA": "NTSC-K", "SCKA": "NTSC-K", "SLAJ": "NTSC-J", "TCPS": "NTSC-J"}
VALID_TYPES = (0, 1, 2, 3, 4, 5, 6, 7, 0xC, 0xD, 0xE)


def norm(t: str) -> str:
    t = t.lower().replace("&", "and")
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def clean(text: str) -> tuple[str, int, int]:
    """Keep sections whose patch lines all target EE RAM with a legal type nibble."""
    out: list[str] = []
    cur: list[str] = []
    ok = True
    kept = dropped = 0

    def flush() -> None:
        nonlocal cur, ok, kept, dropped
        if cur:
            n = sum(1 for l in cur if l.startswith("patch="))
            if ok and n:
                out.extend(cur + [""])
                kept += n
            else:
                dropped += n
        cur, ok = [], True

    for line in text.splitlines():
        if line.startswith("["):
            flush()
            cur = [line]
            continue
        if line.startswith("patch="):
            # extended: type nibble + address; byte/short/word/double: the plain address.
            m = re.match(r"patch=[01],EE,([0-9A-Fa-f]{1,8}),(extended|byte|short|word|double|beshort|beword|bedouble),([0-9A-Fa-f]{1,16})", line, re.IGNORECASE)
            if not m:
                ok = False
            else:
                w = int(m.group(1), 16)
                if m.group(2).lower() == "extended":
                    if (w >> 28) not in VALID_TYPES or (w & 0x0FFFFFFF) >= 0x02000000:
                        ok = False
                elif w >= 0x02000000:
                    ok = False
        if cur:
            cur.append(line)
        else:
            out.append(line)
    flush()
    return "\n".join(out) + "\n", kept, dropped


async def run(targets: list[tuple[str, str, str]], out: Path, pace: float) -> dict:
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    report: dict[str, dict] = {}
    report_path = out / "report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))

    async def pause(mult: float = 1.0) -> None:
        await asyncio.sleep(random.uniform(0.6, 1.4) * pace * mult)

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", accept_downloads=True)
        pg = await ctx.new_page()
        await pg.goto("https://gamehacking.org/system/ps2", wait_until="domcontentloaded")
        for _ in range(30):
            if "moment" not in (await pg.title()).lower():
                break
            await asyncio.sleep(1)
        for serial, title, query in targets:
            if serial in report and (report[serial].get("file") or report[serial].get("skip")):
                continue
            want = REGION.get(serial[:4], "?")
            rec = {"title": title, "region": want, "results": [], "picked": None, "url": None, "page_serial": None,
                   "codes": 0, "kept": 0, "dropped": 0, "file": None, "error": None}
            report[serial] = rec
            try:
                await pause()
                inp = await pg.query_selector("input[name=gamTitle]")
                if inp is None:
                    await pg.goto("https://gamehacking.org/system/ps2", wait_until="domcontentloaded")
                    await pause(0.5)
                    inp = await pg.query_selector("input[name=gamTitle]")
                await inp.click()
                await inp.fill("")
                await inp.type(query, delay=random.randint(50, 120))
                await inp.press("Enter")
                await pg.wait_for_load_state("domcontentloaded")
                await pause(0.4)
                if "attention required" in (await pg.title()).lower():
                    rec["error"] = "cloudflare block - stop and try later"
                    print(serial, "BLOCKED; stopping", flush=True)
                    break
                # Only the "Playstation 2" panel: the search is site-wide and PS1 shares many titles.
                res = await pg.evaluate("""() => {
                    const out = [];
                    for (const panel of document.querySelectorAll('.panel')) {
                        const h = panel.querySelector('.panel-heading');
                        if (!h || !/playstation 2/i.test(h.innerText)) continue;
                        for (const tr of panel.querySelectorAll('tbody tr')) {
                            const a = tr.querySelector('a[href*="/game/"]'); if (!a) continue;
                            const tds = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                            out.push([a.innerText.trim(), a.href, tds[tds.length - 1]]);
                        }
                    }
                    return out; }""")
                rec["results"] = res[:15]
                # Spaceless compare: the site writes "MegaMan X7" where the GameDB has "Mega Man X7".
                nt = norm(title).replace(" ", "")
                exact = [r for r in res if norm(r[0]).replace(" ", "") == nt and want in r[0]]
                # Only the site title may be the longer one (a subtitle we lack); the reverse
                # once matched "Shadow Hearts - Covenant" to plain "Shadow Hearts".
                loose = [r for r in res if want in r[0] and norm(r[0]).replace(" ", "").startswith(nt)]
                pick = (exact or loose or [None])[0]
                if pick is None:
                    rec["skip"] = True
                    print(serial, title, "-> no", want, "PS2 match;", [r[0] for r in res[:4]], flush=True)
                    continue
                rec["picked"], rec["url"] = pick[0], pick[1]
                await pause()
                await pg.goto(pick[1], wait_until="domcontentloaded")
                await pause(0.5)
                body = await pg.inner_text("body")
                m = re.search(r"Serial\s*\n.*?([A-Z]{4}-\d{5})", body, re.S)
                rec["page_serial"] = m.group(1) if m else None
                if rec["page_serial"] and rec["page_serial"] != serial:
                    # Another revision or region of the same title: addresses differ, so it is
                    # not this disc's cheat file. Left in the report as a lead, not exported.
                    rec["skip"] = True
                    rec["error"] = f"page is {rec['page_serial']}, not {serial}"
                    print(serial, title, "-> page serial", rec["page_serial"], "differs; skipped", flush=True)
                    continue
                btn = await pg.query_selector("button:has-text('Download')")
                if btn is None:
                    rec["error"] = "no download button"
                    continue
                await btn.click()
                await asyncio.sleep(1.5)
                await pg.evaluate("""(serial) => {
                    const f = document.querySelector("form[action*='exportCodes']");
                    f.querySelector("select[name=format]").value = 'PCSX2';
                    const fn = f.querySelector("select[name=filename]");
                    const o = Array.from(fn.options).find(o => o.value === serial); if (o) fn.value = o.value;
                    window.__f = f; }""", serial)
                await pause(0.3)
                async with pg.expect_download(timeout=60000) as dl:
                    await pg.evaluate("() => { const f = window.__f; const s = f.querySelector('[type=submit]'); if (s) s.click(); else f.submit(); }")
                d = await dl.value
                raw = out / "raw" / f"{serial}.pnach"
                raw.parent.mkdir(parents=True, exist_ok=True)
                await d.save_as(raw)
                text = raw.read_text(encoding="utf-8", errors="replace")
                rec["codes"] = text.count("\npatch=")
                cleaned, kept, dropped = clean(text)
                rec["kept"], rec["dropped"] = kept, dropped
                if kept:
                    f = out / f"{serial}_00000000.pnach"
                    f.write_text(cleaned, encoding="utf-8", newline="\n")
                    rec["file"] = str(f)
                print(serial, title, "->", pick[0], "| codes", rec["codes"], "kept", kept, "dropped", dropped, flush=True)
            except Exception as e:  # one failure must not kill the run; the report says what happened
                rec["error"] = str(e).splitlines()[0][:200]
                print(serial, "ERROR", rec["error"], flush=True)
            finally:
                report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        await browser.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", type=Path)
    ap.add_argument("--out", type=Path, default=Path("gamehacking"))
    ap.add_argument("--pace", type=float, default=10.0, help="mean seconds between requests")
    a = ap.parse_args()
    rows = []
    for line in a.targets.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        serial, title = parts[0], parts[1]
        query = parts[2] if len(parts) > 2 and parts[2] else re.sub(r"\[.*?\]", "", title).split(" - ")[0].strip()
        rows.append((serial, title, query))
    a.out.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(rows, a.out, a.pace))
    got = sum(1 for r in report.values() if r.get("file"))
    print(f"done: {got}/{len(rows)} files in {a.out}")


if __name__ == "__main__":
    main()
