# prometheus_monitor_generator.py

import time
import threading
import math
from collections import deque
from PIL import Image, ImageDraw, ImageFont
from prometheus_api_client import PrometheusConnect
from prometheus_api_client.exceptions import PrometheusApiClientException

# --- Configuration ---
PROMETHEUS_URL = "http://127.0.0.1:9090/"
RESOLUTION = (640, 480)  # Increased resolution for better readability
HISTORY_LENGTH = 120  # Store 2 minutes of data (120 points at 1s interval)
UPDATE_INTERVAL = 1.0  # Seconds - Fetch data every second
FONT_PATH = "arial.ttf"  # Make sure this font is available
TITLE_FONT_SIZE = 20
VALUE_FONT_SIZE = 36
UNIT_FONT_SIZE = 18
GRAPH_POINTS = HISTORY_LENGTH

# Neon Colors on Dark Background
COLORS = {
    "background": (10, 10, 20),  # Dark blue/black
    "foreground": (220, 220, 220),  # Light grey text
    "grid_lines": (40, 40, 60),   # Darker grid
    "gpu_load":   (0, 255, 255),  # Cyan
    "gpu_ram":    (0, 255, 128),  # Spring Green
    "gpu_temp":   (255, 100, 0),  # Orange/Red
    "cpu_load":   (255, 0, 255),  # Magenta
    "ram_usage":  (128, 0, 255),  # Purple
    "disk_write": (255, 255, 0),  # Yellow
    "disk_read":  (50, 100, 255),  # Blue
    "error":      (255, 0, 0),    # Red for errors
}

# Metric Definitions (Verify these names/queries with your Prometheus setup)
METRIC_CONFIG = {
    "gpu_load": {
        "title": "GPU LOAD",
        "query": 'avg(nvidia_smi_utilization_gpu_ratio * 100)', # Assuming ratio is 0-1
        "unit": "%",
        "color": COLORS["gpu_load"],
        "range": (0, 100),
    },
    "gpu_ram": {
        "title": "GPU RAM",
        "query": 'avg(nvidia_smi_utilization_memory_ratio * 100)', # Assuming ratio is 0-1
        "unit": "%",
        "color": COLORS["gpu_ram"],
        "range": (0, 100),
    },
    "gpu_temp": {
        "title": "GPU TEMP",
        "query": 'avg(nvidia_smi_temperature_gpu)',
        "unit": "°C",
        "color": COLORS["gpu_temp"],
        "range": (20, 100), # Adjust typical temp range if needed
    },
    "cpu_load": {
        "title": "CPU LOAD",
        # Calculate non-idle percentage over 1 minute
        "query": '(1 - avg(rate(windows_cpu_time_total{mode="idle"}[1m]))) * 100',
        "unit": "%",
        "color": COLORS["cpu_load"],
        "range": (0, 100),
    },
    "ram_usage": {
        "title": "RAM USAGE",
        # Used RAM = Total Visible - Free Physical
        "query_used": 'windows_os_visible_memory_bytes - windows_os_physical_memory_free_bytes',
        "query_total": 'windows_os_visible_memory_bytes',
        "unit": "GB", # Display in GB
        "color": COLORS["ram_usage"],
        "range": None, # Dynamic range based on total RAM
    },
    "disk_usage": {
        "title": "DISK R/W",
        # Note: Using rate directly gives bytes/sec. No /60 needed.
        # Add {instance=...} or {device=...} if you have multiple disks/instances
        "query_write": 'sum(rate(windows_logical_disk_write_bytes_total[1m]))',
        "query_read": 'sum(rate(windows_logical_disk_read_bytes_total[1m]))',
        "unit": "MB/s", # Display in MB/s
        "color_write": COLORS["disk_write"],
        "color_read": COLORS["disk_read"],
        "range": None, # Dynamic range, or set a reasonable max (e.g., 500 MB/s)
    },
}

# Grid Layout (List of metric keys in order)
GRID_LAYOUT = [
    ["gpu_load", "gpu_ram", "gpu_temp"],
    ["cpu_load", "ram_usage", "disk_usage"],
]
GRID_ROWS = len(GRID_LAYOUT)
GRID_COLS = len(GRID_LAYOUT[0])


def format_bytes(byte_value, precision=1):
    """Converts bytes to KB, MB, GB, TB."""
    if byte_value is None or not isinstance(byte_value, (int, float)) or byte_value < 0:
        return "N/A"
    if byte_value == 0:
        return f"0.0 {'B'}"
    log_val = math.log(byte_value, 1024)
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = min(int(log_val), len(units) - 1)
    scaled_value = byte_value / (1024 ** unit_index)
    return f"{scaled_value:.{precision}f} {units[unit_index]}"

