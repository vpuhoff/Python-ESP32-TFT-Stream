#include <SPI.h>
#include <TFT_eSPI.h> // Используем библиотеку TFT_eSPI
#include <WiFi.h>     // Библиотека для работы с Wi-Fi

// Создание объекта TFT_eSPI
TFT_eSPI tft = TFT_eSPI();

// --- Учетные данные Wi-Fi ---
// !!! ЗАМЕНИТЕ НА ВАШИ ДАННЫЕ !!!
const char* ssid = "****************"; // Имя вашей сети Wi-Fi
const char* password = "************"; // Пароль вашей сети Wi-Fi
// ----------------------------

// --- Адрес и порт Python сервера ---
// !!! ЗАМЕНИТЕ НА IP АДРЕС ПК С PYTHON СКРИПТОМ !!!
const char* server_ip = "192.168.0.156";
const uint16_t server_port = 8888;
// ---------------------------------

// --- Сетевой клиент ---
WiFiClient client;
// ---------------------

// --- Буфер для приема пиксельных данных ---
// Определите размер буфера в байтах. Должен быть >= W * H * 2 для САМОГО БОЛЬШОГО
// ожидаемого пакета от Python. Если Python шлет чанки по 8КБ, этого хватит.
const size_t PIXEL_BUFFER_SIZE = 10 * 1024; // 10 КБайт
uint8_t pixelBuffer[PIXEL_BUFFER_SIZE];
// -----------------------------------------

// --- Счетчик неудачных подключений ---
int connection_failure_count = 0;
const int MAX_CONNECTION_FAILURES = 10; // Порог для перерисовки
// ------------------------------------

// --- Определим цвета для удобства (учитывая инверсию) ---
#define BIOS_BACKGROUND TFT_BLUE        // Станет ~желтым/светлым
#define BIOS_TEXT_NORMAL TFT_LIGHTGREY // Станет темным серо-синим
#define BIOS_TEXT_VALUE TFT_WHITE     // Станет черным
#define BIOS_HIGHLIGHT TFT_WHITE      // Выделение в меню (станет черным текстом на фоне)
#define BIOS_HEADER_BG BIOS_BACKGROUND // Фон хедера
#define BIOS_FOOTER_BG BIOS_BACKGROUND // Фон футера

// --- Глобальные переменные для позиционирования ---
const int header_h = 20;
const int footer_h = 12;
const int padding = 5;
const int line_spacing = 12; // Базовый интервал
const int label_spacing = 14; // Интервал для строк с метками/значениями
const int value_x_offset = 120; // X-позиция значений в левой панели

// --- Объявления функций ---
void draw_bios_screen();
bool readExact(WiFiClient& client, uint8_t* buf, size_t count, uint32_t timeout_ms = 2000);

// =========================================================================
// ===                          SETUP                                    ===
// =========================================================================
void setup() {
    Serial.begin(115200);
    while (!Serial); // Дождаться открытия Serial Monitor (для некоторых плат)
    Serial.println("\n--- ESP32 TFT Stream Receiver ---");

    // --- Инициализация TFT ---
    Serial.print("Initializing TFT...");
    tft.init();
    Serial.println(" Done.");
    tft.setRotation(3); // Ландшафтная ориентация
    Serial.print("Inverting display colors...");
    tft.invertDisplay(true); // Включаем инверсию, на этом конкретном дисплее нормальные цвета только в режиме инверсии
    Serial.println(" Done.");

    // Включение подсветки (если пин определен в User_Setup.h)
#ifdef TFT_BL
    pinMode(TFT_BL, OUTPUT);
    digitalWrite(TFT_BL, TFT_BACKLIGHT_ON);
    Serial.println("Backlight ON.");
#endif

    // --- Подключение к Wi-Fi ---
    Serial.printf("Connecting to WiFi SSID: %s ", ssid);
    WiFi.begin(ssid, password);
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) { // Ожидаем подключения (макс. ~10 сек)
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nFailed to connect to WiFi. Cannot proceed.");
        // Отобразить ошибку на экране
        tft.fillScreen(TFT_BLACK); // Используем стандартный черный
        tft.setTextColor(TFT_RED); // Используем стандартный красный
        tft.setTextDatum(MC_DATUM); // Выравнивание по центру
        tft.drawString("WiFi Connection Failed!", tft.width() / 2, tft.height() / 2, 4); // Шрифт 4
        // Остановка выполнения
        while (1) { delay(1000); }
    }
    // --------------------------

    // Отрисовка начального экрана BIOS
    Serial.print("Drawing initial BIOS screen...");
    draw_bios_screen();
    Serial.println(" Done.");

    Serial.println("Setup complete. Waiting for server connection and data...");
}

