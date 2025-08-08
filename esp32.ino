/********************************************************************
 *  ESP32-C3  TFT Screen Streamer (BIOS-style UI)
 *******************************************************************/
#include <algorithm>
#include <SPI.h>
#include <TFT_eSPI.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

/* ---------- TFT ---------- */
TFT_eSPI tft = TFT_eSPI();

/* ---------- Wi-Fi ---------- */
const char* ssid     = "DarkNet 2G";
const char* password = "Integral320";

/* ---------- TCP ---------- */
const char*   server_ip   = "192.168.0.156";
const uint16_t server_port = 8888;
WiFiClient client;

/* ---------- буфер ---------- */
const size_t PIXEL_BUFFER_SIZE = 10 * 1024;
uint8_t pixelBuffer[PIXEL_BUFFER_SIZE];

/* ---------- контроль сбоев ---------- */
int  connection_failure_count = 0;
const int MAX_CONNECTION_FAILURES       = 10;
const unsigned long COOLDOWN_AFTER_MAX_FAILS_MS = 10000UL;   // 10 с

/* ---------- WDT ---------- */
unsigned long lastFeed = 0;

/* ---------- цвета BIOS UI ---------- */
#define BIOS_BACKGROUND   TFT_BLUE
#define BIOS_TEXT_NORMAL  TFT_LIGHTGREY
#define BIOS_TEXT_VALUE   TFT_WHITE
#define BIOS_HIGHLIGHT    TFT_WHITE
#define BIOS_HEADER_BG    BIOS_BACKGROUND
#define BIOS_FOOTER_BG    BIOS_BACKGROUND

/* ---------- геометрия UI ---------- */
const int header_h        = 20;
const int footer_h        = 12;
const int padding         = 5;
const int line_spacing    = 12;
const int label_spacing   = 14;
const int value_x_offset  = 120;

/* ---------- прототипы ---------- */
void  draw_bios_screen();
bool  readExact(WiFiClient& cl, uint8_t* buf, size_t count, uint32_t timeout_ms = 2000);

/* ==================================================================== */
void setup() {
  Serial.begin(115200);
  while (!Serial) {}

  tft.init();
  tft.setRotation(3);
  tft.invertDisplay(true);
#ifdef TFT_BL
  pinMode(TFT_BL, OUTPUT); digitalWrite(TFT_BL, TFT_BACKLIGHT_ON);
#endif

  WiFi.begin(ssid, password);
  for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; ++i) {
    delay(500); Serial.print('.');
  }
  if (WiFi.status() != WL_CONNECTED) {
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_RED);
    tft.setTextDatum(MC_DATUM);
    tft.drawString("WiFi Connection Failed!", tft.width()/2, tft.height()/2, 4);
    while (true) delay(1000);
  }

  draw_bios_screen();
  esp_task_wdt_init(10, true);
  esp_task_wdt_add(nullptr);
}

/* ==================================================================== */
void loop() {

  /* ---------- (re)connect ---------- */
  if (!client.connected()) {
    Serial.printf("Connecting to %s:%d ...\n", server_ip, server_port);
    if (client.connect(server_ip, server_port)) {
      connection_failure_count = 0;
      Serial.println("TCP connected.");
    } else {
      ++connection_failure_count;
      Serial.printf("Fail %d/%d\n", connection_failure_count, MAX_CONNECTION_FAILURES);
      if (connection_failure_count >= MAX_CONNECTION_FAILURES) {
        draw_bios_screen();
        delay(COOLDOWN_AFTER_MAX_FAILS_MS);
        connection_failure_count = 0;
      } else {
        delay(5000);
      }
      return;
    }
  }

  /* ---------- приём данных ---------- */
  const size_t HEADER_SIZE = 12;
  if (client.available() >= HEADER_SIZE) {
    uint8_t header[HEADER_SIZE];
    if (!readExact(client, header, HEADER_SIZE, 500)) { client.stop(); return; }

    uint16_t x = (header[0]<<8)|header[1];
    uint16_t y = (header[2]<<8)|header[3];
    uint16_t w = (header[4]<<8)|header[5];
    uint16_t h = (header[6]<<8)|header[7];
    uint32_t dataLen = (uint32_t)header[8]<<24 |
                       (uint32_t)header[9]<<16 |
                       (uint32_t)header[10]<<8 |
                       header[11];

    if (dataLen > PIXEL_BUFFER_SIZE) {                 // защита
      uint8_t dump[64];
      size_t skipped = 0;
      while (skipped < dataLen && client.connected()) {
        size_t chunk = std::min<size_t>(sizeof(dump), dataLen - skipped); // ★ FIX
        skipped += client.read(dump, chunk);
      }
      client.stop();
      return;
    }

    if (dataLen && w && h) {
      if (!readExact(client, pixelBuffer, dataLen)) { client.stop(); return; }
      tft.pushImage(x, y, w, h, reinterpret_cast<uint16_t*>(pixelBuffer));
    }
  }

  /* ---------- WDT ---------- */
  if (millis() - lastFeed > 500) { esp_task_wdt_reset(); lastFeed = millis(); }
}

