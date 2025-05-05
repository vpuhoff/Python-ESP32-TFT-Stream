# prometheus_monitor_generator.py

import time
import threading
import math
from collections import deque
from PIL import Image, ImageDraw, ImageFont
# Используем стандартные исключения, если специфичные не нужны явно
from prometheus_api_client import PrometheusConnect # , PrometheusApiClientException # Убрали импорт специфичного исключения, если не используется

# --- Configuration ---
PROMETHEUS_URL = "http://127.0.0.1:9090/"
RESOLUTION = (640, 480)
HISTORY_LENGTH = 120
UPDATE_INTERVAL = 1.0
FONT_PATH = "arial.ttf" # Убедитесь, что шрифт доступен
# Размеры шрифтов из последней удачной версии интерфейса
TITLE_FONT_SIZE = 18
VALUE_FONT_SIZE = 36
UNIT_FONT_SIZE = 20
GRAPH_POINTS = HISTORY_LENGTH

# Цвета из последней удачной версии интерфейса
MAIN_COLOR = (0, 255, 255) # Яркий циан/бирюзовый
COLORS = {
    "background": (10, 15, 25),
    "foreground": (200, 220, 220),
    "value_color": MAIN_COLOR,
    "graph_line": MAIN_COLOR,
    "grid_lines": (80, 110, 140),   # Яркая сетка
    "cell_border": (60, 80, 100),
    "error":      (255, 80, 80),
}

# Определяем цвета для графиков
# Используем основной цвет для всех, как в последней версии интерфейса
GPU_LOAD_COLOR = COLORS["graph_line"]
GPU_RAM_COLOR = COLORS["graph_line"] # Используем основной цвет
GPU_TEMP_COLOR = COLORS["graph_line"]
CPU_LOAD_COLOR = COLORS["graph_line"]
RAM_USAGE_COLOR = COLORS["graph_line"]
DISK_WRITE_COLOR = COLORS["graph_line"]
DISK_READ_COLOR = (0, 180, 200) # Оставим чтение чуть другим для отличия

# Metric Definitions - Используем запросы из предоставленной ВАМИ рабочей версии
METRIC_CONFIG = {
    "gpu_load": {
        "title": "GPU LOAD",
        "query": 'avg(nvidia_smi_utilization_gpu_ratio * 100)', # Как было
        "unit": "%",
        "color": GPU_LOAD_COLOR, # Новый цвет
        "range": (0, 100),
    },
    "gpu_ram": {
        "title": "GPU RAM",
        "query": 'avg(nvidia_smi_memory_used_bytes)/avg(nvidia_smi_memory_total_bytes)*100', # Как было (РАБОЧИЙ ВАРИАНТ)
        "unit": "%",
        "color": GPU_RAM_COLOR, # Новый цвет
        "range": (0, 100),
    },
    "gpu_temp": {
        "title": "GPU TEMP",
        "query": 'avg(nvidia_smi_temperature_gpu)', # Как было
        "unit": "°C",
        "color": GPU_TEMP_COLOR, # Новый цвет
        "range": (20, 100),
    },
    "cpu_load": {
        "title": "CPU LOAD",
        "query": '(1 - avg(rate(windows_cpu_time_total{mode="idle"}[1m]))) * 100', # Как было
        "unit": "%",
        "color": CPU_LOAD_COLOR, # Новый цвет
        "range": (0, 100),
    },
    "ram_usage": {
        "title": "RAM USAGE",
        "query_used": 'windows_os_visible_memory_bytes - windows_os_physical_memory_free_bytes', # Как было
        "query_total": 'windows_os_visible_memory_bytes', # Как было
        "unit": "GB",
        "color": RAM_USAGE_COLOR, # Новый цвет
        "range": None,
    },
    "disk_usage": {
        "title": "DISK R/W",
        "query_write": 'sum(rate(windows_logical_disk_write_bytes_total[1m]))', # Как было
        "query_read": 'sum(rate(windows_logical_disk_read_bytes_total[1m]))', # Как было
        "unit": "B/s", # Базовая единица
        "color_write": DISK_WRITE_COLOR, # Новый цвет
        "color_read": DISK_READ_COLOR,   # Новый цвет
        "range": None,
    },
}

