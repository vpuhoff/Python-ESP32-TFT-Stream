# prometheus_monitor_generator.py

import time
import threading
import math
from collections import deque
from PIL import Image # Still need Image to create the initial canvas
# Import the new graphics engine
from graphics_engine import MonitorGraphicsEngine
# Prometheus client is still needed here
from prometheus_api_client import PrometheusConnect

# --- Configuration (Most remains here) ---
PROMETHEUS_URL = "http://127.0.0.1:9090/"
RESOLUTION = (640, 480)
HISTORY_LENGTH = 120
UPDATE_INTERVAL = 1.0
FONT_PATH = "arial.ttf" # Graphics engine needs this path

# Font sizes are now passed to the graphics engine
TITLE_FONT_SIZE = 18
VALUE_FONT_SIZE = 36
UNIT_FONT_SIZE = 20
# GRAPH_POINTS = HISTORY_LENGTH # Implicitly HISTORY_LENGTH

# Colors are passed to the graphics engine
MAIN_COLOR = (0, 255, 255) # Яркий циан/бирюзовый
COLORS = {
    "background": (10, 15, 25),
    "foreground": (200, 220, 220),
    "value_color": MAIN_COLOR,
    "graph_line": MAIN_COLOR,
    "grid_lines": (80, 110, 140),
    "cell_border": (60, 80, 100),
    "error":      (255, 80, 80),
}
# Specific colors defined here, but used by graphics engine via METRIC_CONFIG
GPU_LOAD_COLOR = COLORS["graph_line"]
GPU_RAM_COLOR = COLORS["graph_line"]
GPU_TEMP_COLOR = COLORS["graph_line"]
CPU_LOAD_COLOR = COLORS["graph_line"]
RAM_USAGE_COLOR = COLORS["graph_line"]
DISK_WRITE_COLOR = COLORS["graph_line"]
DISK_READ_COLOR = (0, 180, 200)

# Metric Definitions (Remain here, passed to graphics engine)
METRIC_CONFIG = {
    "gpu_load": {
        "title": "GPU LOAD",
        "query": 'avg(nvidia_smi_utilization_gpu_ratio * 100)',
        "unit": "%",
        "color": GPU_LOAD_COLOR,
        "range": (0, 100),
    },
    "gpu_ram": {
        "title": "GPU RAM",
        "query": 'avg(nvidia_smi_memory_used_bytes)/avg(nvidia_smi_memory_total_bytes)*100',
        "unit": "%",
        "color": GPU_RAM_COLOR,
        "range": (0, 100),
    },
    "gpu_temp": {
        "title": "GPU TEMP",
        "query": 'avg(nvidia_smi_temperature_gpu)',
        "unit": "°C",
        "color": GPU_TEMP_COLOR,
        "range": (20, 100), # Example range
    },
    "cpu_load": {
        "title": "CPU LOAD",
        "query": '(1 - avg(rate(windows_cpu_time_total{mode="idle"}[1m]))) * 100',
        "unit": "%",
        "color": CPU_LOAD_COLOR,
        "range": (0, 100),
    },
    "ram_usage": {
        "title": "RAM USAGE",
        "query_used": 'windows_os_visible_memory_bytes - windows_os_physical_memory_free_bytes',
        "query_total": 'windows_os_visible_memory_bytes',
        "unit": "GB", # Display unit
        "color": RAM_USAGE_COLOR,
        "range": None, # Dynamic range based on total
    },
    "disk_usage": {
        "title": "DISK R/W",
        "query_write": 'sum(rate(windows_logical_disk_write_bytes_total[1m]))',
        "query_read": 'sum(rate(windows_logical_disk_read_bytes_total[1m]))',
        "unit": "B/s", # Base unit for display formatting
        "color_write": DISK_WRITE_COLOR,
        "color_read": DISK_READ_COLOR,
        "range": None, # Dynamic range based on recent max
    },
}

# Grid Layout (Remains here, passed to graphics engine)
GRID_LAYOUT = [
    ["gpu_load", "gpu_ram", "gpu_temp"],
    ["cpu_load", "ram_usage", "disk_usage"],
]
# GRID_ROWS/COLS are calculated within graphics engine now

# --- Helper functions for formatting are now in graphics_engine.py ---