/* ==================================================================== */
bool readExact(WiFiClient& cl, uint8_t* buf, size_t count, uint32_t timeout_ms) {
  size_t got = 0;
  unsigned long start = millis();
  while (got < count && millis() - start < timeout_ms) {
    if (!cl.connected()) return false;
    size_t avail = cl.available();
    if (avail) got += cl.read(buf + got,
                              std::min<size_t>(avail, count - got));
    yield();
  }
  return got == count;
}

/* ==================================================================== */
void draw_bios_screen() {
  int w = tft.width(), h = tft.height(), y = 0;

  tft.fillScreen(BIOS_BACKGROUND);

  /* header */
  tft.setTextFont(2);
  tft.setTextColor(BIOS_HIGHLIGHT, BIOS_HEADER_BG);
  tft.setCursor(padding + 5, y + 4); tft.print(" Main ");
  int x = tft.getCursorX();
  tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_HEADER_BG);
  tft.setCursor(x + 10, y + 4); tft.print("Advanced");
  x = tft.getCursorX();
  tft.setCursor(x + 10, y + 4); tft.print("H/W Monitor");
  x = tft.getCursorX();
  tft.setCursor(x + 10, y + 4); tft.print("Boot");
  x = tft.getCursorX();
  tft.setCursor(x + 10, y + 4); tft.print("Security");

  y += header_h;
  tft.drawFastHLine(0, y, w, TFT_DARKGREY);
  y += padding;

  /* left panel */
  tft.setTextFont(2);
  tft.setTextColor(BIOS_TEXT_VALUE);
  tft.setCursor(padding, y); tft.print("System Overview");
  y += tft.fontHeight(2) + padding;

  auto label = [&](const char* txt){
    tft.setTextColor(BIOS_TEXT_NORMAL);
    tft.setCursor(padding, y); tft.print(txt);
  };
  auto value = [&](const String& v){
    tft.setTextColor(BIOS_TEXT_VALUE);
    tft.setCursor(value_x_offset, y); tft.print(v);
    y += label_spacing;
  };

  label("System Time");  value("[--:--:--]");
  label("System Date");  value("[--/--/----]");
  label("BIOS Version"); value("ESP32-C3 TFT V1.3");
  label("Processor Type");  value("ESP32-C3 RISC-V");
  label("Processor Speed"); value("160 MHz");
  label("Total Memory");    value("4 MB Flash");
  label("IP Address");      value("[" + WiFi.localIP().toString() + "]");
  label("Server Status");
  value(client.connected() ?
        "[Connected]" :
        "[FAIL " + String(connection_failure_count) + "/" +
        String(MAX_CONNECTION_FAILURES) + "]");

  /* footer */
  y = h - footer_h - padding;
  tft.drawFastHLine(0, y, w, TFT_DARKGREY);
  y += padding;
  tft.setTextColor(BIOS_TEXT_VALUE);
  tft.setTextFont(1);
  tft.setTextDatum(BC_DATUM);
  tft.drawString(client.connected() ?
                 "Receiving Screen Stream..." :
                 "Attempting connection to host...",
                 w / 2, h - 2, 1);
  tft.setTextDatum(TL_DATUM);
}
