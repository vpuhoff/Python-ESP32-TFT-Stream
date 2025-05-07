import socket
import time
import mss # Для скриншотов
from PIL import Image # Для обработки изображений
import struct # Для упаковки данных в байты
import io # Для работы с байтами как с файлом
import numpy as np # Для работы с массивами
from bios_drawer import draw_bios_on_image, DEFAULT_BIOS_RESOLUTION
from cpu_monitor_generator import CpuMonitorGenerator
from prometheus_monitor_generator import COLORS, RESOLUTION, PrometheusMonitorGenerator

# --- Prometheus Exporter ---
from prometheus_client import start_http_server, Histogram, Counter
import threading # Для запуска HTTP сервера Prometheus в отдельном потоке

# --- Настройки Prometheus Exporter ---
PROMETHEUS_EXPORTER_PORT = 8000 # Порт, на котором будут доступны метрики

# --- Определяем метрики ---
# Используем Histogram для измерения распределения времени выполнения
FRAME_PROCESSING_TIME = Histogram('esp32_frame_processing_seconds', 'Время обработки одного кадра',
                                  ['stage'])

packet_size_buckets = (
    12,  # Только заголовок (маловероятно, но для полноты)
    256, 512, 1024, 2048, 4096, 
    8192, 8192 + 12, # Размер максимального чанка и с заголовком
    10000,
    16384, 25000, 32768, 50000, float('inf') # Если вдруг будут пакеты больше
)

PACKET_SIZE_BYTES = Histogram('esp32_packet_size_bytes', 'Размер отправленных пакетов в байтах', buckets=packet_size_buckets)
CHUNKS_PER_FRAME = Histogram('esp32_chunks_per_frame', 'Количество чанков на кадр')
CONNECTION_ERRORS = Counter('esp32_connection_errors_total', 'Количество ошибок соединения при отправке')
RECONNECTIONS_TOTAL = Counter('esp32_reconnections_total', 'Общее количество переподключений ESP32')


# --- Настройки ---
ESP32_PORT = 8888
TARGET_WIDTH = 320 # Ширина дисплея ESP32
TARGET_HEIGHT = 240 # Высота дисплея ESP32
UPDATE_INTERVAL_SEC = 0.1 # Пауза между обновлениями (10 кадров/сек)

# Область захвата на ПК (замените на нужные координаты и размеры)
# Монитор 1, отступ 100,100, размер 800x600
CAPTURE_REGION = {'top': 170, 'left': 60, 'width': 320, 'height': 240, 'mon': 1}
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
    gamma_start_time = time.monotonic()
    try:
         # print(f"Applying gamma correction...") # Add print
         img = apply_gamma_and_white_balance(img, gamma=GAMMA, wb_scale=WB_SCALE) # Используем глобальные настройки
         # print("Gamma applied.")
    except ImportError:
         print("Numpy not found, skipping gamma correction.")
    except Exception as e:
         print(f"Error during gamma correction: {e}")
    FRAME_PROCESSING_TIME.labels(stage='color_correction').observe(time.monotonic() - gamma_start_time)
    # -----------------------------

    conversion_start_time = time.monotonic()
    pixels = img.load()
    width, height = img.size
    byte_data = bytearray(width * height * 2) # 2 байта на пиксель
    idx = 0
    for y_coord in range(height): # Изменено имя переменной, чтобы не конфликтовать с глобальной y
        for x_coord in range(width): # Изменено имя переменной
            r, g, b = pixels[x_coord, y_coord]
            rgb565 = rgb_to_rgb565(r, g, b)
            # Упаковываем как 16-битное беззнаковое целое в big-endian (network order)
            struct.pack_into('!H', byte_data, idx, rgb565)
            idx += 2
    FRAME_PROCESSING_TIME.labels(stage='rgb565_conversion').observe(time.monotonic() - conversion_start_time)
    return bytes(byte_data)

