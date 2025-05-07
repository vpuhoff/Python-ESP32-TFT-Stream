# ESP32-C3 Real-time Screen/Data Streaming Client/Server

This project demonstrates streaming visual data in real-time from a PC to an ESP32-C3 microcontroller equipped with a TFT display. The multi-threaded Python server on the PC can capture a selected screen region or generate various data visualizations (like a BIOS-style screen, CPU monitor, or Prometheus metrics dashboard). An Arduino sketch on the ESP32-C3 receives and renders this stream over a TCP connection. Differential updates with an adaptive threshold are used to minimize latency and bandwidth, optimizing for visual quality versus frame rate. Server-side color correction options are included for better display fidelity.

* Wiki: [https://deepwiki.com/vpuhoff/Python-ESP32-TFT-Stream](https://deepwiki.com/vpuhoff/Python-ESP32-TFT-Stream/1-overview)

## Features

* **Multiple Stream Sources:**
    * Streams a selected screen region from a PC (Windows/macOS/Linux).
    * Generates and streams a pseudo BIOS/POST screen.
    * Generates and streams a real-time CPU usage monitor.
    * Generates and streams a dashboard of system metrics collected from a Prometheus instance.
* Displays the stream on an ESP32-C3 driven TFT screen (using the TFT_eSPI library).
* Uses TCP for communication with `TCP_NODELAY` enabled for potentially lower latency.
* **Advanced Differential Updates:**
    * Implements differential updates (sending only changed screen regions).
    * Features an **adaptive threshold** for `dirty_rect` detection, dynamically balancing image quality and frame rate based on processing performance.
* Handles large updates by chunking them into smaller packets.
* Includes server-side gamma and white balance correction for fine-tuning color reproduction.
* **Multi-threaded Python Server:** Utilizes separate threads for frame generation and frame processing/sending, improving responsiveness and throughput.
* Arduino client for receiving and rendering on ESP32.
* Configurable resolution, capture area (for screen streaming), and target FPS for adaptive threshold.
* Handles connection drops and attempts reconnection with visual feedback on ESP32.
* **Comprehensive Prometheus Exporter:** The Python server includes a Prometheus exporter to monitor its own performance in detail (e.g., processing times for different stages, packet sizes, queue lengths, calculated FPS, adaptive threshold value).

## Hardware Requirements

* **ESP32-C3 Development Board:** Any ESP32-C3 based board (e.g., ESP32-C3-DevKitM-1, Seeed Studio XIAO ESP32C3, Lolin C3 Mini).
* **SPI TFT Display:** A display compatible with the [TFT_eSPI library](https://github.com/Bodmer/TFT_eSPI) (e.g., based on ILI9341, ST7789, ST7735 controllers). Common resolutions are 320x240, 240x240, etc.
* **PC:** A computer running Windows, macOS, or Linux to host the Python server.
* **Wi-Fi Network:** A router or access point that both the PC and ESP32-C3 can connect to.
* **Wiring:** Jumper wires to connect the TFT display to the ESP32-C3 via SPI.
* **(Optional for Prometheus Monitor):** A running Prometheus instance ([https://prometheus.io/](https://prometheus.io/)) collecting desired system metrics (e.g., via `node_exporter`, `windows_exporter`, `nvidia_gpu_exporter`).

## Software Requirements

### PC (Server)

* **Python:** Version 3.7 or higher recommended.
* **Pip:** Python package installer (usually comes with Python).
* **Python Libraries:** Install using `pip install -r requirements.txt`. Key libraries include:
    * `mss`: For efficient cross-platform screen capture.
    * `Pillow`: For image manipulation (resizing, format conversion, drawing).
    * `numpy`: For efficient numerical operations (used in color correction and optimized diffing).
    * `psutil`: For CPU utilization metrics (used by the CPU monitor).
    * `py-cpuinfo`: For fetching CPU name (used by the CPU monitor).
    * `prometheus_api_client`: For querying a Prometheus instance (used by the Prometheus monitor generator).
    * `prometheus-client`: For exposing the server's own performance metrics.
    * *(Optional, for specific window capture):* `pywin32` (Windows), `python-xlib` (Linux), `pyobjc-core` & `pyobjc-framework-Quartz` (macOS).

    ```bash
    pip install -r requirements.txt
    ```

### ESP32 (Client)

* **Arduino IDE** (version 1.8.19 or 2.x) OR **PlatformIO IDE** (within VS Code).
* **ESP32 Arduino Core:** Board support package for ESP32. Install via the Boards Manager in Arduino IDE or PlatformIO's interface.
* **TFT_eSPI Library:** Install via the Arduino Library Manager or manually from [Bodmer's GitHub](https://github.com/Bodmer/TFT_eSPI).
    * **Crucially, you MUST configure TFT_eSPI for your specific ESP32 board and TFT display.** This involves editing the library's `User_Setup.h` file (or selecting the correct setup in `User_Setup_Select.h`) to define the correct pins (MOSI, SCLK, CS, DC, RST, BL - if used) and the display driver chip (e.g., `ILI9341_DRIVER`, `ST7789_DRIVER`).

## Setup & Installation

1.  **Clone Repository:**
    ```bash
    git clone [https://github.com/vpuhoff/Python-ESP32-TFT-Stream.git](https://github.com/vpuhoff/Python-ESP32-TFT-Stream.git) esp32_stream
    cd esp32_stream
    ```

2.  **Configure TFT_eSPI:**
    * Locate the installed TFT_eSPI library folder (usually in your Arduino `libraries` folder).
    * Edit `User_Setup.h` (or `User_Setup_Select.h`) according to your ESP32-C3 board's SPI pins and your specific TFT display model/driver. **This step is essential for the display to work correctly.**

3.  **Configure ESP32 Client (`esp32.ino.txt` or your `.ino` file):**
    * Open the `.ino` file in your Arduino IDE or PlatformIO.
    * Modify the following variables at the top of the file:
        * `ssid`: Your Wi-Fi network name.
        * `password`: Your Wi-Fi password.
        * `server_ip`: The **static IP address** of the PC running the Python server.
        * `server_port`: The port the server will listen on (must match Python script). Default: `8888`.
        * `PIXEL_BUFFER_SIZE`: Ensure this is large enough to hold the biggest data chunk sent by Python (must be >= `MAX_CHUNK_DATA_SIZE` in Python). Default: `10 * 1024` (10KB).
    * Select your ESP32-C3 board from the IDE's board menu.
    * Compile and upload the sketch to the ESP32-C3.

4.  **Configure Python Server (`server.py`):**
    * Navigate to the server script directory.
    * Install Python dependencies if you haven't already: `pip install -r requirements.txt`
    * Edit the script and configure these settings near the top:
        * `IMAGE_SOURCE_MODE`: Choose the desired source: `"SCREEN_CAPTURE"`, `"BIOS"`, `"CPU_MONITOR"`, or `"PROMETHEUS_MONITOR"`.
        * `PROMETHEUS_EXPORTER_PORT`: Port for the server's own performance metrics (default: `8000`).
        * `ESP32_PORT`: The port to listen on for ESP32 connections (must match ESP32 sketch). Default: `8888`.
        * `TARGET_WIDTH`, `TARGET_HEIGHT`: The resolution of the ESP32's display.
        * `GENERATOR_TARGET_INTERVAL_SEC`: Approximate interval for the frame generation thread (e.g., `0.05` for 20 FPS target generation rate if resources allow).
        * `MAX_CHUNK_DATA_SIZE`: Maximum size (in bytes) of pixel data per network packet.
        * **Adaptive Threshold Settings:**
            * `TARGET_FPS`: Desired FPS for the consumer thread, influences threshold adaptation.
            * `MIN_DIRTY_RECT_THRESHOLD`, `MAX_DIRTY_RECT_THRESHOLD`: Range for the adaptive threshold.
            * `THRESHOLD_ADJUSTMENT_STEP_UP`, `THRESHOLD_ADJUSTMENT_STEP_DOWN`: How aggressively the threshold changes.
            * `FPS_HISTORY_SIZE`, `FPS_HYSTERESIS_FACTOR`: Parameters for FPS calculation and adaptation stability.
        * **For Screen Capture:**
            * `CAPTURE_REGION`: Dictionary defining the screen area to capture.
        * **For Prometheus Monitor:**
            * In `prometheus_monitor_generator.py`, configure `PROMETHEUS_URL` and review `METRIC_CONFIG` for your desired metrics and queries.
        * **Color Correction (applied to all visual streams):**
            * `GAMMA`: Gamma correction value.
            * `WB_SCALE`: Tuple for white balance adjustment `(R_mult, G_mult, B_mult)`.

## Usage

1.  **Network:** Ensure the PC and ESP32-C3 are connected to the same Wi-Fi network. It's recommended to assign a static IP address to the PC running the server.
2.  **(Optional) Prometheus Setup**: If using `PROMETHEUS_MONITOR` mode, ensure your Prometheus instance is running and scraping the necessary exporters.
3.  **Start Server:** Open a terminal or command prompt on your PC, navigate to the script directory, and run the server:
    ```bash
    python server.py
    ```
    The server will start listening for a connection from the ESP32 and begin exposing its own metrics on `http://localhost:PROMETHEUS_EXPORTER_PORT` (or your PC's IP).
4.  **Start Client:** Power on or reset your ESP32-C3 board.
    * It will connect to Wi-Fi.
    * It will display an initial screen showing its IP address and connection status.
    * It will then attempt to connect to the configured `server_ip` and `server_port`.
5.  **Streaming:** Once the ESP32 connects, the TFT display should start showing the content generated or captured by the Python server.
6.  **(Optional) Monitor Server Performance**: Point your Prometheus instance to scrape `http://<PC_IP_ADDRESS>:PROMETHEUS_EXPORTER_PORT` to collect server performance metrics. Visualize them using Grafana or the Prometheus UI. Key metrics include `esp32_consumer_calculated_fps` and `esp32_current_dynamic_threshold`.

## How It Works

1.  **TCP Connection:** ESP32 client connects to Python server. `TCP_NODELAY` is enabled.
2.  **Multi-threaded Server Architecture (Python):**
    * **Frame Generation Thread:** Captures or generates "raw" image frames based on `IMAGE_SOURCE_MODE`.
        * Screen Capture: `mss` captures the screen region.
        * CPU Monitor: `psutil` and `py-cpuinfo` gather data; `Pillow` draws.
        * BIOS Screen: `Pillow` draws.
        * Prometheus Monitor: `prometheus_api_client` fetches metrics; `graphics_engine.py` renders.
        * Generated frames are placed into a thread-safe queue.
    * **Frame Consumer Thread:**
        * Retrieves raw frames from the queue.
        * **Image Processing:**
            * Resizing: `Pillow` resizes to target ESP32 resolution.
            * Color Correction: `numpy` and `Pillow` apply gamma/white balance.
            * Diffing: An `numpy`-based algorithm compares with the previous frame to find `dirty_rects` using an **adaptive threshold** based on current processing FPS.
        * **Packetizing & Transmission:**
            * Changed regions are converted to RGB565.
            * Large updates are chunked.
            * Headers (X, Y, W, H, DataLen) are packed.
            * Packets sent via TCP to ESP32.
        * **Adaptive Threshold Control:** Adjusts the `dirty_rect` threshold to maintain a target FPS.
3.  **Reception & Rendering (ESP32):**
    * Client reads TCP stream, parses header, reads pixel data.
    * `TFT_eSPI.pushImage()` renders data.
4.  **Connection Management (ESP32):** Handles connection state and retries.
5.  **Server Performance Monitoring (Python):** `prometheus-client` exposes internal metrics (stage durations, FPS, threshold, queue size, etc.).

## Troubleshooting

* **ESP32 Cannot Connect:** Check `server_ip` in ESP32 code, server running, PC firewall, Wi-Fi.
* **ESP32 Reboots / Crashes:** Check for Out of Memory (try reducing `PIXEL_BUFFER_SIZE` on ESP32), verify TFT_eSPI pin configuration and driver.
* **ESP32 "Exceeds buffer size" error:** Ensure `MAX_CHUNK_DATA_SIZE` (Python) <= `PIXEL_BUFFER_SIZE` (ESP32).
* **Display Blank / Garbage / Wrong Colors:**
    * **Crucial: Double-check TFT_eSPI configuration (`User_Setup.h`) for pins and driver.**
    * Verify wiring.
    * Adjust `tft.invertDisplay(true/false)` in ESP32 `setup()`.
    * Fine-tune `GAMMA` and `WB_SCALE` in `server.py`.
* **Slow / Laggy Performance / High Latency:**
    * Check server Prometheus metrics: `esp32_consumer_calculated_fps` (is it near `TARGET_FPS`?), `esp32_dirty_rects_send_duration_seconds` (is network send slow?), `esp32_current_dynamic_threshold` (is it very high, indicating struggle?).
    * High resolution/large capture area, or very frequent updates from the source generator.
    * Slow Wi-Fi.
    * Inefficient ESP32-side processing/rendering.
    * Try adjusting `TARGET_FPS` and threshold range (`MIN_DIRTY_RECT_THRESHOLD`, `MAX_DIRTY_RECT_THRESHOLD`) in `server.py`.
* **Too Many Artifacts / Blocky Updates:**
    * The `current_dynamic_threshold` might be too high. Try increasing `TARGET_FPS` to encourage a lower threshold, or narrow the `MAX_DIRTY_RECT_THRESHOLD`.
* **Prometheus Monitor Issues:**
    * Prometheus server running and accessible.
    * Correct PromQL queries in `prometheus_monitor_generator.py`.
    * Necessary exporters running.
* **`mss` related errors in `frame_generator_thread_func`:** Ensure `mss` is correctly initialized within the thread if capturing screen. The current code does this.

## TODO / Potential Improvements

* Further optimize `find_dirty_rects` (e.g., merging adjacent small dirty rectangles).
* Implement reliable specific window capture on the server.
* Add options for different pixel formats (e.g., grayscale).
* Explore simple lossless compression (e.g., Run-Length Encoding) if beneficial.
* Make server settings configurable via command-line arguments or a configuration file.
* Implement dithering during RGB565 conversion for smoother gradients.
* More sophisticated error handling and reporting.
* Allow dynamic selection of `IMAGE_SOURCE_MODE` without restarting the server.
* Refine the adaptive threshold algorithm for smoother transitions and better target FPS adherence.

## License

This project is released under the MIT License.

## Acknowledgements

* [Python](https://www.python.org/)
* [mss](https://github.com/BoboTiG/python-mss) library
* [Pillow](https://python-pillow.org/) library
* [NumPy](https://numpy.org/) library
* [psutil](https://github.com/giampaolo/psutil) library
* [py-cpuinfo](https://github.com/workhorsy/py-cpuinfo) library
* [Prometheus Python Client](https://github.com/prometheus/client_python)
* [Prometheus API Client Python](https://github.com/prometheus-community/prometheus-api-client-python)
* [TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) library by Bodmer
* Espressif IoT Development Framework ([ESP-IDF](https://github.com/espressif/esp-idf)) and the [Arduino Core for ESP32](https://github.com/espressif/arduino-esp32)