# graphics_engine.py

import math
from collections import deque
from PIL import Image, ImageDraw, ImageFont

# --- Helper Functions (Moved Here) ---
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

class MonitorGraphicsEngine:
    """
    Handles the drawing of monitor frames using Pillow.
    Takes configuration and data, and renders it onto an image.
    """
    def __init__(self, resolution, font_path, colors, grid_layout, metric_config,
                 title_font_size, value_font_size, unit_font_size, history_length):
        print("Initializing MonitorGraphicsEngine...")
        self.resolution = resolution
        self._font_path = font_path
        self._colors = colors
        self._grid_layout = grid_layout
        self._metric_config = metric_config
        self._history_length = history_length # Needed for default deque on error

        if not self._grid_layout:
             raise ValueError("Grid layout cannot be empty.")
        self._grid_rows = len(self._grid_layout)
        self._grid_cols = len(self._grid_layout[0]) if self._grid_rows > 0 else 0
        if self._grid_rows == 0 or self._grid_cols == 0:
             raise ValueError("Grid layout must have rows and columns.")

        self._font_title = self._load_font(title_font_size)
        self._font_value = self._load_font(value_font_size)
        self._font_unit = self._load_font(unit_font_size)
        print("MonitorGraphicsEngine initialized.")

    def _load_font(self, font_size):
        """Loads the specified font or falls back to default."""
        try:
            # Try using RAQM layout for better complex script handling if available
            return ImageFont.truetype(self._font_path, font_size, layout_engine=ImageFont.Layout.RAQM)
        except ImportError:
             # Fallback if RAQM not needed or PIL version is older
             return ImageFont.truetype(self._font_path, font_size)
        except IOError:
            print(f"CRITICAL ERROR: Font file '{self._font_path}' not found.")
            try:
                print("Attempting to load default PIL font as fallback.")
                # Load default font with specific size (requires recent Pillow versions)
                # return ImageFont.load_default(font_size) # Use this if your Pillow supports it
                return ImageFont.load_default() # Older fallback
            except Exception as e:
                raise RuntimeError(f"Font '{self._font_path}' not found and default font failed: {e}")

    def draw_sparkline_with_grid(self, draw, history_deque, x, y, width, height, color, data_range=None, current_value_for_range=None, metric_key_debug=None, disk_range_max_value=None):
        """Draws the sparkline graph with grid lines."""
        # --- This method remains largely the same as before ---
        # --- Key change: Receives disk_range_max_value if needed ---
        history = list(history_deque)
        num_points = len(history)
        grid_color = self._colors["grid_lines"]
        num_h_lines = 3
        num_v_lines = 4
        grid_line_width = 1

        # Draw Grid
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

        if num_points < 2: return # Need at least two points to draw a line

        # Determine Y-axis Range
        min_val, max_val = 0.0, 100.0 # Default range
        range_source = "default (0-100)"

        if isinstance(data_range, (tuple, list)) and len(data_range) == 2 and all(v is not None for v in data_range):
            min_val, max_val = data_range
            range_source = f"config {data_range}"
        elif metric_key_debug == 'ram_usage' and current_value_for_range is not None and current_value_for_range > 0:
             # Dynamic RAM range (0 to total RAM)
             min_val = 0.0
             max_val = current_value_for_range
             range_source = f"dynamic RAM (0-{max_val:.2f})"
        elif metric_key_debug == 'disk_usage' and disk_range_max_value is not None:
             # Dynamic Disk range (0 to calculated max)
             min_val = 0.0
             # Ensure max_val is at least a small number to avoid division by zero
             max_val = max(disk_range_max_value, 1.0) # Use passed value
             range_source = f"dynamic Disk (0-{max_val/1024/1024:.2f} MB/s)"
        elif metric_key_debug == 'gpu_temp' and data_range is None: # Explicit default for temp if no range given
             min_val, max_val = 20.0, 100.0
             range_source = "default temp (20-100)"
        # Add other specific defaults if needed

        if max_val <= min_val: max_val = min_val + 1.0 # Avoid division by zero or negative span

        value_span = max_val - min_val
        if value_span <= 0: value_span = 1.0 # Prevent division by zero

        # Debug print for range
        # if metric_key_debug: print(f"DEBUG [{metric_key_debug}]: Range={min_val:.2f}-{max_val:.2f}, Span={value_span:.2f}, Source='{range_source}', Points={num_points}")

        # Prepare points
        points_to_draw = []
        for i, value in enumerate(history):
            draw_value = min_val # Default to min if value is invalid
            if value is not None and isinstance(value, (int, float)) and not math.isnan(value):
                # Clamp value within the determined range for drawing
                draw_value = max(min_val, min(value, max_val))
                # draw_value = value # Alternative: Don't clamp, let it go off-graph

            point_x = x + (i / max(1, num_points - 1)) * width
            normalized_y = (draw_value - min_val) / value_span
            point_y = y + height - (normalized_y * height) # Y=0 is top

            points_to_draw.append((round(point_x), round(point_y)))

            # Debug print for points
            # if metric_key_debug and i % (num_points // 5 + 1) == 0: print(f"   Point {i}: raw={value}, draw={draw_value:.2f}, normY={normalized_y:.2f}, X={point_x:.1f}, Y={point_y:.1f}")

        # Draw line
        if len(points_to_draw) > 1:
            # Debug print before drawing
            # print(f"DEBUG [{metric_key_debug}]: Drawing line with {len(points_to_draw)} points. First: {points_to_draw[0]}, Last: {points_to_draw[-1]}")
            draw.line(points_to_draw, fill=color, width=2)
        # else: print(f"DEBUG [{metric_key_debug}]: Not drawing line, points count = {len(points_to_draw)}")


    def draw_frame(self, target_image, current_metric_data):
        """Draws a complete frame onto the target_image using the provided data."""
        if target_image.size != self.resolution:
             print(f"Warning: Target image size {target_image.size} differs from configured resolution {self.resolution}.")
             # Consider resizing target_image or adjusting drawing logic if needed
             # For now, proceed with the target image size, drawing might be clipped/misaligned

        draw = ImageDraw.Draw(target_image)
        # Use target_image size for calculations to be safe
        width, height = target_image.size

        # Clear background
        draw.rectangle([0, 0, width, height], fill=self._colors["background"])

        # --- Drawing logic remains very similar to the original draw_frame ---
        # --- Key difference: Uses 'current_metric_data' passed as argument ---
        # --- instead of 'self.metric_data'                         ---

        padding = 5
        # Ensure division by zero is avoided if grid_cols/rows are somehow 0
        cell_outer_width = width // max(1, self._grid_cols)
        cell_outer_height = height // max(1, self._grid_rows)

        # Get the dynamic disk range max value from the passed data
        disk_dynamic_max = current_metric_data.get('disk_range_max')

        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                # Defensive check for layout index
                if r >= len(self._grid_layout) or c >= len(self._grid_layout[r]):
                    print(f"Warning: Grid layout index out of bounds at ({r}, {c})")
                    continue

                metric_key = self._grid_layout[r][c]

                # Defensive check for metric config
                if metric_key not in self._metric_config:
                    print(f"Warning: Metric key '{metric_key}' not found in metric_config.")
                    continue
                config = self._metric_config[metric_key]

                # Cell boundaries
                cell_outer_x0 = c * cell_outer_width
                cell_outer_y0 = r * cell_outer_height
                # Clamp to image boundaries to prevent drawing errors if grid doesn't fit perfectly
                cell_outer_x1 = min(width, cell_outer_x0 + cell_outer_width)
                cell_outer_y1 = min(height, cell_outer_y0 + cell_outer_height)

                # Draw cell border
                draw.rectangle([cell_outer_x0, cell_outer_y0, cell_outer_x1, cell_outer_y1],
                               outline=self._colors["cell_border"], width=2)

                # Content area within cell
                content_x = cell_outer_x0 + padding
                content_y = cell_outer_y0 + padding
                content_width = max(0, (cell_outer_x1 - cell_outer_x0) - 2 * padding)
                content_height = max(0, (cell_outer_y1 - cell_outer_y0) - 2 * padding)

                if content_width <= 0 or content_height <= 0:
                    continue # Skip drawing content if cell is too small

                # --- Text and Graph Rendering (mostly same as before) ---
                title = config["title"]
                unit = config.get("unit", "")
                data_range_from_config = config.get("range")

                # Title
                title_y = content_y + 5
                draw.text((content_x + 5, title_y), title, fill=self._colors["foreground"], font=self._font_title)

                # Value Area Start
                value_area_y_start = title_y + self._font_title.size + 10 # Use font size directly

                # Prepare variables
                value_text = "N/A"
                unit_text = ""
                unit_color = self._colors["value_color"]
                graph_colors = []
                graph_histories = []
                current_total_for_range = None # Specifically for RAM total

                # --- Get data from current_metric_data ---
                if metric_key == "disk_usage":
                    # Disk: Read/Write
                    read_data = current_metric_data.get('disk_read', {})
                    write_data = current_metric_data.get('disk_write', {})
                    current_read = read_data.get('current')
                    current_write = write_data.get('current')

                    # Format values (use helper functions)
                    val_read_str = format_bytes_per_second(current_read, 0).replace('/s','')
                    val_write_str = format_bytes_per_second(current_write, 0).replace('/s','')
                    value_text = f"{val_read_str} R\n{val_write_str} W"
                    unit_text = "B/s" # Base unit text

                    # Graph data
                    graph_colors = [config.get("color_read", self._colors["graph_line"]),
                                    config.get("color_write", self._colors["graph_line"])]
                    graph_histories = [read_data.get('history', deque([0.0]*self._history_length, maxlen=self._history_length)),
                                       write_data.get('history', deque([0.0]*self._history_length, maxlen=self._history_length))]

                elif metric_key == "ram_usage":
                    # RAM: Used/Total
                    used_data = current_metric_data.get('ram_used', {})
                    total_data = current_metric_data.get('ram_total', {}) # Total is stored differently
                    current_used = used_data.get('current')
                    # Total RAM is usually static, get the single value from its 'history' deque
                    # Provide a default list [0.0] if key/history is missing
                    total_history = total_data.get('history', deque([0.0], maxlen=1))
                    current_total_val = total_history[0] if total_history else 0.0

                    if current_used is not None and current_total_val is not None and \
                       not math.isnan(current_used) and not math.isnan(current_total_val) and current_total_val > 0:
                        # Format value (use helper function)
                        used_gb_str = format_bytes(current_used, 1).replace(' GB', '')
                        # total_gb_str = format_bytes(current_total_val, 1) # Could display total too
                        value_text = f"{used_gb_str}"
                        unit_text = "GB"
                        current_total_for_range = current_total_val # Pass total for dynamic range
                        graph_colors = [config.get("color", self._colors["graph_line"])]
                        graph_histories = [used_data.get('history', deque([0.0]*self._history_length, maxlen=self._history_length))]
                    else:
                        value_text = "N/A"
                        unit_color = self._colors["error"]
                        graph_colors = [config.get("color", self._colors["graph_line"])]
                        # Provide default history on error to prevent crash in sparkline
                        graph_histories = [deque([0.0] * self._history_length, maxlen=self._history_length)]

                else:
                    # Single value metrics (GPU Load, GPU RAM %, GPU Temp, CPU Load)
                    metric_data = current_metric_data.get(metric_key, {})
                    current_val = metric_data.get('current')

                    if current_val is not None and not (isinstance(current_val, float) and math.isnan(current_val)):
                        # Format based on unit
                        if unit == "%": value_text = f"{current_val:.0f}"
                        elif unit == "°C": value_text = f"{current_val:.0f}"
                        else: value_text = f"{current_val:.1f}" # Default formatting
                        unit_text = unit
                    else:
                        value_text = "N/A"
                        unit_color = self._colors["error"]

                    graph_colors = [config.get("color", self._colors["graph_line"])]
                    # Provide default history on error
                    graph_histories = [metric_data.get('history', deque([0.0]*self._history_length, maxlen=self._history_length))]


                # Draw Value Text
                # Use textbbox for potentially better sizing with multi-line text (like Disk R/W)
                value_bbox = draw.textbbox((content_x + 10, value_area_y_start), value_text, font=self._font_value, anchor="la", align="left", spacing=0)
                value_width = value_bbox[2] - value_bbox[0]
                value_height = value_bbox[3] - value_bbox[1]
                value_x = content_x + 10
                value_y = value_area_y_start

                draw.text((value_x, value_y), value_text, fill=unit_color, font=self._font_value, anchor="la", align="left") # Use 'la' anchor (left, baseline of first line)

                # Draw Unit Text (if present)
                if unit_text:
                    # Position unit relative to the bounding box of the value text
                    unit_bbox = draw.textbbox((0, 0), unit_text, font=self._font_unit, anchor="ls") # Left, top
                    unit_width = unit_bbox[2] - unit_bbox[0]
                    # unit_height = unit_bbox[3] - unit_bbox[1] # Not usually needed for positioning

                    unit_x = value_x + value_width + 5  # Position after value text
                    unit_y = value_y + value_height      # Align baseline with bottom of value text bbox

                    # Prevent unit from going off the right edge
                    if unit_x + unit_width > content_x + content_width - 5:
                        unit_x = content_x + content_width - unit_width - 5 # Adjust to fit

                    # Draw unit text using 'ls' anchor (left, baseline)
                    draw.text((unit_x, unit_y), unit_text, fill=unit_color, font=self._font_unit, anchor="ls")


                # Graph Area Calculation
                graph_area_y_start = value_y + value_height + 15 # Space below value text
                graph_height = max(10, (content_y + content_height) - graph_area_y_start - 5) # Remaining height
                graph_y = graph_area_y_start
                graph_width = content_width # Use full content width

                # Draw Sparkline(s)
                # Ensure histories are valid deques before iterating
                valid_histories = [h for h in graph_histories if isinstance(h, deque)]

                if not graph_colors:
                     print(f"ERROR: graph_colors list is empty for metric {metric_key}")
                     continue # Skip drawing graph if no colors defined

                for i, history in enumerate(valid_histories):
                    if i >= len(graph_colors):
                        print(f"Warning: More histories than colors for metric {metric_key}. Reusing colors.")
                    color = graph_colors[i % len(graph_colors)] # Cycle through colors if needed

                    # Pass dynamic ranges if applicable
                    self.draw_sparkline_with_grid(
                        draw, history,
                        content_x, graph_y, graph_width, graph_height,
                        color,
                        data_range=data_range_from_config,
                        current_value_for_range=current_total_for_range if metric_key == 'ram_usage' else None,
                        metric_key_debug=metric_key, # Pass key for debug messages inside sparkline
                        disk_range_max_value=disk_dynamic_max if metric_key == 'disk_usage' else None
                    )

        return target_image # Return the modified image