def find_dirty_rects(img_prev: Image.Image | None, img_curr: Image.Image, threshold=10):
    """
    Находит измененные прямоугольники с использованием NumPy для ускорения.
    Возвращает один большой прямоугольник, охватывающий все изменения.
    """
    diff_start_time = time.monotonic()

    if img_prev is None or img_prev.size != img_curr.size:
        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        yield (0, 0, img_curr.width, img_curr.height)
        return

    # Преобразуем изображения Pillow в массивы NumPy
    # Убедимся, что изображения в режиме RGB для корректного сравнения каналов
    if img_prev.mode != 'RGB':
        img_prev_rgb = img_prev.convert('RGB')
    else:
        img_prev_rgb = img_prev

    if img_curr.mode != 'RGB':
        img_curr_rgb = img_curr.convert('RGB')
    else:
        img_curr_rgb = img_curr
        
    arr_prev = np.array(img_prev_rgb, dtype=np.int16) # Используем int16 чтобы избежать переполнения при вычитании
    arr_curr = np.array(img_curr_rgb, dtype=np.int16)

    # Вычисляем абсолютную разницу по каждому каналу, затем суммируем разницы
    # Это эквивалентно abs(r1-r2) + abs(g1-g2) + abs(b1-b2)
    abs_diff_arr = np.sum(np.abs(arr_curr - arr_prev), axis=2)

    # Находим пиксели, где суммарная разница превышает порог
    changed_pixels_mask = abs_diff_arr > threshold

    # Находим координаты изменившихся пикселей
    # np.where возвращает кортеж массивов (один для каждой размерности)
    changed_y_coords, changed_x_coords = np.where(changed_pixels_mask)

    if changed_y_coords.size > 0: # Если есть хотя бы один измененный пиксель
        min_x = np.min(changed_x_coords)
        max_x = np.max(changed_x_coords)
        min_y = np.min(changed_y_coords)
        max_y = np.max(changed_y_coords)

        rect_w = int(max_x - min_x + 1) # Преобразуем в int, т.к. numpy может вернуть свои типы
        rect_h = int(max_y - min_y + 1)

        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        yield (int(min_x), int(min_y), rect_w, rect_h)
    else:
        # Изменений не найдено
        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        # Ничего не возвращаем, или можно вернуть специальный флаг/пустой кортеж,
        # в зависимости от того, как вызывающий код это обрабатывает.
        # Текущая логика ожидает, что если ничего не yield, то dirty_rects будет пустым.
        return

def pack_update_packet(x, y, w, h, data: bytes):
    """
    Упаковывает данные обновления.
    Формат: X(2B), Y(2B), W(2B), H(2B), DataLen(4B), Data(DataLen B)
    """
    pack_start_time = time.monotonic()
    data_len = len(data)
    header = struct.pack('!HHHH I', x, y, w, h, data_len)
    packet = header + data
    FRAME_PROCESSING_TIME.labels(stage='packet_packing').observe(time.monotonic() - pack_start_time)
    PACKET_SIZE_BYTES.observe(len(packet))
    return packet

