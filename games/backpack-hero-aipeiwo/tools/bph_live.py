"""直播面板的「提前预览」：终端看板 + 一次性 HTML 快照。

    python tools\\bph_live.py                     # 终端实时看板（Ctrl+C 停）
    python tools\\bph_live.py --seconds 20        # 只看 20 秒
    python tools\\bph_live.py --html live.html    # 生成一张 HTML 快照（可以截图/发人）

为什么先做这个：真正的直播是 DSH 的 client 插件（M4），但「长什么样、信息够不够」
不用等插件——面板要展示的就是这几块：楼层/血条/等级、敌人意图、背包网格、事件滚动、
以及鲸鱼娘的一句话。这里用同一份数据先摆出来，觉得能用再搬进插件。

数据源：`runtime/state.json`（10Hz）+ `data/runs/loop-*.jsonl`（事件流）。
**只读**，不碰游戏；节流到 2Hz（插件以后也是这个频率，10Hz 会把 UI 拖卡）。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = ROOT / "runtime" / "state.json"
RUNS = ROOT / "data" / "runs"


# ---------------------------------------------------------------- 读数

def read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rc() -> str:
    """ANSI 颜色（Windows Terminal / VS Code 终端都认）。"""
    return "\x1b[0m"


class Colors:
    dim = "\x1b[2m"
    bold = "\x1b[1m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    cyan = "\x1b[36m"
    reset = "\x1b[0m"


def hp_bar(hp: int, mx: int, width: int = 20) -> str:
    if not mx or mx <= 0:
        return "?" * width
    filled = max(0, min(width, round(width * hp / mx)))
    color = Colors.green if hp / mx > 0.5 else (Colors.yellow if hp / mx > 0.25 else Colors.red)
    return f"{color}{'█' * filled}{Colors.dim}{'░' * (width - filled)}{Colors.reset}"


def bag_grid(items: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """按 cells 画一个 ASCII 背包 + 图例。

    用字母当占位（`A`、`B`…）而不是名字前两个字：`BA SH`「ME ME」那种猜不出来，
    观众也读不懂。图例给「字母 = 名字（几格）」。
    """
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    cells: dict[tuple[int, int], str] = {}
    occupied: list[tuple[int, int]] = []
    legend: list[str] = []
    for idx, it in enumerate(items):
        tag = labels[idx] if idx < len(labels) else "?"
        name = it.get("name") or "?"
        legend.append(f"{tag}={name}({len(it.get('cells') or [])}格)")
        for c in it.get("cells") or []:
            try:
                x, y = (int(v) for v in str(c).split(","))
            except ValueError:
                continue
            cells[(x, y)] = tag
            occupied.append((x, y))
    if not occupied:
        return ["（背包是空的）"], []
    xs = [c[0] for c in occupied]
    ys = [c[1] for c in occupied]
    lines = []
    for y in range(min(ys), max(ys) + 1):
        row = []
        for x in range(min(xs), max(xs) + 1):
            row.append(f"{cells.get((x, y), '·'):>2}")
        lines.append(" ".join(row))
    return lines, legend


def recent_events(n: int = 5) -> list[str]:
    """最近几件「观众会关心」的事：战斗胜负、掉血、回血、付 hazard 血、死亡。"""
    if not RUNS.exists():
        return []
    files = sorted(RUNS.glob("loop-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return []
    out: list[str] = []
    for line in files[-1].read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = o.get("event")
        if ev == "act":
            a = o.get("action") or {}
            d = o.get("delta") or {}
            t = a.get("type")
            php = d.get("player_hp")
            if t == "after_battle":
                out.append("打赢一场")
            elif t == "use_item" and isinstance(php, int) and php > 0:
                out.append(f"用了 {a.get('name') or a.get('uid')}，回 {php} 血")
            elif isinstance(php, int) and php <= -5:
                out.append(f"挨了 {-php} 点")
        elif ev == "hazard_event":
            out.append(f"拒绝诅咒，付 {o.get('damage')} 血")
        elif ev == "dead":
            out.append("这一局结束了")
    return out[-n:]


def comment(st: dict[str, Any]) -> str:
    """鲸鱼娘的一句话：**只用 digest 里的事实**，模板生成（以后可换模型）。"""
    p = st.get("player") or {}
    hp, mx = p.get("hp"), p.get("max_hp")
    enemies = st.get("enemies") or []
    mode = st.get("mode")
    if mode == "dead" or hp == 0:
        return "呜……这局到此为止了，我们复盘一下再来！"
    if mode == "battle" and enemies and isinstance(hp, int) and isinstance(mx, int):
        incoming = sum(a.get("power", 0) for e in enemies for a in (e.get("attacks") or []))
        if hp <= incoming:
            return "这波挨满就要倒了，得先清一个！"
        if len(enemies) >= 3:
            return f"{len(enemies)} 个怪一起上，先挑能秒的。"
    if isinstance(hp, int) and isinstance(mx, int):
        if hp / mx < 0.35:
            return "血不多了，找点吃的吧。"
        if hp == mx:
            return "满血，可以激进一点～"
    if mode == "map":
        return "看地图，挑收益高的房间走。"
    return "稳住，按计划来。"


# ---------------------------------------------------------------- 终端

CLEAR = "\x1b[2J\x1b[H"


def render_terminal(st: dict[str, Any]) -> str:
    p = st.get("player") or {}
    r = st.get("run") or {}
    b = st.get("backpack") or {}
    enemies = st.get("enemies") or []
    hp, mx = p.get("hp"), p.get("max_hp")
    lines = [
        f"{Colors.bold}┌─ 小师妹的直播间 ────────────────────────────────────┐{Colors.reset}",
        f"  floor {r.get('floor')}   {hp_bar(hp or 0, mx or 0)}  {hp}/{mx}   "
        f"lv {r.get('level')}   金币 {r.get('gold')}   回合 {r.get('turn')}",
        f"  模式 {st.get('mode')}   背包 {b.get('items_in_grid')}/{b.get('grid_squares')} 格"
        f"   空格 {b.get('free_squares')}   散落 {b.get('items_loose')}",
    ]
    if enemies:
        inc = sum(a.get("power", 0) for e in enemies for a in (e.get("attacks") or []))
        es = "  ".join(f"{e.get('name', '').strip()}({e.get('hp')})" for e in enemies)
        lines.append(f"  {Colors.red}敌人:{Colors.reset} {es}   本回合 incoming ≈ {inc}")
    lines.append("  " + "─" * 50)
    art, legend = bag_grid((b.get("items") or []))
    lines.extend("  " + ln for ln in art)
    if legend:
        lines.append("  " + "  ".join(legend[:6]))
        if len(legend) > 6:
            lines.append("  " + "  ".join(legend[6:12]))
    lines.append("  " + "─" * 50)
    for e in recent_events(4):
        lines.append(f"  {Colors.dim}·{Colors.reset} {e}")
    lines.append(f"  {Colors.cyan}小师妹：{comment(st)}{Colors.reset}")
    return "\n".join(lines)


def run_terminal(seconds: float, interval: float) -> int:
    deadline = time.monotonic() + seconds if seconds else None
    try:
        while True:
            st = read_state()
            sys.stdout.write(CLEAR + render_terminal(st) + "\n")
            sys.stdout.flush()
            if deadline and time.monotonic() >= deadline:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n（停了）")
    return 0


# ---------------------------------------------------------------- HTML 快照

HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>小师妹的直播间 · 预览</title>
<style>
 body {{ margin:0; background:#12131a; color:#e8e8ef;
        font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif; }}
 .room {{ max-width:760px; margin:24px auto; background:#1b1d27; border-radius:14px;
          box-shadow:0 10px 40px #0008; overflow:hidden; }}
 .hd {{ padding:14px 18px; background:linear-gradient(90deg,#2a2f45,#1b1d27);
        display:flex; align-items:center; gap:10px; }}
 .dot {{ width:9px; height:9px; border-radius:50%; background:#ff5252; }}
 .title {{ font-weight:600; letter-spacing:.5px; }}
 .sub {{ margin-left:auto; opacity:.6; font-size:12px; }}
 .body {{ padding:16px 18px; }}
 .row {{ display:flex; gap:18px; align-items:center; margin-bottom:12px; }}
 .hp {{ flex:1; height:14px; background:#2b2f3f; border-radius:7px; overflow:hidden; }}
 .hp > i {{ display:block; height:100%; background:linear-gradient(90deg,#31c48d,#7ee787); }}
 .kv {{ display:flex; gap:16px; font-size:12px; opacity:.75; }}
 .grid {{ font-family:ui-monospace,Consolas,monospace; background:#151722;
          border:1px solid #2b2f3f; border-radius:10px; padding:10px 12px;
          white-space:pre; font-size:13px; overflow:auto; }}
 .legend {{ margin-top:6px; font-size:12px; opacity:.6; font-family:ui-monospace,monospace; }}
 .enemy {{ color:#ff8a8a; }}
 .events {{ margin:12px 0; font-size:13px; opacity:.85; }}
 .events li {{ margin:2px 0; }}
 .bubble {{ display:flex; gap:10px; align-items:flex-start; margin-top:14px; }}
 .avatar {{ width:38px; height:38px; border-radius:50%;
            background:radial-gradient(circle at 35% 30%,#9ad8ff,#4a7ee0); flex:none; }}
 .say {{ background:#242838; border-radius:12px 12px 12px 2px; padding:9px 12px; }}
</style></head><body>
<div class="room">
  <div class="hd"><span class="dot"></span><span class="title">小师妹的直播间</span>
    <span class="sub">floor {floor} · {mode} · 第 {turn} 回合</span></div>
  <div class="body">
    <div class="row">
      <div class="hp"><i style="width:{pct}%"></i></div>
      <div class="kv"><span>HP {hp}/{max_hp}</span><span>Lv {level}</span><span>{gold} 金</span></div>
    </div>
    <div class="kv" style="margin-bottom:10px">
      <span>背包 {ingrid}/{grid} 格</span><span>空格 {free}</span>
      <span class="enemy">敌人 {enemy_txt}</span><span>incoming ≈ {incoming}</span>
    </div>
    <div class="grid">{grid_art}</div>
    <div class="legend">{legend}</div>
    <ul class="events">{events}</ul>
    <div class="bubble"><div class="avatar"></div><div class="say">{say}</div></div>
  </div>
</div></body></html>
"""