def format_bytes_per_second(bps_value, precision=1):
    """Formats bytes per second into KB/s, MB/s, etc."""
    if bps_value is None or not isinstance(bps_value, (int, float)):
         return "N/A" # Handle None explicitly
    formatted = format_bytes(bps_value, precision)
    if formatted == "N/A":
        return "N/A" # Propagate N/A
    # Ensure "/s" is added correctly, even for "0.0 B"
    if formatted == "0.0 B":
        return "0.0 B/s"
    else:
        return f"{formatted}/s"


class PrometheusMonitorGenerator:
    """
    Generates image frames displaying system metrics fetched from Prometheus.
    """
    def __init__(self,
                 prometheus_url=PROMETHEUS_URL,
                 resolution=RESOLUTION,
                 history_length=HISTORY_LENGTH,
                 update_interval=UPDATE_INTERVAL,
                 font_path=FONT_PATH):
        """
        Initializes the monitor generator.
        """
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

        # --- Initialize Prometheus Client ---
        try:
            self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
            # Basic connectivity check
            if not self.prom.check_prometheus_connection():
                 raise ConnectionError("Initial Prometheus connection check failed.")
            print(f"Connected to Prometheus at {self.prometheus_url}")
        except Exception as e:
            print(f"CRITICAL: Failed to connect to Prometheus at {self.prometheus_url}: {e}")
            # Allow initialization but data fetching will fail
            self.prom = None

        # --- Initialize Data History ---
        self.metric_data = {}
        for key, config in self._metric_config.items():
             if key == "disk_usage": # Special case for dual-value metric
                 self.metric_data['disk_read'] = {"history": deque([0.0] * self.history_length, maxlen=self.history_length), "current": 0.0}
                 self.metric_data['disk_write'] = {"history": deque([0.0] * self.history_length, maxlen=self.history_length), "current": 0.0}
                 self.metric_data['disk_range_max'] = 10 * 1024 * 1024 # Default 10MB/s max range, adjust as needed
             elif key == "ram_usage":
                 self.metric_data['ram_used'] = {"history": deque([0.0] * self.history_length, maxlen=self.history_length), "current": 0.0}
                 self.metric_data['ram_total'] = {"history": deque([0.0] * 1, maxlen=1), "current": 0.0} # Total usually doesn't change
             else:
                 self.metric_data[key] = {"history": deque([0.0] * self.history_length, maxlen=self.history_length), "current": 0.0}


        # --- Load Fonts ---
        self._font_title = self._load_font(TITLE_FONT_SIZE)
        self._font_value = self._load_font(VALUE_FONT_SIZE)
        self._font_unit = self._load_font(UNIT_FONT_SIZE)

        # --- Setup Threading ---
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._data_thread = threading.Thread(target=self._data_collection_loop, daemon=True)

        # --- Start Data Collection ---
        self._data_thread.start()
        print("Data collection thread started.")
        print("PrometheusMonitorGenerator initialized.")

    def _load_font(self, font_size):
        """Loads a font file, raises error if not found."""
        try:
            return ImageFont.truetype(self._font_path, font_size)
        except IOError:
            print(f"CRITICAL ERROR: Font '{self._font_path}' not found.")
            # Fallback to default font if possible, otherwise raise
            try:
                print("Attempting to load default PIL font.")
                return ImageFont.load_default() # May look bad
            except Exception:
                raise RuntimeError(f"Font '{self._font_path}' not found and default font failed.")
        except ImportError:
             print(f"CRITICAL ERROR: Pillow cannot load TTF fonts.")
             raise

    def _fetch_metric(self, query):
        """Fetches a single metric value from Prometheus."""
        if not self.prom:
            return None # Prometheus connection failed during init

        try:
            result = self.prom.custom_query(query=query)
            if result and isinstance(result, list) and 'value' in result[0]:
                # result[0]['value'] is [timestamp, value_string]
                return float(result[0]['value'][1])
            else:
                # print(f"Warning: No data or unexpected format for query: {query} -> {result}")
                return None # No data points found
        except PrometheusApiClientException as e:
            print(f"Warning: Prometheus API error for query '{query}': {e}")
            return None
        except ConnectionError as e:
            print(f"Warning: Prometheus connection error: {e}")
            # Consider trying to reconnect or signal major failure
            return None
        except Exception as e:
            print(f"Warning: Unexpected error fetching query '{query}': {e}")
            return None

    def _data_collection_loop(self):
        """Background thread function to periodically fetch metrics."""
        print("Data collection loop starting.")
        while not self._stop_event.is_set():
            fetched_data = {}
            is_connected = self.prom and self.prom.check_prometheus_connection()

            if not is_connected and self.prom:
                 print("Warning: Prometheus disconnected. Attempting to fetch anyway...")
                 # Or: could skip fetch attempts until reconnected

            for key, config in self._metric_config.items():
                 if not is_connected and not self.prom:
                     # Handle case where prom failed completely in init
                     fetched_data[key] = None
                     if key == "disk_usage":
                         fetched_data['disk_read'] = None
                         fetched_data['disk_write'] = None
                     elif key == "ram_usage":
                         fetched_data['ram_used'] = None
                         fetched_data['ram_total'] = None
                     continue

                 if key == "disk_usage":
                     fetched_data['disk_read'] = self._fetch_metric(config['query_read'])
                     fetched_data['disk_write'] = self._fetch_metric(config['query_write'])
                 elif key == "ram_usage":
                     fetched_data['ram_used'] = self._fetch_metric(config['query_used'])
                     fetched_data['ram_total'] = self._fetch_metric(config['query_total'])
                 else:
                     fetched_data[key] = self._fetch_metric(config['query'])

            # Update shared data structure under lock
            with self._lock:
                for key, value in fetched_data.items():
                    # Use 0.0 if fetch failed (None) to keep graph continuous,
                    # but actual display logic should handle None for text value.
                    current_value = value if value is not None else 0.0

                    if key in self.metric_data:
                         # Handle RAM total update (less frequent)
                         if key == 'ram_total' and value is not None:
                            self.metric_data[key]["current"] = current_value
                            self.metric_data[key]["history"].append(current_value)
                         elif key != 'ram_total': # For all regular history metrics
                            self.metric_data[key]["current"] = current_value # Store raw value or None
                            self.metric_data[key]["history"].append(current_value if value is not None else 0.0) # Use 0 for graph continuity on error


                    # Update dynamic range for disk I/O if needed
                    if key == 'disk_read' or key == 'disk_write':
                        # Simple dynamic max: increase if value exceeds current max * 0.9
                        current_max = self.metric_data.get('disk_range_max', 10*1024*1024)
                        if value is not None and value > current_max * 0.9:
                             self.metric_data['disk_range_max'] = value * 1.2 # Increase max by 20%
                        # Could also add logic to decrease max if values stay low

            # Wait for the next interval or stop signal
            self._stop_event.wait(self.update_interval)
        print("Data collection loop stopped.")


    def draw_sparkline(self, draw, history_deque, x, y, width, height, color, data_range=None, current_value_for_range=None):
        """Draws a single sparkline graph for a metric."""
        history = list(history_deque)
        num_points = len(history)

        if num_points < 2:
            return # Not enough data to draw a line

        # Determine data range for scaling
        min_val, max_val = 0.0, 100.0 # Default (e.g., percentage)
        if data_range and data_range[0] is not None and data_range[1] is not None:
             min_val, max_val = data_range
        elif data_range is None and current_value_for_range is not None:
             # Dynamic range based on a separate value (e.g., RAM total)
              min_val = 0.0
              max_val = current_value_for_range if current_value_for_range > 0 else 1.0 # Avoid division by zero
        elif key == 'disk_usage': # Use dynamic disk range
            min_val = 0.0
            max_val = self.metric_data.get('disk_range_max', 1.0) # Get dynamic max, default 1
        else:
             # Fallback: Calculate range from history (less stable for volatile data)
             valid_history = [p for p in history if isinstance(p, (int, float))]
             if valid_history:
                min_val = min(valid_history)
                max_val = max(valid_history)
             else:
                 min_val, max_val = 0.0, 1.0 # Cannot determine range


        # Avoid division by zero if max == min
        value_span = max_val - min_val
        if value_span <= 0:
            value_span = 1.0 # Prevent division by zero; graph will be flat line

        points_to_draw = []
        for i, value in enumerate(history):
            if value is None or not isinstance(value, (int, float)):
                # Handle missing data points - skip or create gap
                # For simplicity, we'll treat None as min_val for continuity here
                value = min_val

            # Normalize X coordinate
            point_x = x + (i / (num_points - 1)) * width

            # Normalize Y coordinate (inverted Y axis for drawing)
            normalized_y = (value - min_val) / value_span
            point_y = y + height - (normalized_y * height)
            # Clamp point_y to bounds to avoid drawing outside the cell
            point_y = max(y, min(y + height, point_y))


            points_to_draw.append((point_x, point_y))

        if points_to_draw:
            draw.line(points_to_draw, fill=color, width=2) # Thicker line


    def draw_frame(self, target_image):
        """Draws the monitoring dashboard onto the target image."""
        if target_image.size != self.resolution:
            print(f"Warning: Target image size {target_image.size} differs from configured resolution {self.resolution}.")
            # Optional: Resize target_image or raise error
            # target_image = target_image.resize(self.resolution)
        draw = ImageDraw.Draw(target_image)
        width, height = self.resolution

        # --- Get Copy of Data ---
        with self._lock:
            data_copy = {}
            # Deep copy deques and values to avoid race conditions during drawing
            for key, values in self.metric_data.items():
                 if isinstance(values, dict) and "history" in values:
                     data_copy[key] = {
                         "history": values["history"].copy(), # Copy deque
                         "current": values["current"]         # Copy current value
                     }
                 else:
                     data_copy[key] = values # For simple values like disk_range_max


        # 1. Background
        draw.rectangle([0, 0, width, height], fill=self._colors["background"])

        # 2. Grid Calculation
        padding = 10
        cell_outer_width = width // self._grid_cols
        cell_outer_height = height // self._grid_rows
        cell_inner_width = cell_outer_width - 2 * padding
        cell_inner_height = cell_outer_height - 2 * padding

        # 3. Draw Cells
        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                metric_key = self._grid_layout[r][c]
                config = self._metric_config[metric_key]

                cell_x = c * cell_outer_width + padding
                cell_y = r * cell_outer_height + padding

                # Bounding box for content within the cell
                content_x = cell_x
                content_y = cell_y
                content_width = cell_inner_width
                content_height = cell_inner_height

                # --- Draw Cell Content ---
                title = config["title"]
                unit = config.get("unit", "")
                data_range = config.get("range") # Fixed range from config

                # A) Title
                title_y = content_y + 5
                draw.text((content_x, title_y), title, fill=self._colors["foreground"], font=self._font_title)

                # B) Value & Unit
                value_y = title_y + TITLE_FONT_SIZE + 15 # Position value below title
                value_text = "N/A"
                current_val = None
                unit_color = self._colors["foreground"] # Default unit color

                if metric_key == "disk_usage":
                     current_read = data_copy['disk_read']['current']
                     current_write = data_copy['disk_write']['current']
                     val_read_str = format_bytes_per_second(current_read).replace('/s','') # Format read
                     val_write_str = format_bytes_per_second(current_write).replace('/s','') # Format write
                     value_text = f"{val_read_str} R\n{val_write_str} W" # Multi-line display
                     # Use primary color for unit if needed, or specific colors?
                     # Let's keep unit grey for now.
                     unit = "MB/s" # Set explicitly as formatting handles scale
                     # Value positioning needs adjustment for two lines
                     value_y -= 10 # Move up slightly
                elif metric_key == "ram_usage":
                     current_used = data_copy['ram_used']['current']
                     current_total = data_copy['ram_total']['current']
                     if current_used is not None and current_total is not None and current_total > 0:
                         # Format used and total in GB
                         used_gb_str = format_bytes(current_used, 1).replace(' GB', '')
                         total_gb_str = format_bytes(current_total, 1).replace(' GB', '')
                         value_text = f"{used_gb_str} / {total_gb_str}"
                         unit = "GB"
                         unit_color = config["color"]
                         data_range = (0, current_total) # Dynamic range for graph
                     else:
                         value_text = "N/A"
                else: # Standard single-value metrics
                     current_val = data_copy[metric_key]['current']
                     unit_color = config["color"]
                     if current_val is not None:
                         if unit == "%":
                             value_text = f"{current_val:.0f}" # No decimal for percentage
                         elif unit == "°C":
                              value_text = f"{current_val:.0f}" # No decimal for temp
                         else:
                              value_text = f"{current_val:.1f}" # Default to 1 decimal
                     else:
                          value_text = "N/A"
                          unit_color = COLORS["error"]


                # Calculate size and draw value (centered horizontally for now)
                value_bbox = draw.textbbox((0,0), value_text, font=self._font_value)
                value_width = value_bbox[2] - value_bbox[0]
                value_height = value_bbox[3] - value_bbox[1] # Needed for multi-line disk
                value_x = content_x + (content_width - value_width) / 2
                draw.text((value_x, value_y), value_text, fill=unit_color, font=self._font_value, align="center" if "\n" in value_text else "left")

                # Draw Unit next to or below value depending on space/metric
                unit_bbox = draw.textbbox((0,0), unit, font=self._font_unit)
                unit_width = unit_bbox[2] - unit_bbox[0]

                # Position unit to the right of the value text
                unit_x = value_x + value_width + 5
                # Align unit vertically with the *last* line of the value text
                unit_y = value_y + value_height - (unit_bbox[3]-unit_bbox[1])

                # Prevent unit going off edge
                if unit_x + unit_width > content_x + content_width:
                     unit_x = content_x + content_width - unit_width

                if unit: # Only draw unit if defined
                     draw.text((unit_x, unit_y), unit, fill=self._colors["foreground"], font=self._font_unit)


                # C) Graph
                graph_height = content_height // 3 # Allocate bottom third for graph
                graph_y = content_y + content_height - graph_height
                graph_width = content_width

                # Draw grid lines for graph area (optional)
                # num_h_lines = 2
                # for i in range(1, num_h_lines + 1):
                # 	line_y = graph_y + i * (graph_height / (num_h_lines + 1))
                # 	draw.line([(content_x, line_y), (content_x + graph_width, line_y)], fill=self._colors["grid_lines"], width=1)

                if metric_key == "disk_usage":
                     # Draw two lines for disk R/W
                     history_read = data_copy['disk_read']['history']
                     history_write = data_copy['disk_write']['history']
                     disk_max_range = data_copy.get('disk_range_max', 1.0) # Use dynamic max

                     self.draw_sparkline(draw, history_read, content_x, graph_y, graph_width, graph_height,
                                         config["color_read"], data_range=(0, disk_max_range))
                     self.draw_sparkline(draw, history_write, content_x, graph_y, graph_width, graph_height,
                                         config["color_write"], data_range=(0, disk_max_range))
                elif metric_key == "ram_usage":
                     # RAM graph uses dynamic range based on total RAM
                     history = data_copy['ram_used']['history']
                     total_ram = data_copy['ram_total']['current']
                     self.draw_sparkline(draw, history, content_x, graph_y, graph_width, graph_height,
                                          config["color"], data_range=(0, total_ram if total_ram else 1)) # Pass dynamic range
                else:
                     # Standard graph
                     history = data_copy[metric_key]['history']
                     self.draw_sparkline(draw, history, content_x, graph_y, graph_width, graph_height,
                                          config["color"], data_range=data_range) # Use fixed range if available

        return target_image

    def stop(self):
        """Stops the background data collection thread."""
        print("Stopping data collection thread...")
        self._stop_event.set()
        if self._data_thread.is_alive():
            # Wait for the thread to finish, with a timeout
            self._data_thread.join(timeout=self.update_interval * 2 + 1)
        if self._data_thread.is_alive():
            print("Warning: Data collection thread did not stop gracefully.")
        else:
            print("Data collection thread stopped.")

    # --- Context Manager Support ---
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