def apply_gamma_and_white_balance(img: Image.Image, gamma=2.2, wb_scale=(1.0, 1.0, 0.95)):
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img_array = np.array(img, dtype=np.float32) / 255.0
    img_corrected = np.power(img_array, gamma)
    scale = np.array(wb_scale).reshape(1, 1, 3)
    img_balanced = img_corrected * scale
    img_final = np.clip(img_balanced * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(img_final, 'RGB')

# --- Основная логика ---

prev_image = None
conn = None
addr = None

# --- Запуск Prometheus HTTP сервера в отдельном потоке ---
def start_prometheus_server():
    try:
        start_http_server(PROMETHEUS_EXPORTER_PORT)
        print(f"[*] Prometheus exporter запущен на порту {PROMETHEUS_EXPORTER_PORT}")
    except Exception as e:
        print(f"[!] Не удалось запустить Prometheus exporter: {e}")

prometheus_thread = threading.Thread(target=start_prometheus_server, daemon=True)
prometheus_thread.start()
# ---------------------------------------------------------


# Настройка TCP сервера
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', ESP32_PORT))
server_socket.listen(1)
print(f"[*] Ожидание подключения ESP32 на порту {ESP32_PORT}...")

# --- Выбор источника изображения (раскомментируйте один) ---
IMAGE_SOURCE_MODE = "PROMETHEUS_MONITOR" # "SCREEN_CAPTURE", "BIOS", "CPU_MONITOR", "PROMETHEUS_MONITOR"
# ----------------------------------------------------------

try:
    conn, addr = server_socket.accept()
    print(f"[*] ESP32 подключен: {addr}")
    RECONNECTIONS_TOTAL.inc()

    # Инициализация генераторов один раз
    cpu_monitor_instance = None
    prometheus_monitor_instance = None

    if IMAGE_SOURCE_MODE == "CPU_MONITOR":
        cpu_monitor_instance = CpuMonitorGenerator()
    elif IMAGE_SOURCE_MODE == "PROMETHEUS_MONITOR":
        prometheus_monitor_instance = PrometheusMonitorGenerator()

    with mss.mss() as sct:
        while True:
            frame_loop_start_time = time.monotonic() # Начало полного цикла обработки кадра
            total_chunks_current_frame = 0

            # 1. Получение/Генерация изображения
            capture_gen_start_time = time.monotonic()
            if IMAGE_SOURCE_MODE == "SCREEN_CAPTURE":
                try:
                    sct_img_bgra = sct.grab(CAPTURE_REGION)
                    curr_image_full = Image.frombytes('RGB', (sct_img_bgra.width, sct_img_bgra.height), sct_img_bgra.rgb, 'raw', 'BGR') # BGR to RGB
                except mss.ScreenShotError as e:
                    print(f"Ошибка захвата экрана: {e}")
                    time.sleep(1)
                    continue
                FRAME_PROCESSING_TIME.labels(stage='capture_screen').observe(time.monotonic() - capture_gen_start_time)
            elif IMAGE_SOURCE_MODE == "BIOS":
                my_image = Image.new('RGB', DEFAULT_BIOS_RESOLUTION)
                curr_image_full = draw_bios_on_image(my_image)
                FRAME_PROCESSING_TIME.labels(stage='generate_bios').observe(time.monotonic() - capture_gen_start_time)
            elif IMAGE_SOURCE_MODE == "CPU_MONITOR" and cpu_monitor_instance:
                curr_image_full = Image.new('RGB', cpu_monitor_instance.resolution)
                cpu_monitor_instance.draw_frame(curr_image_full)
                FRAME_PROCESSING_TIME.labels(stage='generate_cpu_monitor').observe(time.monotonic() - capture_gen_start_time)
            elif IMAGE_SOURCE_MODE == "PROMETHEUS_MONITOR" and prometheus_monitor_instance:
                curr_image_full = Image.new('RGB', RESOLUTION, color=COLORS["background"])
                prometheus_monitor_instance.generate_image_frame(curr_image_full) # Используем глобальные RESOLUTION и COLORS
                FRAME_PROCESSING_TIME.labels(stage='generate_prometheus_monitor').observe(time.monotonic() - capture_gen_start_time)
            else:
                print(f"Неизвестный или неинициализированный IMAGE_SOURCE_MODE: {IMAGE_SOURCE_MODE}")
                time.sleep(1)
                continue


            # 2. Масштабирование до целевого разрешения
            resize_start_time = time.monotonic()
            curr_image_resized = curr_image_full.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
            FRAME_PROCESSING_TIME.labels(stage='resize').observe(time.monotonic() - resize_start_time)

            # 3. Поиск измененных областей (color_correction и rgb565_conversion будут вызываться внутри image_to_rgb565_bytes)
            dirty_rects = list(find_dirty_rects(prev_image, curr_image_resized))

            # 4. Отправка изменений
            if dirty_rects:
                total_chunks_current_frame = 0
                for (x, y, w, h) in dirty_rects:
                    chunking_crop_start_time = time.monotonic()
                    full_rect_data_size = w * h * 2
                    if full_rect_data_size > MAX_CHUNK_DATA_SIZE:
                        bytes_per_row = w * 2
                        if bytes_per_row == 0: # Избегаем деления на ноль, если w=0
                             print(f"      ПРЕДУПРЕЖДЕНИЕ: Ширина области {w} равна нулю. Пропуск чанка.")
                             continue
                        if bytes_per_row > MAX_CHUNK_DATA_SIZE : # Это условие должно проверяться до bytes_per_row == 0
                            print(f"      ОШИБКА: Ширина {w} ({bytes_per_row} байт/строка) уже больше MAX_CHUNK_DATA_SIZE ({MAX_CHUNK_DATA_SIZE})! Увеличьте MAX_CHUNK_DATA_SIZE или уменьшите ширину захвата.")
                            continue

                        chunk_h = MAX_CHUNK_DATA_SIZE // bytes_per_row
                        if chunk_h == 0: chunk_h = 1

                        for current_y_offset in range(0, h, chunk_h):
                            actual_chunk_h = min(chunk_h, h - current_y_offset)
                            chunk_x = x
                            chunk_y = y + current_y_offset
                            chunk_w = w

                            chunk_img = curr_image_resized.crop((chunk_x, chunk_y, chunk_x + chunk_w, chunk_y + actual_chunk_h))
                            FRAME_PROCESSING_TIME.labels(stage='chunking_and_crop').observe(time.monotonic() - chunking_crop_start_time) # Время на один чанк

                            # Конвертация и упаковка измеряются внутри функций image_to_rgb565_bytes и pack_update_packet
                            chunk_data_rgb565 = image_to_rgb565_bytes(chunk_img)
                            packet = pack_update_packet(chunk_x, chunk_y, chunk_w, actual_chunk_h, chunk_data_rgb565)

                            send_start_time = time.monotonic()
                            try:
                                conn.sendall(packet)
                                FRAME_PROCESSING_TIME.labels(stage='send_packet').observe(time.monotonic() - send_start_time)
                                total_chunks_current_frame += 1
                            except socket.error as e:
                                print(f"Ошибка отправки чанка: {e}")
                                CONNECTION_ERRORS.inc()
                                conn = None
                                break
                            chunking_crop_start_time = time.monotonic() # Сброс для следующего чанка
                        # --- Конец цикла по чанкам ---
                        if conn is None: break

                    else: # Область достаточно мала
                        FRAME_PROCESSING_TIME.labels(stage='chunking_and_crop').observe(time.monotonic() - chunking_crop_start_time) # Время на одну "не-чанкованную" область
                        region_img = curr_image_resized.crop((x, y, x + w, y + h))
                        region_data_rgb565 = image_to_rgb565_bytes(region_img)
                        packet = pack_update_packet(x, y, w, h, region_data_rgb565)

                        send_start_time = time.monotonic()
                        try:
                            conn.sendall(packet)
                            FRAME_PROCESSING_TIME.labels(stage='send_packet').observe(time.monotonic() - send_start_time)
                            total_chunks_current_frame += 1
                        except socket.error as e:
                            print(f"Ошибка отправки: {e}")
                            CONNECTION_ERRORS.inc()
                            conn = None
                            break
                if total_chunks_current_frame > 0 : # Записываем только если были чанки
                    CHUNKS_PER_FRAME.observe(total_chunks_current_frame)


            if conn is None:
                print("Соединение потеряно, ожидание нового подключения...")
                prev_image = None
                # Закрываем старое соединение перед новым accept, если оно еще не None
                # (хотя в данном потоке оно уже должно быть None)
                # if conn:
                # try:
                # conn.close()
                # except Exception:
                # pass # Игнорируем ошибки при закрытии уже разорванного сокета
                conn, addr = server_socket.accept()
                print(f"[*] ESP32 переподключен: {addr}")
                RECONNECTIONS_TOTAL.inc()
                continue

            prev_image = curr_image_resized

            # Общее время на обработку и отправку одного кадра
            FRAME_PROCESSING_TIME.labels(stage='full_frame_loop').observe(time.monotonic() - frame_loop_start_time)

            elapsed_time = time.monotonic() - frame_loop_start_time # Используем monotonic для измерения длительности
            sleep_time = UPDATE_INTERVAL_SEC - elapsed_time
            if sleep_time > 0:
                time.sleep(sleep_time)

except KeyboardInterrupt:
    print("\n[*] Завершение работы...")
finally:
    if cpu_monitor_instance:
        cpu_monitor_instance.stop()
    if prometheus_monitor_instance:
        prometheus_monitor_instance.stop()
    if conn:
        try:
            conn.close()
        except Exception as e:
            print(f"Ошибка при закрытии соединения с ESP32: {e}")
        print("[*] Соединение с ESP32 закрыто.")
    try:
        server_socket.close()
    except Exception as e:
        print(f"Ошибка при закрытии серверного сокета: {e}")
    print("[*] Серверный сокет закрыт.")
    print("[*] Prometheus exporter поток также должен завершиться (daemon).")