# Grid Layout - Как в последней версии
GRID_LAYOUT = [
    ["gpu_load", "gpu_ram", "gpu_temp"],
    ["cpu_load", "ram_usage", "disk_usage"],
]
GRID_ROWS = len(GRID_LAYOUT)
GRID_COLS = len(GRID_LAYOUT[0]) if GRID_ROWS > 0 else 0

# Функции форматирования - как в последней версии
def format_bytes(byte_value, precision=1):
    """Converts bytes to a human-readable format (KB, MB, GB, TB)."""
    if byte_value is None or not isinstance(byte_value, (int, float)) or byte_value < 0 or math.isnan(byte_value):
        return "N/A"
    if byte_value == 0:
        return f"{0.0:.{precision}f} B"
    log_val = math.log(max(byte_value, 1), 1024)
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = min(int(log_val), len(units) - 1)
    scaled_value = byte_value / (1024 ** unit_index)
    return f"{scaled_value:.{precision}f} {units[unit_index]}"

def format_bytes_per_second(bps_value, precision=1):
    """Formats bytes per second into a human-readable format (KB/s, MB/s, etc.)."""
    if bps_value is None or not isinstance(bps_value, (int, float)) or math.isnan(bps_value):
         return "N/A"
    if bps_value == 0:
         base_str = format_bytes(bps_value, precision)
         return f"{base_str}/s"
    formatted_bytes = format_bytes(bps_value, precision)
    if formatted_bytes == "N/A": return "N/A"
    parts = formatted_bytes.split(' ')
    if len(parts) == 2: return f"{parts[0]}{parts[1]}/s"
    else: return f"{formatted_bytes}/s"


