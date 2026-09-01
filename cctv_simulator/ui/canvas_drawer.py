import math
import tkinter as tk
from typing import Dict, List, Any, Tuple, Optional
from ..calculations import mode_label, polar_point, target_analysis_for_result
from ..models import CameraConfig, OpticResult, TargetPoint, PPMLevel

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


def world_to_canvas(x: float, y: float, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = plot
    min_x, max_x, min_y, max_y = world
    px = x0 + (x - min_x) * (x1 - x0) / max(max_x - min_x, 0.01)
    py = y1 - (y - min_y) * (y1 - y0) / max(max_y - min_y, 0.01)
    return px, py


def canvas_to_world(px: float, py: float, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = plot
    min_x, max_x, min_y, max_y = world
    x = min_x + (px - x0) * (max_x - min_x) / max(x1 - x0, 0.01)
    y = min_y + (y1 - py) * (max_y - min_y) / max(y1 - y0, 0.01)
    return x, y


def top_plot_from_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    return (x0 + 58, y0 + 34, x1 - 22, y1 - 26)


def grid_step(span: float) -> int:
    if span <= 15:
        return 2
    if span <= 45:
        return 5
    if span <= 80:
        return 10
    if span <= 200:
        return 20
    if span <= 500:
        return 50
    if span <= 1500:
        return 200
    if span <= 5000:
        return 500
    return 1000


def wedge_polygon_points(
    camera: CameraConfig,
    start_dist: float,
    end_dist: float,
    half_angle: float,
    plot: Tuple[float, float, float, float],
    world: Tuple[float, float, float, float]
) -> List[float]:
    start_dist = max(0.0, start_dist)
    end_dist = max(start_dist + 0.01, end_dist)
    world_points = [
        polar_point(camera, start_dist, half_angle),
        polar_point(camera, end_dist, half_angle),
        polar_point(camera, end_dist, -half_angle),
        polar_point(camera, start_dist, -half_angle),
    ]
    canvas_points = []
    for point in world_points:
        canvas_points.extend(world_to_canvas(point[0], point[1], plot, world))
    return canvas_points


class CanvasDrawer:
    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas

    def draw_all(
        self,
        cameras: List[CameraConfig],
        selected_camera_index: int,
        last_all_results: Dict[str, List[OpticResult]],
        last_selected_results: List[OpticResult],
        ppm_levels: List[PPMLevel],
        target_point: TargetPoint,
        selected_design_level: PPMLevel,
        plan_path: str,
        plan_width_m: float,
        auto_view_scale: bool,
        plan_photo_cache: Dict[str, Any]
    ) -> Dict[str, Any]:
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        split_y = int(height * 0.46)
        side_rect = (0, 0, width, split_y)
        top_rect = (0, split_y + 1, width, height)

        self.canvas.create_rectangle(0, 0, width, height, fill="#FFFFFF", outline="")
        self.canvas.create_line(0, split_y, width, split_y, fill="#B0BEC5", width=2)
        self.canvas.create_text(12, 10, text="YATAY PROFİL", anchor=tk.NW, fill="#37474F", font=("Arial", 8, "bold"))
        self.canvas.create_text(
            12,
            split_y + 10,
            text="KUŞBAKIŞI / PLAN ÜSTÜ",
            anchor=tk.NW,
            fill="#37474F",
            font=("Arial", 8, "bold"),
        )

        max_draw_dist = self._get_max_draw_distance(plan_width_m, last_all_results)
        if len(last_selected_results) == 2:
            top_lane = (0, 26, width, split_y // 2)
            bottom_lane = (0, split_y // 2, width, split_y - 4)
            self._draw_side_lane(last_selected_results[0], top_lane, max_draw_dist, "#1565C0", ppm_levels)
            self._draw_side_lane(last_selected_results[1], bottom_lane, max_draw_dist, "#6A1B9A", ppm_levels)
        elif last_selected_results:
            self._draw_side_lane(last_selected_results[0], (0, 26, width, split_y - 4), max_draw_dist, "#1565C0", ppm_levels)

        world = self._draw_top_down(
            top_rect, max_draw_dist, cameras, selected_camera_index,
            last_all_results, ppm_levels, target_point, selected_design_level,
            plan_path, plan_width_m, auto_view_scale, plan_photo_cache
        )
        return {
            "side_rect": side_rect,
            "top_rect": top_rect,
            "world": world,
            "max_draw_dist": max_draw_dist,
        }

    def _get_max_draw_distance(self, plan_width_m: float, last_all_results: Dict[str, List[OpticResult]]) -> float:
        # PERF: single running max instead of materialising a list of every
        # ground distance of every camera on every redraw.
        largest = plan_width_m if plan_width_m > 15.0 else 15.0
        for results in last_all_results.values():
            for result in results:
                candidate = result.dead_zone_m + 10.0
                if candidate > largest:
                    largest = candidate
                for dist in result.ground_distances.values():
                    if dist > largest and math.isfinite(dist):
                        largest = dist
                geom = result.max_geom_dist_m
                if geom > largest and math.isfinite(geom):
                    largest = geom
                if largest >= 150.0:
                    return 150.0        # already clamped, stop scanning
        return max(15.0, min(largest, 150.0))

    def _draw_side_lane(self, result: OpticResult, rect: Tuple[int, int, int, int], max_draw_dist: float, accent: str, ppm_levels: List[PPMLevel]):
        x0, y0, x1, y1 = rect
        origin_x = x0 + 64
        right_x = x1 - 28
        available_w = max(right_x - origin_x, 100)
        px_per_m = available_w / max(max_draw_dist, 1.0)
        ground_y = y1 - 22
        camera = result.camera
        max_pole_px = max(32, ground_y - y0 - 34)
        px_per_m_y = min(px_per_m, max_pole_px / max(camera.pole_height_m, 1.0))
        cam_y = ground_y - camera.pole_height_m * px_per_m_y
        target_y = ground_y - camera.target_height_m * px_per_m_y

        # PERF: the original drew one line per metre (up to 152 create_line +
        # 31 create_text per redraw). Below ~6 px spacing the lines overlap into
        # a grey smear anyway, so snap the tick to a 1/2/5/10... ladder.
        tick_m = 1
        for step in (1, 2, 5, 10, 20, 25, 50, 100):
            tick_m = step
            if step * px_per_m >= 6.0:
                break
        label_every = tick_m * 5
        create_line = self.canvas.create_line
        create_text = self.canvas.create_text
        for meter in range(0, int(max_draw_dist) + 2, tick_m):
            x = origin_x + meter * px_per_m
            major = meter % label_every == 0
            create_line(x, y0, x, y1, fill="#E0E0E0" if major else "#F5F5F5")
            if major:
                create_text(x + 2, y0 + 2, text=f"{meter}m", anchor=tk.NW, fill="#757575", font=("Arial", 7))

        self.canvas.create_text(
            x0 + 12,
            y0 + 2,
            text=f"{camera.name} - {mode_label(result.mode)} ({result.focal_mm:g} mm)",
            anchor=tk.NW,
            fill=accent,
            font=("Arial", 8, "bold"),
        )
        self.canvas.create_line(0, ground_y, x1, ground_y, fill="#37474F", width=2)
        self.canvas.create_line(origin_x, target_y, x1, target_y, fill="#FFB74D", dash=(3, 3))
        self.canvas.create_text(x1 - 100, target_y - 12, text=f"Hedef: {camera.target_height_m:g}m", fill="#E65100", font=("Arial", 7))
        self.canvas.create_line(origin_x, cam_y, x1, cam_y, fill="#CFD8DC", dash=(2, 4))
        self.canvas.create_text(x1 - 95, cam_y - 12, text="0° ufuk", fill="#90A4AE", font=("Arial", 7))

        levels_by_dist = sorted(ppm_levels, key=lambda level: result.ground_distances.get(level.key, 0))
        last_x = origin_x + result.dead_zone_m * px_per_m
        for level in levels_by_dist:
            dist = result.ground_distances.get(level.key, 0)
            if dist > result.dead_zone_m:
                end_x = origin_x + min(dist, max_draw_dist) * px_per_m
                if end_x > last_x:
                    self.canvas.create_rectangle(last_x, target_y - 16, end_x, target_y, fill=level.color, outline="#546E7A")
                    last_x = end_x

        self._draw_side_ray(result, result.bottom_ray_deg, origin_x, cam_y, target_y, px_per_m, max_draw_dist, "#0288D1")
        self._draw_side_ray(result, camera.tilt_deg, origin_x, cam_y, target_y, px_per_m, max_draw_dist, "#1976D2", dash=(2, 4))
        self._draw_side_ray(result, result.top_ray_deg, origin_x, cam_y, target_y, px_per_m, max_draw_dist, "#0288D1")

        dead_x = origin_x + result.dead_zone_m * px_per_m
        self.canvas.create_line(dead_x, y0, dead_x, y1, fill="#E65100", dash=(2, 2))
        self.canvas.create_text(
            dead_x + 4,
            max(y0 + 18, target_y - 34),
            text=f"Kör: {result.dead_zone_m:.1f}m",
            fill="#D32F2F",
            font=("Arial", 7, "bold"),
        )
        self.canvas.create_line(origin_x, ground_y, origin_x, cam_y, fill="#263238", width=3)
        self.canvas.create_oval(origin_x - 5, cam_y - 5, origin_x + 5, cam_y + 5, fill="#D32F2F", outline="")

    def _draw_side_ray(self, result: OpticResult, angle_deg: float, origin_x: float, cam_y: float, target_y: float, px_per_m: float, max_draw_dist: float, color: str, dash=(4, 2)):
        if angle_deg > 0.2:
            hit_m = result.vertical_drop_m / math.tan(math.radians(angle_deg))
            end_dist = min(hit_m, max_draw_dist)
            end_x = origin_x + end_dist * px_per_m
            if hit_m <= max_draw_dist:
                end_y = target_y
            else:
                drop_at_end = end_dist * math.tan(math.radians(angle_deg))
                end_y = cam_y + drop_at_end * (target_y - cam_y) / result.vertical_drop_m
        else:
            end_x = origin_x + max_draw_dist * px_per_m
            end_y = cam_y - abs(max_draw_dist * math.tan(math.radians(angle_deg))) * 6
        self.canvas.create_line(origin_x, cam_y, end_x, end_y, fill=color, width=1.4, dash=dash)

    def _draw_top_down(
        self,
        rect: Tuple[int, int, int, int],
        max_draw_dist: float,
        cameras: List[CameraConfig],
        selected_camera_index: int,
        last_all_results: Dict[str, List[OpticResult]],
        ppm_levels: List[PPMLevel],
        target_point: TargetPoint,
        selected_design_level: PPMLevel,
        plan_path: str,
        plan_width_m: float,
        auto_view_scale: bool,
        plan_photo_cache: Dict[str, Any]
    ) -> Tuple[float, float, float, float]:
        plot = top_plot_from_rect(rect)
        self._draw_plan_background(plot, plan_path, plan_photo_cache)

        plot_w = max(plot[2] - plot[0], 100)
        plot_h = max(plot[3] - plot[1], 100)
        if auto_view_scale:
            world = self._view_angle_world_bounds(max_draw_dist, plot_w / plot_h, cameras, last_all_results, target_point, plan_width_m)
        else:
            world_width = max(plan_width_m, max_draw_dist, 15.0)
            world_height = max(world_width * plot_h / plot_w, 10.0)
            min_x = min(-2.0, min(camera.pos_x_m for camera in cameras) - 2.0)
            max_x = max(world_width, max(camera.pos_x_m for camera in cameras) + max_draw_dist)
            half_h = world_height / 2
            min_y = min(-half_h, min(camera.pos_y_m for camera in cameras) - half_h * 0.2)
            max_y = max(half_h, max(camera.pos_y_m for camera in cameras) + half_h * 0.2)
            world = (min_x, max_x, min_y, max_y)

        self.canvas.create_rectangle(*plot, outline="#90A4AE")
        self._draw_plan_grid(plot, world)

        for camera in cameras:
            results = last_all_results.get(camera.name, [])
            if len(results) == 2:
                self._draw_topdown_result(results[0], plot, world, ppm_levels, outline_only=False, max_draw_dist=max_draw_dist)
                self._draw_topdown_result(results[1], plot, world, ppm_levels, outline_only=True, max_draw_dist=max_draw_dist)
            elif results:
                self._draw_topdown_result(results[0], plot, world, ppm_levels, outline_only=False, max_draw_dist=max_draw_dist)
            self._draw_camera_marker(camera, plot, world, selected=(camera == cameras[selected_camera_index]))

        self._draw_target_marker(plot, world, target_point, selected_design_level, last_all_results.get(cameras[selected_camera_index].name, []))
        return world

    def _view_angle_world_bounds(
        self,
        max_draw_dist: float,
        target_aspect: float,
        cameras: List[CameraConfig],
        last_all_results: Dict[str, List[OpticResult]],
        target_point: TargetPoint,
        plan_width_m: float
    ) -> Tuple[float, float, float, float]:
        points = []
        for camera in cameras:
            points.append((camera.pos_x_m, camera.pos_y_m))
            for result in last_all_results.get(camera.name, []):
                reach_values = [result.dead_zone_m, result.max_geom_dist_m]
                reach_values.extend(result.ground_distances.values())
                reach_values = [value for value in reach_values if math.isfinite(value)]
                reach = min(max(reach_values or [max_draw_dist]), max_draw_dist)
                for distance in (0.0, result.dead_zone_m, reach):
                    for offset in (-result.hfov_deg / 2, 0.0, result.hfov_deg / 2):
                        points.append(polar_point(camera, distance, offset))
        if target_point.active:
            points.append((target_point.x_m, target_point.y_m))

        if not points:
            return (-2.0, max(plan_width_m, 15.0), -8.0, 8.0)

        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        margin_x = max((max_x - min_x) * 0.12, 2.0)
        margin_y = max((max_y - min_y) * 0.12, 2.0)
        min_x -= margin_x
        max_x += margin_x
        min_y -= margin_y
        max_y += margin_y

        width = max(max_x - min_x, 8.0)
        height = max(max_y - min_y, 8.0)
        current_aspect = width / height
        if current_aspect < target_aspect:
            desired_width = height * target_aspect
            extra = (desired_width - width) / 2
            min_x -= extra
            max_x += extra
        else:
            desired_height = width / max(target_aspect, 0.01)
            extra = (desired_height - height) / 2
            min_y -= extra
            max_y += extra
        return (min_x, max_x, min_y, max_y)

    def _draw_plan_background(self, plot: Tuple[float, float, float, float], plan_path: str, plan_photo_cache: Dict[str, Any]):
        if not plan_path:
            self.canvas.create_rectangle(*plot, fill="#FAFAFA", outline="")
            return
        x0, y0, x1, y1 = plot
        target_size = (max(int(x1 - x0), 1), max(int(y1 - y0), 1))
        try:
            if Image is not None and ImageTk is not None:
                source_key = (plan_path, target_size)
                if plan_photo_cache.get("photo") is None or plan_photo_cache.get("source") != source_key:
                    image = Image.open(plan_path)
                    image.thumbnail(target_size)
                    canvas_image = Image.new("RGB", target_size, "#FAFAFA")
                    offset = ((target_size[0] - image.width) // 2, (target_size[1] - image.height) // 2)
                    canvas_image.paste(image.convert("RGB"), offset)
                    plan_photo_cache["photo"] = ImageTk.PhotoImage(canvas_image, master=self.canvas)
                    plan_photo_cache["source"] = source_key
                self.canvas.create_image(x0, y0, image=plan_photo_cache["photo"], anchor=tk.NW)
            else:
                if plan_photo_cache.get("photo") is None:
                    plan_photo_cache["photo"] = tk.PhotoImage(file=plan_path, master=self.canvas)
                    plan_photo_cache["source"] = plan_path
                self.canvas.create_image((x0 + x1) / 2, (y0 + y1) / 2, image=plan_photo_cache["photo"], anchor=tk.CENTER)
        except Exception:
            self.canvas.create_rectangle(*plot, fill="#FFF8E1", outline="")
            self.canvas.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text="Plan görseli açılamadı. PNG deneyin veya Pillow kurun.",
                fill="#E65100",
                font=("Arial", 9, "bold"),
            )

    def _draw_plan_grid(self, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float]):
        min_x, max_x, min_y, max_y = world
        step = grid_step(max_x - min_x)
        start_x = math.floor(min_x / step) * step
        x = start_x
        while x <= max_x + step:
            px, _ = world_to_canvas(x, 0, plot, world)
            self.canvas.create_line(px, plot[1], px, plot[3], fill="#ECEFF1")
            if x >= min_x:
                self.canvas.create_text(px + 2, plot[1] + 2, text=f"{x:g}m", anchor=tk.NW, fill="#78909C", font=("Arial", 7))
            x += step
        start_y = math.floor(min_y / step) * step
        y = start_y
        while y <= max_y + step:
            _, py = world_to_canvas(0, y, plot, world)
            self.canvas.create_line(plot[0], py, plot[2], py, fill="#ECEFF1")
            y += step

    def _draw_topdown_result(self, result: OpticResult, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float], ppm_levels: List[PPMLevel], outline_only=False, max_draw_dist=None):
        levels_by_dist = sorted(ppm_levels, key=lambda level: result.ground_distances.get(level.key, 0))
        last_dist = result.dead_zone_m
        for level in levels_by_dist:
            dist = result.ground_distances.get(level.key, 0)
            if dist <= last_dist:
                continue
            cur_dist = min(dist, max_draw_dist or dist)
            if outline_only:
                self._draw_wedge_outline(result.camera, last_dist, cur_dist, result.hfov_deg / 2, plot, world, "#263238")
            else:
                points = wedge_polygon_points(result.camera, last_dist, cur_dist, result.hfov_deg / 2, plot, world)
                self.canvas.create_polygon(points, fill=level.color, outline="#455A64", width=1)
            last_dist = cur_dist

        if result.dead_zone_m > 0:
            dz_points = wedge_polygon_points(
                result.camera, 0, result.dead_zone_m, result.hfov_deg / 2, plot, world
            )
            if result.dead_zone_covered_by:
                self.canvas.create_polygon(dz_points, fill="#E8F5E9", outline="#2E7D32", width=1.5, dash=(4, 2), stipple="gray25")
            else:
                self.canvas.create_polygon(dz_points, fill="#FFCDD2", outline="#C62828", width=1.5, stipple="gray25")
            center_dist = result.dead_zone_m * 0.5
            center_point = polar_point(result.camera, center_dist, 0)
            cx, cy = world_to_canvas(center_point[0], center_point[1], plot, world)
            self.canvas.create_text(
                cx, cy, text=f"Kör\n{result.dead_zone_m:.1f}m",
                fill="#C62828" if not result.dead_zone_covered_by else "#2E7D32",
                font=("Arial", 7, "bold"),
            )

    def _draw_wedge_outline(self, camera: CameraConfig, start_dist: float, end_dist: float, half_angle: float, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float], color: str):
        points = wedge_polygon_points(camera, start_dist, end_dist, half_angle, plot, world)
        coords = points + points[:2]
        self.canvas.create_line(coords, fill=color, width=1.4, dash=(4, 2))

    def _draw_camera_marker(self, camera: CameraConfig, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float], selected=False):
        x, y = world_to_canvas(camera.pos_x_m, camera.pos_y_m, plot, world)
        angle = math.radians(camera.heading_deg)
        tip = (x + 12 * math.cos(angle), y - 12 * math.sin(angle))
        left = (x + 7 * math.cos(angle + math.radians(145)), y - 7 * math.sin(angle + math.radians(145)))
        right = (x + 7 * math.cos(angle - math.radians(145)), y - 7 * math.sin(angle - math.radians(145)))
        fill = "#D32F2F" if selected else "#1976D2"
        self.canvas.create_polygon([tip, left, right], fill=fill, outline="#263238")
        self.canvas.create_text(x + 8, y + 8, text=camera.name, anchor=tk.NW, fill="#263238", font=("Arial", 8, "bold"))

    def _draw_target_marker(self, plot: Tuple[float, float, float, float], world: Tuple[float, float, float, float], target_point: TargetPoint, selected_level: PPMLevel, last_selected_results: List[OpticResult]):
        if not target_point.active:
            return
        x, y = world_to_canvas(target_point.x_m, target_point.y_m, plot, world)
        ok = False
        if last_selected_results:
            data = target_analysis_for_result(target_point, last_selected_results[0], selected_level)
            ok = data["inside_angle"] and data["inside_geometry"] and data["ppm_ok"] and data["ir_ok"]
        color = "#2E7D32" if ok else "#C62828"
        self.canvas.create_line(x - 9, y, x + 9, y, fill=color, width=2)
        self.canvas.create_line(x, y - 9, x, y + 9, fill=color, width=2)
        self.canvas.create_oval(x - 6, y - 6, x + 6, y + 6, outline=color, width=2)
        self.canvas.create_text(
            x + 10,
            y - 10,
            text=target_point.name,
            anchor=tk.NW,
            fill=color,
            font=("Arial", 8, "bold"),
        )
