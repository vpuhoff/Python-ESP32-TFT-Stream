import socket
import time
import mss # Для скриншотов
from PIL import Image # Для обработки изображений
import struct # Для упаковки данных в байты
import io # Для работы с байтами как с файлом
import numpy as np # Для работы с массивами

# --- Настройки ---
ESP32_PORT = 8888
TARGET_WIDTH = 320 # Ширина дисплея ESP32
TARGET_HEIGHT = 240 # Высота дисплея ESP32
UPDATE_INTERVAL_SEC = 0.1 # Пауза между обновлениями (10 кадров/сек)

# Область захвата на ПК (замените на нужные координаты и размеры)
# Монитор 1, отступ 100,100, размер 800x600
CAPTURE_REGION = {'top': 100, 'left': 100, 'width': 320, 'height': 240, 'mon': 1}
MAX_CHUNK_DATA_SIZE = 8192
GAMMA=2.8 # Гамма-коррекция (можно настроить)
WB_SCALE=(0.85, 0.95, 0.75) # Баланс белого (можно настроить)

# Или можно попробовать захватить весь экран:
# CAPTURE_REGION = mss.mss().monitors[1] # Основной монитор

# --- Вспомогательные функции ---

def rgb_to_rgb565(r, g, b):
    """Конвертирует 8-битный RGB в 16-битный RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

def image_to_rgb565_bytes(img: Image.Image):
    """Конвертирует Pillow Image (RGB) в байты RGB565."""
    # --- APPLY GAMMA CORRECTION ---
    try:
         # print(f"Applying gamma correction...") # Add print
         img = apply_gamma_and_white_balance(img, gamma=2.6, wb_scale=(0.95, 1.0, 0.75)) # Adjust gamma value (e.g., 1.8, 2.0, 2.2, 2.4) experimentally
         # print("Gamma applied.")
    except ImportError:
         print("Numpy not found, skipping gamma correction.")
    except Exception as e:
         print(f"Error during gamma correction: {e}")
    # -----------------------------
    pixels = img.load()
    width, height = img.size
    byte_data = bytearray(width * height * 2) # 2 байта на пиксель
    idx = 0
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            rgb565 = rgb_to_rgb565(r, g, b)
            # Упаковываем как 16-битное беззнаковое целое в big-endian (network order)
            struct.pack_into('!H', byte_data, idx, rgb565)
            idx += 2
    return bytes(byte_data)

def find_dirty_rects(img_prev: Image.Image | None, img_curr: Image.Image, threshold=10):
    """
    Находит измененные прямоугольники.
    Простой алгоритм: находит ОДИН большой прямоугольник, охватывающий все изменения.
    Более сложный алгоритм мог бы находить несколько меньших прямоугольников.
    threshold: порог разницы в цвете, чтобы считать пиксель измененным.
    """
    if img_prev is None:
        # Первый кадр - отправляем все
        yield (0, 0, img_curr.width, img_curr.height)
        return

    if img_prev.size != img_curr.size:
        # Размер изменился (не должно происходить при масштабировании) - отправляем все
        yield (0, 0, img_curr.width, img_curr.height)
        return

    pixels_prev = img_prev.load()
    pixels_curr = img_curr.load()
    width, height = img_curr.size

    min_x, min_y = width, height
    max_x, max_y = -1, -1
    changed = False

    # Находим границы изменившейся области
    for y in range(height):
        for x in range(width):
            r1, g1, b1 = pixels_prev[x, y]
            r2, g2, b2 = pixels_curr[x, y]
            # Простое сравнение суммы абсолютных разниц
            diff = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
            if diff > threshold:
                changed = True
                if x < min_x: min_x = x
                if y < min_y: min_y = y
                if x > max_x: max_x = x
                if y > max_y: max_y = y

    if changed:
        # Возвращаем один прямоугольник (добавляем 1 к max_x/max_y для получения ширины/высоты)
        # Убедимся, что ширина/высота > 0
        rect_w = max_x - min_x + 1
        rect_h = max_y - min_y + 1
        if rect_w > 0 and rect_h > 0:
             # **Улучшение:** Можно было бы реализовать алгоритм, возвращающий МНОГО
             #               маленьких прямоугольников, но начнем с одного большого.
            yield (min_x, min_y, rect_w, rect_h)

def pack_update_packet(x, y, w, h, data: bytes):
    """
    Упаковывает данные обновления.
    Формат: X(2B), Y(2B), W(2B), H(2B), DataLen(4B), Data(DataLen B)
    Используем Network Order (Big-Endian).
    """
    data_len = len(data)
    # '!HHHH I' - Big-endian, 4x unsigned short, 1x unsigned int
    header = struct.pack('!HHHH I', x, y, w, h, data_len)
    return header + data

def apply_gamma_and_white_balance(img: Image.Image, gamma=2.2, wb_scale=(1.0, 1.0, 0.95)): # Reduce Blue slightly
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img_array = np.array(img, dtype=np.float32) / 255.0
    # Apply gamma
    img_corrected = np.power(img_array, gamma)
    # Apply white balance scaling
    # Ensure wb_scale is broadcastable (1, 1, 3)
    scale = np.array(wb_scale).reshape(1, 1, 3)
    img_balanced = img_corrected * scale
    # Combine and convert back
    img_final = np.clip(img_balanced * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(img_final, 'RGB')
# --- Основная логика ---

prev_image = None
conn = None
addr = None

# Настройка TCP сервера
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Позволяет переиспользовать адрес
server_socket.bind(('', ESP32_PORT)) # Слушаем на всех интерфейсах
server_socket.listen(1)
print(f"[*] Ожидание подключения ESP32 на порту {ESP32_PORT}...")

try:
    conn, addr = server_socket.accept()
    print(f"[*] ESP32 подключен: {addr}")

    with mss.mss() as sct: # Инициализация захвата экрана
        while True:
            start_time = time.time()

            # 1. Захват экрана
            try:
                sct_img_bgra = sct.grab(CAPTURE_REGION) # Захват в формате BGRA
                # Конвертируем в Pillow Image (RGB)
                curr_image_full = Image.frombytes('RGB', (sct_img_bgra.width, sct_img_bgra.height), sct_img_bgra.rgb)
            except mss.ScreenShotError as e:
                print(f"Ошибка захвата экрана: {e}")
                time.sleep(1)
                continue

            # 2. Масштабирование до целевого разрешения
            curr_image_resized = curr_image_full.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS) # Или Image.BILINEAR для сглаживания

            # 3. Поиск измененных областей
            dirty_rects = list(find_dirty_rects(prev_image, curr_image_resized))

            # 4. Отправка изменений
            if dirty_rects:
                # print(f"Кадр {time.time():.2f}: Найдено {len(dirty_rects)} измененных областей.")
                for (x, y, w, h) in dirty_rects:
                    # print(f"  Обработка Rect({x},{y}, {w}x{h})")

                    # --- НАЧАЛО ИЗМЕНЕНИЙ: Логика разбиения больших областей ---
                    full_rect_data_size = w * h * 2
                    if full_rect_data_size > MAX_CHUNK_DATA_SIZE:
                        # print(f"    Область {w}x{h} ({full_rect_data_size} Б) слишком большая, разбиваем...")
                        # Разбиваем на горизонтальные полосы (простой вариант)
                        # Определяем высоту одной полосы так, чтобы она помещалась в MAX_CHUNK_DATA_SIZE
                        bytes_per_row = w * 2
                        if bytes_per_row > MAX_CHUNK_DATA_SIZE:
                             print(f"      ОШИБКА: Ширина {w} ({bytes_per_row} байт/строка) уже больше MAX_CHUNK_DATA_SIZE ({MAX_CHUNK_DATA_SIZE})! Увеличьте MAX_CHUNK_DATA_SIZE или уменьшите ширину захвата.")
                             # Пропустить эту область или обработать иначе (например, разбить по ширине)
                             continue # Пока пропускаем

                        chunk_h = MAX_CHUNK_DATA_SIZE // bytes_per_row # Макс. строк в одном чанке
                        if chunk_h == 0: chunk_h = 1 # Минимум 1 строка

                        for current_y_offset in range(0, h, chunk_h):
                            actual_chunk_h = min(chunk_h, h - current_y_offset) # Высота текущего чанка
                            chunk_x = x
                            chunk_y = y + current_y_offset
                            chunk_w = w

                            # Вырезаем чанк из ТЕКУЩЕГО кадра
                            chunk_img = curr_image_resized.crop((chunk_x, chunk_y, chunk_x + chunk_w, chunk_y + actual_chunk_h))
                            # Конвертируем чанк в RGB565 байты
                            chunk_data_rgb565 = image_to_rgb565_bytes(chunk_img)
                            # Упаковываем пакет для чанка
                            packet = pack_update_packet(chunk_x, chunk_y, chunk_w, actual_chunk_h, chunk_data_rgb565)
                            # Отправляем пакет чанка
                            try:
                                # print(f"      Отправка чанка: Rect({chunk_x},{chunk_y}, {chunk_w}x{actual_chunk_h}), Данные: {len(chunk_data_rgb565)} Б")
                                conn.sendall(packet)
                            except socket.error as e:
                                print(f"Ошибка отправки чанка: {e}")
                                conn = None
                                break # Прервать отправку чанков этой области
                        # --- Конец цикла по чанкам ---
                        if conn is None: break # Прервать обработку dirty_rects если была ошибка сети

                    else:
                        # Область достаточно мала, отправляем как есть
                        #print(f"    Область {w}x{h} ({full_rect_data_size} Б) ОК.")
                        # Вырезаем измененную область из ТЕКУЩЕГО кадра
                        region_img = curr_image_resized.crop((x, y, x + w, y + h))
                        # Конвертируем область в RGB565 байты
                        region_data_rgb565 = image_to_rgb565_bytes(region_img)
                        # Упаковываем пакет
                        packet = pack_update_packet(x, y, w, h, region_data_rgb565)
                        # Отправляем пакет
                        try:
                            # print(f"    Отправка: Rect({x},{y}, {w}x{h}), Размер данных: {len(region_data_rgb565)} байт")
                            conn.sendall(packet)
                        except socket.error as e:
                            print(f"Ошибка отправки: {e}")
                            conn = None
                            break # Выход из цикла for dirty_rects

            # else:
            #     print(f"Кадр {time.time():.2f}: Изменений нет.")

            # Если соединение было разорвано во время отправки
            if conn is None:
                 print("Соединение потеряно, ожидание нового подключения...")
                 prev_image = None # Сбросить предыдущий кадр для полной отправки при переподключении
                 conn, addr = server_socket.accept() # Ждем нового подключения
                 print(f"[*] ESP32 переподключен: {addr}")
                 continue # Начать цикл заново с новым соединением

            # Сохраняем текущий кадр как предыдущий для следующей итерации
            prev_image = curr_image_resized # Важно: сохраняем именно масштабированный кадр

            # Пауза для контроля частоты кадров
            elapsed_time = time.time() - start_time
            sleep_time = UPDATE_INTERVAL_SEC - elapsed_time
            if sleep_time > 0:
                time.sleep(sleep_time)

except KeyboardInterrupt:
    print("\n[*] Завершение работы...")
finally:
    if conn:
        conn.close()
        print("[*] Соединение с ESP32 закрыто.")
    server_socket.close()
    print("[*] Серверный сокет закрыт.")