class PrometheusMonitorGenerator:
    """
    Generates image frames displaying system metrics fetched from Prometheus.
    """
    # --- __init__ --- (Взят из последней версии, т.к. инициализация данных там корректнее)
    def __init__(self,
                 prometheus_url=PROMETHEUS_URL,
                 resolution=RESOLUTION,
                 history_length=HISTORY_LENGTH,
                 update_interval=UPDATE_INTERVAL,
                 font_path=FONT_PATH):
        print("Initializing PrometheusMonitorGenerator...")
        self.prometheus_url = prometheus_url
        self.resolution = resolution
        self.history_length = history_length
        self.update_interval = update_interval
        self._font_path = font_path
        self._colors = COLORS
        self._metric_config = METRIC_CONFIG
        self._grid_layout = GRID_LAYOUT
        self._grid_rows = GRID_ROWS
        self._grid_cols = GRID_COLS
        self.prom = None
        try:
            self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
            if not self.prom.check_prometheus_connection():
                 raise ConnectionError("Initial Prometheus connection check failed.")
            print(f"Successfully connected to Prometheus at {self.prometheus_url}")
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to connect or verify Prometheus at {self.prometheus_url}: {e}")
        self.metric_data = {}
        for key, config in self._metric_config.items():
             sub_metrics = []
             if key == "disk_usage":
                 sub_metrics = ['disk_read', 'disk_write']
                 self.metric_data['disk_range_max'] = 10 * 1024 * 1024
             elif key == "ram_usage":
                 sub_metrics = ['ram_used', 'ram_total']
             # Убрали обработку gpu_ram_bytes, т.к. используем старый запрос только на %
             #elif key == "gpu_ram":
             #     sub_metrics = ['gpu_ram', 'gpu_ram_used_bytes', 'gpu_ram_total_bytes']
             else:
                 sub_metrics = [key]
             for sub_key in sub_metrics:
                  hist_len = 1 if 'total' in sub_key else self.history_length
                  default_val = 0.0
                  self.metric_data[sub_key] = {
                      "history": deque([default_val] * hist_len, maxlen=hist_len),
                      "current": default_val
                  }
             # Убрали переименование gpu_ram_percent, т.к. ключ теперь просто gpu_ram
             #if key == "gpu_ram":
             #    self.metric_data["gpu_ram"] = self.metric_data.pop("gpu_ram_percent")
        self._font_title = self._load_font(TITLE_FONT_SIZE)
        self._font_value = self._load_font(VALUE_FONT_SIZE)
        self._font_unit = self._load_font(UNIT_FONT_SIZE)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._data_thread = threading.Thread(target=self._data_collection_loop, daemon=True)
        self._data_thread.start()
        print("Background data collection thread started.")
        print("PrometheusMonitorGenerator initialized.")

    # --- _load_font --- (Взят из последней версии)
    def _load_font(self, font_size):
        try:
            return ImageFont.truetype(self._font_path, font_size, layout_engine=ImageFont.Layout.RAQM)
        except ImportError:
             return ImageFont.truetype(self._font_path, font_size)
        except IOError:
            print(f"CRITICAL ERROR: Font file '{self._font_path}' not found.")
            try:
                print("Attempting to load default PIL font as fallback.")
                return ImageFont.load_default()
            except Exception as e:
                raise RuntimeError(f"Font '{self._font_path}' not found and default font failed: {e}")

    # --- _fetch_metric --- (Взят из предоставленной ВАМИ рабочей версии)
    # Важно: эта версия не обрабатывает 'NaN' явно, но если она работала, оставим её
    def _fetch_metric(self, query):
        if not self.prom:
            return None
        try:
            result = self.prom.custom_query(query=query)
            if result and isinstance(result, list) and 'value' in result[0]:
                # Возвращаем значение как float
                return float(result[0]['value'][1])
            else:
                # Возвращаем None, если данных нет
                return None
        # Убрали обработку PrometheusApiClientException, если она не нужна
        except ConnectionError as e:
            print(f"Warning: Prometheus connection error: {e}")
            return None
        except Exception as e:
            print(f"Warning: Unexpected error fetching query '{query}': {e}")
            return None

    # --- _data_collection_loop --- (Взят из последней версии, но использует _fetch_metric выше)
    # Убрана обработка gpu_ram_bytes
    def _data_collection_loop(self):
        while not self._stop_event.is_set():
            start_fetch_time = time.time()
            fetched_data = {}
            is_connected = False
            if self.prom:
                 try: is_connected = self.prom.check_prometheus_connection()
                 except Exception: is_connected = False
            if not is_connected:
                 for key in self._metric_config.keys():
                     value = None
                     if key == "disk_usage": fetched_data['disk_read'] = value; fetched_data['disk_write'] = value
                     elif key == "ram_usage": fetched_data['ram_used'] = value; fetched_data['ram_total'] = value
                     #elif key == "gpu_ram": fetched_data['gpu_ram'] = value # Больше не нужно для байтов
                     else: fetched_data[key] = value
            else:
                for key, config in self._metric_config.items():
                    if key == "disk_usage":
                        fetched_data['disk_read'] = self._fetch_metric(config['query_read'])
                        fetched_data['disk_write'] = self._fetch_metric(config['query_write'])
                    elif key == "ram_usage":
                        fetched_data['ram_used'] = self._fetch_metric(config['query_used'])
                        fetched_data['ram_total'] = self._fetch_metric(config['query_total'])
                    # Убрали обработку gpu_ram_bytes
                    #elif key == "gpu_ram":
                    #     fetched_data['gpu_ram'] = self._fetch_metric(config['query'])
                    else:
                        fetched_data[key] = self._fetch_metric(config['query'])

            with self._lock:
                # --> Отладочный вывод, если все еще N/A <--
                # print(f"DEBUG - Fetched this cycle: {fetched_data}")
                # ---
                for sub_key, value in fetched_data.items():
                     if sub_key not in self.metric_data: continue
                     current_value_for_display = value
                     value_for_history = 0.0
                     # Используем проверку isnan из math
                     if value is not None and not (isinstance(value, float) and math.isnan(value)):
                         value_for_history = value
                     self.metric_data[sub_key]["current"] = current_value_for_display
                     if 'total' not in sub_key:
                        self.metric_data[sub_key]["history"].append(value_for_history)
                     elif value is not None and not (isinstance(value, float) and math.isnan(value)):
                          self.metric_data[sub_key]["history"].clear()
                          self.metric_data[sub_key]["history"].append(value)
                     if sub_key == 'disk_read' or sub_key == 'disk_write':
                         current_max = self.metric_data.get('disk_range_max', 1.0)
                         if value is not None and not math.isnan(value) and value > current_max * 0.9:
                             self.metric_data['disk_range_max'] = value * 1.2
            fetch_duration = time.time() - start_fetch_time
            sleep_time = max(0, self.update_interval - fetch_duration)
            self._stop_event.wait(sleep_time)

    # --- draw_sparkline_with_grid --- (Взят из последней версии, т.к. там была исправлена отрисовка)
    def draw_sparkline_with_grid(self, draw, history_deque, x, y, width, height, color, data_range=None, current_value_for_range=None, metric_key_debug=None): # Добавлен параметр для отладки
        history = list(history_deque)
        num_points = len(history)
        grid_color = self._colors["grid_lines"]
        num_h_lines = 3
        num_v_lines = 4
        grid_line_width = 1
        if height > 0 and num_h_lines > 0:
            step_y = height / (num_h_lines + 1)
            for i in range(1, num_h_lines + 1):
                line_y = round(y + i * step_y)
                draw.line([(x, line_y), (x + width, line_y)], fill=grid_color, width=grid_line_width)
        if width > 0 and num_v_lines > 0:
            step_x = width / (num_v_lines + 1)
            for i in range(1, num_v_lines + 1):
                line_x = round(x + i * step_x)
                draw.line([(line_x, y), (line_x, y + height)], fill=grid_color, width=grid_line_width)
        if num_points < 2: return
        min_val, max_val = 0.0, 100.0
        range_source = "default (0-100)"
        if isinstance(data_range, (tuple, list)) and len(data_range) == 2 and all(v is not None for v in data_range):
            min_val, max_val = data_range
            range_source = f"config {data_range}"
        elif current_value_for_range is not None and current_value_for_range > 0:
            min_val = 0.0
            max_val = current_value_for_range
            range_source = f"dynamic RAM (0-{max_val:.2f})"
        elif metric_key_debug == 'disk_usage' and 'disk_range_max' in self.metric_data:
             min_val = 0.0
             max_val = max(self.metric_data.get('disk_range_max', 1024), 1024)
             range_source = f"dynamic Disk (0-{max_val/1024/1024:.2f} MB/s)"
        elif metric_key_debug == 'gpu_temp' and data_range is None:
             min_val, max_val = 20.0, 100.0
             range_source = "default temp (20-100)"
        if max_val <= min_val: max_val = min_val + 1.0
        value_span = max_val - min_val
        if value_span <= 0: value_span = 1.0
        # if metric_key_debug: print(f"DEBUG [{metric_key_debug}]: Range={min_val:.2f}-{max_val:.2f}, Span={value_span:.2f}, Source='{range_source}', Points={num_points}")
        points_to_draw = []
        for i, value in enumerate(history):
            draw_value = min_val
            if value is not None and isinstance(value, (int, float)) and not math.isnan(value):
                draw_value = value
            point_x = x + (i / max(1, num_points - 1)) * width
            if value_span > 0: normalized_y = (draw_value - min_val) / value_span
            else: normalized_y = 0
            point_y = y + height - (normalized_y * height)
            points_to_draw.append((round(point_x), round(point_y)))
            # if metric_key_debug and i % (num_points // 5 + 1) == 0: print(f"  Point {i}: raw={value}, draw={draw_value:.2f}, normY={normalized_y:.2f}, X={point_x:.1f}, Y={point_y:.1f}")
        if len(points_to_draw) > 1:
            # print(f"DEBUG [{metric_key_debug}]: Drawing line with {len(points_to_draw)} points. First: {points_to_draw[0]}, Last: {points_to_draw[-1]}")
            draw.line(points_to_draw, fill=color, width=2)
        # else: print(f"DEBUG [{metric_key_debug}]: Not drawing line, points count = {len(points_to_draw)}")

    # --- draw_frame --- (Взят из последней версии с исправлением ZeroDivisionError)
    def draw_frame(self, target_image):
        if target_image.size != self.resolution: print(f"Warning: Target image size {target_image.size} differs from configured resolution {self.resolution}.")
        draw = ImageDraw.Draw(target_image)
        width, height = self.resolution
        with self._lock:
            data_copy = {}
            for key, values in self.metric_data.items():
                 if isinstance(values, dict) and "history" in values: data_copy[key] = {"history": values["history"].copy(), "current": values["current"]}
                 else: data_copy[key] = values
        draw.rectangle([0, 0, width, height], fill=self._colors["background"])
        padding = 5
        cell_outer_width = width // self._grid_cols
        cell_outer_height = height // self._grid_rows
        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                metric_key = self._grid_layout[r][c]
                config = self._metric_config[metric_key]
                cell_outer_x0 = c * cell_outer_width; cell_outer_y0 = r * cell_outer_height
                cell_outer_x1 = cell_outer_x0 + cell_outer_width; cell_outer_y1 = cell_outer_y0 + cell_outer_height
                draw.rectangle([cell_outer_x0, cell_outer_y0, cell_outer_x1, cell_outer_y1], outline=self._colors["cell_border"], width=2)
                content_x = cell_outer_x0 + padding; content_y = cell_outer_y0 + padding
                content_width = cell_outer_width - 2 * padding; content_height = cell_outer_height - 2 * padding
                title = config["title"]; unit = config.get("unit", ""); data_range_from_config = config.get("range")
                title_y = content_y + 5
                draw.text((content_x + 5, title_y), title, fill=self._colors["foreground"], font=self._font_title)
                value_area_y_start = title_y + TITLE_FONT_SIZE + 10
                value_text = "N/A"; unit_text = ""; unit_color = self._colors["value_color"]
                graph_colors = []; graph_histories = []; current_total_for_range = None
                if metric_key == "disk_usage":
                    current_read = data_copy.get('disk_read', {}).get('current'); current_write = data_copy.get('disk_write', {}).get('current')
                    val_read_str = format_bytes_per_second(current_read, 0).replace('/s',''); val_write_str = format_bytes_per_second(current_write, 0).replace('/s','')
                    value_text = f"{val_read_str} R\n{val_write_str} W"; unit_text = "B/s"
                    graph_colors = [config["color_read"], config["color_write"]]
                    graph_histories = [data_copy.get('disk_read', {}).get('history', deque()), data_copy.get('disk_write', {}).get('history', deque())]
                elif metric_key == "ram_usage":
                    current_used = data_copy.get('ram_used',{}).get('current'); current_total_val = data_copy.get('ram_total',{}).get('history',[0.0])[0]
                    if current_used is not None and current_total_val is not None and not math.isnan(current_used) and not math.isnan(current_total_val) and current_total_val > 0:
                        used_gb_str = format_bytes(current_used, 1).replace(' GB', ''); value_text = f"{used_gb_str}"; unit_text = "GB"
                        current_total_for_range = current_total_val; graph_colors = [config["color"]]; graph_histories = [data_copy.get('ram_used',{}).get('history', deque())]
                    else: value_text = "N/A"; unit_color = COLORS["error"]; graph_colors = [config["color"]]; graph_histories = [deque([0.0] * HISTORY_LENGTH, maxlen=HISTORY_LENGTH)] # FIX: Added default history on error
                # Убрали отдельную обработку gpu_ram, т.к. запрос стандартный
                else: # GPU Load, GPU RAM (old query), GPU Temp, CPU Load
                    current_val = data_copy.get(metric_key, {}).get('current')
                    if current_val is not None and not (isinstance(current_val, float) and math.isnan(current_val)):
                         if unit == "%": value_text = f"{current_val:.0f}"
                         elif unit == "°C": value_text = f"{current_val:.0f}"
                         else: value_text = f"{current_val:.1f}"
                         unit_text = unit
                    else: value_text = "N/A"; unit_color = COLORS["error"]
                    graph_colors = [config["color"]]
                    graph_histories = [data_copy.get(metric_key, {}).get('history', deque())]
                value_bbox = draw.textbbox((0, 0), value_text, font=self._font_value, anchor="la", spacing=0)
                value_width = value_bbox[2] - value_bbox[0]; value_height = value_bbox[3] - value_bbox[1]
                value_x = content_x + 10; value_y = value_area_y_start
                draw.text((value_x, value_y), value_text, fill=unit_color, font=self._font_value, anchor="la", align="left")
                if unit_text:
                     unit_bbox = draw.textbbox((0, 0), unit_text, font=self._font_unit, anchor="ls")
                     unit_width = unit_bbox[2] - unit_bbox[0]
                     unit_x = value_x + value_width + 5; unit_y = value_y + value_height
                     if unit_x + unit_width > content_x + content_width - 5: unit_x = content_x + content_width - unit_width - 5
                     draw.text((unit_x, unit_y), unit_text, fill=unit_color, font=self._font_unit, anchor="ls")
                graph_area_y_start = value_y + value_height + 15
                graph_height = max(10, content_y + content_height - graph_area_y_start - 5)
                graph_y = graph_area_y_start; graph_width = content_width
                valid_histories = [h for h in graph_histories if isinstance(h, deque)]
                if not graph_colors: print(f"ERROR: graph_colors list is empty for metric {metric_key}"); continue
                for i, history in enumerate(valid_histories):
                    color = graph_colors[i % len(graph_colors)]
                    self.draw_sparkline_with_grid(draw, history, content_x, graph_y, graph_width, graph_height, color, data_range=data_range_from_config, current_value_for_range=current_total_for_range, metric_key_debug = metric_key)
        return target_image

    # --- stop, __enter__, __exit__ --- (Как в последней версии)
    def stop(self):
        print("\nStopping data collection thread...")
        self._stop_event.set()
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            self._data_thread.join(timeout=self.update_interval * 2 + 1)
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            print("Warning: Data collection thread did not stop gracefully.")
        else:
            print("Data collection thread stopped.")
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.stop()

# --- Example Usage --- (Как в последней версии)
if __name__ == "__main__":
    print("Starting Prometheus Monitor Example...")
    monitor_instance = None
    try:
        img = Image.new('RGB', RESOLUTION, color=COLORS["background"])
        with PrometheusMonitorGenerator() as monitor_instance:
            frame_count = 0
            max_frames = 120
            print(f"Generating {max_frames} frames (press Ctrl+C to stop)...")
            while frame_count < max_frames:
                start_time = time.time()
                monitor_instance.draw_frame(img)
                try:
                     img.save(f"monitor_frame_{frame_count:03d}.png")
                     print(f"\rGenerated frame: {frame_count+1}/{max_frames}", end="")
                except Exception as e: print(f"\nError saving/displaying image frame: {e}")
                frame_count += 1
                elapsed = time.time() - start_time
                sleep_time = max(0, monitor_instance.update_interval - elapsed)
                time.sleep(sleep_time)
            print("\nFinished generating frames.")
    except RuntimeError as e: print(f"\nRuntime Error: {e}")
    except KeyboardInterrupt: print("\nInterrupted by user.")
    except Exception as e: print(f"\nAn unexpected error occurred: {e}"); import traceback; traceback.print_exc()
    finally: print("Monitor example finished.")