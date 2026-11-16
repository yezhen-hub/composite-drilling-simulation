import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import sys
import ctypes

# 导入保护
try:
    from Glmsh1 import DrillingModel
    from jisuan import CalculiXSolver
except Exception as e:
    print(f"Import Error: {e}")
    import sys
    sys.exit(f"缺少依赖，无法启动: {e}")


# ================================================================
# 配色方案 (专业工程软件风格)
# ================================================================
COLOR_BG = "#EEF1F5"        # 主背景 (浅灰蓝)
COLOR_CARD = "#FFFFFF"      # 卡片背景 (白)
COLOR_PRIMARY = "#2C3E50"   # 主色 (深蓝灰)
COLOR_ACCENT = "#3498DB"    # 强调色 (蓝)
COLOR_SUCCESS = "#27AE60"   # 成功 (绿)
COLOR_SUCCESS_D = "#219150" # 成功悬停
COLOR_DANGER = "#E67E22"    # 危险 (橙)
COLOR_DANGER_D = "#CA6510"  # 危险悬停
COLOR_MUTED = "#7F8C8D"     # 次要文字 (灰)
COLOR_LOG_BG = "#1E1E2E"    # 日志背景
COLOR_LOG_FG = "#A6E3A1"    # 日志文字

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_UI_B = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 17, "bold")
FONT_SUB = ("Microsoft YaHei UI", 9)
FONT_LOG = ("Consolas", 10)


