import os
import subprocess
import numpy as np
import gmsh
import math


class CalculiXSolver:
    def __init__(self, model_data):
        self.data = model_data
        self.work_dir = os.path.dirname(os.path.abspath(__file__))
        # =======================================================
        # 请确认指向你的 ccx.exe
        self.full_ccx_path = os.path.join(self.work_dir, "ccx.exe")
        # =======================================================
        self.gauge_sets = {}
        self.gauge_elements = {}
        self.job_name = ""
        self.ccx_log = ""

    def _get_all_nodes(self):
        try:
            tags, coords, _ = gmsh.model.mesh.getNodes()
            if len(tags) == 0:
                return np.array([]), np.array([])
            return np.array(tags), np.array(coords).reshape(-1, 3)
        except:
            return np.array([]), np.array([])

    def _get_nodes_in_box_manual(self, xmin, ymin, zmin, xmax, ymax, zmax):
        tags, coords = self._get_all_nodes()
        if len(tags) == 0:
            return np.array([]), np.array([])
        mask = (
                (coords[:, 0] >= xmin) & (coords[:, 0] <= xmax) &
                (coords[:, 1] >= ymin) & (coords[:, 1] <= ymax) &
                (coords[:, 2] >= zmin) & (coords[:, 2] <= zmax)
        )
        return tags[mask], coords[mask]

    def _find_load_nodes_geometrically(self, current_step):
        """
        寻找当前步需要施加载荷的节点 (Top-Down)。
        """
        r1 = self.data.get("r1", 1.0)
        slice_h = self.data.get("slice_height", 0.0)
        total_th = self.data.get("total_thickness", 0.0)
        num_slices = self.data.get("num_slices", 1)
        layer_zs = self.data.get("layer_zs") or []

        # 自顶向下: 第 current_step 步移除自顶数第 current_step 层
        # (底部索引 j = num_slices - current_step), 按真实层边界取 z 范围
        if len(layer_zs) >= num_slices + 1 and num_slices >= 1:
            j = max(0, min(num_slices - current_step, num_slices - 1))
            z_bottom_limit = layer_zs[j]
            z_top_limit = layer_zs[j + 1]
        else:
            z_top_limit = total_th - (current_step - 1) * slice_h
            z_bottom_limit = total_th - current_step * slice_h

        # 极微小尺度的容差
        tol_z = 1e-4
        tol_r = 1e-4

        tags, coords = self._get_all_nodes()
        load_nodes = set()
        load_coords_map = {}

        if len(tags) == 0:
            return set(), {}

        # 调试输出
        # print(f"Debug: Step {current_step} Z=[{z_bottom_limit:.4f}, {z_top_limit:.4f}]")

        for i, tag in enumerate(tags):
            x, y, z = coords[i]
            if not (z_bottom_limit - tol_z <= z <= z_top_limit + tol_z):
                continue

            r_node = math.sqrt(x ** 2 + y ** 2)
            if abs(r_node - r1) < tol_r:
                nid = int(tag)
                load_nodes.add(nid)
                load_coords_map[nid] = (x, y)

        return load_nodes, load_coords_map

    def _find_strain_gauge_sets(self):
        """记录共点应变花 (rosette) 测量区。

        真实应变花三栅 (0/45/90) 共点, 故不在孔周 0/45/90 三处取点。改为以
        (sg_pos, 0) 顶面为中心、sg_size 为边长的方形栅格区; 提取时选取水平投影
        与该区重叠的顶层单元, 对其应变做面积平均后按 0/45/90 合成 ε1/ε2/ε3。
        用单元包围盒重叠(而非节点落在区内)判定, 对粗网格鲁棒且位置准确。
        """
        sg_pos = self.data.get("sg_pos", 4.5)
        sg_size = self.data.get("sg_size", 2.0)
        half = sg_size / 2.0
        self.gauge_box = (sg_pos - half, sg_pos + half, -half, half)
        self.gauge_z_top = self.data.get("total_thickness", 0.0)
        self.gauge_sets = {}
        self.gauge_node_ids = set()

    def _elem_in_gauge(self, n_ids, node_coord, gz_top, ztol=1e-3):
        """判断单元是否为顶层且水平投影与应变花栅格区重叠。"""
        gx0, gx1, gy0, gy1 = self.gauge_box
        xs, ys, zs = [], [], []
        for n in n_ids:
            c = node_coord.get(n)
            if c is None:
                return False
            xs.append(c[0]); ys.append(c[1]); zs.append(c[2])
        if not xs:
            return False
        if abs(max(zs) - gz_top) > ztol:      # 仅顶层单元
            return False
        ex0, ex1, ey0, ey1 = min(xs), max(xs), min(ys), max(ys)
        return ex0 <= gx1 and ex1 >= gx0 and ey0 <= gy1 and ey1 >= gy0

    def write_inp(self, mat_props, load_props, current_step):
        self.job_name = f"Step_{current_step}"
        # 记录本次工况, 供 extract 按 youhua.py 公式反推标定系数 C
        self.last_load_props = dict(load_props)
        self.last_mat_props = dict(mat_props)
        inp_path = os.path.join(self.work_dir, f"{self.job_name}.inp")
        if not gmsh.is_initialized():
            gmsh.initialize()

        # === 1. 单元移除逻辑 ===
        total_slices = self.data.get("num_slices", 1)
        max_keep_layer = total_slices - current_step

        vols_to_write = set(self.data.get("plate_vols", []))
        step_plugs = self.data.get("step_plugs", {})

        for slice_id, tags in step_plugs.items():
            if slice_id <= max_keep_layer:
                for t in tags:
                    vols_to_write.add(t)

        if not vols_to_write:
            raise Exception("错误：无实体单元（所有层都被钻空）。")

        load_nodes, load_coords_map = self._find_load_nodes_geometrically(current_step)
        self._find_strain_gauge_sets()
        self.gauge_elements = {"SG_0": None, "SG_45": None, "SG_90": None}
        self.gauge_elem_ids = set()

        with open(inp_path, "w") as f:
            f.write(f"*HEADING\nIncremental Drilling Step {current_step}\n")
            f.write("*PREPRINT, ECHO=YES, MODEL=YES\n")
            tags, coords = self._get_all_nodes()
            node_coord = {int(tags[i]): coords[i] for i in range(len(tags))}
            gz_top = self.gauge_z_top
            f.write("*NODE\n")
            for i in range(len(tags)):
                f.write(f"{int(tags[i])}, {coords[i][0]:.6f}, {coords[i][1]:.6f}, {coords[i][2]:.6f}\n")

            all_vols = gmsh.model.getEntities(3)
            ele_cnt = 0
            lines_by_type = {5: [], 4: []}
            for dim, tag in all_vols:
                if tag in vols_to_write:
                    etypes, etags, enodes = gmsh.model.mesh.getElements(3, tag)
                    for k, etype in enumerate(etypes):
                        if etype not in (4, 5):
                            continue
                        npe = 4 if etype == 4 else 8
                        for j, ele_tag in enumerate(etags[k]):
                            idx = npe * j
                            n_ids = [int(x) for x in enodes[k][idx:idx + npe]]
                            ele_id = int(ele_tag)
                            lines_by_type[etype].append(
                                f"{ele_id}, " + ", ".join(str(x) for x in n_ids) + "\n")
                            ele_cnt += 1
                            if self._elem_in_gauge(n_ids, node_coord, gz_top):
                                self.gauge_elem_ids.add(ele_id)

            # 兼容字段: 三个方向读数同源(共点张量), 指向同一代表单元
            if self.gauge_elem_ids:
                rep = min(self.gauge_elem_ids)
                for name in self.gauge_elements:
                    self.gauge_elements[name] = rep

            if lines_by_type[5]:
                f.write("*ELEMENT, TYPE=C3D8, ELSET=E_ALL\n")
                f.writelines(lines_by_type[5])
            if lines_by_type[4]:
                f.write("*ELEMENT, TYPE=C3D4, ELSET=E_ALL\n")
                f.writelines(lines_by_type[4])

            f.write("*NSET, NSET=N_ALL\n")
            for i in range(len(tags)):
                f.write(f"{int(tags[i])},\n")

            # === 2. 修正材料精度 ===
            f.write("*MATERIAL, NAME=CFRP\n*ELASTIC, TYPE=ENGINEERING CONSTANTS\n")
            vals = [mat_props[k] for k in ['E1', 'E2', 'E3', 'nu12', 'nu13', 'nu23', 'G12', 'G13']]
            line1 = ", ".join([f"{v:.6f}" for v in vals])
            f.write(f"{line1}\n")
            f.write(f"{mat_props['G23']:.6f}\n")
            f.write("*SOLID SECTION, ELSET=E_ALL, MATERIAL=CFRP\n")

            # === 3. 修正底面固定容差 ===
            f.write("*NSET, NSET=BottomNodes\n")
            L = self.data.get("L", 10.0)
            if len(coords) > 0:
                min_z_model = np.min(coords[:, 2])
                # 容差改为 1e-4，防止固定整个薄板
                b_tags, _ = self._get_nodes_in_box_manual(-L, -L, min_z_model - 1e-4, L, L, min_z_model + 1e-4)
                for nid in b_tags:
                    f.write(f"{int(nid)},\n")
            f.write("*BOUNDARY\nBottomNodes, 1, 3\n")

            f.write("\n*STEP\n*STATIC\n0.1, 1.0\n*CLOAD\n")
            sigma_x = load_props.get("Sigma X", 0.0)
            sigma_y = load_props.get("Sigma Y", 0.0)
            tau_xy = load_props.get("Tau XY", 0.0)

            r1 = self.data.get("r1", 0.1)
            slice_h = self.data.get("slice_height", 0.25)
            num_slices = self.data.get("num_slices", 1)
            layer_zs = self.data.get("layer_zs") or []
            # 当前钻孔增量的真实层厚(用于孔壁载荷面积), 兼容非均匀层厚
            if len(layer_zs) >= num_slices + 1 and num_slices >= 1:
                j = max(0, min(num_slices - current_step, num_slices - 1))
                inc_h = layer_zs[j + 1] - layer_zs[j]
            else:
                inc_h = slice_h

            num_nodes = len(load_nodes)

            if num_nodes > 0:
                total_area = 2 * math.pi * r1 * inc_h
                dA = total_area / num_nodes

                print(f"--- Step {current_step} 载荷 ---")
                print(f"节点数: {num_nodes}, 面积: {total_area:.4e}")

                for nid in load_nodes:
                    x, y = load_coords_map[nid]
                    theta = math.atan2(y, x)
                    cos_t = math.cos(theta)
                    sin_t = math.sin(theta)
                    Tx = sigma_x * cos_t + tau_xy * sin_t
                    Ty = tau_xy * cos_t + sigma_y * sin_t
                    Fx = -Tx * dA
                    Fy = -Ty * dA
                    if abs(Fx) > 1e-12:
                        f.write(f"{nid}, 1, {Fx:.9f}\n")
                    if abs(Fy) > 1e-12:
                        f.write(f"{nid}, 2, {Fy:.9f}\n")

            else:
                print("警告：未找到载荷节点。")

            f.write("*EL PRINT, ELSET=E_ALL\nE\n")
            f.write("*END STEP\n")

        return inp_path, self.gauge_elements

    def run_calculix(self):
        if not os.path.exists(self.full_ccx_path):
            return False, "CCX路径错误"
        for ext in [".frd", ".dat", ".sta", ".cvg"]:
            try:
                os.remove(os.path.join(self.work_dir, self.job_name + ext))
            except:
                pass
        cmd = [self.full_ccx_path, self.job_name]
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            p = subprocess.run(cmd, shell=False, capture_output=True, cwd=self.work_dir, startupinfo=si)
            self.ccx_log = p.stdout.decode('utf-8', 'ignore') + "\n" + p.stderr.decode('utf-8', 'ignore')
            return True, "CCX 运行结束"
        except Exception as e:
            return False, str(e)

    def extract_3_nodes_data(self):
        dat_path = os.path.join(self.work_dir, f"{self.job_name}.dat")
        # 检查文件是否存在
        if not os.path.exists(dat_path):
            return False, f"未生成 DAT 文件。\nLogs:\n{self.ccx_log[-500:]}"

        file_size = os.path.getsize(dat_path)
        if file_size == 0:
            return False, f"DAT 文件为空 (0 bytes)。\n求解可能不收敛。\nLog尾部:\n{self.ccx_log[-500:]}"

        target_eids = set(getattr(self, "gauge_elem_ids", set()))
        if not target_eids:
            return False, "未定位到应变花测量单元。"
        sums = {"E11": 0.0, "E22": 0.0, "E12": 0.0}
        cnt = 0
        lines = []
        try:
            with open(dat_path, 'r') as f:
                lines = f.readlines()

            for line in lines:
                parts = line.split()
                if len(parts) < 8:
                    continue
                if not parts[0].isdigit():
                    continue
                eid = int(parts[0])
                if eid in target_eids:
                    try:
                        sums["E11"] += float(parts[2])
                        sums["E22"] += float(parts[3])
                        sums["E12"] += float(parts[5])
                        cnt += 1
                    except:
                        continue
        except Exception as e:
            return False, f"解析异常: {str(e)}"

        if cnt == 0:
            tail = "".join(lines[-20:])
            return False, f"未匹配到应变花单元数据。\n目标ID: {target_eids}\n\nDAT文件尾部:\n{tail}"

        # 栅格面积平均的应变张量 (共点 rosette 位置)
        E11 = sums["E11"] / cnt
        E22 = sums["E22"] / cnt
        E12 = sums["E12"] / cnt
        # 应变花合成: ε_θ = E11 cos²θ + E22 sin²θ + 2·E12 sinθ cosθ, θ=0/45/90
        e1 = E11
        e2 = 0.5 * (E11 + E22) + E12
        e3 = E22
        sg_pos = self.data.get("sg_pos", 0.175)
        results_txt = (
            f"=== Step {self.job_name.split('_')[-1]} 应变花结果 (微应变) ===\n"
            f"共点 rosette @ ({sg_pos}, 0), 面积平均 {cnt} 个单元\n"
            f"ε1 (0°) : {e1 * 1e6:.1f}\n"
            f"ε2 (45°): {e2 * 1e6:.1f}\n"
            f"ε3 (90°): {e3 * 1e6:.1f}\n"
            f"[张量] Exx: {E11 * 1e6:.1f}  Eyy: {E22 * 1e6:.1f}  Exy: {E12 * 1e6:.1f}\n"
        )
        # 与 youhua.py 一致: 单方向工况时反推该方向对应的标定系数列
        # C_i = ε_i * sqrt(Ex*Ey) / σ_direction
        lp = getattr(self, "last_load_props", None) or {}
        mp = getattr(self, "last_mat_props", None) or {}
        sx = lp.get("Sigma X", 0.0); sy = lp.get("Sigma Y", 0.0); txy = lp.get("Tau XY", 0.0)
        nonzero = [v for v in (sx, sy, txy) if abs(v) > 1e-12]
        if len(nonzero) == 1 and mp.get("E1", 0) > 0 and mp.get("E2", 0) > 0:
            sqrtE = math.sqrt(mp["E1"] * mp["E2"])
            sigma = nonzero[0]
            if abs(sx) > 1e-12:
                col = "C_i1 (σx)"
            elif abs(txy) > 1e-12:
                col = "C_i2 (τxy)"
            else:
                col = "C_i3 (σy)"
            c1 = e1 * sqrtE / sigma
            c2 = e2 * sqrtE / sigma
            c3 = e3 * sqrtE / sigma
            results_txt += (
                f"[标定] 单方向工况 {col}, sqrt(Ex*Ey)={sqrtE:.1f}, σ={sigma:.6f}\n"
                f"  C1={c1:.6f}  C2={c2:.6f}  C3={c3:.6f}\n"
            )
        return True, results_txt