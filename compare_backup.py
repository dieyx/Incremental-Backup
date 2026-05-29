# -*- coding: utf-8 -*-
"""
PC 到移动硬盘增量备份工具
按文件名 + 大小 + 修改时间对比两个文件夹，列出差异后确认一键复制。
"""

import os
import sys
import shutil
import fnmatch
import threading
import queue
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
from typing import NamedTuple


class FileInfo(NamedTuple):
    rel_path: str
    size: int
    mtime: float


class CompareResult(NamedTuple):
    source_root: str
    target_root: str
    missing: list
    modified: list
    errors: list


SKIP_PATTERNS = [
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "~$*",
    ".~*",
    "*.tmp",
    "*.temp",
]

EXCLUDE_DIRS = {
    ".git", ".svn", "__pycache__",
    "$RECYCLE.BIN", "System Volume Information",
}

MTIME_TOLERANCE = 2.0

# ==================== 配置持久化 ====================

CONFIG_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'file-compare')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

def load_config():
    defaults = {'last_source': '', 'last_target': ''}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            defaults.update(data)
    except Exception:
        pass
    return defaults

def save_config(config):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ==================== 工具函数 ====================

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"


def format_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def should_skip(name: str) -> bool:
    for pattern in SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def scan_directory(root: str) -> tuple:
    files: dict[str, FileInfo] = {}
    errors: list[str] = []
    root_path = Path(root)
    try:
        if not root_path.exists():
            errors.append(f"路径不存在: {root}")
            return files, errors
    except OSError as e:
        errors.append(f"无法访问路径 {root}: {e}")
        return files, errors
    for dirpath_str, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not should_skip(d)]
        for filename in filenames:
            if should_skip(filename):
                continue
            full_path = os.path.join(dirpath_str, filename)
            try:
                stat = os.stat(full_path)
            except OSError as e:
                errors.append(f"无法读取文件信息: {full_path} - {e}")
                continue
            rel = os.path.relpath(full_path, root)
            files[rel] = FileInfo(rel_path=rel, size=stat.st_size, mtime=stat.st_mtime)
    return files, errors


def compare_folders(source: str, target: str, output_callback=None) -> CompareResult:
    def out(msg):
        if output_callback:
            output_callback(msg)
        else:
            print(msg)
    out(f"正在扫描源目录: {source}")
    source_files, source_errors = scan_directory(source)
    out(f"  找到 {len(source_files)} 个文件")
    out(f"正在扫描目标目录: {target}")
    target_files, target_errors = scan_directory(target)
    out(f"  找到 {len(target_files)} 个文件")
    missing: list[str] = []
    modified: list = []
    errors = source_errors + target_errors
    for rel, src_info in source_files.items():
        tgt_info = target_files.get(rel)
        if tgt_info is None:
            missing.append(rel)
        elif src_info.size != tgt_info.size or abs(src_info.mtime - tgt_info.mtime) > MTIME_TOLERANCE:
            src_desc = f"源: {format_size(src_info.size)} {format_mtime(src_info.mtime)}"
            tgt_desc = f"目标: {format_size(tgt_info.size)} {format_mtime(tgt_info.mtime)}"
            modified.append((rel, src_desc, tgt_desc))
    return CompareResult(
        source_root=source, target_root=target,
        missing=missing, modified=modified, errors=errors,
    )


def print_results(result: CompareResult, output_callback=None) -> None:
    def out(msg):
        if output_callback:
            output_callback(msg)
        else:
            print(msg)
    total = len(result.missing) + len(result.modified)
    out(f"{'='*60}")
    out("=== 文件对比结果 ===")
    out(f"源:   {result.source_root}")
    out(f"目标: {result.target_root}")
    out(f"{'='*60}")
    if total == 0:
        out("两边文件完全一致，无需同步。")
        return
    if result.missing:
        out(f"--- [缺失] 目标端不存在的文件 ({len(result.missing)} 个) ---")
        for rel in result.missing:
            out(f"  [缺失] {rel}")
    if result.modified:
        out(f"--- [差异] 大小或时间不一致的文件 ({len(result.modified)} 个) ---")
        for rel, src_desc, tgt_desc in result.modified:
            out(f"  [差异] {rel}")
            out(f"         {src_desc}")
            out(f"         {tgt_desc}")
    out(f"{'─'*60}")
    out(f"共 {total} 个文件需要同步 (缺失: {len(result.missing)}, 差异: {len(result.modified)})")
    if result.errors:
        out(f"扫描期间出现 {len(result.errors)} 个错误（详见报告文件）")


