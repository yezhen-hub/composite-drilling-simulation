import math
import os
import sys
import gmsh


class DrillingModel:
    def __init__(self, model_data=None):
        self.is_initialized = False
        self.mesh_generated = False
        self.geometry_built = False
        self.gui_open = False
        self.data = model_data if model_data else {
            "r1": 1.0, "layup_data": [], "total_thickness": 0.0,
            "num_slices": 0, "n_per_ply": 1, "layer_zs": [0.0],
            "step_plugs": {},
            "step_walls": {},
            "strain_gauges": [], "plate_vols": [], "extra_frags": [],
            "ply_mid_hide": [],
            "gauge_vis_nodes": [],
            "layer_markers": {},
            "sg_pos": 4.5, "sg_size": 2.0
        }
        self.work_dir = os.path.dirname(os.path.abspath(__file__))

    def initialize(self):
        if not self.is_initialized:
            gmsh.initialize()
            self.is_initialized = True

    def finalize(self):
        if self.is_initialized:
            gmsh.finalize()
            self.is_initialized = False

    def generate_mesh(self, config):
        self.initialize()
        gmsh.clear()
        gmsh.model.add("Drilling_Structured_Incremental")

        # ================================================================
        # 参数解析
        # ================================================================
        L = config.get("L", 5.0)
        r1 = config["r1"]
        layup = config["layup"]
        n_arc = max(2, int(config.get("n_arc", 9)))
        n_rad = max(2, int(config.get("n_rad", 9)))
        n_per_ply = max(1, int(config.get("n_per_ply", 2)))
        n_sg = max(1, int(config.get("n_sg", 3)))  # 应变片每条边节点数

        if len(layup) < 1:
            return False, "错误：至少需要 1 个铺层"
        total_thick = sum([p["thickness"] for p in layup])
        if total_thick <= 0:
            return False, "错误：总厚度不能为 0"

        # 厚度分层 = 铺层数 × 每层单元数; 每个铺层再均分为 n_per_ply 个钻孔增量,
        # 层边界严格对齐铺层界面(各铺层厚度不同时也精确)。num_slices 由铺层驱动。
        num_slices = len(layup) * n_per_ply
        layer_zs = [0.0]
        for ply in layup:
            hh = ply["thickness"] / n_per_ply
            for _ in range(n_per_ply):
                layer_zs.append(layer_zs[-1] + hh)
        slice_height = total_thick / num_slices  # 平均层厚(仅作兼容/显示)

        if L <= 2 * r1:
            return False, f"几何错误：板长 L ({L}) 必须大于 2倍孔径 ({2 * r1})！"

        print(f"=== 结构化网格模式 (C3D8 六面体) ===")
        print(f"环向节点: {n_arc}, 径向节点: {n_rad}, 每层单元数: {n_per_ply}")
        print(f"铺层数: {len(layup)} → 钻孔步数(切片): {num_slices}")
        print(f"平均层厚: {slice_height:.4f}mm, 总厚: {total_thick:.4f}mm")

        self.data.update({
            "L": L, "r1": r1, "num_slices": num_slices, "layup_data": layup,
            "step_plugs": {}, "step_walls": {},
            "strain_gauges": [], "plate_vols": [], "extra_frags": [],
            "ply_mid_hide": [],
            "sg_pos": config.get("sg_pos", 0.175), "sg_size": config.get("sg_size", 0.1),
            "total_thickness": total_thick,
            "slice_height": slice_height,
            "layer_zs": layer_zs, "n_per_ply": n_per_ply,
            "n_arc": n_arc, "n_rad": n_rad, "n_sg": n_sg,
            "layer_markers": {}
        })

        try:
            # ============================================================
            # A. 2D 结构化面 (内置几何内核): 4 象限环区 + 1 孔盘
            #    正方形分 4 区, Transfinite + Recombine → 全四边形
            # ============================================================
            geo = gmsh.model.geo
            h = L / 2.0
            d = r1 / math.sqrt(2.0)

            c1 = geo.addPoint(-h, -h, 0)
            c2 = geo.addPoint(h, -h, 0)
            c3 = geo.addPoint(h, h, 0)
            c4 = geo.addPoint(-h, h, 0)
            p1 = geo.addPoint(d, -d, 0)
            p2 = geo.addPoint(d, d, 0)
            p3 = geo.addPoint(-d, d, 0)
            p4 = geo.addPoint(-d, -d, 0)
            pc = geo.addPoint(0, 0, 0)

            e1 = geo.addLine(c1, c2)
            e2 = geo.addLine(c2, c3)
            e3 = geo.addLine(c3, c4)
            e4 = geo.addLine(c4, c1)
            l1 = geo.addLine(c1, p4)
            l2 = geo.addLine(c2, p1)
            l3 = geo.addLine(c3, p2)
            l4 = geo.addLine(c4, p3)
            a1 = geo.addCircleArc(p1, pc, p2)
            a2 = geo.addCircleArc(p2, pc, p3)
            a3 = geo.addCircleArc(p3, pc, p4)
            a4 = geo.addCircleArc(p4, pc, p1)

            quad_defs = [
                ([e1, l2, -a4, -l1], [c1, c2, p1, p4]),
                ([e2, l3, -a1, -l2], [c2, c3, p2, p1]),
                ([e3, l4, -a2, -l3], [c3, c4, p3, p2]),
                ([e4, l1, -a3, -l4], [c4, c1, p4, p3]),
            ]
            quads = []
            quad_corners = []
            for loop, corners in quad_defs:
                cl = geo.addCurveLoop(loop)
                s = geo.addPlaneSurface([cl])
                quads.append(s)
                quad_corners.append(corners)

            cl_disk = geo.addCurveLoop([a1, a2, a3, a4])
            disk = geo.addPlaneSurface([cl_disk])

            geo.synchronize()

            # ============================================================
            # B. 逐切片拉伸成六面体 (每切片 1 层; plate 与 plug 同步分层, 共形)
            #    geo.extrude 对每个输入面返回 [顶面, 体积, 侧面×4] 共 6 项
            # ============================================================
            nq = len(quads)
            tops = quads + [disk]
            for i in range(num_slices):
                hgt = layer_zs[i + 1] - layer_zs[i]
                out = geo.extrude([(2, s) for s in tops], 0, 0, hgt,
                                  numElements=[1], recombine=True)
                new_tops = []
                zmid = (layer_zs[i] + layer_zs[i + 1]) / 2.0
                for k in range(len(tops)):
                    top_tag = out[k * 6][1]
                    vol_tag = out[k * 6 + 1][1]
                    new_tops.append(top_tag)
                    if k < nq:
                        self.data["plate_vols"].append(vol_tag)
                        self._apply_layup_color(vol_tag, zmid, layup)
                    else:
                        self.data["step_plugs"][i + 1] = [vol_tag]
                        gmsh.model.setColor([(3, vol_tag)], 100, 100, 100)
                tops = new_tops

            # 顶面三个应变片 (金色小方块, 仅可视化; 略微抬高避免与顶面 z-fighting)
            # 内置 geo 内核无 addRectangle/rotate, 直接用旋转后的角点构造平面
            sg_size = config.get("sg_size", 0.1)
            sg_pos = config.get("sg_pos", 0.175)
            gauge_z = total_thick + max(1e-3, sg_size * 0.03)
            half = sg_size / 2.0
            corners_local = [(sg_pos - half, -half), (sg_pos + half, -half),
                             (sg_pos + half, half), (sg_pos - half, half)]
            self.data["strain_gauges"] = []
            gauge_edge_lines = []
            for ang in (0, 45, 90):
                rad = math.radians(ang)
                ca, sa = math.cos(rad), math.sin(rad)
                pts = []
                for lx, ly in corners_local:
                    pts.append(geo.addPoint(lx * ca - ly * sa, lx * sa + ly * ca, gauge_z))
                lns = [geo.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
                gauge_edge_lines.extend(lns)
                cl = geo.addCurveLoop(lns)
                gs = geo.addPlaneSurface([cl])
                self.data["strain_gauges"].append(gs)

            geo.synchronize()
            for gs in self.data["strain_gauges"]:
                gmsh.model.setColor([(2, gs)], 255, 215, 0)
            # 网格模式不建进度小球(体积在网格视图不渲染); 进度由单元移除体现
            self.data["layer_markers"] = {}
            self.data["marker_spheres"] = {}

            # 拉伸+synchronize 会清除网格约束, 必须在最终同步后对原始底面
            # 重新施加 Transfinite/Recombine, 否则 generate(3) 会退化为非结构棱柱
            for c in (e1, e2, e3, e4, a1, a2, a3, a4):
                gmsh.model.mesh.setTransfiniteCurve(c, n_arc)
            for c in (l1, l2, l3, l4):
                gmsh.model.mesh.setTransfiniteCurve(c, n_rad)
            for s in quads + [disk]:
                gmsh.model.mesh.setTransfiniteSurface(s)
                gmsh.model.mesh.setRecombine(2, s)

            # 应变片单独的结构化网格密度 (n_sg = 每条边节点数; <2 时用默认网格)
            if n_sg >= 2:
                for c in gauge_edge_lines:
                    gmsh.model.mesh.setTransfiniteCurve(c, n_sg)
                for gs in self.data["strain_gauges"]:
                    gmsh.model.mesh.setTransfiniteSurface(gs)
                    gmsh.model.mesh.setRecombine(2, gs)

            # ============================================================
            # C. 孔壁面 (r≈r1 的竖直侧面) 染红
            # ============================================================
            for dim, tag in gmsh.model.getEntities(2):
                xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(2, tag)
                radmax = max(abs(xmin), abs(ymin), abs(xmax), abs(ymax))
                if abs(radmax - r1) < 1e-4 and (zmax - zmin) > 1e-6 and zmin >= -1e-9:
                    zmid = (zmin + zmax) / 2.0
                    sid = self._layer_index_of_z(zmid, layer_zs, num_slices)
                    self.data["step_walls"].setdefault(sid, []).append(tag)
                    gmsh.model.setColor([(2, tag)], 255, 0, 0)

            if not self.data["plate_vols"]:
                return False, "严重错误：未能生成板材实体"

            print(f"  板材体积: {len(self.data['plate_vols'])}")
            print(f"  塞子体积: {sum(len(v) for v in self.data['step_plugs'].values())}")

            gmsh.model.addPhysicalGroup(3, self.data["plate_vols"], name="E_Plate")
            for sid, tags in self.data["step_plugs"].items():
                gmsh.model.addPhysicalGroup(3, tags, name=f"Plug_Layer_{sid}")

            # ============================================================
            # D. 生成结构化六面体网格
            # ============================================================
            print("正在生成结构化六面体网格 (Transfinite + Recombine + Extrude)...")
            gmsh.model.mesh.generate(3)

            # 记录应变片可视化曲面的节点(浮在顶面上方), 供 jisuan 提取应变时排除,
            # 避免把金色标记节点误当作应变提取节点。按 z 高于顶面判定最稳妥。
            gv = set()
            all_t, all_c, _ = gmsh.model.mesh.getNodes()
            for i, t in enumerate(all_t):
                if all_c[3 * i + 2] > total_thick + 1e-6:
                    gv.add(int(t))
            self.data["gauge_vis_nodes"] = gv

            nodes_test = gmsh.model.mesh.getNodes()
            if len(nodes_test[0]) == 0:
                return False, "网格生成失败：节点数为 0"

            ele_cnt = 0
            for dim, tag in gmsh.model.getEntities(3):
                etypes, etags, _ = gmsh.model.mesh.getElements(3, tag)
                for k, et in enumerate(etypes):
                    if et == 5:
                        ele_cnt += len(etags[k])

            print(f"  节点数: {len(nodes_test[0])}  六面体单元数: {ele_cnt}")

            self.mesh_generated = True
            return (
                True,
                f"结构化网格生成完毕 (C3D8 六面体)\n"
                f"板材体积: {len(self.data['plate_vols'])}\n"
                f"节点数: {len(nodes_test[0])}\n"
                f"六面体单元数: {ele_cnt}\n"
                f"密度: 环向 {n_arc} × 径向 {n_rad} 节点, {num_slices} 层\n"
                f"应变片网格: 每边 {n_sg} 节点 ({(n_sg - 1) ** 2 if n_sg >= 2 else '默认'} 四边形/片)"
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"建模错误: {str(e)}"

    def _apply_layup_color(self, tag, z, layup, alpha=255):
        cum = 0
        for ply in layup:
            if cum - 0.01 <= z <= cum + ply["thickness"] + 0.01:
                c = ply.get("_color_rgb", (200, 200, 200))
                gmsh.model.setColor([(3, tag)], *c, alpha)
                return
            cum += ply["thickness"]

    def _layer_index_of_z(self, z, layer_zs, num_slices):
        """返回 z 所在钻孔增量(层)的 1-based 序号; 兼容非均匀层厚。"""
        if not layer_zs or len(layer_zs) < 2:
            return max(1, min(num_slices, 1))
        import bisect
        idx = bisect.bisect_right(layer_zs, z + 1e-9)
        return max(1, min(idx, num_slices))

    def _classify_volume(self, tag, r1, layer_zs, num_slices):
        """判断体积是否为孔内塞子(plug)，返回 (is_plug, slice_id)。

        基于径向包围盒分类，不受质量影响：避免相邻塞子被 OCC fragment
        合并后质量翻倍而误判为板材（导致该段材料永远无法被去除、残留在两层之间）。
        slice_id 用塞子 z 中点定位所属层：每个塞子恰好是一个钻孔层，中点稳定落
        在层内部，避免因顶面恰在层边界而产生的差一错位。
        """
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.occ.getBoundingBox(3, tag)
        radmax = max(abs(xmin), abs(ymin), abs(xmax), abs(ymax))
        # 孔内判定用宽松阈值(3*r1)：板材径向≈L/2(远大于孔)，而孔壁/铺层交界面
        # 的小碎片径向≈r1 量级。放宽可把这些交界面碎片也归为塞子(去除时一并
        # 移除、染灰色)，又不会误吞径向很大的板材。
        if radmax > r1 * 3.0:
            return False, None
        # 用塞子 z 中点定位所属层(稳定落在层内部); 若用顶面 zmax 恰好落在层边界
        # layer_zs[j+1] 上, bisect_right 会把它推到上一层 → 整体错位一层。
        zmid = (zmin + zmax) / 2.0
        slice_id = self._layer_index_of_z(zmid, layer_zs, num_slices)
        return True, slice_id

    def _split_extra_plugs(self):
        """每个切片只保留质量最大的塞子为主塞子；同一切片内多出来的其余孔内
        小碎片(OCC fragment 在铺层交界面产生的退化小块)记入 extra_frags，
        在 update_view_direct 中始终隐藏，避免在灰色塞子堆上多出异色小球。"""
        extra = []
        for sid in list(self.data["step_plugs"].keys()):
            tags = self.data["step_plugs"][sid]
            if len(tags) <= 1:
                continue
            masses = sorted(((gmsh.model.occ.getMass(3, t), t) for t in tags),
                            reverse=True)
            self.data["step_plugs"][sid] = [masses[0][1]]
            for _, t in masses[1:]:
                extra.append(t)
        self.data["extra_frags"] = extra

    def _collect_ply_mid_artifacts(self, layup, r1):
        """收集位于每个铺层厚度中点(层中间面)、孔内的内部界面(圆盘面)及其
        边界圆。这些是 fragment 在层中间产生的“虚线面”，小球就在其中心，
        在 update_view_direct 中始终隐藏。"""
        mids = []
        cum = 0.0
        for ply in layup:
            mids.append(cum + ply["thickness"] / 2.0)
            cum += ply["thickness"]
        tags = []
        for dim in (1, 2):
            for _, tag in gmsh.model.getEntities(dim):
                c = gmsh.model.occ.getCenterOfMass(dim, tag)
                if abs(c[0]) <= r1 * 1.2 and abs(c[1]) <= r1 * 1.2:
                    for mz in mids:
                        if abs(c[2] - mz) < 1e-6:
                            tags.append((dim, tag))
                            break
        self.data["ply_mid_hide"] = tags

    def update_view_direct(self, step_val):
        if not (self.mesh_generated or self.geometry_built):
            return
        total = self.data.get("num_slices", 0)
        if total <= 0:
            return
        r1 = self.data.get("r1", 0.0)
        sh = self.data.get("slice_height", 0.0)
        layer_zs = self.data.get("layer_zs") or []
        max_keep = total - step_val
        # 孔内判定阈值与 _classify_volume 一致(3*r1)：交界面孔壁碎片也算孔内
        tol = r1 * 2.0
        # 已钻区下边界：顶面 z 高于此值的孔内材料视为已移除 (按层边界, 兼容非均匀层厚)
        if len(layer_zs) >= max_keep + 1 and max_keep >= 0:
            z_cut = layer_zs[max_keep] + sh * 1e-3
        else:
            z_cut = max_keep * sh + sh * 1e-3
        # 纯几何判定每个体积的可见性，不依赖预分类(step_plugs/plate_vols)：
        # 只要体积径向在孔内(r<=r1)且顶面落在已钻区，就强制隐藏。
        # 这样即使某个孔内小块被误分到“板材”桶，也不会永远显示而残留。
        hide_tags, show_inhole, plates = [], [], []
        marker_sph = set((self.data.get("marker_spheres", {}) or {}).values())
        for dim, tag in gmsh.model.getEntities(3):
            if tag in marker_sph:
                continue          # 进度小球单独按深度显隐, 不参与孔内/板材判定
            bb = gmsh.model.getBoundingBox(3, tag)
            radmax = max(abs(bb[0]), abs(bb[1]), abs(bb[3]), abs(bb[4]))
            if radmax <= r1 + tol:            # 孔内体积(塞子/碎片)
                if bb[5] > z_cut:
                    hide_tags.append(tag)     # 顶面在已钻区 → 移除
                else:
                    show_inhole.append(tag)   # 保留区 → 显示
            else:
                plates.append(tag)            # 板材
        # 1. 先隐藏已钻的孔内体积 (recursive=True 连带隐藏其表面网格，
        #    否则划网格后被钻掉层的 2D face 仍会渲染)
        for t in hide_tags:
            gmsh.model.setVisibility([(3, t)], 0, recursive=True)
        # 2. 显示保留区的孔内体积
        for t in show_inhole:
            gmsh.model.setVisibility([(3, t)], 1, recursive=True)
        # 3. 板材最后显示，recursive=True 重新显示板材边界面(含共享孔壁)，
        #    使已钻层的孔壁仍可见 → 形成“有壁的真实孔洞”。
        #    (塞子的顶/底盖面不属于板材边界，保持隐藏 → 顶部露出开口)
        for t in plates:
            gmsh.model.setVisibility([(3, t)], 1, recursive=True)
        for t in self.data["strain_gauges"]:
            gmsh.model.setVisibility([(2, t)], 1, recursive=True)
        # 交界面冗余小碎片始终隐藏 (不随钻孔显隐)
        for t in self.data.get("extra_frags", []):
            gmsh.model.setVisibility([(3, t)], 0, recursive=True)
        # 隐藏所有几何顶点(接缝点/角点), 避免被渲染成杂散小圆球
        for _, t in gmsh.model.getEntities(0):
            gmsh.model.setVisibility([(0, t)], 0)
        # 钻孔进度小圆球(独立球体): 已钻层(slice_id > max_keep)隐藏, 未钻层显示
        # → 随钻孔深度自顶向下逐个消失
        for sid, sp in (self.data.get("marker_spheres", {}) or {}).items():
            gmsh.model.setVisibility([(3, sp)], 1 if sid <= max_keep else 0)
        # 隐藏铺层中间的内部界面(虚线面)及其边界圆
        for dim, t in self.data.get("ply_mid_hide", []):
            gmsh.model.setVisibility([(dim, t)], 0)
        gmsh.graphics.draw()

    def preview_geometry(self, config):
        """构建几何 (不划分网格)，含分层塞子与应变片，
        用于设置阶段按钻孔深度预览三维模型 (Top-Down 逐层移除)。"""
        self.initialize()
        gmsh.clear()
        gmsh.model.add("Drilling_Preview")

        L = config.get("L", 5.0)
        r1 = config["r1"]
        sg_size, sg_pos = config.get("sg_size", 0.1), config.get("sg_pos", 0.175)
        layup = config["layup"]
        n_per_ply = max(1, int(config.get("n_per_ply", 2)))
        total_thick = sum([p["thickness"] for p in layup])
        if total_thick <= 0:
            return False, "错误：总厚度不能为 0"
        if L <= 2 * r1:
            return False, f"几何错误：板长 L ({L}) 必须大于 2倍孔径 ({2 * r1})！"
        if len(layup) < 1:
            return False, "错误：至少需要 1 个铺层"
        # 厚度分层 = 铺层数 × 每层单元数, 层边界对齐铺层界面
        num_slices = len(layup) * n_per_ply
        layer_zs = [0.0]
        for ply in layup:
            hh = ply["thickness"] / n_per_ply
            for _ in range(n_per_ply):
                layer_zs.append(layer_zs[-1] + hh)
        slice_height = total_thick / num_slices

        self.data.update({
            "L": L, "r1": r1, "num_slices": num_slices, "layup_data": layup,
            "step_plugs": {}, "step_walls": {},
            "strain_gauges": [], "plate_vols": [], "extra_frags": [],
            "ply_mid_hide": [],
            "sg_pos": sg_pos, "sg_size": sg_size,
            "total_thickness": total_thick,
            "slice_height": slice_height,
            "layer_zs": layer_zs, "n_per_ply": n_per_ply,
            "layer_markers": {}
        })

        try:
            gmsh.model.occ.synchronize()

            # --- 1. 板料按钻孔切片逐层挖孔 ---
            # 每个铺层再细分为 n_per_ply 片(与 layer_zs 对齐), 使预览外表面能看到
            # 每层被分成 n_per_ply 段的分层线; 颜色仍取切片所属铺层。
            layer_tools = []
            for i in range(num_slices):
                z0 = layer_zs[i]
                hgt = layer_zs[i + 1] - layer_zs[i]
                ply = layup[min(len(layup) - 1, i // n_per_ply)]
                box = gmsh.model.occ.addBox(-L / 2, -L / 2, z0, L, L, hgt)
                cyl = gmsh.model.occ.addCylinder(0, 0, z0, 0, 0, hgt, r1)
                cut_res = gmsh.model.occ.cut([(3, box)], [(3, cyl)])
                vol = cut_res[0][0]
                c = (200, 200, 200)
                if abs(ply["angle"]) == 45:
                    c = (255, 180, 180)
                elif abs(ply["angle"]) == 90:
                    c = (180, 180, 255)
                elif abs(ply["angle"]) == 0:
                    c = (180, 255, 180)
                ply["_color_rgb"] = c
                layer_tools.append((3, vol[1]))

            # --- 2. 钻孔塞子圆柱 (逐层, 用于 Top-Down 移除; 层边界对齐铺层) ---
            drill_tools = []
            for i in range(num_slices):
                hgt = layer_zs[i + 1] - layer_zs[i]
                cyl = gmsh.model.occ.addCylinder(0, 0, layer_zs[i], 0, 0, hgt, r1)
                drill_tools.append((3, cyl))

            # --- 3. 应变片 (顶面印记) ---
            sg_tools = []
            top_z = total_thick
            for ang in [0, 45, 90]:
                rad = math.radians(ang)
                s = gmsh.model.occ.addRectangle(
                    sg_pos - sg_size / 2, -sg_size / 2, top_z, sg_size, sg_size
                )
                gmsh.model.occ.rotate([(2, s)], 0, 0, 0, 0, 0, 1, rad)
                sg_tools.append((2, s))

            # --- 4. 碎片化 ---
            gmsh.model.occ.fragment(layer_tools, drill_tools + sg_tools)
            gmsh.model.occ.synchronize()

            # --- 5. 实体分类 (塞子 vs 板材) ---
            for dim, tag in gmsh.model.getEntities(3):
                is_plug, slice_id = self._classify_volume(tag, r1, layer_zs, num_slices)
                if is_plug:
                    self.data["step_plugs"].setdefault(slice_id, []).append(tag)
                    # 塞子半透明(a=80): 让孔内的进度小球能透过来被看见
                    gmsh.model.setColor([(3, tag)], 100, 100, 100, 80)
                else:
                    cm = gmsh.model.occ.getCenterOfMass(dim, tag)
                    self.data["plate_vols"].append(tag)
                    # 板材半透明(a=90): 让孔内的进度小球能透过板壁被看见,
                    # 从而在拖动深度滑块时观察到小球自顶向下逐个消失。
                    self._apply_layup_color(tag, cm[2], layup, alpha=90)

            # 剔除同切片多出的交界面小碎片 (始终隐藏)
            self._split_extra_plugs()
            # 收集铺层中间虚线面 (始终隐藏)
            self._collect_ply_mid_artifacts(layup, r1)

            # --- 6. 面分类 (应变片) ---
            for dim, tag in gmsh.model.getEntities(2):
                cm = gmsh.model.occ.getCenterOfMass(dim, tag)
                if abs(cm[2] - total_thick) < 1e-4:
                    s_mass = gmsh.model.occ.getMass(2, tag)
                    if s_mass < sg_size ** 2 * 1.5:
                        self.data["strain_gauges"].append(tag)
                        gmsh.model.setColor([(2, tag)], 255, 215, 0)

            # --- 7. 钻孔进度小圆球: 孔内每层中心一个独立球体(体积), 随深度自顶向下消失。
            # 用体积而非几何点(可见性确定生效); 塞子设为半透明, 使孔内小球可见。
            self.data["layer_markers"] = {}
            self.data["marker_spheres"] = {}
            # 小球放大到几乎填满孔(0.9*r1), 配合半透明板材/塞子, 保证肉眼可辨
            rad = max(r1 * 0.9, 1e-3)
            for i in range(num_slices):
                z = (layer_zs[i] + layer_zs[i + 1]) / 2.0
                self.data["marker_spheres"][i + 1] = gmsh.model.occ.addSphere(0, 0, z, rad)
            gmsh.model.occ.synchronize()
            for sp in self.data["marker_spheres"].values():
                gmsh.model.setColor([(3, sp)], 0, 220, 120, 255)

            self.geometry_built = True
            return True, "三维模型已生成 (拖动深度滑块可逐层移除材料)"
        except Exception as e:
            return False, f"预览错误: {str(e)}"

    def show_gui(self):
        # 预览 (仅几何) 或网格生成后都可查看
        if not self.is_initialized:
            return
        # 关闭几何顶点渲染: 接缝点会被画成杂散小圆球; 进度小球已改为独立球体体积
        gmsh.option.setNumber("Geometry.Points", 0)
        if self.mesh_generated:
            # 网格模式：以表面网格 (2D face) 显示，关闭几何着色避免遮挡网格
            gmsh.option.setNumber("Geometry.Surfaces", 0)
            gmsh.option.setNumber("Geometry.Volumes", 0)
        else:
            # 几何预览模式：开启面/体着色 + 光照 (类似 Abaqus)，
            # gmsh 默认 Surfaces/Volumes=0 只显示线条，开启后被隐藏的
            # 材料会真正消失，直观看到逐层去除效果。
            gmsh.option.setNumber("Geometry.Surfaces", 1)
            gmsh.option.setNumber("Geometry.Volumes", 1)
            gmsh.option.setNumber("Geometry.Light", 1)
            gmsh.option.setNumber("Geometry.LightTwoSide", 1)
        gmsh.graphics.draw()
        # 非阻塞创建 3D 窗口: 不用 fltk.run()(会阻塞 Tkinter 主循环, 导致窗口打开时
        # 滑块拖不动、看不到小球实时消失)。改由 GUI 侧用 root.after 定期调用
        # pump_gui_events() 与本主循环交错泵送 gmsh 事件。
        gmsh.fltk.initialize()
        self.gui_open = True
        gmsh.graphics.draw()

    def pump_gui_events(self, dt=0.001):
        """非阻塞地处理一次 gmsh GUI 事件(至多 dt 秒后返回)。
        由 Tkinter 的 root.after 循环反复调用, 使 3D 窗口保持响应,
        同时拖动滑块时能实时看到孔内小球逐个消失。只能在主线程调用。"""
        if self.is_initialized and self.gui_open:
            try:
                gmsh.fltk.wait(dt)
            except Exception:
                pass