// =========================================================================
// ===                           LOOP                                    ===
// =========================================================================
void loop() {
    // 1. Проверяем и устанавливаем соединение с сервером
    if (!client.connected()) {

        // Сообщение в Serial, если соединение было потеряно (не при самой первой попытке)
        if(connection_failure_count > 0) {
            Serial.println("Connection lost or previously failed.");
        }

        Serial.printf("Attempting to connect to server %s:%d (Attempt %d)...\n",
                      server_ip, server_port, connection_failure_count + 1);

        // Попытка подключения
        if (client.connect(server_ip, server_port)) {
            Serial.println("Connected to server!");
            // УСПЕХ: Сброс счетчика неудач
            connection_failure_count = 0;
            // Опционально: Запросить полный кадр при подключении, если сервер это умеет
            // client.print("FULL_FRAME_REQUEST"); // Пример команды
        } else {
            Serial.println("Connection failed.");
            // НЕУДАЧА: Увеличиваем счетчик и проверяем порог
            connection_failure_count++;
            Serial.printf("Consecutive failures: %d / %d\n", connection_failure_count, MAX_CONNECTION_FAILURES);

            if (connection_failure_count >= MAX_CONNECTION_FAILURES) {
                // Порог достигнут - перерисовываем экран
                Serial.printf("Max connection failures (%d) reached. Redrawing initial screen.\n", MAX_CONNECTION_FAILURES);
                draw_bios_screen(); // Перерисовываем BIOS-экран
                connection_failure_count = 0; // Сбрасываем счетчик после перерисовки
                Serial.println("Waiting 5 seconds before next connection attempt cycle...");
                delay(5000); // Дополнительная пауза после перерисовки
            } else {
                 // Обычная пауза перед следующей попыткой
                 Serial.println("Retrying connection in 5 seconds...");
                 delay(5000);
            }
            // Выходим из текущей итерации loop, чтобы не пытаться читать данные
            return;
        }
    } // Конец блока if (!client.connected())

    // --- Если мы здесь, клиент ПОДКЛЮЧЕН ---

    // 2. Проверяем наличие данных от сервера
    // Заголовок: X(2B)+Y(2B)+W(2B)+H(2B)+DataLen(4B) = 12 байт
    const size_t HEADER_SIZE = 12;
    if (client.available() >= HEADER_SIZE) {
        uint8_t header[HEADER_SIZE];

        // Читаем заголовок
        if (!readExact(client, header, HEADER_SIZE, 500)) { // Таймаут 500мс
             Serial.println("Failed to read header or client disconnected during read.");
             client.stop(); // Закрываем соединение
             return; // Перейдем к попытке переподключения в следующей итерации
        }

        // Разбираем заголовок (Big-Endian от Python)
        uint16_t x = ((uint16_t)header[0] << 8) | header[1];
        uint16_t y = ((uint16_t)header[2] << 8) | header[3];
        uint16_t w = ((uint16_t)header[4] << 8) | header[5];
        uint16_t h = ((uint16_t)header[6] << 8) | header[7];
        uint32_t dataLen = ((uint32_t)header[8]  << 24) |
                           ((uint32_t)header[9]  << 16) |
                           ((uint32_t)header[10] << 8)  |
                           header[11];

        // Debug: можно раскомментировать для отладки заголовков
        // Serial.printf("Received Header: Rect(%u, %u, %u x %u), DataLen: %u bytes\n", x, y, w, h, dataLen);

        // Проверка размера данных на соответствие буферу
        if (dataLen > PIXEL_BUFFER_SIZE) {
            Serial.printf("Error: Received dataLen (%u) exceeds buffer size (%u)!\n", dataLen, PIXEL_BUFFER_SIZE);
             Serial.println("Discarding data and closing connection to prevent overflow.");
             // Попытка прочитать и отбросить данные из TCP буфера клиента
             uint32_t discarded = 0;
             unsigned long discard_start = millis();
             uint8_t dummy_buf[64]; // Маленький буфер для чтения мусора
             while(discarded < dataLen && millis() - discard_start < 1000) { // Таймаут на отбрасывание 1 сек
                 size_t available_now = client.available();
                 if(available_now > 0) {
                     size_t to_read = min((size_t)64, available_now);
                     to_read = min(to_read, (size_t)(dataLen - discarded));  // Не читать больше чем dataLen
                     size_t read_count = client.read(dummy_buf, to_read);
                     if (read_count > 0) {
                         discarded += read_count;
                     } else {
                         break; // Ошибка чтения или 0 байт
                     }
                 } else {
                     if (!client.connected()) break; // Выход если отключился
                     yield();
                 }
             }
             Serial.printf("Discarded approx %u bytes.\n", discarded);
             client.stop(); // Закрыть соединение
             return; // Переход к переподключению
        }

        // Проверка на нулевые размеры или длину данных
        if ((w == 0 || h == 0) && dataLen > 0) {
             Serial.printf("Warning: Received zero dimension (w=%u, h=%u) but dataLen=%u?\n", w, h, dataLen);
             // Прочитаем и отбросим данные, чтобы не нарушать поток
             readExact(client, pixelBuffer, dataLen, 500); // Используем буфер для отбрасывания
             // Продолжаем работу, не отображая
        } else if (dataLen == 0 && (w > 0 && h > 0)) {
             Serial.printf("Warning: Received non-zero dimension (w=%u, h=%u) but dataLen=0.\n", w, h);
             // Ничего не делаем, пропускаем
        } else if (dataLen > 0 && w > 0 && h > 0) {
            // Ожидаемый размер для RGB565
             uint32_t expectedDataLen = (uint32_t)w * h * 2;
             if (dataLen != expectedDataLen) {
                 Serial.printf("Warning: Received dataLen (%u) != expected (%ux%ux2 = %u)! Using received len.\n",
                               dataLen, w, h, expectedDataLen);
             }
             // Читаем пиксельные данные в буфер
             // Debug: можно раскомментировать
             // Serial.printf("Reading %u bytes of pixel data...\n", dataLen);
             if (readExact(client, pixelBuffer, dataLen)) {
                 // Отображаем полученную область
                 // Данные в pixelBuffer должны быть RGB565 (по 2 байта на пиксель)
                 tft.pushImage(x, y, w, h, (uint16_t*)pixelBuffer);
                 // Debug: можно раскомментировать
                 // Serial.printf("Displayed image at (%u, %u) size %ux%u\n", x, y, w, h);
             } else {
                 Serial.println("Failed to read pixel data or client disconnected during read.");
                 client.stop(); // Закрываем соединение
                 return; // Переход к переподключению
             }
        }
        // Если dataLen=0 и w=0/h=0, просто ничего не делаем

    } else {
      // Недостаточно данных для заголовка, ждем
      delay(1); // Небольшая пауза, чтобы не загружать CPU на 100%
    }

     yield(); // Даем шанс поработать другим процессам (WiFi и т.д.)
}