class PrometheusMonitorGenerator:
    """
    Fetches system metrics from Prometheus and uses a graphics engine
    to generate visualization frames.
    """
    def __init__(self,
                 prometheus_url=PROMETHEUS_URL,
                 resolution=RESOLUTION,
                 history_length=HISTORY_LENGTH,
                 update_interval=UPDATE_INTERVAL,
                 font_path=FONT_PATH,
                 colors=COLORS, # Pass colors dict
                 grid_layout=GRID_LAYOUT, # Pass layout
                 metric_config=METRIC_CONFIG # Pass metric config
                 ):
        print("Initializing PrometheusMonitorGenerator...")
        self.prometheus_url = prometheus_url
        # Store graphics parameters needed for engine init
        self.resolution = resolution
        self.history_length = history_length
        self.update_interval = update_interval
        self._colors = colors # Keep a reference if needed elsewhere, or just pass
        self._grid_layout = grid_layout # Keep if needed, or just pass
        self._metric_config = metric_config # Keep reference

        self.prom = None
        try:
            self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
            # Initial connection check
            if not self.prom.check_prometheus_connection():
                # Changed to warning, will try to reconnect in loop
                print(f"Warning: Initial Prometheus connection check failed at {self.prometheus_url}. Will retry.")
                # raise ConnectionError("Initial Prometheus connection check failed.") # Or raise error
            else:
                 print(f"Successfully connected to Prometheus at {self.prometheus_url}")
        except Exception as e:
            # Changed to warning, will try to reconnect in loop
            print(f"Warning: Failed to connect or verify Prometheus at {self.prometheus_url}: {e}. Will retry.")
            # print(f"CRITICAL ERROR: Failed to connect or verify Prometheus at {self.prometheus_url}: {e}") # Or raise

        # Initialize metric data storage (remains the same)
        self.metric_data = {}
        for key, config in self._metric_config.items():
             sub_metrics = []
             if key == "disk_usage":
                 sub_metrics = ['disk_read', 'disk_write']
                 # Initialize dynamic range tracker for disk
                 self.metric_data['disk_range_max'] = 10 * 1024 * 1024 # Initial 10 MB/s max
             elif key == "ram_usage":
                 sub_metrics = ['ram_used', 'ram_total']
             else:
                 sub_metrics = [key]

             for sub_key in sub_metrics:
                 # Use history_length for history, length 1 for static totals
                 hist_len = 1 if 'total' in sub_key else self.history_length
                 default_val = 0.0
                 self.metric_data[sub_key] = {
                     "history": deque([default_val] * hist_len, maxlen=hist_len),
                     "current": default_val
                 }

        # --- Graphics Engine Initialization ---
        try:
             self.graphics = MonitorGraphicsEngine(
                 resolution=self.resolution,
                 font_path=font_path, # Pass font path
                 colors=self._colors, # Pass colors
                 grid_layout=self._grid_layout, # Pass layout
                 metric_config=self._metric_config, # Pass metric config
                 title_font_size=TITLE_FONT_SIZE, # Pass font sizes
                 value_font_size=VALUE_FONT_SIZE,
                 unit_font_size=UNIT_FONT_SIZE,
                 history_length=self.history_length # Pass history length
             )
        except (ValueError, RuntimeError, IOError) as e:
             print(f"CRITICAL ERROR: Failed to initialize MonitorGraphicsEngine: {e}")
             # Decide how to handle this - maybe raise the error, or set self.graphics = None
             raise # Re-raise critical error

        # Threading setup (remains the same)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._data_thread = threading.Thread(target=self._data_collection_loop, daemon=True)
        self._data_thread.start()
        print("Background data collection thread started.")
        print("PrometheusMonitorGenerator initialized.")

    # --- Font loading is now handled by MonitorGraphicsEngine ---
    # def _load_font(self, font_size): ... removed ...

    def _fetch_metric(self, query):
        """Fetches a single metric value from Prometheus."""
        # --- This method remains the same ---
        if not self.prom:
            # print("Warning: Prometheus connection not available for fetch.")
            return None # No connection object

        # Try to reconnect or verify connection before query if needed
        # try:
        #     if not self.prom.check_prometheus_connection():
        #         print("Warning: Prometheus connection lost, attempting query anyway...")
        #         # Optionally try to re-establish connection here if possible
        # except Exception as conn_err:
        #      print(f"Warning: Error checking Prometheus connection before query: {conn_err}")
        #      return None # Don't attempt query if check fails badly


        try:
            result = self.prom.custom_query(query=query)
            # Basic validation of result structure
            if result and isinstance(result, list) and len(result) > 0 and \
               isinstance(result[0], dict) and 'value' in result[0] and \
               isinstance(result[0]['value'], (list, tuple)) and len(result[0]['value']) == 2:
                # Extract value, attempt float conversion
                raw_value = result[0]['value'][1]
                try:
                    # Handle potential 'NaN' string from Prometheus
                    if isinstance(raw_value, str) and raw_value.lower() == 'nan':
                         return math.nan # Return actual NaN
                    return float(raw_value)
                except (ValueError, TypeError):
                     print(f"Warning: Could not convert value '{raw_value}' to float for query '{query}'.")
                     return None # Conversion failed
            else:
                # print(f"Warning: No data or unexpected format received for query '{query}'. Result: {result}")
                return None # No data or unexpected format
        # More specific exceptions can be useful
        except ConnectionError as e:
            print(f"Warning: Prometheus connection error during query '{query}': {e}")
            # Consider attempting reconnect here? For now, return None.
            # self.prom = None # Maybe reset prom object?
            return None
        except Exception as e:
            # Catch other potential errors from prometheus_api_client or network issues
            print(f"Warning: Unexpected error fetching query '{query}': {type(e).__name__} - {e}")
            return None

    def _data_collection_loop(self):
        """Background loop to fetch metrics periodically."""
        # --- This method remains largely the same ---
        # --- It updates self.metric_data                       ---
        while not self._stop_event.is_set():
            start_fetch_time = time.time()
            fetched_data = {}
            is_connected = False

            # Check connection status (or try to connect if not connected)
            if not self.prom:
                 try:
                      print("Attempting to establish Prometheus connection...")
                      self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
                      is_connected = self.prom.check_prometheus_connection()
                      if is_connected: print("Prometheus connection established.")
                      else: self.prom = None # Reset if check failed
                 except Exception as e:
                      print(f"Warning: Failed to establish Prometheus connection: {e}")
                      self.prom = None
                      is_connected = False
            else:
                 try:
                      is_connected = self.prom.check_prometheus_connection()
                      # if not is_connected: print("Warning: Prometheus connection check failed.")
                 except Exception as e:
                      print(f"Warning: Prometheus connection check failed: {e}")
                      is_connected = False
                      # Consider setting self.prom = None here?

            # Fetch data only if connected
            if is_connected:
                for key, config in self._metric_config.items():
                    if key == "disk_usage":
                        fetched_data['disk_read'] = self._fetch_metric(config['query_read'])
                        fetched_data['disk_write'] = self._fetch_metric(config['query_write'])
                    elif key == "ram_usage":
                        fetched_data['ram_used'] = self._fetch_metric(config['query_used'])
                        fetched_data['ram_total'] = self._fetch_metric(config['query_total'])
                    else:
                        fetched_data[key] = self._fetch_metric(config['query'])
            else:
                # If not connected, populate with None to show "N/A"
                for key in self._metric_config.keys():
                    value = None
                    if key == "disk_usage":
                         fetched_data['disk_read'] = value; fetched_data['disk_write'] = value
                    elif key == "ram_usage":
                         fetched_data['ram_used'] = value; fetched_data['ram_total'] = value
                    else: fetched_data[key] = value


            # --- Update shared metric_data structure (same logic) ---
            with self._lock:
                # Debug print
                # print(f"DEBUG - Fetched this cycle: {fetched_data}")

                for sub_key, value in fetched_data.items():
                    if sub_key not in self.metric_data: continue # Skip unknown keys like 'disk_range_max'

                    current_value_for_display = value # Keep None/NaN for display as "N/A"
                    value_for_history = 0.0 # Default history value if fetch failed

                    # Store the actual fetched value (even if None/NaN) as 'current'
                    self.metric_data[sub_key]["current"] = current_value_for_display

                    # Determine value for history (use 0.0 for None/NaN)
                    if value is not None and not (isinstance(value, float) and math.isnan(value)):
                        value_for_history = value

                    # Update history deque (append non-total, replace total)
                    if 'total' not in sub_key:
                        self.metric_data[sub_key]["history"].append(value_for_history)
                    else:
                        # For totals (like RAM total), replace the deque content
                        self.metric_data[sub_key]["history"].clear()
                        self.metric_data[sub_key]["history"].append(value_for_history)


                    # Update dynamic disk range maximum (remains same logic)
                    if sub_key == 'disk_read' or sub_key == 'disk_write':
                        current_max = self.metric_data.get('disk_range_max', 1.0) # Get current dynamic max
                        # Increase max range if current value exceeds 90% of it
                        if value is not None and not math.isnan(value) and value > current_max * 0.9:
                            new_max = value * 1.2 # Set new max 20% above current value
                            self.metric_data['disk_range_max'] = new_max
                            # print(f"DEBUG: Increased disk_range_max to {new_max / 1024 / 1024 :.2f} MB/s")
                        # Optional: Add logic to decrease max range slowly if values stay low?
                        # E.g., if max(history) < current_max * 0.5 for a while, decrease current_max


            # Calculate sleep time
            fetch_duration = time.time() - start_fetch_time
            sleep_time = max(0, self.update_interval - fetch_duration)
            # Use wait with timeout for better interrupt handling
            self._stop_event.wait(sleep_time)

    # --- Drawing methods are removed ---
    # def draw_sparkline_with_grid(...): ... removed ...
    # def draw_frame(...): ... removed ...

    def generate_image_frame(self, target_image):
        """
        Generates a monitor frame onto the target_image using the graphics engine.
        """
        if not hasattr(self, 'graphics') or self.graphics is None:
             print("Error: Graphics engine not initialized.")
             # Optionally draw an error message on the image
             # draw = ImageDraw.Draw(target_image)
             # draw.rectangle([0,0, *target_image.size], fill=self._colors['background'])
             # draw.text((10,10), "Error: Graphics Engine Failed", fill=self._colors['error'])
             return target_image # Return original or error image

        # Get a thread-safe copy of the current data
        with self._lock:
            # Create a copy to pass to the drawing function.
            # Shallow copy is usually okay if the drawing function doesn't modify
            # nested structures deeply, but deepcopy is safest if unsure.
            # Let's do a structured copy to be safe with deques.
            data_copy = {}
            for key, values in self.metric_data.items():
                 if isinstance(values, dict) and "history" in values:
                      # Explicitly copy the deque and the current value
                      data_copy[key] = {
                          "history": values["history"].copy(),
                          "current": values["current"]
                          }
                 else:
                      # Copy other top-level items like 'disk_range_max'
                      data_copy[key] = values

        # Call the graphics engine's draw method
        try:
             self.graphics.draw_frame(target_image, data_copy)
        except Exception as e:
             print(f"Error during graphics engine draw_frame: {e}")
             # Handle drawing error, maybe draw error message on image
             import traceback
             traceback.print_exc()

        return target_image


    # --- Lifecycle methods remain the same ---
    def stop(self):
        """Stops the background data collection thread."""
        print("\nStopping data collection thread...")
        self._stop_event.set()
        # Join the thread with a timeout
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            self._data_thread.join(timeout=max(1.0, self.update_interval * 2)) # Wait longer
        # Check if join succeeded
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            print("Warning: Data collection thread did not stop gracefully.")
        else:
            print("Data collection thread stopped.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

# --- Example Usage (Updated) ---
if __name__ == "__main__":
    print("Starting Prometheus Monitor Example...")
    monitor_instance = None # Define outside try for finally block
    try:
        # Create the monitor instance (which also creates the graphics engine)
        # Configuration is read from constants defined above
        monitor_instance = PrometheusMonitorGenerator()

        # Create the initial blank image canvas
        img = Image.new('RGB', RESOLUTION, color=COLORS["background"])

        frame_count = 0
        max_frames = 120 # Generate 120 frames (2 minutes)
        print(f"Generating up to {max_frames} frames (press Ctrl+C to stop)...")

        while frame_count < max_frames:
            start_time = time.time()

            # Use the new method to generate the frame content
            monitor_instance.generate_image_frame(img)

            # Save or display the frame
            try:
                # Save the generated frame
                img.save(f"monitor_frame_{frame_count:03d}.png")
                print(f"\rGenerated frame: {frame_count+1}/{max_frames}", end="")
            except Exception as e:
                print(f"\nError saving image frame: {e}")
                # Decide if loop should break on save error

            frame_count += 1

            # Wait appropriately before the next frame
            elapsed = time.time() - start_time
            # Use the monitor's update interval as the target frame rate
            sleep_time = max(0, monitor_instance.update_interval - elapsed)
            time.sleep(sleep_time)

        print("\nFinished generating frames.")

    # Handle specific errors if needed
    except ConnectionError as e:
         print(f"\nConnection Error: {e}")
    except RuntimeError as e:
         print(f"\nRuntime Error: {e}")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        # Catch-all for unexpected errors
        print(f"\nAn unexpected error occurred: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
    finally:
        # Ensure the monitor thread is stopped even if errors occurred
        if monitor_instance:
            print("Ensuring monitor is stopped...")
            monitor_instance.stop()
        print("Monitor example finished.")