def write_html(path: Path, st: dict[str, Any]) -> None:
    p = st.get("player") or {}
    r = st.get("run") or {}
    b = st.get("backpack") or {}
    enemies = st.get("enemies") or []
    hp, mx = p.get("hp") or 0, p.get("max_hp") or 0
    incoming = sum(a.get("power", 0) for e in enemies for a in (e.get("attacks") or []))
    enemy_txt = "、".join(f"{e.get('name', '').strip()}({e.get('hp')})" for e in enemies) or "无"
    events = "".join(f"<li>{html.escape(e)}</li>" for e in recent_events(5)) or "<li>（暂无）</li>"
    art, legend = bag_grid(b.get("items") or [])
    path.write_text(HTML.format(
        floor=r.get("floor"), mode=st.get("mode"), turn=r.get("turn"),
        pct=int(100 * hp / mx) if mx else 0, hp=hp, max_hp=mx,
        level=r.get("level"), gold=r.get("gold"),
        ingrid=b.get("items_in_grid"), grid=b.get("grid_squares"), free=b.get("free_squares"),
        enemy_txt=html.escape(enemy_txt), incoming=incoming,
        grid_art=html.escape("\n".join(art)),
        legend=html.escape("  ".join(legend)),
        events=events, say=html.escape(comment(st)),
    ), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="直播面板预览（终端 / HTML 快照）")
    ap.add_argument("--seconds", type=float, default=0, help="0 = 一直跑（Ctrl+C 停）")
    ap.add_argument("--interval", type=float, default=0.5, help="刷新间隔（秒，默认 2Hz）")
    ap.add_argument("--html", default="", help="生成 HTML 快照到指定路径后退出")
    args = ap.parse_args(argv)

    if args.html:
        out = Path(args.html)
        write_html(out, read_state())
        print(f"HTML 快照 -> {out}")
        return 0
    return run_terminal(args.seconds, args.interval)


if __name__ == "__main__":
    sys.exit(main())