# ================================================================
# Windows 高清 (DPI) 适配工具
# ================================================================
def enable_dpi_awareness():
    """开启 DPI 感知，必须在创建 Tk() 之前调用，避免高分屏发虚。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_screen_dpi():
    try:
        return ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        return 96


def get_work_area():
    """返回屏幕可用区域 (排除任务栏): (left, top, right, bottom)。"""
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


class ScrollableFrame(tk.Frame):
    """可滚动容器：内容过高时自动出现滚动条。"""

    def __init__(self, parent, bg=COLOR_BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_inner(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfig(self._window, width=event.width)

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class DrillingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("复合材料增量钻孔仿真系统")
        self.root.configure(bg=COLOR_BG)

        # --- 高清 (DPI) 适配 ---
        dpi = get_screen_dpi()
        self._scale = dpi / 96.0
        try:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        self.model = DrillingModel()

        # 默认铺层: 总厚度 = 0.25 * 4 = 1.0 mm
        self.default_layup = [
            (45.0, 0.25),
            (90.0, 0.25),
            (-45.0, 0.25),
            (90.0, 0.25)
        ]

        self._setup_style()
        self._build_header()
        self.setup_ui()
        self._build_statusbar()
        self._setup_geometry()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ================================================================
    # 窗口尺寸：自适应屏幕可用区域，居中且不被任务栏遮挡
    # ================================================================
    def _setup_geometry(self):
        self.root.update_idletasks()
        work = get_work_area()
        if work:
            left, top, right, bottom = work
            avail_w = right - left
            avail_h = bottom - top
        else:
            left = top = 0
            avail_w = self.root.winfo_screenwidth()
            avail_h = self.root.winfo_screenheight()

        want_w = int(940 * self._scale)
        want_h = int(900 * self._scale)
        w = max(720, min(want_w, avail_w - 24))
        h = max(560, min(want_h, avail_h - 24))

        x = left + (avail_w - w) // 2
        y = top + (avail_h - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ================================================================
    # 样式设置
    # ================================================================
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TNotebook", background=COLOR_BG, borderwidth=0, tabmargins=[8, 6, 8, 0])
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 11, "bold"),
                        padding=[20, 11], background="#D5DBE3", foreground=COLOR_MUTED)
        style.map("TNotebook.Tab",
                  background=[("selected", COLOR_ACCENT)],
                  foreground=[("selected", "white")])

        style.configure("Card.TLabelframe", background=COLOR_CARD, borderwidth=1,
                        relief="solid", bordercolor="#DCE1E8")
        style.configure("Card.TLabelframe.Label", background=COLOR_CARD,
                        foreground=COLOR_PRIMARY, font=FONT_UI_B)

        style.configure("TEntry", fieldbackground="white", bordercolor="#CBD3DC",
                        lightcolor="white", darkcolor="white", padding=4)
        style.map("TEntry", bordercolor=[("focus", COLOR_ACCENT)])

        style.configure("Success.TButton", background=COLOR_SUCCESS, foreground="white",
                        font=("Microsoft YaHei UI", 12, "bold"), borderwidth=0,
                        focusthickness=0, padding=[12, 12])
        style.map("Success.TButton",
                  background=[("active", COLOR_SUCCESS_D), ("pressed", COLOR_SUCCESS_D)])

        style.configure("Danger.TButton", background=COLOR_DANGER, foreground="white",
                        font=("Microsoft YaHei UI", 12, "bold"), borderwidth=0,
                        focusthickness=0, padding=[12, 14])
        style.map("Danger.TButton",
                  background=[("active", COLOR_DANGER_D), ("pressed", COLOR_DANGER_D)])

        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="white",
                        font=FONT_UI_B, borderwidth=0, focusthickness=0, padding=[10, 6])
        style.map("Accent.TButton", background=[("active", "#2980B9")])

        style.configure("Muted.TButton", background="#E4E8ED", foreground=COLOR_PRIMARY,
                        font=FONT_UI, borderwidth=0, focusthickness=0, padding=[10, 6])
        style.map("Muted.TButton", background=[("active", "#D5DBE3")])

        style.configure("Treeview", background="white", fieldbackground="white",
                        font=FONT_UI, rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", background=COLOR_PRIMARY, foreground="white",
                        font=FONT_UI_B, relief="flat", padding=[6, 8])
        style.map("Treeview", background=[("selected", COLOR_ACCENT)],
                  foreground=[("selected", "white")])

    # ================================================================
    # 顶部标题栏
    # ================================================================
    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_PRIMARY, height=76)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Frame(header, bg=COLOR_ACCENT, width=6).pack(side="left", fill="y")

        txt = tk.Frame(header, bg=COLOR_PRIMARY)
        txt.pack(side="left", fill="both", expand=True, padx=18, pady=10)

        tk.Label(txt, text="复合材料增量钻孔仿真系统", bg=COLOR_PRIMARY, fg="white",
                 font=FONT_TITLE, anchor="w").pack(fill="x")
        tk.Label(txt, text="Composite Incremental Drilling Simulation  ·  层厚 0.25mm",
                 bg=COLOR_PRIMARY, fg="#AEB9C4", font=FONT_SUB, anchor="w").pack(fill="x")

        tk.Label(header, text="CFRP\nFEM", bg=COLOR_ACCENT, fg="white",
                 font=("Microsoft YaHei UI", 10, "bold"), justify="center",
                 width=8, padx=6, pady=6).pack(side="right", padx=16)

    # ================================================================
    # 底部状态栏
    # ================================================================
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg="#D5DBE3", height=30)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.status_dot = tk.Label(bar, text="●", bg="#D5DBE3", fg=COLOR_MUTED, font=("Arial", 12))
        self.status_dot.pack(side="left", padx=(12, 4))

        self.status_lbl = tk.Label(bar, text="就绪 · 请先在 [1.初始设置] 配置参数", bg="#D5DBE3",
                                   fg=COLOR_PRIMARY, font=FONT_SUB, anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)

        tk.Label(bar, text="CalculiX Solver Ready", bg="#D5DBE3", fg=COLOR_MUTED,
                 font=FONT_SUB).pack(side="right", padx=12)

    def _update_status(self, text, color=COLOR_PRIMARY):
        self.status_lbl.config(text=text, fg=color)
        self.status_dot.config(fg=color)

    def _make_card(self, parent, title):
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=14)

    # ================================================================
    # 主界面 (四个标签页 = 新工作流)
    # ================================================================
    def setup_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both", padx=14, pady=(6, 4))

        self.tab1 = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab1, text="  1. 初始设置  ")
        self.init_setup_ui(self.tab1)

        self.tab2 = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab2, text="  2. 钻孔深度与模型  ")
        self.init_load_mesh_ui(self.tab2)

        self.tab3 = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab3, text="  3. 载荷·网格·计算  ")
        self.init_view_ui(self.tab3)

        self.tab4 = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab4, text="  4. 计算日志  ")
        self.init_log_ui(self.tab4)

    # ================================================================
    # Tab 1: 初始设置 (材料 + 几何 + 铺层 + 挖多深 + 三维预览)
    # ================================================================
    def init_setup_ui(self, p):
        # --- 固定底部：挖多深 + 三维预览 ---
        bottom = tk.Frame(p, bg=COLOR_BG)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 10))

        f = tk.Frame(bottom, bg=COLOR_BG)
        f.pack(side="bottom", fill="x", pady=(8, 0), padx=10)
        tk.Label(f, text="钻孔步数 = 铺层数 × 每层单元数 (自动)", bg=COLOR_BG,
                 fg=COLOR_MUTED, font=FONT_UI_B).pack(side="left")

        ttk.Button(bottom, text="🧊  预览三维几何", style="Accent.TButton",
                   command=self.preview_3d).pack(side="bottom", fill="x", pady=(6, 0), padx=10)

        # --- 可滚动内容区 ---
        scroll = ScrollableFrame(p)
        scroll.pack(side="top", fill="both", expand=True)
        c = scroll.inner

        # --- 材料参数卡片 ---
        lf_mat = self._make_card(c, "材料参数 (Engineering Constants)")
        lf_mat.pack(fill="x", pady=(12, 6), padx=12)
        self.mat_ent = {}
        mat_defs = [("E1", 195000), ("E2", 8580), ("E3", 8580),
                    ("nu12", 0.3447), ("nu13", 0.3447), ("nu23", 0.4247),
                    ("G12", 4580), ("G13", 4580), ("G23", 3036)]
        for i, (k, v) in enumerate(mat_defs):
            row, col = i // 3, (i % 3)
            cell = tk.Frame(lf_mat, bg=COLOR_CARD)
            cell.grid(row=row, column=col, padx=12, pady=6, sticky="w")
            tk.Label(cell, text=k, bg=COLOR_CARD, fg=COLOR_ACCENT,
                     font=("Consolas", 11, "bold"), width=6, anchor="w").pack(side="left")
            e = ttk.Entry(cell, width=11, font=FONT_UI)
            e.insert(0, str(v))
            e.pack(side="left")
            self.mat_ent[k] = e

        # --- 几何参数卡片 ---
        lf = self._make_card(c, "几何参数 (mm) · 微纳尺度")
        lf.pack(fill="x", pady=6, padx=12)
        self.entries = {}
        defaults = [("L", "板长", 5.0), ("r1", "孔径", 0.05), ("r2", "渐变范围", 2.0),
                    ("sg_size", "应变片尺寸", 0.1), ("sg_pos", "应变片位置", 0.175),
                    ("n_arc", "环向节点数", 9), ("n_rad", "径向节点数", 9),
                    ("n_per_ply", "每层单元数", 2), ("n_sg", "应变片节点数", 3)]
        for i, (k, desc, v) in enumerate(defaults):
            row, col = i // 2, (i % 2) * 2
            cell = tk.Frame(lf, bg=COLOR_CARD)
            cell.grid(row=row, column=col, columnspan=2, sticky="w", padx=10, pady=6)
            tk.Label(cell, text=k, bg=COLOR_CARD, fg=COLOR_ACCENT,
                     font=("Consolas", 11, "bold"), width=9, anchor="w").pack(side="left")
            tk.Label(cell, text=desc, bg=COLOR_CARD, fg=COLOR_MUTED,
                     font=FONT_SUB, width=9, anchor="w").pack(side="left")
            e = ttk.Entry(cell, width=10, font=FONT_UI)
            e.insert(0, str(v))
            e.pack(side="left", padx=6)
            self.entries[k] = e

        # --- 铺层定义卡片 ---
        lf_ply = self._make_card(c, "铺层定义 (Layer Definition)")
        lf_ply.pack(fill="x", pady=6, padx=12)

        frame_ctrl = tk.Frame(lf_ply, bg=COLOR_CARD)
        frame_ctrl.pack(fill="x", side="bottom", pady=(6, 0))
        tk.Label(frame_ctrl, text="角度:", bg=COLOR_CARD, fg=COLOR_PRIMARY,
                 font=FONT_UI).pack(side="left", padx=(0, 4))
        self.ent_new_ang = ttk.Entry(frame_ctrl, width=7, font=FONT_UI)
        self.ent_new_ang.insert(0, "0.0")
        self.ent_new_ang.pack(side="left", padx=(0, 12))
        tk.Label(frame_ctrl, text="厚度:", bg=COLOR_CARD, fg=COLOR_PRIMARY,
                 font=FONT_UI).pack(side="left", padx=(0, 4))
        self.ent_new_thk = ttk.Entry(frame_ctrl, width=8, font=FONT_UI)
        self.ent_new_thk.insert(0, "0.25")
        self.ent_new_thk.pack(side="left", padx=(0, 12))
        ttk.Button(frame_ctrl, text="＋ 添加层", style="Accent.TButton",
                   command=self.add_layer).pack(side="left", padx=(0, 6))
        ttk.Button(frame_ctrl, text="－ 删除选中", style="Muted.TButton",
                   command=self.del_layer).pack(side="left")

        tree_wrap = tk.Frame(lf_ply, bg=COLOR_CARD)
        tree_wrap.pack(fill="x", side="top", expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=("I", "A", "T"), show="headings", height=6)
        self.tree.heading("I", text="层号")
        self.tree.heading("A", text="角度 (deg)")
        self.tree.heading("T", text="厚度 (mm)")
        self.tree.column("I", width=60, anchor="center")
        self.tree.column("A", width=120, anchor="center")
        self.tree.column("T", width=120, anchor="center")
        self.tree.tag_configure("odd", background="#F7F9FB")
        self.tree.tag_configure("even", background="white")
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="x", expand=True, pady=(4, 8))
        vsb.pack(side="left", fill="y", pady=(4, 8))
        for i, (a, t) in enumerate(self.default_layup):
            tag = "even" if i % 2 == 0 else "odd"
            self.tree.insert("", "end", values=(i + 1, a, t), tags=(tag,))

    # ================================================================
    # Tab 2: 钻孔深度与模型 (选深度 + 生成三维模型, 不划网格)
    # ================================================================
    def init_load_mesh_ui(self, p):
        # --- 固定底部：生成三维模型按钮 ---
        bottom = tk.Frame(p, bg=COLOR_BG)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 10))

        ttk.Button(bottom, text="🧊  生成三维模型", style="Accent.TButton",
                   command=self.generate_model).pack(side="bottom", fill="x", pady=(6, 0), padx=10)

        # --- 可滚动内容区 ---
        scroll = ScrollableFrame(p)
        scroll.pack(side="top", fill="both", expand=True)
        c = scroll.inner

        lf_step = self._make_card(c, "钻孔深度 (Top-Down) · 选择当前钻孔步")
        lf_step.pack(fill="x", pady=(12, 6), padx=12)

        tk.Label(lf_step, text="拖动滑块选择钻孔深度 (第几步):", bg=COLOR_CARD, fg=COLOR_PRIMARY,
                 font=FONT_UI_B).pack(anchor="w")

        self.step_var = tk.IntVar(value=0)
        slider_frame = tk.Frame(lf_step, bg=COLOR_CARD)
        slider_frame.pack(fill="x", padx=10, pady=8)
        self.slider = tk.Scale(slider_frame, from_=0, to=8, orient="horizontal",
                               variable=self.step_var, command=self.slide,
                               bg=COLOR_CARD, troughcolor="#DCE1E8", highlightthickness=0,
                               activebackground=COLOR_ACCENT, sliderrelief="flat",
                               font=FONT_UI, fg=COLOR_PRIMARY, length=520,
                               showvalue=True, bd=0)
        self.slider.pack(fill="x")

        self.lbl_step_desc = tk.Label(lf_step, text="当前状态: 原始模型 (无孔)",
                                      bg="#EAF3FB", fg=COLOR_ACCENT,
                                      font=("Microsoft YaHei UI", 11, "bold"),
                                      anchor="center", pady=10)
        self.lbl_step_desc.pack(fill="x", pady=(4, 8))

    # ================================================================
    # Tab 3: 载荷·网格·计算 (孔壁载荷 + 划分网格 + 查看 3D + 运行计算)
    # ================================================================
    def init_view_ui(self, p):
        # --- 固定底部：运行计算按钮 ---
        bottom = tk.Frame(p, bg=COLOR_BG)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        self.btn_run = ttk.Button(bottom, text="▶  运行当前步计算 (CalculiX)", style="Danger.TButton",
                                  command=self.run_calc_step)
        self.btn_run.pack(fill="x", padx=10)

        # --- 可滚动内容区 ---
        scroll = ScrollableFrame(p)
        scroll.pack(side="top", fill="both", expand=True)
        c = scroll.inner

        lf_load = self._make_card(c, "孔壁残余应力场 (MPa) · 施加于当前步孔壁")
        lf_load.pack(fill="x", pady=(12, 6), padx=12)
        self.l_ent = {}
        frame_input = tk.Frame(lf_load, bg=COLOR_CARD)
        frame_input.pack(pady=6)
        load_defaults = [("Sigma X", "σx", 50.0), ("Sigma Y", "σy", 0.0), ("Tau XY", "τxy", 0.0)]
        for i, (k, sym, v) in enumerate(load_defaults):
            cell = tk.Frame(frame_input, bg=COLOR_CARD)
            cell.grid(row=0, column=i, padx=12)
            tk.Label(cell, text=sym, bg=COLOR_CARD, fg=COLOR_ACCENT,
                     font=("Consolas", 13, "bold")).pack()
            tk.Label(cell, text=k, bg=COLOR_CARD, fg=COLOR_MUTED,
                     font=FONT_SUB).pack()
            e = ttk.Entry(cell, width=10, font=FONT_UI, justify="center")
            e.insert(0, str(v))
            e.pack(pady=(4, 0))
            self.l_ent[k] = e
        lbl_hint = tk.Label(lf_load,
                            text="注: 载荷自动施加在当前钻孔步的孔壁上 (第N步→第N层孔壁, 已钻层不加)",
                            bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_SUB)
        lbl_hint.pack(pady=(4, 0))

        # --- 划分网格卡片 (施加载荷之后才划分网格) ---
        lf_mesh = self._make_card(c, "划分网格 (施加载荷之后)")
        lf_mesh.pack(fill="x", pady=6, padx=12)
        self.btn_gen = ttk.Button(lf_mesh, text="⚙  划分网格", style="Success.TButton",
                                  command=self.start_gen_thread)
        self.btn_gen.pack(fill="x", padx=10, pady=(4, 6))
        self.lbl_status = tk.Label(lf_mesh, text="● 请先施加孔壁载荷，再划分网格", bg=COLOR_CARD,
                                   fg=COLOR_MUTED, font=FONT_UI)
        self.lbl_status.pack(pady=(0, 4))

        lf_view = self._make_card(c, "查看网格模型")
        lf_view.pack(fill="x", pady=6, padx=12)
        ttk.Button(lf_view, text="🔍  查看网格模型 (3D)", style="Accent.TButton",
                   command=self.view_mesh).pack(pady=4)
        tk.Label(lf_view, text="按当前钻孔深度显示，已钻层材料会被去除 (表面网格 2D face)",
                 bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_SUB).pack(pady=(0, 4))

    # ================================================================
    # Tab 4: 计算日志
    # ================================================================
    def init_log_ui(self, p):
        lf_log = self._make_card(p, "计算日志 (Calculation Log)")
        lf_log.pack(fill="both", expand=True, pady=12, padx=12)

        log_wrap = tk.Frame(lf_log, bg=COLOR_LOG_BG)
        log_wrap.pack(fill="both", expand=True)

        self.log_w = tk.Text(log_wrap, bg=COLOR_LOG_BG, fg=COLOR_LOG_FG, font=FONT_LOG,
                             relief="flat", padx=12, pady=10, insertbackground=COLOR_LOG_FG,
                             selectbackground=COLOR_ACCENT)
        log_sb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_w.yview)
        self.log_w.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")
        self.log_w.pack(side="left", fill="both", expand=True)

        self.log_w.tag_configure("header", foreground="#89B4FA")
        self.log_w.tag_configure("sep", foreground="#585B70")

    # ================================================================
    # 铺层增删
    # ================================================================
    def add_layer(self):
        try:
            ang = float(self.ent_new_ang.get())
            thk = float(self.ent_new_thk.get())
            self.tree.insert("", "end", values=("", ang, thk))
            self.refresh_layer_ids()
        except ValueError:
            messagebox.showerror("输入错误", "角度和厚度必须是数字。")

    def del_layer(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先在列表中选中要删除的层。")
            return
        for item in selected:
            self.tree.delete(item)
        self.refresh_layer_ids()

    def refresh_layer_ids(self):
        for i, item in enumerate(self.tree.get_children()):
            vals = self.tree.item(item)["values"]
            tag = "even" if i % 2 == 0 else "odd"
            self.tree.item(item, values=(i + 1, vals[1], vals[2]), tags=(tag,))

    # ================================================================
    # 三维几何预览 (设置阶段，不划网格)
    # ================================================================
    def preview_3d(self):
        try:
            l = []
            for item in self.tree.get_children():
                v = self.tree.item(item)["values"]
                l.append({"thickness": float(v[2]), "angle": float(v[1])})
            if not l:
                raise Exception("请至少添加一层铺层！")
            cfg = {k: float(e.get()) for k, e in self.entries.items()}
            n_per_ply = max(1, int(cfg.get("n_per_ply", 2)))
            num_slices = len(l) * n_per_ply
            cfg.update({"layup": l, "num_slices": num_slices, "n_per_ply": n_per_ply})
            ok, msg = self.model.preview_geometry(cfg)
            if ok:
                # 同步滑块范围到钻孔步数，并立即应用当前钻孔深度的材料移除
                n = num_slices
                self.slider.config(to=n)
                cur = self.step_var.get()
                if cur > n:
                    cur = n
                    self.step_var.set(cur)
                self.slide(cur)
                self.model.show_gui()
                self._start_gmsh_pump()
            else:
                messagebox.showerror("预览失败", msg)
        except Exception as err:
            messagebox.showerror("预览失败", str(err))

    # ================================================================
    # 生成三维模型 (Tab 2: 选择钻孔深度后, 仅几何不划网格)
    # ================================================================
    def generate_model(self):
        # 与几何预览一致：构建三维几何模型供查看，划分网格留到施加载荷之后
        self.preview_3d()

    # ================================================================
    # 网格生成
    # ================================================================
    def start_gen_thread(self):
        # 注意：gmsh 内部使用 signal，只能在主线程运行，不能用后台线程
        self.btn_gen.config(state="disabled", text="⏳ 正在生成网格...")
        self._update_status("正在生成网格，请稍候...", COLOR_ACCENT)
        self.root.update()
        self.gen()

    def gen(self):
        try:
            l = []
            for item in self.tree.get_children():
                v = self.tree.item(item)["values"]
                l.append({"thickness": float(v[2]), "angle": float(v[1])})

            if not l:
                raise Exception("请至少添加一层铺层！")

            cfg = {k: float(e.get()) for k, e in self.entries.items()}
            n_per_ply = max(1, int(cfg.get("n_per_ply", 2)))
            steps = len(l) * n_per_ply
            cfg.update({"layup": l, "num_slices": steps, "n_per_ply": n_per_ply})

            ok, msg = self.model.generate_mesh(cfg)
            self.root.after(0, lambda: self.finish_gen(ok, msg, steps))
        except Exception as err:
            err_msg = str(err)
            self.root.after(0, lambda: self.finish_gen(False, err_msg, 0))

    def finish_gen(self, ok, msg, slices):
        self.btn_gen.config(state="normal", text="⚙  划分网格")
        if ok:
            messagebox.showinfo("成功", msg)
            self.slider.config(to=slices)
            # 保留用户在 Tab2 已选的钻孔深度 (不重置为 0)，仅防止越界
            cur = self.step_var.get()
            if cur > slices:
                cur = slices
            self.slider.set(cur)
            self.lbl_status.config(text="● 网格就绪", fg=COLOR_SUCCESS)
            self._update_status(f"网格就绪 · 共 {slices} 个切片步 · 可查看模型或运行计算", COLOR_SUCCESS)
            self.slide(cur)
        else:
            messagebox.showerror("失败", msg)
            self.lbl_status.config(text="● 生成失败", fg=COLOR_DANGER)
            self._update_status("网格生成失败", COLOR_DANGER)

    # ================================================================
    # 步进 & 计算
    # ================================================================
    def slide(self, v):
        step = int(v)
        self.model.update_view_direct(step)
        if step == 0:
            txt = "当前状态: 原始模型 (无孔)"
        else:
            txt = f"当前状态: 第 {step} 步 (移除 Top 层, 载荷施加于第 {step} 层孔壁)"
        self.lbl_step_desc.config(text=txt)

    # ================================================================
    # gmsh 3D 窗口事件泵: 与 Tkinter 主循环交错(均在主线程), 使窗口打开时
    # 滑块仍可拖动, 拖动时能实时看到孔内小球随深度逐个消失。
    # ================================================================
    def _start_gmsh_pump(self):
        if getattr(self, "_gmsh_pump_id", None) is None:
            self._gmsh_pump_id = self.root.after(30, self._gmsh_pump_tick)

    def _gmsh_pump_tick(self):
        self._gmsh_pump_id = None
        try:
            self.model.pump_gui_events(0.001)
        except Exception:
            return
        # 持续泵送, 保持 3D 窗口响应
        self._gmsh_pump_id = self.root.after(30, self._gmsh_pump_tick)

    def view_mesh(self):
        # 查看网格模型：先按当前钻孔深度应用材料移除，再打开 3D 窗口
        if not (self.model.mesh_generated or self.model.geometry_built):
            messagebox.showwarning("提示", "请先生成三维模型或划分网格")
            return
        self.model.update_view_direct(self.step_var.get())
        self.model.show_gui()
        self._start_gmsh_pump()

    def run_calc_step(self):
        if not self.model.mesh_generated:
            messagebox.showwarning("警告", "请先在本页上方 [划分网格] 生成网格")
            return

        current_step = self.step_var.get()
        if current_step == 0:
            messagebox.showwarning("提示", "第 0 步为完整模型，无钻孔。请选择 1 或更大。")
            return

        solver = CalculiXSolver(self.model.data)
        mp = {k: float(e.get()) for k, e in self.mat_ent.items()}
        lp = {k: float(e.get()) for k, e in self.l_ent.items()}

        self.notebook.select(self.tab4)  # 自动切到日志页
        self.log_w.delete("1.0", "end")
        self.log(f"=== 开始计算 第 {current_step} 步 ===")
        self._update_status(f"正在计算第 {current_step} 步...", COLOR_DANGER)

        try:
            self.log(">>> 生成 INP...")
            inp_path, nodes = solver.write_inp(mp, lp, current_step)
            self.log(f"INP 生成成功. 路径: {inp_path}")
            threading.Thread(target=self._worker_ccx_only, args=(solver,), daemon=True).start()

        except Exception as e:
            self.log(f"INP生成失败: {str(e)}")
            self._update_status("INP 生成失败", COLOR_DANGER)
            messagebox.showerror("错误", f"INP 生成失败:\n{str(e)}")

    def _worker_ccx_only(self, solver):
        try:
            self.log(">>> 调用 CalculiX 求解器...")
            ok, msg = solver.run_calculix()
            self.log(f"求解器状态: {msg}")

            if ok:
                self.log(">>> 提取应变结果...")
                res, txt = solver.extract_3_nodes_data()
                if res:
                    self.log("-" * 30)
                    self.log(txt)
                    self.log("-" * 30)
                    self.root.after(0, lambda: self._update_status("计算完成 ✓", COLOR_SUCCESS))
                else:
                    self.log(f"提取失败: {txt}")
                    self.root.after(0, lambda: self._update_status("结果提取失败", COLOR_DANGER))
            else:
                self.log("求解器运行出错")
                self.root.after(0, lambda: self._update_status("求解器运行出错", COLOR_DANGER))
        except Exception as e:
            self.log(f"运行时错误: {str(e)}")
            self.root.after(0, lambda: self._update_status("运行时错误", COLOR_DANGER))

    def log(self, m):
        def _append():
            tag = None
            if m.startswith("==="):
                tag = "header"
            elif set(m) == {"-"} and len(m) > 3:
                tag = "sep"
            self.log_w.insert("end", m + "\n", tag)
            self.log_w.see("end")
        self.root.after(0, _append)

    def on_close(self):
        try:
            if getattr(self, "_gmsh_pump_id", None) is not None:
                self.root.after_cancel(self._gmsh_pump_id)
                self._gmsh_pump_id = None
        except Exception:
            pass
        try:
            self.model.finalize()
        except:
            pass
        self.root.destroy()


if __name__ == "__main__":
    enable_dpi_awareness()
    root = tk.Tk()
    app = DrillingGUI(root)
    root.mainloop()
