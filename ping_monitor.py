#!/usr/bin/env python3
"""
Ping Monitor — 多目标网络延迟检测（macOS / Linux / Windows）

运行示例（建议在项目目录使用虚拟环境）::

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python ping_monitor.py
"""
from __future__ import annotations

import asyncio
import re
import socket
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import flet as ft

# macOS 风格配色
BG = "#F2F2F7"
CARD = "#FFFFFF"
TEXT_PRIMARY = "#1C1C1E"
TEXT_SECONDARY = "#8E8E93"
ACCENT = "#007AFF"
SUCCESS = "#34C759"
DANGER = "#FF3B30"
BORDER = "#E5E5EA"  # 0.12 alpha on white
# 检测设置三列内层统一「子卡片」样式，与中间「局域扫描」一致
INNER_PANEL_BG = "#F8F8FA"
INNER_PANEL_PAD = 12
INNER_PANEL_RADIUS = 12


def collapsible_card(
    page: ft.Page,
    title: str,
    title_icon: str,
    content: ft.Control,
    default_expanded: bool = True,
    on_toggle: Optional[Callable[[bool], None]] = None,
) -> ft.Container:
    """可折叠卡片：点击标题栏切换展开/收起状态。"""
    expanded = [default_expanded]
    expand_label_ref = ft.Ref[ft.Text]()
    chevron_ref = ft.Ref[ft.Icon]()

    def _toggle(_: ft.ControlEvent) -> None:
        expanded[0] = not expanded[0]
        if expand_label_ref.current:
            expand_label_ref.current.value = "展开" if not expanded[0] else "收起"
        if chevron_ref.current:
            chevron_ref.current.icon = (
                ft.Icons.KEYBOARD_ARROW_DOWN if expanded[0] else ft.Icons.KEYBOARD_ARROW_RIGHT
            )
        content_container.visible = expanded[0]
        content_container.height = None if expanded[0] else 0
        if on_toggle:
            on_toggle(expanded[0])
        page.update()

    content_container = ft.Container(
        content=content,
        visible=default_expanded,
        height=None if default_expanded else 0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    return ft.Container(
        bgcolor=CARD,
        border_radius=14,
        border=ft.border.all(1, BORDER),
        shadow=ft.BoxShadow(spread_radius=0, blur_radius=16, color="#1A000000", offset=ft.Offset(0, 4)),
        padding=0,
        margin=ft.margin.only(left=24, right=24, bottom=16),
        content=ft.Column(
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Container(
                    on_click=_toggle,
                    padding=ft.padding.symmetric(horizontal=16, vertical=12),
                    content=ft.Row(
                        [
                            ft.Icon(title_icon, size=18, color=ACCENT),
                            ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
                            ft.Container(expand=True),
                            ft.Text(
                                ref=expand_label_ref,
                                value="收起" if default_expanded else "展开",
                                size=12,
                                color=TEXT_SECONDARY,
                            ),
                            ft.Icon(
                                ref=chevron_ref,
                                icon=(
                                    ft.Icons.KEYBOARD_ARROW_DOWN
                                    if default_expanded
                                    else ft.Icons.KEYBOARD_ARROW_RIGHT
                                ),
                                size=18,
                                color=TEXT_SECONDARY,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                content_container,
            ],
        ),
    )

TIME_RE = re.compile(r"time=(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
SEQ_RE = re.compile(r"icmp_seq=(\d+)", re.IGNORECASE)
SEQ_TIMEOUT_RE = re.compile(r"icmp_seq\s*(\d+)", re.IGNORECASE)


def ipv4_literal_error(line: str) -> Optional[str]:
    """
    若一行明显是「四段纯数字」的 IPv4 写法但某段超出 0–255，返回错误文案。
    域名、主机名或非四段写法不校验（交给系统 ping）。
    """
    s = line.strip()
    parts = s.split(".")
    if len(parts) != 4:
        return None
    if not all(p != "" and p.isdigit() for p in parts):
        return None
    for p in parts:
        if int(p) > 255:
            return f"非法 IPv4「{s}」：每段须在 0–255"
    return None


def ping_args(host: str, count: int) -> list[str]:
    if sys.platform == "win32":
        return ["ping", "-n", str(count), host]
    elif sys.platform == "darwin":
        return ["ping", "-c", str(count), host]
    else:
        return ["ping", "-c", str(count), host]


def ping_once_cmd(host: str) -> list[str]:
    """单次 ping，用于局域网扫描（超时尽量短）。"""
    if sys.platform == "win32":
        return ["ping", "-n", "1", "-w", "500", host]
    if sys.platform == "darwin":
        return ["ping", "-c", "1", "-W", "800", host]
    return ["ping", "-c", "1", "-W", "1", host]


def get_local_ipv4() -> Optional[str]:
    """通过 UDP 出口推断本机 IPv4（未真正发包到对端）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def ipv4_to_lan_prefix(ip: str) -> Optional[str]:
    """将本机 IP 视为 /24，返回前三段，如 192.168.1.5 -> 192.168.1。"""
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 or n > 255 for n in nums):
        return None
    if nums[0] == 127:
        return None
    return f"{nums[0]}.{nums[1]}.{nums[2]}"


class LanScanSession:
    """局域网扫描取消与进程跟踪。"""

    def __init__(self) -> None:
        self._procs: list[asyncio.subprocess.Process] = []
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        for p in self._procs:
            try:
                p.terminate()
            except ProcessLookupError:
                pass

    def add_proc(self, p: asyncio.subprocess.Process) -> None:
        self._procs.append(p)

    def clear_refs(self) -> None:
        self._procs.clear()
        self._cancelled = asyncio.Event()


@dataclass
class TargetStats:
    target: str
    sent: int = 0
    received: int = 0
    lost: int = 0
    last_delay_ms: Optional[float] = None
    status: str = "等待中"


class PingSession:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._procs: list[asyncio.subprocess.Process] = []
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        for p in self._procs:
            try:
                p.terminate()
            except ProcessLookupError:
                pass
        for t in self._tasks:
            if not t.done():
                t.cancel()

    def add_task(self, t: asyncio.Task) -> None:
        self._tasks.append(t)

    def add_proc(self, p: asyncio.subprocess.Process) -> None:
        self._procs.append(p)

    def clear_refs(self) -> None:
        self._tasks.clear()
        self._procs.clear()
        self._cancelled = asyncio.Event()


async def stream_ping(
    host: str,
    count: int,
    session: PingSession,
    stats: TargetStats,
    on_line: Callable[[str, int, int, Optional[float]], None],
) -> None:
    """逐行解析 ping 输出，更新 stats 并回调逐次记录。"""
    args = ping_args(host, count)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        session.add_proc(proc)
    except Exception as e:
        stats.status = f"启动失败: {e}"
        return

    assert proc.stdout is not None
    seq_counter = 0
    try:
        while True:
            if session._cancelled.is_set():
                break
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode(errors="replace").strip()
            if not line:
                continue

            lower = line.lower()
            if "time=" in lower or "time<" in lower:
                m = TIME_RE.search(line)
                delay = float(m.group(1)) if m else None
                sm = SEQ_RE.search(line)
                seq = int(sm.group(1)) if sm else seq_counter
                seq_counter = max(seq_counter, seq + 1)
                stats.sent += 1
                stats.received += 1
                stats.lost = stats.sent - stats.received
                stats.last_delay_ms = delay
                stats.status = "检测中"
                on_line(host, seq + 1, 1, delay)
            elif "timeout" in lower or "no answer" in lower:
                sm = SEQ_TIMEOUT_RE.search(line)
                seq = int(sm.group(1)) if sm else seq_counter
                seq_counter = max(seq_counter, seq + 1)
                stats.sent += 1
                stats.lost = stats.sent - stats.received
                stats.status = "检测中"
                on_line(host, seq + 1, 0, None)
            elif "packets transmitted" in lower or "packet loss" in lower:
                # 统计摘要行，部分系统在最后打印
                pass
    finally:
        if proc.returncode is None and not session._cancelled.is_set():
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
        elif proc.returncode is None:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass

    if session._cancelled.is_set():
        stats.status = "已停止"
    else:
        stats.status = "已完成"


def merge_target_lines(existing_lines: Iterable[str], new_ips: list[str]) -> str:
    """合并 IP 与原有目标，IPv4 按地址排序，域名等非 IPv4 排在后面。"""
    existing = {ln.strip() for ln in existing_lines if ln.strip()}
    all_lines = sorted(existing.union(new_ips), key=_target_sort_key)
    return "\n".join(all_lines)


def _target_sort_key(line: str) -> tuple:
    s = line.strip()
    if not s:
        return (2, 0, s)
    parts = s.split(".")
    if len(parts) == 4:
        try:
            return (0, tuple(int(p) for p in parts))
        except ValueError:
            pass
    return (1, s)


async def ping_once_for_scan(ip: str, scan_session: LanScanSession) -> bool:
    """单次 ping，用于局域网存活探测。"""
    if scan_session._cancelled.is_set():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            *ping_once_cmd(ip),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        scan_session.add_proc(proc)
    except Exception:
        return False
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return False


def main(page: ft.Page) -> None:
    page.title = "Ping Monitor"
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 980
    page.window.height = 720
    page.window.min_width = 760
    page.window.min_height = 560
    page.theme = ft.Theme(
        color_scheme_seed=ACCENT,
        visual_density=ft.VisualDensity.COMPACT,
    )

    session = PingSession()
    scan_session = LanScanSession()
    stats_map: dict[str, TargetStats] = {}
    log_rows: list[ft.DataRow] = []
    log_max = 500

    stats_table_ref = ft.Ref[ft.DataTable]()
    log_table_ref = ft.Ref[ft.DataTable]()
    start_btn_ref = ft.Ref[ft.ElevatedButton]()
    stop_btn_ref = ft.Ref[ft.ElevatedButton]()
    scan_start_btn_ref = ft.Ref[ft.ElevatedButton]()
    scan_stop_btn_ref = ft.Ref[ft.ElevatedButton]()
    targets_field_ref = ft.Ref[ft.TextField]()
    targets_err_ref = ft.Ref[ft.Container]()
    count_field_ref = ft.Ref[ft.TextField]()
    scan_status_ref = ft.Ref[ft.TextField]()
    scan_progress_ref = ft.Ref[ft.ProgressBar]()
    scan_result_ref = ft.Ref[ft.TextField]()
    scan_prefix_ref = ft.Ref[ft.TextField]()

    def _on_targets_change(_: ft.ControlEvent) -> None:
        raw = (targets_field_ref.current.value or "").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        err_msgs: list[str] = []
        seen: set[str] = set()
        for ln in lines:
            msg = ipv4_literal_error(ln)
            if msg and msg not in seen:
                seen.add(msg)
                err_msgs.append(msg)
        if not err_msgs:
            if targets_err_ref.current:
                targets_err_ref.current.content = None
                targets_err_ref.current.update()
            return
        tip = "；".join(err_msgs[:5])
        if len(err_msgs) > 5:
            tip += f" 等共 {len(err_msgs)} 处"
        if targets_err_ref.current:
            targets_err_ref.current.content = ft.Row(
                [
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=15, color=DANGER),
                    ft.Text(tip, size=12, color=DANGER),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            targets_err_ref.current.update()

    def update_ui() -> None:
        page.update()

    def add_log_row(target: str, nth: int, ok: int, delay: Optional[float]) -> None:
        ok_text = "成功" if ok else "超时"
        delay_text = f"{delay:.3f} ms" if delay is not None else "—"
        color = TEXT_PRIMARY if ok else DANGER

        row = ft.DataRow(
            color=DANGER if not ok else None,
            cells=[
                ft.DataCell(ft.Text(target, size=13, color=TEXT_PRIMARY if ok else "#FFFFFF")),
                ft.DataCell(ft.Text(str(nth), size=13, color=TEXT_SECONDARY)),
                ft.DataCell(ft.Text(ok_text, size=13, color=color, weight=ft.FontWeight.W_500)),
                ft.DataCell(ft.Text(delay_text, size=13, color=TEXT_PRIMARY if ok else "#FFFFFF")),
            ]
        )
        log_rows.append(row)
        if len(log_rows) > log_max:
            log_rows.pop(0)
        if log_table_ref.current:
            log_table_ref.current.rows = list(log_rows)
        update_ui()

    def refresh_stats_table() -> None:
        if not stats_table_ref.current:
            return
        rows = []
        for t, s in stats_map.items():
            rate = (s.lost / s.sent * 100.0) if s.sent > 0 else 0.0
            last = f"{s.last_delay_ms:.3f} ms" if s.last_delay_ms is not None else "—"
            status_color = TEXT_SECONDARY
            if s.status == "已完成":
                status_color = SUCCESS
            elif s.status == "已停止":
                status_color = DANGER
            elif s.status == "检测中":
                status_color = ACCENT

            has_loss = s.lost > 0
            rows.append(
                ft.DataRow(
                    color=DANGER if has_loss else None,
                    cells=[
                        ft.DataCell(ft.Text(t, size=13, weight=ft.FontWeight.W_500, color="#FFFFFF" if has_loss else TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(str(s.sent), size=13, color="#FFFFFF" if has_loss else TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(str(s.received), size=13, color="#FFFFFF" if has_loss else TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(str(s.lost), size=13, color="#FFFFFF" if has_loss else TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(f"{rate:.1f}%", size=13, color="#FFFFFF" if has_loss else (DANGER if rate > 0 else TEXT_PRIMARY))),
                        ft.DataCell(ft.Text(last, size=13, color="#FFFFFF" if has_loss else TEXT_PRIMARY)),
                        ft.DataCell(
                            ft.Text(s.status, size=13, color="#FFFFFF" if has_loss else status_color, weight=ft.FontWeight.W_500)
                        ),
                    ]
                )
            )
        stats_table_ref.current.rows = rows

    def on_ping_line(target: str, seq: int, ok: int, delay: Optional[float]) -> None:
        add_log_row(target, seq, ok, delay)
        refresh_stats_table()

    async def run_detection() -> None:
        raw = (targets_field_ref.current.value or "").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            page.snack_bar = ft.SnackBar(ft.Text("请至少填写一个目标地址"), bgcolor=DANGER)
            page.snack_bar.open = True
            update_ui()
            return

        ipv4_errs: list[str] = []
        seen_err: set[str] = set()
        for ln in lines:
            msg = ipv4_literal_error(ln)
            if msg and msg not in seen_err:
                seen_err.add(msg)
                ipv4_errs.append(msg)
        if ipv4_errs:
            tip = "；".join(ipv4_errs[:3])
            if len(ipv4_errs) > 3:
                tip += f" 等共 {len(ipv4_errs)} 处"
            page.snack_bar = ft.SnackBar(ft.Text(tip), bgcolor=DANGER)
            page.snack_bar.open = True
            update_ui()
            return

        try:
            count = int((count_field_ref.current.value or "10").strip())
        except ValueError:
            page.snack_bar = ft.SnackBar(ft.Text("发送次数必须是数字"), bgcolor=DANGER)
            page.snack_bar.open = True
            update_ui()
            return

        if count < 1 or count > 5000:
            page.snack_bar = ft.SnackBar(ft.Text("发送次数建议 1–5000"), bgcolor=DANGER)
            page.snack_bar.open = True
            update_ui()
            return

        scan_session.cancel()
        scan_session.clear_refs()
        scan_start_btn_ref.current.disabled = True
        scan_stop_btn_ref.current.disabled = True

        session.cancel()
        session.clear_refs()
        nonlocal log_rows
        log_rows = []
        stats_map.clear()
        for host in lines:
            stats_map[host] = TargetStats(target=host)

        if log_table_ref.current:
            log_table_ref.current.rows = []
        refresh_stats_table()

        start_btn_ref.current.disabled = True
        stop_btn_ref.current.disabled = False
        update_ui()

        tasks: list[asyncio.Task] = []
        for host in lines:

            async def run_one(h: str = host) -> None:
                st = stats_map[h]
                await stream_ping(h, count, session, st, on_ping_line)

            t = asyncio.create_task(run_one())
            session.add_task(t)
            tasks.append(t)

        async def _done() -> None:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for h, st in stats_map.items():
                if st.status == "检测中":
                    st.status = "已完成"
            refresh_stats_table()
            start_btn_ref.current.disabled = False
            stop_btn_ref.current.disabled = True
            scan_start_btn_ref.current.disabled = False
            scan_stop_btn_ref.current.disabled = True
            update_ui()

        asyncio.create_task(_done())

    def start_click(_: ft.ControlEvent) -> None:
        page.run_task(run_detection)

    def stop_click(_: ft.ControlEvent) -> None:
        session.cancel()
        for st in stats_map.values():
            if st.status == "检测中":
                st.status = "已停止"
        refresh_stats_table()
        start_btn_ref.current.disabled = False
        stop_btn_ref.current.disabled = True
        scan_start_btn_ref.current.disabled = False
        scan_stop_btn_ref.current.disabled = True
        update_ui()

    def scan_stop_click(_: ft.ControlEvent) -> None:
        scan_session.cancel()
        scan_start_btn_ref.current.disabled = False
        scan_stop_btn_ref.current.disabled = True
        start_btn_ref.current.disabled = False
        stop_btn_ref.current.disabled = True
        update_ui()

    async def run_lan_scan() -> None:
        local_ip = get_local_ipv4()

        user_prefix = (scan_prefix_ref.current.value or "").strip()
        if user_prefix:
            prefix = user_prefix
        elif local_ip:
            prefix = ipv4_to_lan_prefix(local_ip)
        else:
            prefix = None

        if not prefix:
            page.snack_bar = ft.SnackBar(
                ft.Text("请填写网段前三段（如 192.168.1）或检查网络连接"), bgcolor=DANGER
            )
            page.snack_bar.open = True
            update_ui()
            return

        session.cancel()
        scan_session.cancel()
        scan_session.clear_refs()

        scan_start_btn_ref.current.disabled = True
        scan_stop_btn_ref.current.disabled = False
        start_btn_ref.current.disabled = True
        stop_btn_ref.current.disabled = True
        update_ui()

        if scan_result_ref.current:
            scan_result_ref.current.value = ""
        if scan_status_ref.current:
            suffix = f" · 本机 {local_ip}" if local_ip else ""
            scan_status_ref.current.value = f"扫描 {prefix}.0/24{suffix}"
        if scan_progress_ref.current:
            scan_progress_ref.current.value = 0.0
        update_ui()

        alive: list[str] = []
        try:
            sem = asyncio.Semaphore(48)

            async def probe_host(last_octet: int) -> Optional[str]:
                if scan_session._cancelled.is_set():
                    return None
                ip = f"{prefix}.{last_octet}"
                async with sem:
                    if scan_session._cancelled.is_set():
                        return None
                    if await ping_once_for_scan(ip, scan_session):
                        return ip
                return None

            tasks = [asyncio.create_task(probe_host(i)) for i in range(1, 255)]
            total = 254
            done_n = 0
            for fut in asyncio.as_completed(tasks):
                r = await fut
                done_n += 1
                if isinstance(r, str) and r:
                    alive.append(r)
                    alive.sort(key=lambda x: int(x.rsplit(".", 1)[-1]))
                    if scan_result_ref.current:
                        scan_result_ref.current.value = "\n".join(alive)
                if scan_status_ref.current:
                    scan_status_ref.current.value = (
                        f"{prefix}.0/24 · 进度 {done_n}/{total} · 已发现 {len(alive)} 台"
                    )
                if scan_progress_ref.current:
                    scan_progress_ref.current.value = min(1.0, done_n / total)
                if done_n % 8 == 0 or r:
                    page.update()

            if scan_session._cancelled.is_set():
                page.snack_bar = ft.SnackBar(ft.Text("已停止扫描"), bgcolor=TEXT_SECONDARY)
                page.snack_bar.open = True
                return

            if not alive:
                page.snack_bar = ft.SnackBar(
                    ft.Text(f"未在 {prefix}.0/24 发现存活主机"), bgcolor=TEXT_SECONDARY
                )
                page.snack_bar.open = True
                return

            raw_lines = (targets_field_ref.current.value or "").splitlines()
            targets_field_ref.current.value = merge_target_lines(raw_lines, alive)
            page.snack_bar = ft.SnackBar(
                ft.Text(f"已发现 {len(alive)} 台主机，已合并到目标地址"),
                bgcolor=SUCCESS,
            )
            page.snack_bar.open = True
        finally:
            if scan_progress_ref.current:
                scan_progress_ref.current.value = 1.0 if not scan_session._cancelled.is_set() else 0.0
            scan_start_btn_ref.current.disabled = False
            scan_stop_btn_ref.current.disabled = True
            start_btn_ref.current.disabled = False
            stop_btn_ref.current.disabled = True
            update_ui()

    def scan_start_click(_: ft.ControlEvent) -> None:
        page.run_task(run_lan_scan)

    # 顶部标题区（与下方卡片同宽，随窗口拉伸）
    header = ft.Container(
        padding=ft.padding.symmetric(horizontal=24, vertical=20),
        content=ft.Column(
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SPEED_ROUNDED, color=ACCENT, size=28),
                        ft.Text(
                            "Ping Monitor",
                            size=26,
                            weight=ft.FontWeight.W_600,
                            color=TEXT_PRIMARY,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Text(
                    "目标网络检测面板V1.2",
                    size=14,
                    color=TEXT_SECONDARY,
                ),
            ],
        ),
    )

    card_shadow = ft.BoxShadow(
        spread_radius=0,
        blur_radius=16,
        color="#1A000000",
        offset=ft.Offset(0, 4),
    )

    def section_title(icon: str, title: str) -> ft.Row:
        return ft.Row(
            [
                ft.Icon(icon, size=18, color=ACCENT),
                ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
            ],
            spacing=8,
        )

    # 检测设置：左目标地址 · 中局域扫描结果 · 右次数与按钮
    settings_card = collapsible_card(
        page,
        title="检测设置",
        title_icon=ft.Icons.TUNE_ROUNDED,
        content=ft.Column(
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.ResponsiveRow(
                    spacing=12,
                    run_spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Container(
                            col={"xs": 12, "md": 5},
                            padding=INNER_PANEL_PAD,
                            bgcolor=INNER_PANEL_BG,
                            border=ft.border.all(1, BORDER),
                            border_radius=INNER_PANEL_RADIUS,
                            content=ft.Column(
                                spacing=10,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                controls=[
                                    ft.Row(
                                        [
                                            ft.Icon(
                                                ft.Icons.DNS_ROUNDED,
                                                color=ACCENT,
                                                size=18,
                                            ),
                                            ft.Text(
                                                "目标地址",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=TEXT_PRIMARY,
                                            ),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.TextField(
                                        ref=targets_field_ref,
                                        label="每行一个 IP / 域名",
                                        multiline=True,
                                        min_lines=8,
                                        max_lines=14,
                                        value="8.8.8.8\n1.1.1.1\n223.5.5.5",
                                        border_radius=10,
                                        bgcolor=CARD,
                                        color=TEXT_PRIMARY,
                                        border_color=BORDER,
                                        focused_border_color=ACCENT,
                                        cursor_color=ACCENT,
                                        text_size=14,
                                        on_change=_on_targets_change,
                                    ),
                                    ft.Container(ref=targets_err_ref),
                                ],
                            ),
                        ),
                        ft.Container(
                            col={"xs": 12, "md": 3},
                            padding=INNER_PANEL_PAD,
                            bgcolor=INNER_PANEL_BG,
                            border=ft.border.all(1, BORDER),
                            border_radius=INNER_PANEL_RADIUS,
                            content=ft.Column(
                                spacing=10,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                controls=[
                                    ft.Row(
                                        [
                                            ft.Icon(
                                                ft.Icons.WIFI_TETHERING,
                                                color=ACCENT,
                                                size=18,
                                            ),
                                            ft.Text(
                                                "局域扫描",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=TEXT_PRIMARY,
                                            ),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.TextField(
                                        ref=scan_prefix_ref,
                                        label="网段（如 192.168.1）",
                                        hint_text="留空自动检测本机网段",
                                        text_size=12,
                                        color=TEXT_PRIMARY,
                                        border_radius=8,
                                        border_color=BORDER,
                                        focused_border_color=ACCENT,
                                        cursor_color=ACCENT,
                                        bgcolor=CARD,
                                        dense=True,
                                    ),
                                    ft.TextField(
                                        ref=scan_status_ref,
                                        label="状态",
                                        read_only=True,
                                        value="点击「扫描局域网」开始",
                                        text_size=12,
                                        color=TEXT_SECONDARY,
                                        border_radius=8,
                                        border_color=BORDER,
                                        bgcolor=CARD,
                                        dense=True,
                                    ),
                                    ft.ProgressBar(
                                        ref=scan_progress_ref,
                                        color=ACCENT,
                                        bgcolor=BORDER,
                                        value=0,
                                    ),
                                    ft.TextField(
                                        ref=scan_result_ref,
                                        label="扫描到的 IP",
                                        read_only=True,
                                        multiline=True,
                                        min_lines=8,
                                        max_lines=14,
                                        border_radius=10,
                                        border_color=BORDER,
                                        bgcolor=CARD,
                                        color=TEXT_PRIMARY,
                                        text_size=13,
                                    ),
                                ],
                            ),
                        ),
                        ft.Container(
                            col={"xs": 12, "md": 4},
                            padding=INNER_PANEL_PAD,
                            bgcolor=INNER_PANEL_BG,
                            border=ft.border.all(1, BORDER),
                            border_radius=INNER_PANEL_RADIUS,
                            content=ft.Column(
                                spacing=10,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                controls=[
                                    ft.Row(
                                        [
                                            ft.Icon(
                                                ft.Icons.PLAY_CIRCLE_OUTLINE,
                                                color=ACCENT,
                                                size=18,
                                            ),
                                            ft.Text(
                                                "检测操作",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=TEXT_PRIMARY,
                                            ),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.TextField(
                                        ref=count_field_ref,
                                        label="发送次数",
                                        value="100",
                                        keyboard_type=ft.KeyboardType.NUMBER,
                                        border_radius=10,
                                        bgcolor=CARD,
                                        color=TEXT_PRIMARY,
                                        border_color=BORDER,
                                        focused_border_color=ACCENT,
                                        text_size=14,
                                    ),
                                    ft.Container(expand=True),
                                    ft.Row(
                                        [
                                            ft.ElevatedButton(
                                                ref=start_btn_ref,
                                                content="开始检测",
                                                icon=ft.Icons.PLAY_ARROW_ROUNDED,
                                                bgcolor=ACCENT,
                                                color=ft.Colors.WHITE,
                                                style=ft.ButtonStyle(
                                                    shape=ft.RoundedRectangleBorder(radius=10),
                                                    padding=ft.padding.symmetric(
                                                        horizontal=14, vertical=12
                                                    ),
                                                ),
                                                on_click=start_click,
                                            ),
                                            ft.OutlinedButton(
                                                ref=stop_btn_ref,
                                                content="停止全部",
                                                icon=ft.Icons.STOP_ROUNDED,
                                                disabled=True,
                                                style=ft.ButtonStyle(
                                                    shape=ft.RoundedRectangleBorder(radius=10),
                                                    padding=ft.padding.symmetric(
                                                        horizontal=14, vertical=12
                                                    ),
                                                ),
                                                on_click=stop_click,
                                            ),
                                        ],
                                        spacing=8,
                                        alignment=ft.MainAxisAlignment.END,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Row(
                                        [
                                            ft.ElevatedButton(
                                                ref=scan_start_btn_ref,
                                                content="扫描局域网",
                                                icon=ft.Icons.WIFI_TETHERING,
                                                bgcolor=ACCENT,
                                                color=ft.Colors.WHITE,
                                                style=ft.ButtonStyle(
                                                    shape=ft.RoundedRectangleBorder(radius=10),
                                                    padding=ft.padding.symmetric(
                                                        horizontal=14, vertical=12
                                                    ),
                                                ),
                                                on_click=scan_start_click,
                                            ),
                                            ft.OutlinedButton(
                                                ref=scan_stop_btn_ref,
                                                content="停止扫描",
                                                icon=ft.Icons.STOP_ROUNDED,
                                                disabled=True,
                                                style=ft.ButtonStyle(
                                                    shape=ft.RoundedRectangleBorder(radius=10),
                                                    padding=ft.padding.symmetric(
                                                        horizontal=14, vertical=12
                                                    ),
                                                ),
                                                on_click=scan_stop_click,
                                            ),
                                        ],
                                        spacing=8,
                                        alignment=ft.MainAxisAlignment.END,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
            ],
        ),
    )

    stats_inner = ft.Container(
        bgcolor="#FAFAFA",
        border_radius=10,
        border=ft.border.all(1, BORDER),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        padding=ft.padding.all(4),
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            height=300,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.DataTable(
                    ref=stats_table_ref,
                    bgcolor="#FFFFFF",
                    heading_row_color="#F2F2F7",
                    heading_row_height=40,
                    data_row_min_height=40,
                    data_row_max_height=44,
                    horizontal_lines=ft.BorderSide(1, BORDER),
                    vertical_lines=ft.BorderSide(1, BORDER),
                    column_spacing=16,
                    data_text_style=ft.TextStyle(size=13, color=TEXT_PRIMARY),
                    heading_text_style=ft.TextStyle(
                        size=12, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY
                    ),
                    columns=[
                        ft.DataColumn(ft.Text("目标", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("发送", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("接收", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("丢包", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("丢包率", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("最近延迟", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                        ft.DataColumn(ft.Text("状态", weight=ft.FontWeight.W_600,
                                              size=12, color=TEXT_SECONDARY)),
                    ],
                    rows=[],
                ),
            ],
        ),
    )
    stats_card = collapsible_card(
        page,
        title="多 IP 统计",
        title_icon=ft.Icons.ANALYTICS_OUTLINED,
        content=stats_inner,
    )

    log_card = collapsible_card(
        page,
        title="逐次记录",
        title_icon=ft.Icons.LIST_ALT_ROUNDED,
        content=ft.Container(
            bgcolor="#FAFAFA",
            border_radius=10,
            border=ft.border.all(1, BORDER),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=ft.padding.all(4),
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                height=360,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.DataTable(
                        ref=log_table_ref,
                        bgcolor="#FFFFFF",
                        heading_row_color="#F2F2F7",
                        heading_row_height=40,
                        data_row_min_height=36,
                        data_row_max_height=40,
                        horizontal_lines=ft.BorderSide(1, BORDER),
                        vertical_lines=ft.BorderSide(1, BORDER),
                        column_spacing=16,
                        data_text_style=ft.TextStyle(size=13, color=TEXT_PRIMARY),
                        heading_text_style=ft.TextStyle(
                            size=12, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY
                        ),
                        columns=[
                            ft.DataColumn(ft.Text("目标", weight=ft.FontWeight.W_600,
                                                  size=12, color=TEXT_SECONDARY)),
                            ft.DataColumn(ft.Text("第几次", weight=ft.FontWeight.W_600,
                                                  size=12, color=TEXT_SECONDARY)),
                            ft.DataColumn(ft.Text("结果", weight=ft.FontWeight.W_600,
                                                  size=12, color=TEXT_SECONDARY)),
                            ft.DataColumn(ft.Text("延迟", weight=ft.FontWeight.W_600,
                                                  size=12, color=TEXT_SECONDARY)),
                        ],
                        rows=[],
                    ),
                ],
            ),
        ),
    )

    # 显式操作 root View：scroll 属性转发到 page.views[0]，确保整页滚动正确工作
    page.views[0].scroll = ft.ScrollMode.ALWAYS
    page.views[0].controls = [header, settings_card, stats_card, log_card]


if __name__ == "__main__":
    ft.run(main)