// =========================================================================
// ===                 Функция чтения точного числа байт                 ===
// =========================================================================
bool readExact(WiFiClient& client, uint8_t* buf, size_t count, uint32_t timeout_ms) {
    size_t received = 0;
    unsigned long start_time = millis();
    while (received < count && millis() - start_time < timeout_ms) {
        // Проверка соединения внутри цикла
        if (!client.connected()) {
            // Serial.println("readExact: Client disconnected during read!");
            return false;
        }

        size_t available_now = client.available();
        if (available_now > 0) {
            size_t bytes_to_read = min(available_now, count - received);
            size_t bytes_read = client.read(buf + received, bytes_to_read);

             if (bytes_read > 0) {
                received += bytes_read;
                start_time = millis(); // Сбрасываем таймер только при получении данных
            } else if (bytes_read < 0) {
                 // Ошибка чтения из сокета
                 Serial.println("readExact: Read error from socket!");
                 return false;
            }
            // Если bytes_read == 0, это не ошибка, просто нет данных прямо сейчас
        }
         // Даем шанс другим задачам, особенно если ждем данных
         yield();
    }
    // Возвращаем true только если прочитали ВСЕ запрошенные байты
    if (received != count) {
         // Serial.printf("readExact: Timeout! Received %u / %u bytes.\n", received, count);
    }
    return received == count;
}


