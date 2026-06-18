"""
EDID 编辑器 GUI - 基于 tkinter 的 EDID 编辑工具

功能：
  - 修改制造商 PnP ID (ODM 厂商)
  - 修改机种名 (Monitor Name)
  - 修改序列号 (SN)
  - 添加/编辑/删除详细时序 (Detailed Timing)
  - 管理标准时序
  - 内建常用时序预设
  - 加载/保存 EDID 二进制文件
  - 校验和自动计算与验证
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Optional, List, Tuple
from datetime import datetime

# 导入 EDID 核心模块
from edid_core import (
    EDID, DetailedTiming, DescriptorBlock, MonitorDescriptor,
    TIMING_PRESETS, KNOWN_VENDORS, DESCRIPTOR_COUNT, DescriptorTag,
    CEA861Extension, ShortVideoDescriptor, AudioFormat,
    DataBlock, AudioDataBlock, VideoDataBlock, VendorDataBlock, SpeakerDataBlock,
    VIC_TABLE, analyze_rtd_file, find_edid_in_file, EDID_BLOCK_SIZE
)


# ===========================================================================
# 样式常量
# ===========================================================================
PAD_X = 8
PAD_Y = 4
SECTION_PAD = 10
ENTRY_WIDTH = 30
ENTRY_WIDTH_SHORT = 16


# ===========================================================================
# 辅助组件
# ===========================================================================
class LabeledEntry(ttk.Frame):
    """带标签的输入框"""
    def __init__(self, parent, label: str, width: int = ENTRY_WIDTH, **kwargs):
        super().__init__(parent)
        self.var = tk.StringVar()
        ttk.Label(self, text=label, width=18, anchor='e').pack(side=tk.LEFT, padx=(0, 5))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, **kwargs)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def get(self) -> str:
        return self.var.get()

    def set(self, value):
        self.var.set(str(value))

    def bind(self, *args, **kwargs):
        self.entry.bind(*args, **kwargs)


class LabeledCombo(ttk.Frame):
    """带标签的下拉框"""
    def __init__(self, parent, label: str, values: List[str], width: int = ENTRY_WIDTH_SHORT, **kwargs):
        super().__init__(parent)
        self.var = tk.StringVar()
        ttk.Label(self, text=label, width=18, anchor='e').pack(side=tk.LEFT, padx=(0, 5))
        self.combo = ttk.Combobox(self, textvariable=self.var, values=values, width=width,
                                  state='readonly', **kwargs)
        self.combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def get(self) -> str:
        return self.var.get()

    def set(self, value):
        self.var.set(str(value))

    def bind(self, *args, **kwargs):
        self.combo.bind(*args, **kwargs)


class Section(ttk.LabelFrame):
    """折叠式分组区域"""
    def __init__(self, parent, title: str, **kwargs):
        super().__init__(parent, text=title, padding=10, **kwargs)
        self.columnconfigure(0, weight=1)


# ===========================================================================
# 详细时序编辑对话框
# ===========================================================================
class TimingEditDialog(tk.Toplevel):
    """详细时序编辑弹窗"""

    def __init__(self, parent, timing: Optional[DetailedTiming] = None, preset_names: List[str] = None):
        super().__init__(parent)
        self.result: Optional[DetailedTiming] = None
        self.timing = timing or DetailedTiming()
        self.preset_names = preset_names or []

        self.title("编辑详细时序")
        self.geometry("580x560")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._load_timing()

        # 居中
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = parent.winfo_rootx() + (pw - 580) // 2
        y = parent.winfo_rooty() + (ph - 560) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)

        # ---- 预设 ----
        preset_frame = ttk.Frame(main)
        preset_frame.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(preset_frame, text="预设时序:").pack(side=tk.LEFT, padx=(0, 5))
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_var,
                                         values=self.preset_names, width=36, state='readonly')
        self.preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.preset_combo.bind('<<ComboboxSelected>>', self._on_preset)

        ttk.Button(preset_frame, text="应用", command=self._apply_preset).pack(side=tk.LEFT, padx=5)

        # ---- Timing 参数 ----
        self.fields = {}
        sec = Section(main, "时序参数")
        sec.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        sec.columnconfigure(1, weight=1)

        row = 0
        # Pixel Clock
        def add_row(frame, row, label, key, unit="", width=14):
            ttk.Label(frame, text=label, width=20, anchor='e').grid(row=row, column=0, sticky='e', padx=(0, 5), pady=2)
            var = tk.IntVar()
            self.fields[key] = var
            sp = ttk.Spinbox(frame, textvariable=var, width=width, from_=0, to=65535)
            sp.grid(row=row, column=1, sticky='w', pady=2)
            if unit:
                ttk.Label(frame, text=unit, width=10).grid(row=row, column=2, sticky='w', pady=2)
            return row + 1

        row = add_row(sec, row, "像素时钟 (Pixel Clock):", "pixel_clock", "×10kHz")
        row = add_row(sec, row, "水平有效 (H Active):", "h_active", "px")
        row = add_row(sec, row, "水平消隐 (H Blanking):", "h_blanking", "px")
        row = add_row(sec, row, "水平前肩 (H Front Porch):", "h_front_porch", "px")
        row = add_row(sec, row, "水平同步 (H Sync):", "h_sync", "px")
        row = add_row(sec, row, "垂直有效 (V Active):", "v_active", "lines")
        row = add_row(sec, row, "垂直消隐 (V Blanking):", "v_blanking", "lines")
        row = add_row(sec, row, "垂直前肩 (V Front Porch):", "v_front_porch", "lines")
        row = add_row(sec, row, "垂直同步 (V Sync):", "v_sync", "lines")

        # ---- 尺寸 ----
        sec2 = Section(main, "图像尺寸 & 其他")
        sec2.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        sec2.columnconfigure(1, weight=1)
        row2 = 0
        row2 = add_row(sec2, row2, "水平图像尺寸 (H Size):", "h_image_size", "mm")
        row2 = add_row(sec2, row2, "垂直图像尺寸 (V Size):", "v_image_size", "mm")
        row2 = add_row(sec2, row2, "水平边框 (H Border):", "h_border", "px")
        row2 = add_row(sec2, row2, "垂直边框 (V Border):", "v_border", "px")

        # ---- 计算信息 ----
        self.info_var = tk.StringVar(value="—")
        info_frame = ttk.Frame(main)
        info_frame.grid(row=3, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(info_frame, text="计算信息: ").pack(side=tk.LEFT)
        ttk.Label(info_frame, textvariable=self.info_var, foreground='gray').pack(side=tk.LEFT)

        # ---- 按钮 ----
        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=4, column=0, sticky='e')
        ttk.Button(btn_frame, text="确定", command=self._on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self._on_cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="刷新计算", command=self._update_info).pack(side=tk.RIGHT, padx=5)

    def _load_timing(self):
        """从对象加载到 UI"""
        t = self.timing
        for k, var in self.fields.items():
            var.set(getattr(t, k, 0))
        self._update_info()

    def _save_to_object(self):
        """从 UI 保存到对象"""
        t = self.timing
        for k, var in self.fields.items():
            try:
                setattr(t, k, var.get())
            except (tk.TclError, ValueError):
                setattr(t, k, 0)

    def _on_preset(self, event=None):
        self._update_info()

    def _apply_preset(self):
        name = self.preset_var.get()
        for pname, pt in TIMING_PRESETS:
            if pname == name:
                self.timing = DetailedTiming(
                    pixel_clock=pt.pixel_clock,
                    h_active=pt.h_active, h_blanking=pt.h_blanking,
                    v_active=pt.v_active, v_blanking=pt.v_blanking,
                    h_front_porch=pt.h_front_porch, h_sync=pt.h_sync,
                    v_front_porch=pt.v_front_porch, v_sync=pt.v_sync,
                    h_image_size=pt.h_image_size, v_image_size=pt.v_image_size,
                    h_border=pt.h_border, v_border=pt.v_border,
                )
                self._load_timing()
                return
        messagebox.showwarning("预设", "请先选择一个预设时序", parent=self)

    def _update_info(self):
        """更新计算信息"""
        self._save_to_object()
        t = self.timing
        h_total = t.h_total
        v_total = t.v_total
        rr = t.refresh_rate
        info = (f"H Total: {h_total} | V Total: {v_total} | "
                f"刷新率: {rr:.2f} Hz | 像素时钟: {t.pixel_clock_mhz:.2f} MHz")
        self.info_var.set(info)

    def _on_ok(self):
        self._save_to_object()
        self.result = self.timing
        self.destroy()

    def _on_cancel(self):
        self.destroy()


# ===========================================================================
# 主应用程序
# ===========================================================================
class EDIDEditorApp:
    """EDID 编辑器主窗口"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EDID 编辑器 - EDID Editor Tool V2")
        self.root.geometry("860x720")
        self.root.minsize(800, 600)

        self.edid: Optional[EDID] = None
        self.file_path: Optional[str] = None
        self._modified = False
        self._preset_names = [name for name, _ in TIMING_PRESETS]

        self._build_menu()
        self._build_ui()
        self._update_title()

        # 初始创建一个空白 EDID
        self._new_edid()

    # ==================================================================
    # 菜单栏
    # ==================================================================
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # ---- File ----
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新建空白 EDID (128B)", accelerator="Ctrl+N", command=self._new_edid)
        file_menu.add_command(label="新建 384 字节 EDID", command=lambda: self._new_edid(blocks=3))
        file_menu.add_command(label="打开 EDID 文件...", accelerator="Ctrl+O", command=self._open_file)
        file_menu.add_command(label="打开 RTD 固件文件...", command=self._open_rtd_file)
        file_menu.add_command(label="保存", accelerator="Ctrl+S", command=self._save_file)
        file_menu.add_command(label="另存为...", accelerator="Ctrl+Shift+S", command=self._save_as_file)
        file_menu.add_command(label="写回 RTD 固件...", command=self._save_to_rtd)
        file_menu.add_separator()
        file_menu.add_command(label="退出", accelerator="Alt+F4", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        # ---- Edit ----
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="验证校验和", command=self._verify_checksum)
        edit_menu.add_command(label="重新计算校验和", command=self._recalc_checksum)
        edit_menu.add_separator()
        edit_menu.add_command(label="刷新描述符列表", command=self._refresh_all)
        menubar.add_cascade(label="编辑", menu=edit_menu)

        # ---- Help ----
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._show_about)
        help_menu.add_command(label="常见 PnP ID 参考", command=self._show_vendor_ref)
        menubar.add_cascade(label="帮助", menu=help_menu)

        # 快捷键
        self.root.bind('<Control-n>', lambda e: self._new_edid())
        self.root.bind('<Control-o>', lambda e: self._open_file())
        self.root.bind('<Control-s>', lambda e: self._save_file())
        self.root.bind('<Control-Shift-S>', lambda e: self._save_as_file())

    # ==================================================================
    # 主界面
    # ==================================================================
    def _build_ui(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        ttk.Button(toolbar, text="📂 打开", command=self._open_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="💾 保存", command=self._save_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="📄 新建", command=self._new_edid).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT, padx=8, fill=tk.Y)
        self.toolbar_label = ttk.Label(toolbar, text="", foreground='gray')
        self.toolbar_label.pack(side=tk.LEFT, padx=5)

        # Notebook (选项卡)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_basic_tab()
        self._build_descriptor_tab()
        self._build_timing_tab()
        self._build_cea_tab()
        self._build_raw_tab()

        # 底部状态栏
        status = ttk.Frame(self.root, relief=tk.SUNKEN, padding=2)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, padding=3).pack(side=tk.LEFT)
        self.checksum_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.checksum_var, padding=3).pack(side=tk.RIGHT)

    # ==================================================================
    # Tab 1: 基本信息
    # ==================================================================
    def _build_basic_tab(self):
        frame = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(frame, text="基本信息")

        # 滚动
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _mw(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _mw)
        scroll_frame.bind("<MouseWheel>", _mw)

        # ---- 制造商信息 ----
        sec = Section(scroll_frame, "ODM 制造商信息")
        sec.pack(fill=tk.X, pady=(0, SECTION_PAD))
        sec.columnconfigure(1, weight=1)

        # PnP ID
        ttk.Label(sec, text="PnP ID (3字母):", width=22, anchor='e').grid(row=0, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        pnp_frame = ttk.Frame(sec)
        pnp_frame.grid(row=0, column=1, sticky='w', pady=PAD_Y)
        self.pnp_var = tk.StringVar()
        self.pnp_entry = ttk.Entry(pnp_frame, textvariable=self.pnp_var, width=8, font=('Consolas', 11))
        self.pnp_entry.pack(side=tk.LEFT, padx=(0, 5))
        self.pnp_entry.bind('<KeyRelease>', lambda e: self._mark_modified())
        ttk.Button(pnp_frame, text="▾", width=3, command=self._show_vendor_popup).pack(side=tk.LEFT)

        # 常见厂商快速选择
        ttk.Label(sec, text="快速选择:", width=22, anchor='e').grid(row=1, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        vendor_frame = ttk.Frame(sec)
        vendor_frame.grid(row=1, column=1, sticky='w', pady=PAD_Y)
        self.vendor_var = tk.StringVar()
        vendor_list = [f"{k} - {v}" for k, v in KNOWN_VENDORS.items()]
        self.vendor_combo = ttk.Combobox(vendor_frame, textvariable=self.vendor_var,
                                         values=vendor_list, width=32, state='readonly')
        self.vendor_combo.pack(side=tk.LEFT)
        self.vendor_combo.bind('<<ComboboxSelected>>', self._on_vendor_selected)

        # 产品代码
        ttk.Label(sec, text="产品代码 (Product Code):", width=22, anchor='e').grid(row=2, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.product_var = tk.StringVar()
        product_frame = ttk.Frame(sec)
        product_frame.grid(row=2, column=1, sticky='w', pady=PAD_Y)
        ttk.Entry(product_frame, textvariable=self.product_var, width=12, font=('Consolas', 10)).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(product_frame, text="(Hex: 0x0000–0xFFFF)", foreground='gray').pack(side=tk.LEFT)
        self.product_var.trace_add('write', lambda *_: self._mark_modified())

        # 序列号 (4字节)
        ttk.Label(sec, text="序列号 (Serial Number):", width=22, anchor='e').grid(row=3, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.serial_num_var = tk.StringVar()
        sn_frame = ttk.Frame(sec)
        sn_frame.grid(row=3, column=1, sticky='w', pady=PAD_Y)
        ttk.Entry(sn_frame, textvariable=self.serial_num_var, width=22, font=('Consolas', 10)).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(sn_frame, text="(Hex: 0x00000000–0xFFFFFFFF)", foreground='gray').pack(side=tk.LEFT)
        self.serial_num_var.trace_add('write', lambda *_: self._mark_modified())

        # 制造日期
        ttk.Label(sec, text="制造日期:", width=22, anchor='e').grid(row=4, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        date_frame = ttk.Frame(sec)
        date_frame.grid(row=4, column=1, sticky='w', pady=PAD_Y)
        self.week_var = tk.IntVar(value=1)
        ttk.Spinbox(date_frame, textvariable=self.week_var, from_=1, to=54, width=5).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(date_frame, text="周 /").pack(side=tk.LEFT, padx=2)
        self.year_var = tk.IntVar(value=2025)
        ttk.Spinbox(date_frame, textvariable=self.year_var, from_=1990, to=2090, width=6).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Label(date_frame, text="年").pack(side=tk.LEFT, padx=2)
        self.week_var.trace_add('write', lambda *_: self._mark_modified())
        self.year_var.trace_add('write', lambda *_: self._mark_modified())

        # EDID 版本
        ttk.Label(sec, text="EDID 版本:", width=22, anchor='e').grid(row=5, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        ver_frame = ttk.Frame(sec)
        ver_frame.grid(row=5, column=1, sticky='w', pady=PAD_Y)
        self.ver_major = tk.IntVar(value=1)
        self.ver_minor = tk.IntVar(value=4)
        ttk.Spinbox(ver_frame, textvariable=self.ver_major, from_=1, to=2, width=4).pack(side=tk.LEFT)
        ttk.Label(ver_frame, text=".").pack(side=tk.LEFT)
        ttk.Spinbox(ver_frame, textvariable=self.ver_minor, from_=0, to=4, width=4).pack(side=tk.LEFT)
        self.ver_major.trace_add('write', lambda *_: self._mark_modified())
        self.ver_minor.trace_add('write', lambda *_: self._mark_modified())

        # ---- 显示参数 ----
        sec2 = Section(scroll_frame, "显示参数")
        sec2.pack(fill=tk.X, pady=(0, SECTION_PAD))
        sec2.columnconfigure(1, weight=1)

        self.input_type_var = tk.StringVar(value="数字")
        ttk.Label(sec2, text="输入类型:", width=22, anchor='e').grid(row=0, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        input_frame = ttk.Frame(sec2)
        input_frame.grid(row=0, column=1, sticky='w', pady=PAD_Y)
        ttk.Radiobutton(input_frame, text="数字 (Digital)", variable=self.input_type_var, value="数字",
                        command=self._mark_modified).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(input_frame, text="模拟 (Analog)", variable=self.input_type_var, value="模拟",
                        command=self._mark_modified).pack(side=tk.LEFT)

        ttk.Label(sec2, text="屏幕宽度:", width=22, anchor='e').grid(row=1, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        size_frame = ttk.Frame(sec2)
        size_frame.grid(row=1, column=1, sticky='w', pady=PAD_Y)
        self.scr_width_var = tk.IntVar(value=52)
        ttk.Spinbox(size_frame, textvariable=self.scr_width_var, from_=0, to=255, width=6).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(size_frame, text="cm  ×").pack(side=tk.LEFT, padx=2)
        self.scr_height_var = tk.IntVar(value=29)
        ttk.Spinbox(size_frame, textvariable=self.scr_height_var, from_=0, to=255, width=6).pack(side=tk.LEFT, padx=(2, 2))
        ttk.Label(size_frame, text="cm").pack(side=tk.LEFT, padx=2)
        self.scr_width_var.trace_add('write', lambda *_: self._mark_modified())
        self.scr_height_var.trace_add('write', lambda *_: self._mark_modified())

        ttk.Label(sec2, text="扩展块数量:", width=22, anchor='e').grid(row=2, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.ext_cnt_var = tk.IntVar(value=0)
        ttk.Spinbox(sec2, textvariable=self.ext_cnt_var, from_=0, to=3, width=6).grid(row=2, column=1, sticky='w', pady=PAD_Y)
        self.ext_cnt_var.trace_add('write', lambda *_: self._mark_modified())

        # ---- 机种名 & SN 字符串 ----
        sec3 = Section(scroll_frame, "机种名 & 序列号字符串")
        sec3.pack(fill=tk.X, pady=(0, SECTION_PAD))
        sec3.columnconfigure(1, weight=1)

        ttk.Label(sec3, text="机种名 (Monitor Name):", width=22, anchor='e').grid(row=0, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.model_name_var = tk.StringVar()
        ttk.Entry(sec3, textvariable=self.model_name_var, width=40, font=('Consolas', 10)).grid(row=0, column=1, sticky='ew', pady=PAD_Y)
        self.model_name_var.trace_add('write', lambda *_: self._mark_modified())
        ttk.Label(sec3, text="(最多13字符，含换行符)", foreground='gray', font=('', 8)).grid(row=0, column=2, sticky='w', padx=5)

        ttk.Label(sec3, text="序列号字符串 (SN):", width=22, anchor='e').grid(row=1, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.serial_str_var = tk.StringVar()
        ttk.Entry(sec3, textvariable=self.serial_str_var, width=40, font=('Consolas', 10)).grid(row=1, column=1, sticky='ew', pady=PAD_Y)
        self.serial_str_var.trace_add('write', lambda *_: self._mark_modified())
        ttk.Label(sec3, text="(最多13字符，含换行符)", foreground='gray', font=('', 8)).grid(row=1, column=2, sticky='w', padx=5)

        # ---- 应用按钮 ----
        btn_frame = ttk.Frame(scroll_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="✓ 应用基本信息到 EDID", command=self._apply_basic_info).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="↻ 从 EDID 重新加载", command=self._load_basic_info).pack(side=tk.LEFT, padx=5)

    # ==================================================================
    # Tab 2: 描述符
    # ==================================================================
    def _build_descriptor_tab(self):
        frame = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(frame, text="描述符管理")

        # 说明
        ttk.Label(frame, text="EDID 有 4 个 18 字节描述符块，可存放详细时序 (Detailed Timing) 或显示器信息描述符。",
                  foreground='gray', wraplength=750).pack(anchor='w', pady=(0, 10))

        # 4 个描述符块
        self.desc_frames: List[ttk.LabelFrame] = []
        self.desc_type_labels: List[ttk.Label] = []
        self.desc_summary_labels: List[ttk.Label] = []
        self.desc_edit_buttons: List[ttk.Button] = []

        for i in range(DESCRIPTOR_COUNT):
            blk_frame = ttk.LabelFrame(frame, text=f"描述符块 {i + 1}  (Bytes {54 + i * 18}–{54 + i * 18 + 17})", padding=8)
            blk_frame.pack(fill=tk.X, pady=(0, 8))
            blk_frame.columnconfigure(0, weight=1)

            info_frame = ttk.Frame(blk_frame)
            info_frame.grid(row=0, column=0, sticky='w')

            type_lbl = ttk.Label(info_frame, text="类型: —", font=('', 9, 'bold'))
            type_lbl.pack(side=tk.LEFT, padx=(0, 15))
            self.desc_type_labels.append(type_lbl)

            summary_lbl = ttk.Label(info_frame, text="", foreground='gray')
            summary_lbl.pack(side=tk.LEFT)
            self.desc_summary_labels.append(summary_lbl)

            btn_frame = ttk.Frame(blk_frame)
            btn_frame.grid(row=0, column=1, sticky='e')

            edit_btn = ttk.Button(btn_frame, text="编辑", width=8,
                                  command=lambda idx=i: self._edit_descriptor(idx))
            edit_btn.pack(side=tk.LEFT, padx=3)
            self.desc_edit_buttons.append(edit_btn)

            ttk.Button(btn_frame, text="添加时序", width=9,
                       command=lambda idx=i: self._add_timing_to_slot(idx)).pack(side=tk.LEFT, padx=3)
            ttk.Button(btn_frame, text="转为机种名", width=10,
                       command=lambda idx=i: self._set_as_name(idx)).pack(side=tk.LEFT, padx=3)
            ttk.Button(btn_frame, text="转为序列号", width=10,
                       command=lambda idx=i: self._set_as_serial(idx)).pack(side=tk.LEFT, padx=3)
            ttk.Button(btn_frame, text="清除", width=6,
                       command=lambda idx=i: self._clear_descriptor(idx)).pack(side=tk.LEFT, padx=3)

            self.desc_frames.append(blk_frame)

    # ==================================================================
    # Tab 3: 时序管理
    # ==================================================================
    def _build_timing_tab(self):
        frame = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(frame, text="时序预设 & 标准时序")

        # ---- 预设时序快速添加 ----
        sec1 = Section(frame, "从预设添加详细时序")
        sec1.pack(fill=tk.X, pady=(0, SECTION_PAD))

        ttk.Label(sec1, text="选择预设:", width=12, anchor='e').grid(row=0, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.preset_combo_var = tk.StringVar()
        combo = ttk.Combobox(sec1, textvariable=self.preset_combo_var,
                             values=self._preset_names, width=42, state='readonly')
        combo.grid(row=0, column=1, sticky='ew', padx=5, pady=PAD_Y)
        sec1.columnconfigure(1, weight=1)

        ttk.Label(sec1, text="目标槽位:", width=12, anchor='e').grid(row=1, column=0, sticky='e', padx=(0, 5), pady=PAD_Y)
        self.target_slot_var = tk.StringVar(value="自动选择")
        slot_combo = ttk.Combobox(sec1, textvariable=self.target_slot_var,
                                  values=["自动选择", "槽位 1", "槽位 2", "槽位 3", "槽位 4"],
                                  width=16, state='readonly')
        slot_combo.grid(row=1, column=1, sticky='w', padx=5, pady=PAD_Y)

        ttk.Button(sec1, text="✓ 添加时序", command=self._add_preset_timing).grid(row=1, column=2, padx=10, pady=PAD_Y)

        # ---- 手动输入时序 ----
        sec2 = Section(frame, "手动创建时序")
        sec2.pack(fill=tk.X, pady=(0, SECTION_PAD))

        ttk.Label(sec2, text="自定义详细时序参数，支持所有 CVT/CVT-RB/GTF 时序。",
                  foreground='gray').pack(anchor='w', pady=(0, 5))

        btn_row = ttk.Frame(sec2)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="✎ 手动输入时序参数...", command=self._manual_timing).pack(side=tk.LEFT, padx=5)

        # ---- 标准时序 ----
        sec3 = Section(frame, "标准时序 (Standard Timings)")
        sec3.pack(fill=tk.X, pady=(0, SECTION_PAD))

        ttk.Label(sec3, text="EDID 支持最多 8 条标准时序 (Bytes 38-53)。",
                  foreground='gray').pack(anchor='w', pady=(0, 5))

        self.std_timing_listbox = tk.Listbox(sec3, height=5, font=('Consolas', 10))
        self.std_timing_listbox.pack(fill=tk.X, pady=5)

        std_btn_frame = ttk.Frame(sec3)
        std_btn_frame.pack(fill=tk.X)
        ttk.Button(std_btn_frame, text="添加标准时序", command=self._add_std_timing).pack(side=tk.LEFT, padx=3)
        ttk.Button(std_btn_frame, text="删除选中", command=self._delete_std_timing).pack(side=tk.LEFT, padx=3)
        ttk.Button(std_btn_frame, text="清除全部", command=self._clear_all_std_timings).pack(side=tk.LEFT, padx=3)

        # 常见标准时序快速添加
        common_frame = ttk.Frame(sec3)
        common_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(common_frame, text="快速添加:").pack(side=tk.LEFT, padx=(0, 5))
        for res, ratio, ref in [("1920×1080", 3, 60), ("1680×1050", 0, 60),
                                 ("1280×720", 3, 60), ("1024×768", 1, 60),
                                 ("800×600", 1, 60)]:
            ttk.Button(common_frame, text=f"{res}@{ref}Hz",
                       command=lambda r=res, a=ratio, f=ref: self._quick_add_std(r, a, f)
                       ).pack(side=tk.LEFT, padx=2)

    # ==================================================================
    # Tab 4: CEA-861 扩展
    # ==================================================================
    def _build_cea_tab(self):
        frame = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(frame, text="CEA-861 扩展")

        # 信息标签
        self.cea_status_var = tk.StringVar(value="无 CEA-861 扩展块")
        ttk.Label(frame, textvariable=self.cea_status_var, font=('', 10, 'bold')).pack(anchor='w', pady=(0, 10))

        # 滚动
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _mw(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _mw)
        scroll_frame.bind("<MouseWheel>", _mw)
        scroll_frame.columnconfigure(0, weight=1)

        # ── CEA 基本参数 ──
        sec1 = Section(scroll_frame, "CEA-861 扩展块信息")
        sec1.pack(fill=tk.X, pady=(0, SECTION_PAD))
        sec1.columnconfigure(1, weight=1)

        ttk.Label(sec1, text="版本:", width=14, anchor='e').grid(row=0, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_rev_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_rev_var).grid(row=0, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="Underscan:", width=14, anchor='e').grid(row=1, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_underscan_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_underscan_var).grid(row=1, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="Basic Audio:", width=14, anchor='e').grid(row=2, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_audio_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_audio_var).grid(row=2, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="YCbCr 4:4:4:", width=14, anchor='e').grid(row=3, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_ycbcr444_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_ycbcr444_var).grid(row=3, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="YCbCr 4:2:2:", width=14, anchor='e').grid(row=4, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_ycbcr422_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_ycbcr422_var).grid(row=4, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="Native DTDs:", width=14, anchor='e').grid(row=5, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_native_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_native_var).grid(row=5, column=1, sticky='w', pady=2)

        ttk.Label(sec1, text="校验和:", width=14, anchor='e').grid(row=6, column=0, sticky='e', padx=(0, 5), pady=2)
        self.cea_cs_var = tk.StringVar(value="—")
        ttk.Label(sec1, textvariable=self.cea_cs_var).grid(row=6, column=1, sticky='w', pady=2)

        # ── Data Blocks 列表 ──
        sec2 = Section(scroll_frame, "数据块 (Data Blocks)")
        sec2.pack(fill=tk.X, pady=(0, SECTION_PAD))

        self.cea_db_text = tk.Text(sec2, font=('Consolas', 9), height=10, wrap=tk.WORD,
                                    bg='#f5f5f5', state=tk.DISABLED)
        self.cea_db_text.pack(fill=tk.X, pady=5)

        # ── SVD 编辑 ──
        sec3 = Section(scroll_frame, "短视频描述符 (SVD / 分辨率)")
        sec3.pack(fill=tk.X, pady=(0, SECTION_PAD))

        self.cea_svd_frame = ttk.Frame(sec3)
        self.cea_svd_frame.pack(fill=tk.X)

        svd_add_frame = ttk.Frame(sec3)
        svd_add_frame.pack(fill=tk.X, pady=5)

        self.cea_svd_vic_var = tk.IntVar(value=16)
        ttk.Label(svd_add_frame, text="VIC:").pack(side=tk.LEFT, padx=2)
        ttk.Spinbox(svd_add_frame, textvariable=self.cea_svd_vic_var, from_=1, to=256, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(svd_add_frame, text="(如 16=1080p60, 97=4K60, 74=1440p60)").pack(side=tk.LEFT, padx=5)
        self.cea_svd_native_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(svd_add_frame, text="原生", variable=self.cea_svd_native_var).pack(side=tk.LEFT, padx=5)
        ttk.Button(svd_add_frame, text="添加 SVD", command=self._add_cea_svd).pack(side=tk.LEFT, padx=5)
        ttk.Button(svd_add_frame, text="清除列表", command=self._clear_cea_svds).pack(side=tk.LEFT, padx=5)

        # ── HDMI VSDB ──
        sec4 = Section(scroll_frame, "HDMI VSDB (厂商特定数据)")
        sec4.pack(fill=tk.X, pady=(0, SECTION_PAD))
        sec4.columnconfigure(1, weight=1)

        self.cea_hdmi_text = tk.Text(sec4, font=('Consolas', 9), height=4, wrap=tk.WORD,
                                      bg='#f5f5f5', state=tk.DISABLED)
        self.cea_hdmi_text.pack(fill=tk.X, pady=5)

        # ── Audio ──
        sec5 = Section(scroll_frame, "音频格式 (Audio Formats)")
        sec5.pack(fill=tk.X, pady=(0, SECTION_PAD))

        self.cea_audio_text = tk.Text(sec5, font=('Consolas', 9), height=4, wrap=tk.WORD,
                                       bg='#f5f5f5', state=tk.DISABLED)
        self.cea_audio_text.pack(fill=tk.X, pady=5)

        # ── CEA DTDs ──
        sec6 = Section(scroll_frame, "CEA 扩展中的 DTD (附加详细时序)")
        sec6.pack(fill=tk.X, pady=(0, SECTION_PAD))

        self.cea_dtd_frame = ttk.Frame(sec6)
        self.cea_dtd_frame.pack(fill=tk.X)

        ttk.Button(sec6, text="添加 CEA DTD...", command=self._add_cea_dtd).pack(side=tk.LEFT, padx=5)

        # ── 按钮 ──
        btn_frame = ttk.Frame(scroll_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="创建/重置 CEA-861 扩展", command=self._create_cea).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="↻ 刷新 CEA 信息", command=self._refresh_cea_tab).pack(side=tk.LEFT, padx=5)

    # ==================================================================
    # Tab 5: 原始数据
    # ==================================================================
    def _build_raw_tab(self):
        frame = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(frame, text="原始数据")

        # Hex 视图
        self.raw_title_var = tk.StringVar(value="EDID 原始数据 (128 字节 - Hex View):")
        ttk.Label(frame, textvariable=self.raw_title_var, font=('', 10, 'bold')).pack(anchor='w', pady=(0, 5))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.raw_text = tk.Text(text_frame, font=('Consolas', 10), wrap=tk.NONE,
                                width=75, height=28, bg='#1e1e1e', fg='#d4d4d4',
                                insertbackground='white')
        self.raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(text_frame, orient='vertical', command=self.raw_text.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.raw_text.configure(yscrollcommand=scroll_y.set)

        scroll_x = ttk.Scrollbar(frame, orient='horizontal', command=self.raw_text.xview)
        scroll_x.pack(fill=tk.X)
        self.raw_text.configure(xscrollcommand=scroll_x.set)

        # 操作按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="↻ 刷新", command=self._refresh_raw).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="📋 复制到剪贴板", command=self._copy_raw).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="✓ 验证校验和", command=self._verify_checksum).pack(side=tk.LEFT, padx=3)

        self.raw_checksum_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=self.raw_checksum_var, foreground='gray').pack(side=tk.RIGHT, padx=10)

    # ==================================================================
    # 数据加载与保存
    # ==================================================================
    def _new_edid(self, blocks: int = 1):
        """创建新 EDID"""
        if self._modified and not self._confirm_discard():
            return
        self.edid = EDID.create_blank(blocks=blocks)
        if blocks >= 2:
            self.edid.ensure_cea()
        self.file_path = None
        self._source_rtd_path = None
        self._modified = False
        self._refresh_all()
        self._update_title()
        size_label = {1: "128B", 2: "256B", 3: "384B"}.get(blocks, f"{blocks*128}B")
        self.set_status(f"已创建空白 EDID ({size_label})")

    def _open_file(self):
        """打开 EDID 文件（支持自动识别纯 EDID 或 RTD 固件）"""
        if self._modified and not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开 EDID / RTD 固件文件",
            filetypes=[("EDID & RTD 文件", "*.bin *.edid *.dat *.raw *.rtd"),
                       ("所有文件", "*.*")]
        )
        if not path:
            return
        self._load_file(path)

    def _open_rtd_file(self):
        """专门打开 RTD 固件文件"""
        if self._modified and not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开 RTD 固件文件",
            filetypes=[("RTD 固件 & 二进制文件", "*.bin *.rtd *.hex *.rom"),
                       ("所有文件", "*.*")]
        )
        if not path:
            return
        self._load_file(path, is_rtd=True)

    def _load_file(self, path: str, is_rtd: bool = False):
        """通用文件加载"""
        try:
            # 先尝试 RTD 分析
            rtd_info = analyze_rtd_file(path) if is_rtd or path.lower().endswith(('.rtd', '.rom')) else None

            if rtd_info and rtd_info.edid_offsets:
                # RTD 文件中有 EDID
                self._source_rtd_path = path
                if len(rtd_info.edid_offsets) > 1:
                    # 多个 EDID → 让用户选
                    choice = self._choose_edid_offset(rtd_info)
                    if choice is None:
                        return
                    selected_edid = rtd_info.edids[choice]
                    self._edid_offset = rtd_info.edid_offsets[choice]
                else:
                    selected_edid = rtd_info.edids[0]
                    self._edid_offset = rtd_info.edid_offsets[0]

                self.edid = selected_edid
                self.file_path = path
                self._modified = False
                self._refresh_all()
                self._update_title()
                self.set_status(
                    f"RTD 固件 [{rtd_info.chip_model}]: {os.path.basename(path)} "
                    f"— EDID @ 0x{self._edid_offset:04X} "
                    f"({self.edid.block_count * 128} 字节)"
                )
            else:
                # 普通 EDID 文件
                self.edid = EDID.from_file(path)
                self.file_path = path
                self._source_rtd_path = None
                self._modified = False
                self._refresh_all()
                self._update_title()
                size_info = f"({self.edid.block_count * 128} 字节)" if self.edid.block_count > 1 else "(128 字节)"
                self.set_status(f"已加载: {os.path.basename(path)} {size_info}")
        except Exception as e:
            messagebox.showerror("错误", f"无法加载文件:\n{e}")

    def _choose_edid_offset(self, rtd_info) -> Optional[int]:
        """当 RTD 固件包含多个 EDID 时，让用户选择"""
        dlg = tk.Toplevel(self.root)
        dlg.title("选择 EDID — 固件中包含多个 EDID")
        dlg.geometry("520x350")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text=f"RTD 固件中发现 {len(rtd_info.edid_offsets)} 个 EDID，请选择:",
                  font=('', 10, 'bold'), padding=10).pack()

        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        listbox = tk.Listbox(list_frame, font=('Consolas', 9), width=70)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for i, (offset, edid) in enumerate(zip(rtd_info.edid_offsets, rtd_info.edids)):
            mfr = edid.manufacturer_id
            model = edid.get_model_name() or "?"
            sn = edid.get_serial_string() or edid.serial_number
            listbox.insert(tk.END,
                           f"#{i+1}  Offset 0x{offset:04X}  |  {mfr}  |  {model}  |  SN: {sn}")

        result = [None]

        def on_ok():
            sel = listbox.curselection()
            if sel:
                result[0] = sel[0]
                dlg.destroy()

        listbox.bind('<Double-Button-1>', lambda e: on_ok())
        ttk.Button(dlg, text="选择", command=on_ok).pack(pady=10)

        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = self.root.winfo_rootx() + (pw - 520) // 2
        y = self.root.winfo_rooty() + (ph - 350) // 2
        dlg.geometry(f"+{x}+{y}")
        self.root.wait_window(dlg)
        return result[0]

    def _save_file(self):
        if self.edid is None:
            return
        if self.file_path and not self._source_rtd_path:
            self._write_edid(self.file_path)
        else:
            self._save_as_file()

    def _save_as_file(self):
        if self.edid is None:
            return
        path = filedialog.asksaveasfilename(
            title="保存 EDID 文件",
            defaultextension=".bin",
            filetypes=[("EDID 文件", "*.bin"), ("EDID 文件", "*.edid"), ("所有文件", "*.*")]
        )
        if not path:
            return
        self._write_edid(path)

    def _save_to_rtd(self):
        """将修改后的 EDID 写回 RTD 固件"""
        if self.edid is None:
            return
        if not hasattr(self, '_source_rtd_path') or not self._source_rtd_path:
            messagebox.showinfo("提示", "请先打开一个 RTD 固件文件")
            return
        out_path = filedialog.asksaveasfilename(
            title="保存修改后的 RTD 固件",
            defaultextension=".bin",
            filetypes=[("RTD 固件", "*.bin"), ("所有文件", "*.*")]
        )
        if not out_path:
            return
        try:
            self._apply_basic_info()
            self.edid.save_to_rtd(self._source_rtd_path, out_path,
                                  offset=getattr(self, '_edid_offset', None))
            self.set_status(f"RTD 固件已保存: {os.path.basename(out_path)}")
            messagebox.showinfo("成功", f"EDID 已写回 RTD 固件:\n{out_path}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _write_edid(self, path: str):
        """实际写入文件，先应用所有 UI 修改"""
        self._apply_basic_info()
        try:
            self.edid.save(path)  # 使用 save (to_bytes_all)
            self.file_path = path
            self._source_rtd_path = None
            self._modified = False
            self._refresh_raw()
            self._update_title()
            self.set_status(f"已保存 ({self.edid.block_count * 128} 字节): {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    # ==================================================================
    # UI → EDID 对象 同步
    # ==================================================================
    def _apply_basic_info(self):
        """将基本信息面板的值写入 EDID 对象"""
        if self.edid is None:
            return
        try:
            # PnP ID
            pnp = self.pnp_var.get().strip().upper()
            if pnp and len(pnp) == 3 and pnp.isalpha():
                self.edid.manufacturer_id = pnp

            # Product Code
            pc_text = self.product_var.get().strip()
            if pc_text:
                self.edid.product_code = int(pc_text, 16) if pc_text.lower().startswith('0x') else int(pc_text)

            # Serial Number
            sn_text = self.serial_num_var.get().strip()
            if sn_text:
                self.edid.serial_number = int(sn_text, 16) if sn_text.lower().startswith('0x') else int(sn_text)

            # Date
            self.edid.manufacture_week = self.week_var.get()
            self.edid.manufacture_year = self.year_var.get()

            # Version
            self.edid.edid_version = (self.ver_major.get(), self.ver_minor.get())

            # Input type
            self.edid._data[20] = (self.edid._data[20] & 0x7F) | (0x80 if self.input_type_var.get() == "数字" else 0x00)

            # Screen size
            self.edid.screen_width_cm = self.scr_width_var.get()
            self.edid.screen_height_cm = self.scr_height_var.get()

            # Extension count
            self.edid.extension_count = self.ext_cnt_var.get()

            # Model name
            mn = self.model_name_var.get().strip()
            if mn:
                self.edid.set_model_name(mn)

            # Serial string
            sns = self.serial_str_var.get().strip()
            if sns:
                self.edid.set_serial_string(sns)

            self._refresh_all()
            self.set_status("基本信息已应用 ✓")
        except ValueError as e:
            messagebox.showerror("输入错误", f"请检查输入格式:\n{e}")
            return False
        return True

    def _load_basic_info(self):
        """从 EDID 对象加载到基本信息面板"""
        if self.edid is None:
            return
        e = self.edid
        self.pnp_var.set(e.manufacturer_id)
        self.product_var.set(f"0x{e.product_code:04X}")
        self.serial_num_var.set(str(e.serial_number))
        try:
            self.week_var.set(e.manufacture_week)
            self.year_var.set(e.manufacture_year)
        except Exception:
            pass
        try:
            self.ver_major.set(e.edid_version[0])
            self.ver_minor.set(e.edid_version[1])
        except Exception:
            pass
        self.input_type_var.set("数字" if e.is_digital else "模拟")
        self.scr_width_var.set(e.screen_width_cm)
        self.scr_height_var.set(e.screen_height_cm)
        self.ext_cnt_var.set(e.extension_count)

        model_name = e.get_model_name()
        self.model_name_var.set(model_name if model_name else "")

        serial_str = e.get_serial_string()
        self.serial_str_var.set(serial_str if serial_str else "")

        self.set_status("已从 EDID 重新加载")

    # ==================================================================
    # 描述符操作
    # ==================================================================
    def _refresh_descriptors_ui(self):
        """刷新描述符列表显示"""
        if self.edid is None:
            return
        for i in range(DESCRIPTOR_COUNT):
            block = self.edid.descriptors[i]
            self.desc_type_labels[i].config(text=f"类型: {block.type_name}")
            self.desc_summary_labels[i].config(text=block.summary)

    def _edit_descriptor(self, index: int):
        """编辑描述符块"""
        if self.edid is None:
            return
        block = self.edid.descriptors[index]

        if block.is_timing and block.timing:
            # 打开时序编辑对话框
            dlg = TimingEditDialog(self.root, block.timing, self._preset_names)
            self.root.wait_window(dlg)
            if dlg.result:
                self.edid.descriptors[index].timing = dlg.result
                self.edid.descriptors[index].is_timing = True
                self.edid.descriptors[index].monitor = None
                self.edid._sync_descriptors()
                self._mark_modified()
                self._refresh_descriptors_ui()
                self._refresh_raw()
                self.set_status(f"描述符 {index + 1} 已更新")
        elif block.monitor:
            tag = block.monitor.tag
            tag_names = {0xFC: "机种名", 0xFF: "序列号", 0xFD: "范围限制", 0xFE: "未指定文本"}
            label = tag_names.get(tag, f"描述符 0x{tag:02X}")

            dlg = tk.Toplevel(self.root)
            dlg.title(f"编辑 — {label}")
            dlg.geometry("400x200")
            dlg.transient(self.root)
            dlg.grab_set()
            dlg.resizable(False, False)

            ttk.Label(dlg, text=f"编辑 {label} (最多13字符):", padding=10).pack()
            text_var = tk.StringVar(value=block.monitor.get_text())
            entry = ttk.Entry(dlg, textvariable=text_var, width=35, font=('Consolas', 11))
            entry.pack(padx=15, pady=5, fill=tk.X)
            entry.select_range(0, tk.END)
            entry.focus()

            ttk.Label(dlg, text=f"当前标签: 0x{tag:02X}", foreground='gray').pack()

            def on_ok():
                block.monitor.set_text(text_var.get().strip() if text_var.get().strip() else " ")
                self.edid._sync_descriptors()
                self._mark_modified()
                self._refresh_descriptors_ui()
                self._refresh_raw()
                self.set_status(f"描述符 {index + 1} ({label}) 已更新")
                dlg.destroy()

            def on_cancel():
                dlg.destroy()

            btn_frame = ttk.Frame(dlg)
            btn_frame.pack(pady=10)
            ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)

            dlg.update_idletasks()
            pw, ph = self.root.winfo_width(), self.root.winfo_height()
            x = self.root.winfo_rootx() + (pw - 400) // 2
            y = self.root.winfo_rooty() + (ph - 200) // 2
            dlg.geometry(f"+{x}+{y}")

            self.root.wait_window(dlg)
        else:
            # 空描述符 → 询问用户想做什么
            choice = messagebox.askyesnocancel(
                "空描述符",
                f"描述符块 {index + 1} 当前为空。\n\n"
                "选择「是」→ 添加详细时序\n"
                "选择「否」→ 添加机种名\n"
                "选择「取消」→ 不做更改"
            )
            if choice is True:
                self._add_timing_to_slot(index)
            elif choice is False:
                self._set_as_name(index)

    def _add_timing_to_slot(self, index: int):
        """向指定槽位添加详细时序"""
        if self.edid is None:
            return
        dlg = TimingEditDialog(self.root, preset_names=self._preset_names)
        self.root.wait_window(dlg)
        if dlg.result:
            self.edid.descriptors[index].is_timing = True
            self.edid.descriptors[index].timing = dlg.result
            self.edid.descriptors[index].monitor = None
            self.edid._sync_descriptors()
            self._mark_modified()
            self._refresh_descriptors_ui()
            self._refresh_raw()
            self.set_status(f"已向槽位 {index + 1} 添加详细时序")

    def _set_as_name(self, index: int):
        """将描述符设为机种名"""
        if self.edid is None:
            return
        name = self.model_name_var.get().strip()
        if not name:
            name = "LCD MONITOR"
        self.edid.descriptors[index].is_timing = False
        self.edid.descriptors[index].timing = None
        self.edid.descriptors[index].monitor = MonitorDescriptor(tag=0xFC)
        self.edid.descriptors[index].monitor.set_text(name)
        self.edid._sync_descriptors()
        self._mark_modified()
        self._refresh_descriptors_ui()
        self._refresh_raw()
        self.set_status(f"描述符 {index + 1} 已设为机种名")

    def _set_as_serial(self, index: int):
        """将描述符设为序列号"""
        if self.edid is None:
            return
        sn = self.serial_str_var.get().strip()
        if not sn:
            sn = str(self.edid.serial_number) if self.edid.serial_number else "00000001"
        self.edid.descriptors[index].is_timing = False
        self.edid.descriptors[index].timing = None
        self.edid.descriptors[index].monitor = MonitorDescriptor(tag=0xFF)
        self.edid.descriptors[index].monitor.set_text(sn)
        self.edid._sync_descriptors()
        self._mark_modified()
        self._refresh_descriptors_ui()
        self._refresh_raw()
        self.set_status(f"描述符 {index + 1} 已设为序列号")

    def _clear_descriptor(self, index: int):
        """清除描述符"""
        if self.edid is None:
            return
        if messagebox.askyesno("确认", f"确定要清除描述符块 {index + 1} 吗？"):
            self.edid.clear_descriptor(index)
            self._mark_modified()
            self._refresh_descriptors_ui()
            self._refresh_raw()
            self.set_status(f"描述符 {index + 1} 已清除")

    # ==================================================================
    # 时序操作
    # ==================================================================
    def _add_preset_timing(self):
        """从预设添加详细时序"""
        if self.edid is None:
            return
        name = self.preset_combo_var.get()
        if not name:
            messagebox.showwarning("预设", "请先选择一个预设时序")
            return
        for pname, pt in TIMING_PRESETS:
            if pname == name:
                slot_text = self.target_slot_var.get()
                index = None
                if slot_text.startswith("槽位"):
                    index = int(slot_text[-1]) - 1
                timing = DetailedTiming(
                    pixel_clock=pt.pixel_clock, h_active=pt.h_active, h_blanking=pt.h_blanking,
                    v_active=pt.v_active, v_blanking=pt.v_blanking,
                    h_front_porch=pt.h_front_porch, h_sync=pt.h_sync,
                    v_front_porch=pt.v_front_porch, v_sync=pt.v_sync,
                    h_image_size=pt.h_image_size, v_image_size=pt.v_image_size,
                    h_border=pt.h_border, v_border=pt.v_border,
                )
                self.edid.add_detailed_timing(timing, index)
                self._mark_modified()
                self._refresh_descriptors_ui()
                self._refresh_raw()
                self.set_status(f"已添加预设时序: {name}")
                return

    def _manual_timing(self):
        """打开手动时序编辑对话框"""
        if self.edid is None:
            return
        dlg = TimingEditDialog(self.root, preset_names=self._preset_names)
        self.root.wait_window(dlg)
        if dlg.result:
            self.edid.add_detailed_timing(dlg.result)
            self._mark_modified()
            self._refresh_descriptors_ui()
            self._refresh_raw()
            self.set_status("已添加手动时序")

    # ==================================================================
    # 标准时序操作
    # ==================================================================
    def _refresh_std_timings(self):
        """刷新标准时序列表"""
        self.std_timing_listbox.delete(0, tk.END)
        if self.edid is None:
            return
        for i, (ha, ratio, ref) in enumerate(self.edid.get_standard_timings()):
            ratio_names = {0: "16:10", 1: "4:3", 2: "5:4", 3: "16:9"}
            rname = ratio_names.get(ratio, f"AR{ratio}")
            self.std_timing_listbox.insert(tk.END, f"#{i+1}: {ha}×? @ {ref}Hz ({rname})")

    def _add_std_timing(self):
        """添加标准时序"""
        if self.edid is None:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("添加标准时序")
        dlg.geometry("380x220")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="水平有效像素:").grid(row=0, column=0, sticky='e', pady=5)
        ha_var = tk.IntVar(value=1920)
        ttk.Spinbox(frame, textvariable=ha_var, from_=256, to=4095, width=12).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="刷新率 (Hz):").grid(row=1, column=0, sticky='e', pady=5)
        ref_var = tk.IntVar(value=60)
        ttk.Spinbox(frame, textvariable=ref_var, from_=60, to=123, width=12).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="宽高比:").grid(row=2, column=0, sticky='e', pady=5)
        ratio_var = tk.StringVar(value="16:9")
        ttk.Combobox(frame, textvariable=ratio_var, values=["16:10", "4:3", "5:4", "16:9"],
                     width=12, state='readonly').grid(row=2, column=1, padx=5)

        ttk.Label(frame, text="目标槽位:").grid(row=3, column=0, sticky='e', pady=5)
        slot_var = tk.IntVar(value=1)
        ttk.Spinbox(frame, textvariable=slot_var, from_=1, to=8, width=12).grid(row=3, column=1, padx=5)

        def on_add():
            ratio_map = {"16:10": 0, "4:3": 1, "5:4": 2, "16:9": 3}
            r = ratio_map.get(ratio_var.get(), 3)
            self.edid.set_standard_timing(slot_var.get() - 1, ha_var.get(), r, ref_var.get())
            self.edid._sync_descriptors()
            self._mark_modified()
            self._refresh_std_timings()
            self._refresh_raw()
            self.set_status(f"标准时序 #{slot_var.get()} 已添加")
            dlg.destroy()

        ttk.Button(frame, text="添加", command=on_add).grid(row=4, column=0, columnspan=2, pady=15)

        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = self.root.winfo_rootx() + (pw - 380) // 2
        y = self.root.winfo_rooty() + (ph - 220) // 2
        dlg.geometry(f"+{x}+{y}")
        self.root.wait_window(dlg)

    def _delete_std_timing(self):
        sel = self.std_timing_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中一条标准时序")
            return
        idx = sel[0]
        self.edid.clear_standard_timing(idx)
        self._mark_modified()
        self._refresh_std_timings()
        self._refresh_raw()
        self.set_status(f"标准时序 #{idx + 1} 已删除")

    def _clear_all_std_timings(self):
        if messagebox.askyesno("确认", "确定要清除所有标准时序吗？"):
            for i in range(8):
                self.edid.clear_standard_timing(i)
            self._mark_modified()
            self._refresh_std_timings()
            self._refresh_raw()
            self.set_status("所有标准时序已清除")

    def _quick_add_std(self, name: str, ratio: int, refresh: int):
        """快速添加常见标准时序"""
        if self.edid is None:
            return
        # 解析分辨率
        parts = name.split('×')
        ha = int(parts[0])
        # 找到空槽位
        timings = self.edid.get_standard_timings()
        for i in range(8):
            if i >= len(timings) or timings[i] == (0, 0, 0):
                self.edid.set_standard_timing(i, ha, ratio, refresh)
                break
        else:
            # 全部占满，替换最后一个
            self.edid.set_standard_timing(7, ha, ratio, refresh)
        self.edid._sync_descriptors()
        self._mark_modified()
        self._refresh_std_timings()
        self._refresh_raw()
        self.set_status(f"已快速添加标准时序: {name}@{refresh}Hz")

    # ==================================================================
    # 原始数据视图
    # ==================================================================
    def _refresh_raw(self):
        """刷新原始 HEX 视图（支持多块显示）"""
        if self.edid is None:
            return
        self.edid._sync_descriptors()
        if self.edid._cea_ext:
            self.edid._sync_extensions()
        data = self.edid.to_bytes_all()
        total_size = len(data)
        num_blocks = self.edid.block_count

        self.raw_title_var.set(f"EDID 原始数据 ({total_size} 字节, {num_blocks} 块 - Hex View):")

        lines = []
        for offset in range(0, total_size, 16):
            chunk = data[offset:offset + 16]
            hex_part = ' '.join(f'{b:02X}' for b in chunk)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)

            marker = ""
            block_num = offset // 128
            block_offset = offset % 128

            if block_num == 0:
                markers_0 = {
                    0: "← Header", 8: "← Mfr ID / Product / Serial",
                    16: "← Week / Year / Version", 20: "← Display Parameters",
                    38: "← Standard Timings", 54: "← Descriptor Block 1",
                    72: "← Descriptor Block 2", 90: "← Descriptor Block 3",
                    108: "← Descriptor Block 4", 126: "← Ext Count / Checksum"
                }
                marker = markers_0.get(block_offset, "")
                if marker:
                    marker = f"  {marker}"
            elif block_num == 1 and block_offset == 0:
                marker = "  ← CEA-861 Extension (Block 1)"
            elif block_num == 2 and block_offset == 0:
                marker = "  ← Extension Block 2"

            addr_str = f"{block_num:1d}:{block_offset:04X}" if block_num > 0 else f"{offset:04X}"
            lines.append(f"{addr_str}  {hex_part:<48s}  {ascii_part}{marker}")

        self.raw_text.config(state=tk.NORMAL)
        self.raw_text.delete('1.0', tk.END)
        self.raw_text.insert('1.0', '\n'.join(lines))
        self.raw_text.config(state=tk.DISABLED)

        # 校验和状态（检查所有块）
        cs_parts = []
        all_ok = True
        for i in range(num_blocks):
            ok = self.edid.is_checksum_valid(i)
            cs_byte = self.edid._blocks[i][127] if i < len(self.edid._blocks) else 0
            cs_parts.append(f"B{i}=0x{cs_byte:02X} {'✓' if ok else '✗'}")
            if not ok:
                all_ok = False
        cs_str = " | ".join(cs_parts)
        self.raw_checksum_var.set(f"校验和: {cs_str}")
        self.checksum_var.set(f"校验和: {'✓' if all_ok else '✗'} ({cs_str})")

    def _copy_raw(self):
        """复制原始 HEX 到剪贴板"""
        if self.edid is None:
            return
        data = self.edid.to_bytes_all()
        hex_str = ' '.join(f'{b:02X}' for b in data)
        self.root.clipboard_clear()
        self.root.clipboard_append(hex_str)
        self.set_status("已复制 HEX 到剪贴板")

    # ==================================================================
    # 校验和
    # ==================================================================
    def _verify_checksum(self):
        if self.edid is None:
            return
        results = []
        all_ok = True
        for i in range(self.edid.block_count):
            ok = self.edid.is_checksum_valid(i)
            if not ok:
                all_ok = False
            results.append(f"Block {i}: {'✓' if ok else '✗'}")
        if all_ok:
            messagebox.showinfo("校验和", "✓ 所有块校验和正确！\n" + "\n".join(results))
        else:
            messagebox.showwarning("校验和",
                                   f"✗ 校验和错误！\n\n" + "\n".join(results) +
                                   f"\n\n请点击「编辑 → 重新计算校验和」来修复。")

    def _recalc_checksum(self):
        if self.edid is None:
            return
        self.edid._sync_descriptors()
        if self.edid._cea_ext:
            self.edid._sync_extensions()
        self.edid.update_all_checksums()
        self._refresh_raw()
        self._mark_modified()
        self.set_status("所有块校验和已重新计算 ✓")

    # ==================================================================
    # CEA-861 扩展操作
    # ==================================================================
    def _refresh_cea_tab(self):
        """刷新 CEA-861 扩展标签页"""
        cea = self.edid.cea_extension if self.edid else None
        if cea is None:
            self.cea_status_var.set("无 CEA-861 扩展块 — 点击「创建/重置 CEA-861 扩展」添加")
            self.cea_rev_var.set("—")
            self.cea_underscan_var.set("—"); self.cea_audio_var.set("—")
            self.cea_ycbcr444_var.set("—"); self.cea_ycbcr422_var.set("—")
            self.cea_native_var.set("—"); self.cea_cs_var.set("—")
            self.cea_db_text.config(state=tk.NORMAL); self.cea_db_text.delete('1.0', tk.END)
            self.cea_db_text.config(state=tk.DISABLED)
            self.cea_hdmi_text.config(state=tk.NORMAL); self.cea_hdmi_text.delete('1.0', tk.END)
            self.cea_hdmi_text.config(state=tk.DISABLED)
            self.cea_audio_text.config(state=tk.NORMAL); self.cea_audio_text.delete('1.0', tk.END)
            self.cea_audio_text.config(state=tk.DISABLED)
            self._clear_cea_dtd_display()
            self._clear_cea_svd_display()
            return

        self.cea_status_var.set(f"CEA-861 扩展块 (版本 {cea.revision}, "
                                f"{len(cea.data_blocks)} 个数据块, {len(cea.dtds)} 个 DTD)")

        self.cea_rev_var.set(str(cea.revision))
        self.cea_underscan_var.set("是" if cea.underscan else "否")
        self.cea_audio_var.set("是" if cea.basic_audio else "否")
        self.cea_ycbcr444_var.set("是" if cea.ycbcr444 else "否")
        self.cea_ycbcr422_var.set("是" if cea.ycbcr422 else "否")
        self.cea_native_var.set(str(cea.native_dtd_count))

        ok = self.edid.is_checksum_valid(1)
        expected = self.edid.calculate_checksum(1)
        self.cea_cs_var.set(f"{'✓' if ok else '✗'} (0x{self.edid._blocks[1][127]:02X})")

        # Data Blocks 详情
        self.cea_db_text.config(state=tk.NORMAL)
        self.cea_db_text.delete('1.0', tk.END)
        for i, db in enumerate(cea.data_blocks):
            self.cea_db_text.insert(tk.END, f"[{i}] {db.tag_name}\n")
            self.cea_db_text.insert(tk.END, f"    {db.summary}\n\n")
        self.cea_db_text.config(state=tk.DISABLED)

        # HDMI VSDB 详情
        self.cea_hdmi_text.config(state=tk.NORMAL)
        self.cea_hdmi_text.delete('1.0', tk.END)
        for db in cea.data_blocks:
            if isinstance(db, VendorDataBlock):
                self.cea_hdmi_text.insert(tk.END, f"厂商: {db.vendor_name}\n")
                self.cea_hdmi_text.insert(tk.END, f"OUI: 0x{db.ieee_oui:06X}\n")
                self.cea_hdmi_text.insert(tk.END, f"Summary: {db.summary}\n")
                if db.payload:
                    self.cea_hdmi_text.insert(tk.END, f"Raw: {' '.join(f'{b:02X}' for b in db.payload[:32])}\n")
        if not any(isinstance(db, VendorDataBlock) for db in cea.data_blocks):
            self.cea_hdmi_text.insert(tk.END, "(无厂商特定数据块)")
        self.cea_hdmi_text.config(state=tk.DISABLED)

        # Audio 详情
        self.cea_audio_text.config(state=tk.NORMAL)
        self.cea_audio_text.delete('1.0', tk.END)
        for db in cea.data_blocks:
            if isinstance(db, AudioDataBlock):
                for af in db.formats:
                    rates = af.sample_rate_list()
                    self.cea_audio_text.insert(tk.END,
                        f"{af.format_name} | {af.max_channels}ch | {', '.join(rates)}\n")
        if not any(isinstance(db, AudioDataBlock) for db in cea.data_blocks):
            self.cea_audio_text.insert(tk.END, "(无音频数据块 — Basic Audio 可能关闭)")
        self.cea_audio_text.config(state=tk.DISABLED)

        # SVD 显示
        self._refresh_cea_svd_display()

        # CEA DTD 显示
        self._refresh_cea_dtd_display()

    def _refresh_cea_svd_display(self):
        """刷新 SVD 列表显示"""
        for w in self.cea_svd_frame.winfo_children():
            w.destroy()
        cea = self.edid.cea_extension if self.edid else None
        if cea is None:
            return
        for db in cea.data_blocks:
            if isinstance(db, VideoDataBlock):
                for j, svd in enumerate(db.svds):
                    frame = ttk.Frame(self.cea_svd_frame)
                    frame.pack(fill=tk.X, pady=1)
                    ttk.Label(frame, text=f"#{j} VIC={svd.vic} — {svd.description}",
                              font=('Consolas', 9)).pack(side=tk.LEFT)
                    ttk.Button(frame, text="删除", width=6,
                               command=lambda d=db, s=j: self._remove_cea_svd(d, s)).pack(side=tk.RIGHT)

    def _clear_cea_svd_display(self):
        for w in self.cea_svd_frame.winfo_children():
            w.destroy()

    def _refresh_cea_dtd_display(self):
        """刷新 CEA DTD 列表显示"""
        for w in self.cea_dtd_frame.winfo_children():
            w.destroy()
        cea = self.edid.cea_extension if self.edid else None
        if cea is None:
            return
        for j, dtd in enumerate(cea.dtds):
            frame = ttk.Frame(self.cea_dtd_frame)
            frame.pack(fill=tk.X, pady=1)
            ttk.Label(frame, text=f"DTD #{j}: {dtd.h_active}×{dtd.v_active} @ {dtd.refresh_rate:.1f}Hz "
                      f"({dtd.pixel_clock_mhz:.1f}MHz)", font=('Consolas', 9)).pack(side=tk.LEFT)
            ttk.Button(frame, text="编辑", width=6,
                       command=lambda d=dtd, idx=j: self._edit_cea_dtd(d, idx)).pack(side=tk.RIGHT, padx=2)
            ttk.Button(frame, text="删除", width=6,
                       command=lambda idx=j: self._remove_cea_dtd(idx)).pack(side=tk.RIGHT, padx=2)

    def _clear_cea_dtd_display(self):
        for w in self.cea_dtd_frame.winfo_children():
            w.destroy()

    def _add_cea_svd(self):
        """添加 SVD"""
        cea = self.edid.ensure_cea()
        vic = self.cea_svd_vic_var.get()
        native = self.cea_svd_native_var.get()

        # 查找或创建 VideoDataBlock
        vid_db = None
        for db in cea.data_blocks:
            if isinstance(db, VideoDataBlock):
                vid_db = db
                break
        if vid_db is None:
            vid_db = VideoDataBlock(svds=[])
            cea.data_blocks.append(vid_db)

        vid_db.svds.append(ShortVideoDescriptor(vic=vic, native=native))
        self.edid._sync_extensions()
        self._mark_modified()
        self._refresh_cea_tab()
        self.set_status(f"已添加 SVD: VIC={vic} {'(原生)' if native else ''}")

    def _remove_cea_svd(self, db: VideoDataBlock, index: int):
        """删除 SVD"""
        if 0 <= index < len(db.svds):
            db.svds.pop(index)
            # 如果为空则移除此数据块
            if not db.svds:
                cea = self.edid.cea_extension
                if cea:
                    cea.data_blocks = [d for d in cea.data_blocks if d is not db]
            self.edid._sync_extensions()
            self._mark_modified()
            self._refresh_cea_tab()
            self.set_status(f"已删除 SVD #{index}")

    def _clear_cea_svds(self):
        """清除所有 SVD"""
        cea = self.edid.cea_extension
        if cea:
            cea.data_blocks = [d for d in cea.data_blocks if not isinstance(d, VideoDataBlock)]
            self.edid._sync_extensions()
            self._mark_modified()
            self._refresh_cea_tab()
            self.set_status("所有 SVD 已清除")

    def _add_cea_dtd(self):
        """添加 CEA DTD"""
        cea = self.edid.ensure_cea()
        dlg = TimingEditDialog(self.root, preset_names=self._preset_names)
        self.root.wait_window(dlg)
        if dlg.result:
            cea.dtds.append(dlg.result)
            self.edid._sync_extensions()
            self._mark_modified()
            self._refresh_cea_tab()
            self._refresh_raw()
            self.set_status(f"已添加 CEA DTD: {dlg.result.h_active}×{dlg.result.v_active} @ {dlg.result.refresh_rate:.1f}Hz")

    def _edit_cea_dtd(self, dtd: DetailedTiming, index: int):
        """编辑 CEA DTD"""
        cea = self.edid.cea_extension
        if cea is None:
            return
        dlg = TimingEditDialog(self.root, dtd, self._preset_names)
        self.root.wait_window(dlg)
        if dlg.result and 0 <= index < len(cea.dtds):
            cea.dtds[index] = dlg.result
            self.edid._sync_extensions()
            self._mark_modified()
            self._refresh_cea_tab()
            self._refresh_raw()
            self.set_status(f"CEA DTD #{index} 已更新")

    def _remove_cea_dtd(self, index: int):
        """删除 CEA DTD"""
        cea = self.edid.cea_extension
        if cea and 0 <= index < len(cea.dtds):
            cea.dtds.pop(index)
            self.edid._sync_extensions()
            self._mark_modified()
            self._refresh_cea_tab()
            self._refresh_raw()
            self.set_status(f"已删除 CEA DTD #{index}")

    def _create_cea(self):
        """创建/重置 CEA-861 扩展"""
        if self.edid is None:
            return
        if messagebox.askyesno("确认", "将创建/重置 CEA-861 扩展块。\n"
                               "这会覆盖现有的扩展块数据，是否继续？"):
            self.edid._cea_ext = CEA861Extension.create_minimal()
            if self.edid.block_count < 2:
                self.edid.set_blocks(2)
            self.edid._sync_extensions()
            self.edid.update_all_checksums()
            self._mark_modified()
            self._refresh_cea_tab()
            self._refresh_raw()
            self.set_status("CEA-861 扩展块已创建/重置 ✓")

    # ==================================================================
    # 厂商选择
    # ==================================================================
    def _on_vendor_selected(self, event=None):
        sel = self.vendor_var.get()
        if sel:
            pnp_id = sel.split(' - ')[0]
            self.pnp_var.set(pnp_id)
            self._mark_modified()

    def _show_vendor_popup(self):
        """显示厂商选择弹窗"""
        dlg = tk.Toplevel(self.root)
        dlg.title("选择 ODM 厂商")
        dlg.geometry("420x480")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="选择 PnP ID:", font=('', 10, 'bold'), padding=10).pack()

        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(list_frame, font=('Consolas', 10), yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)

        sorted_vendors = sorted(KNOWN_VENDORS.items(), key=lambda x: x[1])
        for pnp, name in sorted_vendors:
            listbox.insert(tk.END, f"{pnp}  —  {name}")

        def on_select(event):
            sel = listbox.curselection()
            if sel:
                idx = sel[0]
                pnp_id = sorted_vendors[idx][0]
                self.pnp_var.set(pnp_id)
                self._mark_modified()
                dlg.destroy()

        listbox.bind('<Double-Button-1>', on_select)

        ttk.Button(dlg, text="选择", command=lambda: on_select(None)).pack(pady=10)

        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = self.root.winfo_rootx() + (pw - 420) // 2
        y = self.root.winfo_rooty() + (ph - 480) // 2
        dlg.geometry(f"+{x}+{y}")
        self.root.wait_window(dlg)

    def _show_vendor_ref(self):
        """显示 PnP ID 参考表"""
        dlg = tk.Toplevel(self.root)
        dlg.title("常见 PnP ID 参考")
        dlg.geometry("480x500")
        dlg.transient(self.root)

        ttk.Label(dlg, text="常见 ODM 厂商 PnP ID 参考",
                  font=('', 11, 'bold'), padding=10).pack()

        text_frame = ttk.Frame(dlg)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        text = tk.Text(text_frame, font=('Consolas', 10), yscrollcommand=scrollbar.set,
                       wrap=tk.NONE)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text.yview)

        lines = []
        sorted_vendors = sorted(KNOWN_VENDORS.items(), key=lambda x: x[1])
        for pnp, name in sorted_vendors:
            lines.append(f"  {pnp}    {name}")
        text.insert('1.0', '\n'.join(lines))
        text.config(state=tk.DISABLED)

        ttk.Button(dlg, text="关闭", command=dlg.destroy).pack(pady=10)

        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        x = self.root.winfo_rootx() + (pw - 480) // 2
        y = self.root.winfo_rooty() + (ph - 500) // 2
        dlg.geometry(f"+{x}+{y}")
        self.root.wait_window(dlg)

    def _show_about(self):
        messagebox.showinfo(
            "关于 - EDID Editor Tool V2",
            "EDID 编辑器 V2\n\n"
            "EDID 数据编辑:\n"
            "  • 修改 ODM 制造商 PnP ID\n"
            "  • 修改机种名 (Monitor Name)、序列号 (SN)\n"
            "  • 添加/编辑详细时序 (Detailed Timing)\n"
            "  • 管理标准时序 (Standard Timings)\n\n"
            "CEA-861 扩展块 (384 字节 EDID):\n"
            "  • SVD 短视频描述符 (VIC 1-256)\n"
            "  • HDMI VSDB / 音频格式 / 扬声器配置\n"
            "  • CEA 扩展 DTD 附加时序\n\n"
            "RTD 固件支持:\n"
            "  • 自动识别 RTD 固件中嵌入的 EDID\n"
            "  • 支持多 EDID 选择\n"
            "  • EDID 写回 RTD 固件 (save_to_rtd)\n\n"
            "支持加载/保存 .bin .edid .rtd 格式文件\n"
            "支持 128 / 256 / 384 字节 EDID"
        )

    # ==================================================================
    # 工具方法
    # ==================================================================
    def _refresh_all(self):
        """刷新所有 UI 组件"""
        self._load_basic_info()
        self._refresh_descriptors_ui()
        self._refresh_std_timings()
        self._refresh_cea_tab()
        self._refresh_raw()

    def _mark_modified(self, *args):
        self._modified = True
        self._update_title()

    def _update_title(self):
        title = "EDID 编辑器 - EDID Editor Tool V2"
        if self.file_path:
            title += f" — {os.path.basename(self.file_path)}"
        if self._modified:
            title += " ●"
        self.root.title(title)

    def set_status(self, msg: str):
        self.status_var.set(msg)

    def _confirm_discard(self) -> bool:
        return messagebox.askyesno("未保存的修改", "当前 EDID 有未保存的修改，是否放弃？")

    def _on_close(self):
        if self._modified and not self._confirm_discard():
            return
        self.root.destroy()


# ===========================================================================
# 入口
# ===========================================================================
def main():
    root = tk.Tk()

    # 设置 DPI 感知 (Windows)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 主题
    style = ttk.Style()
    available = style.theme_names()
    for preferred in ('vista', 'xpnative', 'clam', 'default'):
        if preferred in available:
            style.theme_use(preferred)
            break

    app = EDIDEditorApp(root)

    # 支持拖放文件 (Windows)
    try:
        from tkinterdnd2 import DND_FILES
        root.drop_target_register(DND_FILES)
        root.dnd_bind('<<Drop>>', lambda e: app._open_dropped(e.data))
    except ImportError:
        pass  # tkinterdnd2 不可用

    # 窗口关闭
    root.protocol("WM_DELETE_WINDOW", app._on_close)

    root.mainloop()


if __name__ == '__main__':
    main()
