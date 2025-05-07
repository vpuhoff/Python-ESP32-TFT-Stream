# ESP32-C3 Real-time Screen/Data Streaming Client/Server

This project demonstrates streaming visual data in real-time from a PC to an ESP32-C3 microcontroller equipped with a TFT display. The Python server on the PC can capture a selected screen region, or generate various data visualizations (like a BIOS-style screen, CPU monitor, or Prometheus metrics dashboard). An Arduino sketch on the ESP32-C3 receives and renders this stream over a TCP connection. Differential updates are used for screen capture to minimize latency and bandwidth usage, and server-side color correction options are included for better display fidelity.

* Wiki: [https://deepwiki.com/vpuhoff/Python-ESP32-TFT-Stream](https://deepwiki.com/vpuhoff/Python-ESP32-TFT-Stream/1-overview)

## Features

* **Multiple Stream Sources:**
    * Streams a selected screen region from a PC (Windows/macOS/Linux).
    * Generates and streams a pseudo BIOS/POST screen.
    * Generates and streams a real-time CPU usage monitor.
    * Generates and streams a dashboard of system metrics collected from a Prometheus instance.
* Displays the stream on an ESP32-C3 driven TFT screen (using the TFT_eSPI library).
* Uses TCP for low-latency communication.
* Implements differential updates (sending only changed screen regions) for screen capture efficiency.
* Handles large updates by chunking them into smaller packets.
* Includes server-side gamma and white balance correction for fine-tuning color reproduction.
* Python server for data capture/generation and processing.
* Arduino client for receiving and rendering on ESP32.
* Configurable resolution, capture area (for screen streaming), and update rate.
* Handles connection drops and attempts reconnection with visual feedback on ESP32.
* **Prometheus Exporter:** The Python server includes a Prometheus exporter to monitor its own performance (e.g., processing times for different stages, packet sizes).

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
        * `UPDATE_INTERVAL_SEC`: Time between frame updates.
        * `MAX_CHUNK_DATA_SIZE`: Maximum size (in bytes) of pixel data per network packet.
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
    The server will start listening for a connection from the ESP32 and begin exposing its own metrics on `http://localhost:PROMETHEUS_EXPORTER_PORT`.
4.  **Start Client:** Power on or reset your ESP32-C3 board.
    * It will connect to Wi-Fi.
    * It will display an initial screen showing its IP address and connection status.
    * It will then attempt to connect to the configured `server_ip` and `server_port`.
5.  **Streaming:** Once the ESP32 connects, the TFT display should start showing the content generated or captured by the Python server.
6.  **(Optional) Monitor Server Performance**: Point your Prometheus instance to scrape `http://<PC_IP_ADDRESS>:PROMETHEUS_EXPORTER_PORT` to collect server performance metrics. Visualize them using Grafana or the Prometheus UI.

## How It Works

1.  **TCP Connection:** ESP32 client connects to Python server.
2.  **Data Generation/Capture (Python):**
    * **Screen Capture:** `mss` captures the screen region.
    * **CPU Monitor:** `psutil` and `py-cpuinfo` gather CPU data, `Pillow` draws the visualization.
    * **BIOS Screen:** `Pillow` draws a static BIOS-like image.
    * **Prometheus Monitor:** `prometheus_api_client` fetches metrics, `graphics_engine.py` (using `Pillow`) renders the dashboard.
3.  **Image Processing (Python):**
    * **Resizing:** `Pillow` resizes the image to the target ESP32 display resolution.
    * **Color Correction:** `numpy` and `Pillow` apply gamma and white balance.
    * **Diffing (for Screen Capture & potentially other dynamic modes):** An optimized `numpy`-based algorithm compares the current frame to the previous to find changed rectangles (`dirty_rects`).
4.  **Packetizing (Python):**
    * **Chunking:** Large updates are split into smaller chunks if they exceed `MAX_CHUNK_DATA_SIZE`.
    * **Formatting:** Each chunk/rectangle is converted to RGB565 pixel data. A header (X, Y, W, H, DataLen) is packed with the data using `struct` (Big-Endian).
5.  **Transmission (Python):** Packets are sent over TCP to the ESP32.
6.  **Reception & Rendering (ESP32):**
    * The ESP32 client reads the TCP stream, parsing the 12-byte header.
    * It reads the specified `DataLen` bytes of pixel data into a buffer.
    * `TFT_eSPI.pushImage()` renders the received pixel data at the correct coordinates.
7.  **Connection Management (ESP32):** The client handles connection state, retries on failure, and can redraw its initial status screen.
8.  **Server Performance Monitoring (Python):** A `prometheus-client` HTTP server exposes internal performance metrics (durations of stages, packet sizes, etc.).

## Troubleshooting

* **ESP32 Cannot Connect:** Check `server_ip` in ESP32 code, ensure the server is running on the PC, check firewall settings on PC, verify Wi-Fi network and credentials.
* **ESP32 Reboots / Crashes:** Check for Out of Memory (try reducing `PIXEL_BUFFER_SIZE` on ESP32), verify TFT_eSPI pin configuration and driver.
* **ESP32 "Exceeds buffer size" error:** Ensure `MAX_CHUNK_DATA_SIZE` (Python) is less than or equal to `PIXEL_BUFFER_SIZE` (ESP32).
* **Display Blank / Garbage / Wrong Colors:**
    * Double-check TFT_eSPI configuration (`User_Setup.h`) for correct pins and display driver.
    * Verify wiring between ESP32 and TFT.
    * Adjust `tft.invertDisplay(true/false)` in ESP32 `setup()`.
    * Fine-tune `GAMMA` and `WB_SCALE` settings in `server.py` for color accuracy.
* **Slow / Laggy Performance:**
    * If using screen capture: high resolution/large capture area, low `UPDATE_INTERVAL_SEC` (too fast).
    * Slow Wi-Fi network.
    * Inefficient server processing (check Prometheus metrics for `server.py` if enabled, particularly `diff_calculation` or specific generator times).
    * Low TFT SPI speed (configurable in `TFT_eSPI` library, but requires careful tuning).
* **Prometheus Monitor Issues:**
    * Ensure Prometheus server is running and accessible from the PC running `server.py`.
    * Verify PromQL queries in `prometheus_monitor_generator.py` are correct for your Prometheus setup and exporters.
    * Check that the necessary exporters (e.g., `node_exporter`, `windows_exporter`) are running and providing data to Prometheus.

## TODO / Potential Improvements

* Further optimize `find_dirty_rects` (e.g., merging adjacent small dirty rectangles).
* Implement reliable specific window capture on the server for screen streaming.
* Add options for different pixel formats (e.g., grayscale to reduce data).
* Implement basic lossless compression for pixel data (e.g., Run-Length Encoding).
* Make server settings configurable via command-line arguments or a configuration file.
* Explore using WebSockets or MQTT for communication as alternatives to raw TCP.
* Implement dithering during RGB565 conversion in Python for potentially smoother gradients on the TFT.
* Add more sophisticated error handling and reporting on both client and server.
* Allow dynamic selection of `IMAGE_SOURCE_MODE` without restarting the server (e.g., via a simple command interface).

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