// =========================================================================
// ===              Функция отрисовки экрана в стиле BIOS              ===
// =========================================================================
void draw_bios_screen() {
    int screen_w = tft.width();
    int screen_h = tft.height();
    int current_y = 0; // Текущая Y позиция для отрисовки

    // 1. Заливка фона
    tft.fillScreen(BIOS_BACKGROUND);

    // 2. Хедер (меню)
    tft.setTextFont(2); // Устанавливаем шрифт для хедера
    tft.setTextColor(BIOS_HIGHLIGHT, BIOS_HEADER_BG); // Цвет выделенного пункта
    tft.setCursor(padding + 5, current_y + 4); // Отступы для красоты
    tft.print(" Main "); // Первый пункт - выделен
    int current_x = tft.getCursorX(); // Запоминаем X позицию

    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_HEADER_BG); // Цвет обычных пунктов
    tft.setCursor(current_x + 10, current_y + 4); tft.print("Advanced"); current_x = tft.getCursorX();
    tft.setCursor(current_x + 10, current_y + 4); tft.print("H/W Monitor"); current_x = tft.getCursorX();
    tft.setCursor(current_x + 10, current_y + 4); tft.print("Boot"); current_x = tft.getCursorX();
    tft.setCursor(current_x + 10, current_y + 4); tft.print("Security");

    current_y += header_h; // Сдвигаем Y под хедер
    // Рисуем линию под хедером (станет светлой из-за инверсии)
    tft.drawFastHLine(0, current_y, screen_w, TFT_DARKGREY);
    current_y += padding; // Добавляем отступ после линии

    // 3. Основная область
    // --- Левая панель ---
    tft.setTextFont(2); // Шрифт для основного текста
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND); // Цвет заголовка панели (станет черным)
    tft.setCursor(padding, current_y);
    tft.print("System Overview");
    current_y += tft.fontHeight(2) + padding; // Сдвиг Y под заголовок панели

    // Пункты информации (Метка: Значение)
    // Время
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND); // Цвет метки (темный)
    tft.setCursor(padding, current_y); tft.print("System Time");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND); // Цвет значения (черный)
    tft.setCursor(value_x_offset, current_y); tft.print("[--:--:--]"); // Будет перекрыто
    current_y += label_spacing;
    // Дата
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("System Date");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y); tft.print("[--/--/----]"); // Будет перекрыто
    current_y += label_spacing;
    // Версия BIOS
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("BIOS Version");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y); tft.print("ESP32-C3 TFT V1.2"); // Немного обновим версию :)
    current_y += label_spacing;
    // Тип процессора
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("Processor Type");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y); tft.print("ESP32-C3 RISC-V");
    current_y += label_spacing;
    // Частота процессора
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("Processor Speed");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y); tft.print("160MHz");
    current_y += label_spacing;
    // Память
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("Total Memory");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y); tft.print("4MB Flash");
    current_y += label_spacing;
    // IP Адрес
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("IP Address");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y);
    if (WiFi.status() == WL_CONNECTED) {
        String ipStr = "[" + WiFi.localIP().toString() + "]";
        tft.print(ipStr);
    } else {
        tft.print("[Connecting WiFi...]"); // Если мы здесь, значит WiFi уже есть
    }
    current_y += label_spacing;
    // Статус сервера
    tft.setTextColor(BIOS_TEXT_NORMAL, BIOS_BACKGROUND);
    tft.setCursor(padding, current_y); tft.print("Server Status");
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_BACKGROUND);
    tft.setCursor(value_x_offset, current_y);
    // Отображаем статус в зависимости от текущего состояния клиента
    if (client.connected()) {
        tft.print("[Connected]      "); // Пробелы для затирания пред. текста
    } else {
        tft.printf("[FAIL %d/%d]     ", connection_failure_count, MAX_CONNECTION_FAILURES); // Показываем счетчик ошибок
    }
    current_y += label_spacing;

    // 4. Футер
    current_y = screen_h - footer_h - padding; // Рассчитываем Y позицию для линии над футером
    // Линия над футером
    tft.drawFastHLine(0, current_y, screen_w, TFT_DARKGREY);
    current_y += padding; // Отступ после линии

    // Текст футера
    tft.setTextColor(BIOS_TEXT_VALUE, BIOS_FOOTER_BG); // Цвет текста футера (черный)
    tft.setTextFont(1); // Маленький шрифт
    tft.setTextDatum(BC_DATUM); // Выравнивание по нижнему центру
    // Текст в зависимости от статуса
     if (client.connected()) {
         tft.drawString("Receiving Screen Stream...", screen_w / 2, screen_h - 2, 1);
     } else {
          tft.drawString("Attempting connection to host...", screen_w / 2, screen_h - 2, 1);
     }
    tft.setTextDatum(TL_DATUM); // Возвращаем выравнивание по умолчанию (Top Left)
}