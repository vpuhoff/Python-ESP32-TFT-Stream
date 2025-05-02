# ESP32-C3 Real-time Screen Streaming Client/Server

This project demonstrates streaming a selected region of a PC screen in real-time to an ESP32-C3 microcontroller equipped with a TFT display. It uses a Python server on the PC for capturing and processing, and an Arduino sketch on the ESP32-C3 for receiving and rendering the stream over a TCP connection. Differential updates are used to minimize latency and bandwidth usage, and server-side color correction options are included for better display fidelity.

## Features

* Streams a selected screen region from a PC (Windows/macOS/Linux).
* Displays the stream on an ESP32-C3 driven TFT screen (using the TFT_eSPI library).
* Uses TCP for low-latency communication.
* Implements differential updates (sending only changed screen regions) for efficiency.
* Handles large updates by chunking them into smaller packets.
* **Includes server-side gamma and white balance correction for fine-tuning color reproduction on the target display.**
* Python server for screen capture and processing.
* Arduino client for receiving and rendering on ESP32.
* Configurable resolution, capture area, and update rate.
* Handles connection drops and attempts reconnection.
* Visual feedback on ESP32 if server connection fails repeatedly.

## Hardware Requirements

* **ESP32-C3 Development Board:** Any ESP32-C3 based board (e.g., ESP32-C3-DevKitM-1, Seeed Studio XIAO ESP32C3, Lolin C3 Mini).
* **SPI TFT Display:** A display compatible with the [TFT_eSPI library](https://github.com/Bodmer/TFT_eSPI) (e.g., based on ILI9341, ST7789, ST7735 controllers). Common resolutions are 320x240, 240x240, etc.
* **PC:** A computer running Windows, macOS, or Linux to host the Python server.
* **Wi-Fi Network:** A router or access point that both the PC and ESP32-C3 can connect to.
* **Wiring:** Jumper wires to connect the TFT display to the ESP32-C3 via SPI.

## Software Requirements

### PC (Server)

* **Python:** Version 3.7 or higher recommended.
* **Pip:** Python package installer (usually comes with Python).
* **Python Libraries:** Install using pip:
    * `mss`: For efficient cross-platform screen capture.
    * `Pillow`: For image manipulation (resizing, format conversion).
    * `numpy`: For efficient numerical operations (used in color correction).
    * *(Optional, for specific window capture):* `pywin32` (Windows), `python-xlib` (Linux), `pyobjc-core` & `pyobjc-framework-Quartz` (macOS).

    Create a `requirements.txt` file in the server directory with the following content:
    ```
    mss
    Pillow
    numpy
    ```
    Then install using:
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
    git clone <your-repository-url>
    cd <your-repository-folder>
    ```

2.  **Configure TFT_eSPI:**
    * Locate the installed TFT_eSPI library folder (usually in your Arduino `libraries` folder).
    * Edit `User_Setup.h` (or `User_Setup_Select.h`) according to your ESP32-C3 board's SPI pins and your specific TFT display model/driver. **This step is essential for the display to work correctly.**

3.  **Configure ESP32 Client (`esp32_client/esp32_client.ino`):**
    * Open the `.ino` file in your Arduino IDE or PlatformIO.
    * Modify the following variables at the top of the file:
        * `ssid`: Your Wi-Fi network name.
        * `password`: Your Wi-Fi password.
        * `server_ip`: The **static IP address** of the PC running the Python server.
        * `server_port`: The port the server will listen on (must match Python script). Default: `8888`.
        * `PIXEL_BUFFER_SIZE`: Ensure this is large enough to hold the biggest data chunk sent by Python (must be >= `MAX_CHUNK_DATA_SIZE` in Python). Default: `10 * 1024` (10KB).
    * Select your ESP32-C3 board from the IDE's board menu.
    * Compile and upload the sketch to the ESP32-C3.

4.  **Configure Python Server (`python_server/server.py` - assuming this filename):**
    * Navigate to the server script directory.
    * Install Python dependencies if you haven't already: `pip install -r requirements.txt`
    * Edit the script and configure these settings near the top:
        * `ESP32_PORT`: The port to listen on (must match ESP32 sketch). Default: `8888`.
        * `TARGET_WIDTH`, `TARGET_HEIGHT`: The resolution of the ESP32's display (must match TFT). E.g., `320`, `240`.
        * `CAPTURE_REGION`: A dictionary defining the area on your PC screen to capture (e.g., `{'top': 100, 'left': 100, 'width': 800, 'height': 600, 'mon': 1}`). Adjust `top`, `left`, `width`, `height` as needed. `mon` is the monitor number.
        * `UPDATE_INTERVAL_SEC`: Time between screen captures (e.g., `0.1` for ~10 FPS). Lower values increase frame rate but also CPU/network load.
        * `MAX_CHUNK_DATA_SIZE`: Maximum size (in bytes) of pixel data per network packet. Must be <= `PIXEL_BUFFER_SIZE` on the ESP32. Default: `8192` (8KB).
        * **`GAMMA_VALUE`:** (e.g., `2.2`) The gamma correction value to apply before sending. Value > 1.0 darkens mid-tones/highlights. Tune experimentally (try 1.8-2.6) for best results on your specific display.
        * **`WB_SCALE`:** A tuple `(R_mult, G_mult, B_mult)` for white balance adjustment (e.g., `(1.0, 1.0, 0.95)`). Values < 1.0 reduce the intensity of a channel, > 1.0 increase it. Tune experimentally to make grays appear neutral on the display.

## Usage

1.  **Network:** Ensure the PC and ESP32-C3 are connected to the same Wi-Fi network. It's recommended to assign a static IP address to the PC running the server or use mDNS/hostname resolution if configured.
2.  **Start Server:** Open a terminal or command prompt on your PC, navigate to the `python_server` directory, and run the server script:
    ```bash
    python server.py
    ```
    The server will start listening for a connection from the ESP32.
3.  **Start Client:** Power on or reset your ESP32-C3 board.
    * It will connect to Wi-Fi.
    * It will display the initial "BIOS" screen showing its own IP address.
    * It will then attempt to connect to the configured `server_ip` and `server_port`.
4.  **Streaming:** Once the ESP32 connects to the Python server, the TFT display should start showing the content captured from the `CAPTURE_REGION` on your PC screen, with color correction applied. Updates should appear in near real-time.

## How It Works

1.  **TCP Connection:** ESP32 client connects to Python server.
2.  **Screen Capture (Python):** `mss` captures the screen region.
3.  **Resizing (Python):** `Pillow` resizes to target resolution.
4.  **Color Correction (Python):** Applies gamma correction and white balance scaling using `numpy` based on configured `GAMMA_VALUE` and `WB_SCALE` to adjust for display characteristics.
5.  **Diffing (Python):** Compares current frame to previous to find changed rectangles (`dirty_rects`).
6.  **Chunking (Python):** Splits large rectangles into smaller chunks if they exceed `MAX_CHUNK_DATA_SIZE`.
7.  **Packet Formatting (Python):** For each chunk/rectangle: converts to RGB565, packs header (X, Y, W, H, DataLen) and data using `struct` (Big-Endian).
8.  **Transmission (Python):** Sends packets over TCP.
9.  **Packet Reception (ESP32):** Reads TCP stream, parses 12-byte header (Big-Endian).
10. **Data Reading (ESP32):** Reads `DataLen` bytes into `pixelBuffer` using `readExact`.
11. **Rendering (ESP32):** Uses `tft.pushImage()` to render the received pixel data at the correct coordinates.
12. **Connection Management (ESP32):** Handles connection state, retries, and redraws initial screen on repeated failures.

## Troubleshooting

* **ESP32 Cannot Connect:** Check `server_ip`, server running, firewall, Wi-Fi network/credentials.
* **ESP32 Reboots / Crashes:** Check for Out of Memory (reduce `PIXEL_BUFFER_SIZE`), verify TFT_eSPI configuration.
* **ESP32 "Exceeds buffer size" error:** Ensure `MAX_CHUNK_DATA_SIZE` (Python) <= `PIXEL_BUFFER_SIZE` (ESP32).
* **Display Blank / Garbage:** Check TFT_eSPI config (pins, driver), wiring.
* **Color Issues (Colors, Tints, Saturation):**
    * Verify `tft.invertDisplay(true/false)` setting in ESP32 `setup()`.
    * Check TFT_eSPI configuration (`User_Setup.h`).
    * **Fine-tune `GAMMA_VALUE` and `WB_SCALE` settings in the Python server script (`server.py`)**. This is the primary method to correct color tints, saturation issues, and make grays appear neutral on your specific display. Experiment with values.
* **Slow / Laggy Performance:** High resolution/capture area, low `UPDATE_INTERVAL_SEC`, slow Wi-Fi, inefficient server processing, low TFT SPI speed.

## TODO / Potential Improvements

* Improve the `find_dirty_rects` algorithm on the server (e.g., merging rectangles).
* Implement reliable specific window capture on the server.
* Add options for different pixel formats (e.g., grayscale).
* Implement basic lossless compression (e.g., Run-Length Encoding).
* Add command-line arguments or a config file for the Python server.
* Explore using WebSockets or MQTT.
* Implement dithering during RGB565 conversion in Python for potentially smoother gradients.

## License

This project is released under the MIT License. See the LICENSE file for details.
*(Consider adding a LICENSE file with the MIT License text)*

## Acknowledgements

* [Python](https://www.python.org/)
* [mss](https://github.com/BoboTiG/python-mss) library.
* [Pillow](https://python-pillow.org/) library.
* [NumPy](https://numpy.org/) library.
* [TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) library by Bodmer.
* Espressif IoT Development Framework ([ESP-IDF](https://github.com/espressif/esp-idf)) and the [Arduino Core for ESP32](https://github.com/espressif/arduino-esp32).