# --- Example Usage ---
if __name__ == "__main__":
    print("Starting Prometheus Monitor Example...")

    try:
        # Create an initial blank image
        img = Image.new('RGB', RESOLUTION, color=COLORS["background"])

        # Initialize the generator
        with PrometheusMonitorGenerator() as monitor:
            # Display loop (replace with your actual display method)
            # For example, saving frames to files:
            frame_count = 0
            max_frames = 10 # Generate 10 frames for demo
            while frame_count < max_frames:
                start_time = time.time()

                # Draw the current frame onto the image
                monitor.draw_frame(img)

                # --- Display/Save the image ---
                try:
                     # Example: Save frame
                     img.save(f"monitor_frame_{frame_count:03d}.png")
                     print(f"Saved frame {frame_count:03d}.png")

                     # Example: If you have a display connected (e.g., using pygame or opencv)
                     # display.show(img) # Replace with your display logic
                except Exception as e:
                     print(f"Error displaying/saving image: {e}")


                frame_count += 1

                # Maintain update rate (approximately)
                elapsed = time.time() - start_time
                sleep_time = max(0, (1.0 / 10) - elapsed) # Aim for ~10 FPS display update
                time.sleep(sleep_time)

    except RuntimeError as e:
         print(f"Runtime Error: {e}")
    except KeyboardInterrupt:
         print("Interrupted by user.")
    except Exception as e:
         print(f"An unexpected error occurred: {e}")
    finally:
         print("Monitor example finished.")