def copy_files(source_root: str, target_root: str, files_to_copy: list, progress_callback=None, cancel_event=None) -> list:
    total = len(files_to_copy)
    copy_errors: list[str] = []
    for idx, rel in enumerate(files_to_copy):
        if cancel_event and cancel_event.is_set():
            copy_errors.append(f"操作已取消")
            break
        src_path = os.path.join(source_root, rel)
        dst_path = os.path.join(target_root, rel)
        if progress_callback:
            progress_callback(idx, total, rel)
        else:
            print(f"  ({idx+1}/{total}) {rel}")
        try:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
        except PermissionError as e:
            msg = f"权限不足: {src_path} -> {dst_path} - {e}"
            if progress_callback:
                progress_callback(idx, total, msg, is_error=True)
            else:
                print(f"    X {msg}")
            copy_errors.append(msg)
        except OSError as e:
            msg = f"复制失败: {src_path} -> {dst_path} - {e}"
            if progress_callback:
                progress_callback(idx, total, msg, is_error=True)
            else:
                print(f"    X {msg}")
            copy_errors.append(msg)
    if progress_callback:
        progress_callback(total, total, "完成", done=True)
    return copy_errors


def save_report(result: CompareResult, copy_errors: list, copied_count: int) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(result.source_root, f"对比报告_{timestamp}.txt")
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("PC -> 移动硬盘 增量备份报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append(f"源目录:   {result.source_root}")
    lines.append(f"目标目录: {result.target_root}")
    lines.append("")
    total = len(result.missing) + len(result.modified)
    lines.append(f"需同步文件总数: {total}")
    lines.append(f"  - 缺失 (目标不存在): {len(result.missing)}")
    lines.append(f"  - 差异 (大小/时间不同): {len(result.modified)}")
    lines.append(f"已复制文件数: {copied_count}")
    lines.append("")
    if result.missing:
        lines.append(f"[缺失文件] ({len(result.missing)} 个)")
        for rel in result.missing:
            lines.append(f"  {rel}")
        lines.append("")
    if result.modified:
        lines.append(f"[差异文件] ({len(result.modified)} 个)")
        for rel, src_desc, tgt_desc in result.modified:
            lines.append(f"  {rel}")
            lines.append(f"    {src_desc}")
            lines.append(f"    {tgt_desc}")
        lines.append("")
    if result.errors:
        lines.append(f"[扫描错误] ({len(result.errors)} 个)")
        for err in result.errors:
            lines.append(f"  {err}")
        lines.append("")
    if copy_errors:
        lines.append(f"[复制错误] ({len(copy_errors)} 个)")
        for err in copy_errors:
            lines.append(f"  {err}")
        lines.append("")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path


def normalize_path(path_str: str) -> str:
    path_str = path_str.strip().strip('"').strip("'")
    abspath = os.path.abspath(path_str)
    if sys.platform == "win32" and len(abspath) >= 260 and not abspath.startswith("\\\\?\\"):
        return "\\\\?\\" + abspath
    return abspath


def get_valid_path(prompt: str) -> str:
    while True:
        raw = input(prompt).strip()
        if raw.lower() in ("q", "quit", "exit"):
            print("用户取消，退出程序。")
            sys.exit(0)
        path = normalize_path(raw)
        if os.path.exists(path):
            return path
        print(f"  X 路径不存在: {path}，请重新输入（输入 q 退出）")


def confirm_yes_no(prompt: str) -> bool:
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes", "是"):
            return True
        if answer in ("n", "no", "否", "q", "quit", "exit"):
            return False
        print("  请输入 y (是) 或 n (否)")


# ==================== GUI ====================

class BackupGUI:
    """图形界面"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PC → 移动硬盘 增量备份工具")
        self.root.geometry("900x680")
        self.root.minsize(700, 500)

        # 状态变量
        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_label_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        # 内部状态
        self.compare_result = None
        self.report_path = None
        self.cancel_event = threading.Event()
        self.worker_thread = None
        self.msg_queue = queue.Queue()
        self.search_var = tk.StringVar()
        self.all_tree_items = []
        self.selected_files = set()
        self.is_busy = False

        # 加载配置
        self.config = load_config()
        self.source_var.set(self.config.get('last_source', ''))
        self.target_var.set(self.config.get('last_target', ''))

        # 构建UI
        self._setup_ui()
        self._setup_menu()
        self._center_window()

        # 定时处理队列消息
        self.root.after(100, self._process_queue)

        # 窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI 布局 ----------

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding="12")
        main.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_frame = ttk.Frame(main)
        title_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(title_frame, text="PC → 移动硬盘 增量备份工具",
                  font=("Microsoft YaHei UI", 14, "bold")).pack()
        ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))

        # 路径选择区
        path_frame = ttk.LabelFrame(main, text="文件夹选择", padding="10")
        path_frame.pack(fill=tk.X, pady=(0, 10))

        # 源文件夹
        src_row = ttk.Frame(path_frame)
        src_row.pack(fill=tk.X, pady=3)
        ttk.Label(src_row, text="源文件夹 (PC端):", width=18).pack(side=tk.LEFT)
        src_entry = ttk.Entry(src_row, textvariable=self.source_var, state="readonly")
        src_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(src_row, text="浏览…", command=self._browse_source, width=8).pack(side=tk.RIGHT)

        # 目标文件夹
        tgt_row = ttk.Frame(path_frame)
        tgt_row.pack(fill=tk.X, pady=3)
        ttk.Label(tgt_row, text="目标文件夹 (移动硬盘):", width=18).pack(side=tk.LEFT)
        tgt_entry = ttk.Entry(tgt_row, textvariable=self.target_var, state="readonly")
        tgt_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(tgt_row, text="浏览…", command=self._browse_target, width=8).pack(side=tk.RIGHT)

        # 操作按钮
        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=(0, 10))
        self.compare_btn = ttk.Button(btn_row, text="开始对比", command=self._start_compare)
        self.compare_btn.pack()

        # 摘要
        self.summary_var.set("")
        summary_lbl = ttk.Label(main, textvariable=self.summary_var, font=("Microsoft YaHei UI", 10))
        summary_lbl.pack(pady=(0, 5))

        # 搜索框
        search_frame = ttk.Frame(main)
        search_frame.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(search_frame, text="筛选:", width=5).pack(side=tk.LEFT)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        self.search_var.trace_add('write', self._on_search_changed)

        # 结果列表 Treeview
        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        columns = ("选择", "状态", "文件名", "详情")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("选择", text="选择", command=lambda: self._sort_column("选择", False))
        self.tree.heading("状态", text="状态", command=lambda: self._sort_column("状态", False))
        self.tree.heading("文件名", text="文件名", command=lambda: self._sort_column("文件名", False))
        self.tree.heading("详情", text="详情", command=lambda: self._sort_column("详情", False))
        self.tree.column("选择", width=60, anchor=tk.CENTER, stretch=False)
        self.tree.column("状态", width=80, anchor=tk.CENTER)
        self.tree.column("文件名", width=320)
        self.tree.column("详情", width=400)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 右键菜单
        self.tree_menu = tk.Menu(self.tree, tearoff=0)
        self.tree_menu.add_command(label="打开文件所在位置", command=self._open_file_location)
        self.tree_menu.add_command(label="复制文件路径", command=self._copy_file_path)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        # 排序状态
        self._sort_col = None
        self._sort_reverse = False

        # 备份操作区
        backup_frame = ttk.LabelFrame(main, text="备份操作", padding="10")
        backup_frame.pack(fill=tk.X, pady=(0, 10))

        backup_btn_row = ttk.Frame(backup_frame)
        backup_btn_row.pack(fill=tk.X)
        self.backup_btn = ttk.Button(backup_btn_row, text="开始备份", command=self._start_backup, state=tk.DISABLED)
        self.backup_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.cancel_btn = ttk.Button(backup_btn_row, text="取消", command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.save_btn = ttk.Button(backup_btn_row, text="保存报告", command=self._save_report, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)

        # 进度条
        self.progress = ttk.Progressbar(backup_frame, variable=self.progress_var, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(8, 3))
        self.progress_label_var.set("")
        ttk.Label(backup_frame, textvariable=self.progress_label_var).pack()

        # 状态栏
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Separator(status_frame, orient=tk.HORIZONTAL).pack(fill=tk.X)
        ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W,
                  padding=(5, 2)).pack(fill=tk.X)

    def _setup_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="保存报告", command=self._save_report, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close, accelerator="Ctrl+Q")

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self._show_about)

        # 快捷键
        self.root.bind("<Control-s>", lambda e: self._save_report())
        self.root.bind("<Control-q>", lambda e: self._on_close())
        self.root.bind("<F5>", lambda e: self._start_compare())
        self.root.bind("<Control-b>", lambda e: self._start_backup())

    # ---------- 动作处理 ----------

    def _browse_source(self):
        path = filedialog.askdirectory(title="选择源文件夹 (PC端)")
        if path:
            self.source_var.set(os.path.abspath(path))
            self._save_paths()

    def _browse_target(self):
        path = filedialog.askdirectory(title="选择目标文件夹 (移动硬盘端)")
        if path:
            self.target_var.set(os.path.abspath(path))
            self._save_paths()

    def _save_paths(self):
        self.config['last_source'] = self.source_var.get()
        self.config['last_target'] = self.target_var.get()
        save_config(self.config)

    def _start_compare(self):
        source = self.source_var.get().strip()
        target = self.target_var.get().strip()
        if not source or not os.path.exists(source):
            messagebox.showwarning("路径错误", f"源文件夹路径无效:\n{source}")
            return
        if not target or not os.path.exists(target):
            messagebox.showwarning("路径错误", f"目标文件夹路径无效:\n{target}")
            return
        if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
            messagebox.showwarning("路径错误", "源文件夹和目标文件夹不能相同！")
            return
        self._save_paths()
        self._cancel()
        self._set_busy(True, "正在对比…")
        self.compare_result = None
        self.report_path = None
        self.save_btn.config(state=tk.DISABLED)
        self._clear_tree()
        self.summary_var.set("")

        def worker():
            try:
                result = compare_folders(source, target,
                    output_callback=lambda msg: self.msg_queue.put({'type': 'status', 'text': msg}))
                self.msg_queue.put({'type': 'compare_done', 'result': result})
            except Exception as e:
                self.msg_queue.put({'type': 'error', 'message': str(e)})

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _start_backup(self):
        if not self.compare_result:
            return
        total = len(self._get_selected_files())
        if total == 0:
            messagebox.showinfo("提示", "请至少勾选一个需要同步的文件。")
            return
        if not messagebox.askyesno("确认备份", f"将复制 {total} 个文件到目标文件夹，是否继续？"):
            return
        self._cancel()
        self._set_busy(True, "正在备份…")
        self.progress_var.set(0)

        source = self.compare_result.source_root
        target = self.compare_result.target_root
        all_files = self._get_selected_files()
        self.cancel_event.clear()

        def worker():
            errors = copy_files(source, target, all_files,
                progress_callback=lambda idx, total, filename, is_error=False, done=False:
                    self.msg_queue.put({'type': 'progress', 'idx': idx, 'total': total,
                                        'filename': filename, 'is_error': is_error, 'done': done}),
                cancel_event=self.cancel_event)
            self.msg_queue.put({'type': 'backup_done', 'errors': errors, 'total': len(all_files)})

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _cancel(self):
        self.cancel_event.set()
        # 等待最多0.5秒让线程结束
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=0.5)

    def _set_busy(self, busy: bool, status: str = ""):
        self.is_busy = busy
        if busy:
            self.compare_btn.config(state=tk.DISABLED)
            self.backup_btn.config(state=tk.DISABLED)
            self.cancel_btn.config(state=tk.NORMAL)
            self.status_var.set(status)
        else:
            self.compare_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            self.status_var.set(status)
            self._refresh_result_state()

    def _on_search_changed(self, *_):
        query = self.search_var.get().lower()
        self.tree.delete(*self.tree.get_children())
        for item in self.all_tree_items:
            if not query or query in item[1].lower():
                status, rel, detail = item
                self.tree.insert('', tk.END, values=(self._check_text(rel), status, rel, detail))

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        values = self.tree.item(item_id, 'values')
        if not values:
            return
        rel = values[2]
        if rel in self.selected_files:
            self.selected_files.remove(rel)
        else:
            self.selected_files.add(rel)
        self.tree.selection_set(item_id)
        self.tree.item(item_id, values=(self._check_text(rel), values[1], rel, values[3]))
        self._refresh_result_state()
        return "break"

    def _on_tree_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)

    def _open_file_location(self):
        sel = self.tree.selection()
        if not sel or not self.compare_result:
            return
        values = self.tree.item(sel[0], 'values')
        if not values:
            return
        filename = values[2]
        src_path = os.path.join(self.compare_result.source_root, filename)
        if os.path.exists(src_path):
            os.startfile(os.path.dirname(src_path))
        else:
            messagebox.showinfo("提示", "该文件仅存在于源端。")

    def _copy_file_path(self):
        sel = self.tree.selection()
        if not sel or not self.compare_result:
            return
        values = self.tree.item(sel[0], 'values')
        if not values:
            return
        filename = values[2]
        self.root.clipboard_clear()
        self.root.clipboard_append(filename)

    def _sort_column(self, col, reverse):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        col_idx = {"状态": 0, "文件名": 1, "详情": 2}.get(col)
        if col == "选择":
            self.all_tree_items.sort(key=lambda x: self._check_text(x[1]), reverse=self._sort_reverse)
        else:
            self.all_tree_items.sort(key=lambda x: x[col_idx].lower(), reverse=self._sort_reverse)
        self._on_search_changed()

    def _clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.all_tree_items.clear()
        self.selected_files.clear()

    def _check_text(self, rel: str) -> str:
        return "☑" if rel in self.selected_files else "☐"

    def _get_selected_files(self) -> list:
        return [rel for _, rel, _ in self.all_tree_items if rel in self.selected_files]

    def _refresh_result_state(self):
        if not self.compare_result:
            self.backup_btn.config(state=tk.DISABLED)
            return
        total = len(self.compare_result.missing) + len(self.compare_result.modified)
        selected = len(self._get_selected_files())
        if total == 0:
            self.summary_var.set("当前文件已是最新状态，无需同步。")
            self.backup_btn.config(state=tk.DISABLED)
        else:
            self.summary_var.set(
                f"需同步文件: {total} 个  已选择: {selected} 个  "
                f"(缺失: {len(self.compare_result.missing)}, 差异: {len(self.compare_result.modified)})")
            self.backup_btn.config(state=tk.NORMAL if selected > 0 and not self.is_busy else tk.DISABLED)

    def _save_report(self):
        if not self.compare_result:
            return
        if self.report_path and os.path.exists(self.report_path):
            os.startfile(os.path.dirname(self.report_path))
        else:
            messagebox.showinfo("提示", "报告将在备份完成后自动保存到源文件夹。")

    def _show_about(self):
        messagebox.showinfo("关于",
            "PC → 移动硬盘 增量备份工具\n\n"
            "按文件名 + 大小 + 修改时间对比两个文件夹，\n"
            "列出差异后一键复制同步。\n\n"
            "快捷键:\n"
            "  F5 - 开始对比\n"
            "  Ctrl+B - 开始备份\n"
            "  Ctrl+S - 保存报告\n"
            "  Ctrl+Q - 退出")

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _on_close(self):
        self._cancel()
        self.root.destroy()

    # ---------- 队列消息处理 ----------

    def _process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _handle_msg(self, msg):
        msg_type = msg.get('type')

        if msg_type == 'status':
            self.status_var.set(msg['text'])

        elif msg_type == 'compare_done':
            self.compare_result = msg['result']
            self._populate_tree()
            self.save_btn.config(state=tk.NORMAL)
            self._set_busy(False, "对比完成")

            # 自动保存报告
            if self.compare_result:
                self.report_path = save_report(self.compare_result, [], 0)

        elif msg_type == 'progress':
            idx = msg['idx']
            total = msg['total']
            filename = msg['filename']
            if msg.get('done'):
                self.progress_var.set(100)
                self.progress_label_var.set("")
            elif msg.get('is_error'):
                self.progress_label_var.set(f"[失败] {filename}")
            else:
                pct = ((idx + 1) / total) * 100 if total > 0 else 0
                self.progress_var.set(pct)
                self.progress_label_var.set(f"({idx+1}/{total}) {filename}")

        elif msg_type == 'backup_done':
            errors = msg['errors']
            total = msg['total']
            copied = total - len(errors)
            self._set_busy(False, "备份完成")
            self.progress_var.set(100 if not errors else 0)

            # 更新报告
            if self.compare_result:
                self.report_path = save_report(self.compare_result, errors, copied)

            if errors:
                messagebox.showwarning("备份完成（有错误）",
                    f"成功复制: {copied} 个文件\n失败: {len(errors)} 个文件\n\n报告已保存: {self.report_path}")
            else:
                messagebox.showinfo("备份完成",
                    f"成功复制: {copied} 个文件\n\n报告已保存: {self.report_path}")

        elif msg_type == 'error':
            self._set_busy(False, "出错")
            messagebox.showerror("错误", msg['message'])

    def _populate_tree(self):
        self._clear_tree()
        if not self.compare_result:
            return
        for rel in self.compare_result.missing:
            self.all_tree_items.append(("缺失", rel, "目标端不存在"))
            self.selected_files.add(rel)
        for rel, src_desc, tgt_desc in self.compare_result.modified:
            detail = f"{src_desc}  |  {tgt_desc}"
            self.all_tree_items.append(("差异", rel, detail))
            self.selected_files.add(rel)
        self._on_search_changed()

    def run(self):
        self.root.mainloop()


# ==================== 入口 ====================

def main_cli():
    """命令行模式（原有功能）"""
    print("=" * 60)
    print("  PC -> 移动硬盘 增量备份工具")
    print("  对比两个文件夹，找出差异，一键备份")
    print("=" * 60)
    print("  输入 q 可随时退出")
    print()
    source = get_valid_path("请输入源文件夹路径 (PC端): ")
    print(f"  OK 源: {source}\n")
    target = get_valid_path("请输入目标文件夹路径 (移动硬盘端): ")
    print(f"  OK 目标: {target}\n")
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
        print("X 错误: 源文件夹和目标文件夹不能相同！")
        sys.exit(1)
    result = compare_folders(source, target)
    print_results(result)
    total = len(result.missing) + len(result.modified)
    if total == 0:
        report_path = save_report(result, [], 0)
        print(f"\n对比报告已保存: {report_path}")
        return
    print()
    if not confirm_yes_no("是否开始备份? (y/n): "):
        print("已取消备份。")
        report_path = save_report(result, [], 0)
        print(f"对比报告已保存: {report_path}")
        return
    print("\n开始复制文件...")
    all_to_copy = result.missing + [rel for rel, _, _ in result.modified]
    copy_errors = copy_files(source, target, all_to_copy)
    copied_count = len(all_to_copy) - len(copy_errors)
    report_path = save_report(result, copy_errors, copied_count)
    print(f"\n{'='*60}")
    print("备份完成!")
    print(f"  成功复制: {copied_count} 个文件")
    if copy_errors:
        print(f"  失败: {len(copy_errors)} 个文件")
    print(f"  对比报告已保存: {report_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        main_cli()
    else:
        app = BackupGUI()
        app.run()
