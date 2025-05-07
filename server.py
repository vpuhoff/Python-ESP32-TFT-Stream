import socket
import time
import mss # Для скриншотов
from PIL import Image # Для обработки изображений
import struct # Для упаковки данных в байты
import numpy as np # Для работы с массивами
from collections import deque # Для истории FPS и очереди

# Импорт ваших модулей
from bios_drawer import draw_bios_on_image, DEFAULT_BIOS_RESOLUTION
from cpu_monitor_generator import CpuMonitorGenerator
from prometheus_monitor_generator import PrometheusMonitorGenerator

# Prometheus Exporter
from prometheus_client import start_http_server, Histogram, Counter, Gauge # <--- Убедимся, что Gauge импортирован
import threading 
import queue 

# --- Настройки Prometheus Exporter ---
PROMETHEUS_EXPORTER_PORT = 8000 

# --- Определяем метрики ---
FRAME_PROCESSING_TIME = Histogram('esp32_frame_processing_seconds', 'Время обработки одного кадра',
                                  ['stage'])
packet_size_buckets = (
    12, 256, 512, 1024, 2048, 4096,
    8192, 8192 + 12, 10000,
    16384, 25000, 32768, 50000, float('inf')
)
PACKET_SIZE_BYTES = Histogram('esp32_packet_size_bytes', 'Размер отправленных пакетов в байтах', buckets=packet_size_buckets)
CHUNKS_PER_FRAME = Histogram('esp32_chunks_per_frame', 'Количество чанков на кадр')
CONNECTION_ERRORS = Counter('esp32_connection_errors_total', 'Количество ошибок соединения при отправке')
RECONNECTIONS_TOTAL = Counter('esp32_reconnections_total', 'Общее количество переподключений ESP32')
FRAMES_GENERATED = Counter('esp32_frames_generated_total', 'Количество сгенерированных кадров (потоком генератора)')
FRAMES_PROCESSED = Counter('esp32_frames_processed_total', 'Количество обработанных и отправленных кадров (потоком потребителя)')
QUEUE_SIZE_FRAMES = Histogram('esp32_frames_queue_size', 'Количество кадров в очереди обработки')
DIRTY_RECTS_SEND_DURATION = Histogram('esp32_dirty_rects_send_duration_seconds', 
                                      'Общее время отправки всех dirty_rects для одного кадра')
CURRENT_DYNAMIC_THRESHOLD_VALUE = Gauge('esp32_current_dynamic_threshold', 
                                        'Текущее значение динамического порога для dirty_rects')
# --- НОВАЯ МЕТРИКА GAUGE ДЛЯ FPS ---
CONSUMER_CALCULATED_FPS = Gauge('esp32_consumer_calculated_fps',
                                'Расчетный FPS на основе времени обработки кадров в потребителе')
# ---------------------------------

# --- Общие настройки сервера ---
ESP32_PORT = 8888
TARGET_WIDTH = 320
TARGET_HEIGHT = 240
GENERATOR_TARGET_INTERVAL_SEC = 0.05 

# Настройки захвата экрана и обработки изображения
CAPTURE_REGION = {'top': 170, 'left': 60, 'width': 320, 'height': 240, 'mon': 1}
MAX_CHUNK_DATA_SIZE = 8192
GAMMA = 2.8
WB_SCALE = (0.85, 0.95, 0.75)

# --- Настройки для динамического Threshold ---
TARGET_FPS = 10.0
MIN_DIRTY_RECT_THRESHOLD = 5
MAX_DIRTY_RECT_THRESHOLD = 80
THRESHOLD_ADJUSTMENT_STEP_UP = 10   
THRESHOLD_ADJUSTMENT_STEP_DOWN = 5  
FPS_HISTORY_SIZE = 10             
FPS_HYSTERESIS_FACTOR = 0.1       

current_dynamic_threshold = MIN_DIRTY_RECT_THRESHOLD 
frame_processing_times_history = deque(maxlen=FPS_HISTORY_SIZE) 

# Настройки для многопоточности
FRAMES_QUEUE_MAX_SIZE = 5  
GENERATOR_LOW_WATER_MARK = 2 

# Выбор источника изображения 
IMAGE_SOURCE_MODE = "SCREEN_CAPTURE" # "PROMETHEUS_MONITOR" 

frames_queue = queue.Queue(maxsize=FRAMES_QUEUE_MAX_SIZE)
stop_event = threading.Event() 

# --- Вспомогательные функции (без изменений) ---
def rgb_to_rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

def image_to_rgb565_bytes(img: Image.Image):
    gamma_start_time = time.monotonic()
    try:
        img = apply_gamma_and_white_balance(img, gamma=GAMMA, wb_scale=WB_SCALE)
    except ImportError:
        print("Numpy not found, skipping gamma correction.")
    except Exception as e:
        print(f"Error during gamma correction: {e}")
    FRAME_PROCESSING_TIME.labels(stage='color_correction').observe(time.monotonic() - gamma_start_time)

    conversion_start_time = time.monotonic()
    pixels = img.load()
    width, height = img.size
    byte_data = bytearray(width * height * 2)
    idx = 0
    for y_coord in range(height):
        for x_coord in range(width):
            r, g, b = pixels[x_coord, y_coord]
            rgb565_val = rgb_to_rgb565(r, g, b)
            struct.pack_into('!H', byte_data, idx, rgb565_val)
            idx += 2
    FRAME_PROCESSING_TIME.labels(stage='rgb565_conversion').observe(time.monotonic() - conversion_start_time)
    return bytes(byte_data)

def find_dirty_rects(img_prev: Image.Image | None, img_curr: Image.Image, threshold=10):
    diff_start_time = time.monotonic()
    if img_prev is None or img_prev.size != img_curr.size:
        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        yield (0, 0, img_curr.width, img_curr.height)
        return

    img_prev_rgb = img_prev.convert('RGB') if img_prev.mode != 'RGB' else img_prev
    img_curr_rgb = img_curr.convert('RGB') if img_curr.mode != 'RGB' else img_curr
        
    arr_prev = np.array(img_prev_rgb, dtype=np.int16)
    arr_curr = np.array(img_curr_rgb, dtype=np.int16)
    abs_diff_arr = np.sum(np.abs(arr_curr - arr_prev), axis=2)
    changed_pixels_mask = abs_diff_arr > threshold
    changed_y_coords, changed_x_coords = np.where(changed_pixels_mask)

    if changed_y_coords.size > 0:
        min_x, max_x = int(np.min(changed_x_coords)), int(np.max(changed_x_coords))
        min_y, max_y = int(np.min(changed_y_coords)), int(np.max(changed_y_coords))
        rect_w, rect_h = (max_x - min_x + 1), (max_y - min_y + 1)
        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        yield (min_x, min_y, rect_w, rect_h)
    else:
        FRAME_PROCESSING_TIME.labels(stage='diff_calculation').observe(time.monotonic() - diff_start_time)
        return

def pack_update_packet(x, y, w, h, data: bytes):
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

# --- Запуск Prometheus HTTP сервера ---
def start_prometheus_server():
    try:
        start_http_server(PROMETHEUS_EXPORTER_PORT)
        print(f"[*] Prometheus exporter запущен на порту {PROMETHEUS_EXPORTER_PORT}")
    except Exception as e:
        print(f"[!] Не удалось запустить Prometheus exporter: {e}")

# --- Поток 1: Генератор Кадров ---
def frame_generator_thread_func(
        source_mode,
        cpu_mon_instance, 
        prom_mon_instance, 
        target_interval):
    print("[GeneratorThread] Запущен.")
    current_cpu_monitor_instance = cpu_mon_instance
    current_prometheus_monitor_instance = prom_mon_instance
    
    sct_instance_local = None
    if source_mode == "SCREEN_CAPTURE":
        try:
            sct_instance_local = mss.mss()
            print("[GeneratorThread] mss.mss() инициализирован в потоке генератора.")
        except Exception as e:
            print(f"[GeneratorThread] КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать mss.mss() в потоке: {e}")
            stop_event.set() 
            return 

    while not stop_event.is_set():
        loop_start_time = time.monotonic()
        current_q_size = frames_queue.qsize()
        QUEUE_SIZE_FRAMES.observe(current_q_size) 

        if current_q_size < GENERATOR_LOW_WATER_MARK:
            capture_gen_start_time = time.monotonic()
            generated_image = None

            if source_mode == "SCREEN_CAPTURE":
                if sct_instance_local: 
                    try:
                        sct_img_bgra = sct_instance_local.grab(CAPTURE_REGION)
                        generated_image = Image.frombytes('RGB', (sct_img_bgra.width, sct_img_bgra.height), sct_img_bgra.rgb, 'raw', 'RGB')
                        FRAME_PROCESSING_TIME.labels(stage='capture_screen_thread').observe(time.monotonic() - capture_gen_start_time)
                    except mss.ScreenShotError as e:
                        print(f"[GeneratorThread] Ошибка захвата экрана: {e}")
                        stop_event.wait(0.5) 
                        continue
                    except Exception as e: 
                        print(f"[GeneratorThread] Неожиданная ошибка mss.grab: {e}")
                        stop_event.set() 
                        break 
                else:
                    print(f"[GeneratorThread] Экземпляр mss не доступен для SCREEN_CAPTURE. Остановка.")
                    stop_event.set()
                    break 
            elif source_mode == "BIOS":
                my_image = Image.new('RGB', DEFAULT_BIOS_RESOLUTION)
                generated_image = draw_bios_on_image(my_image)
                FRAME_PROCESSING_TIME.labels(stage='generate_bios_thread').observe(time.monotonic() - capture_gen_start_time)
            elif source_mode == "CPU_MONITOR" and current_cpu_monitor_instance:
                generated_image = Image.new('RGB', current_cpu_monitor_instance.resolution)
                current_cpu_monitor_instance.draw_frame(generated_image) 
                FRAME_PROCESSING_TIME.labels(stage='generate_cpu_monitor_thread').observe(time.monotonic() - capture_gen_start_time)
            elif source_mode == "PROMETHEUS_MONITOR" and current_prometheus_monitor_instance:
                generated_image = Image.new('RGB', current_prometheus_monitor_instance.resolution, color=current_prometheus_monitor_instance._colors["background"])
                current_prometheus_monitor_instance.generate_image_frame(generated_image)
                FRAME_PROCESSING_TIME.labels(stage='generate_prometheus_monitor_thread').observe(time.monotonic() - capture_gen_start_time)
            else:
                print(f"[GeneratorThread] Неизвестный или неинициализированный IMAGE_SOURCE_MODE: {source_mode}. Остановка.")
                stop_event.set() 
                break 

            if generated_image:
                try:
                    frames_queue.put(generated_image, timeout=0.1) 
                    FRAMES_GENERATED.inc()
                except queue.Full:
                    pass 
            
            elapsed_this_loop = time.monotonic() - loop_start_time
            sleep_duration = target_interval - elapsed_this_loop
            if sleep_duration > 0:
                stop_event.wait(sleep_duration)
        else:
            stop_event.wait(0.01) 

    if sct_instance_local and hasattr(sct_instance_local, 'close'): 
        try:
            sct_instance_local.close()
            print("[GeneratorThread] mss.mss() экземпляр закрыт.")
        except Exception as e:
            print(f"[GeneratorThread] Ошибка при закрытии mss.mss(): {e}")
            
    print("[GeneratorThread] Остановлен.")

# --- Поток 2: Обработчик и Отправщик Кадров ---
def frame_consumer_thread_func(client_conn):
    global current_dynamic_threshold
    global frame_processing_times_history

    print("[ConsumerThread] Запущен.")
    prev_processed_image = None

    CURRENT_DYNAMIC_THRESHOLD_VALUE.set(current_dynamic_threshold)
    CONSUMER_CALCULATED_FPS.set(0)

    while not stop_event.is_set():
        try:
            raw_frame = frames_queue.get(timeout=0.1)
            QUEUE_SIZE_FRAMES.observe(frames_queue.qsize())

            actual_processing_start_time = time.monotonic()

            resize_start_time = time.monotonic()
            curr_image_resized = raw_frame.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
            FRAME_PROCESSING_TIME.labels(stage='resize_thread').observe(time.monotonic() - resize_start_time)

            dirty_rects = list(find_dirty_rects(prev_processed_image, curr_image_resized, threshold=current_dynamic_threshold))

            total_dirty_rects_send_time_start = 0
            socket_error_occurred = False # Флаг для отслеживания ошибки сокета в этой итерации

            if dirty_rects:
                total_dirty_rects_send_time_start = time.monotonic()

                for (x, y, w, h) in dirty_rects:
                    full_rect_data_size = w * h * 2
                    if full_rect_data_size > MAX_CHUNK_DATA_SIZE:
                        # --- Логика чанкинга ---
                        bytes_per_row = w * 2
                        if bytes_per_row == 0: continue
                        if bytes_per_row > MAX_CHUNK_DATA_SIZE: continue

                        chunk_h = MAX_CHUNK_DATA_SIZE // bytes_per_row
                        if chunk_h == 0: chunk_h = 1

                        for current_y_offset in range(0, h, chunk_h):
                            actual_chunk_h = min(chunk_h, h - current_y_offset)
                            chunk_x, chunk_y, chunk_w = x, y + current_y_offset, w

                            current_chunk_crop_time_start = time.monotonic()
                            chunk_img = curr_image_resized.crop((chunk_x, chunk_y, chunk_x + chunk_w, chunk_y + actual_chunk_h))
                            FRAME_PROCESSING_TIME.labels(stage='chunking_and_crop_thread').observe(time.monotonic() - current_chunk_crop_time_start)

                            chunk_data_rgb565 = image_to_rgb565_bytes(chunk_img)
                            packet = pack_update_packet(chunk_x, chunk_y, chunk_w, actual_chunk_h, chunk_data_rgb565)
                            send_start_time = time.monotonic()
                            try:
                                client_conn.sendall(packet)
                                FRAME_PROCESSING_TIME.labels(stage='send_packet_thread').observe(time.monotonic() - send_start_time)
                                # total_chunks_current_frame += 1 # Перенесем инкремент ниже
                            except socket.error as e:
                                print(f"[ConsumerThread] Ошибка отправки чанка: {e}")
                                CONNECTION_ERRORS.inc()
                                socket_error_occurred = True # Устанавливаем флаг
                                break # Выходим из внутреннего цикла (по чанкам)
                        # --- Конец цикла по чанкам ---
                        if socket_error_occurred: break # Если была ошибка в чанке, выходим и из внешнего цикла (по dirty_rects)

                    else: # Если не чанкинг
                        current_region_crop_time_start = time.monotonic()
                        region_img = curr_image_resized.crop((x, y, x + w, y + h))
                        FRAME_PROCESSING_TIME.labels(stage='chunking_and_crop_thread').observe(time.monotonic() - current_region_crop_time_start)

                        region_data_rgb565 = image_to_rgb565_bytes(region_img)
                        packet = pack_update_packet(x, y, w, h, region_data_rgb565)
                        send_start_time = time.monotonic()
                        try:
                            client_conn.sendall(packet)
                            FRAME_PROCESSING_TIME.labels(stage='send_packet_thread').observe(time.monotonic() - send_start_time)
                        except socket.error as e:
                            print(f"[ConsumerThread] Ошибка отправки: {e}")
                            CONNECTION_ERRORS.inc()
                            socket_error_occurred = True # Устанавливаем флаг
                            break # Выходим из цикла по dirty_rects
                # --- Конец цикла по dirty_rects ---

                # Если произошла ошибка сокета, прерываем обработку этого кадра и переходим к except socket.error
                if socket_error_occurred:
                    raise socket.error("Socket error occurred during send operations for the frame")

            # --- Этот код выполняется только если НЕ БЫЛО ошибки сокета ---
            if dirty_rects: # Если вообще были изменения
                 # Инкрементируем счетчик чанков здесь, если отправка прошла успешно
                 # Примечание: CHUNKS_PER_FRAME считает количество успешных ОТПРАВОК, а не количество dirty_rects или логических чанков
                 # Если нужна логика по чанкам, нужно инкрементить внутри циклов до sendall
                 # Давайте пока считать количество dirty_rects, если они были успешно отправлены
                 total_chunks_current_frame = len(dirty_rects) # Примерно
                 CHUNKS_PER_FRAME.observe(total_chunks_current_frame)

                 if total_dirty_rects_send_time_start > 0:
                     DIRTY_RECTS_SEND_DURATION.observe(time.monotonic() - total_dirty_rects_send_time_start)

            prev_processed_image = curr_image_resized
            FRAMES_PROCESSED.inc()

            current_frame_actual_processing_time = time.monotonic() - actual_processing_start_time
            frame_processing_times_history.append(current_frame_actual_processing_time)

            # --- Адаптация Threshold ---
            if len(frame_processing_times_history) == FPS_HISTORY_SIZE:
                avg_processing_time = sum(frame_processing_times_history) / FPS_HISTORY_SIZE
                current_fps = 0.0
                if avg_processing_time > 0:
                    current_fps = 1.0 / avg_processing_time

                CONSUMER_CALCULATED_FPS.set(current_fps)

                hysteresis = TARGET_FPS * FPS_HYSTERESIS_FACTOR
                old_threshold = current_dynamic_threshold
                if current_fps < TARGET_FPS - hysteresis and avg_processing_time > 0:
                    current_dynamic_threshold = min(MAX_DIRTY_RECT_THRESHOLD,
                                                    current_dynamic_threshold + THRESHOLD_ADJUSTMENT_STEP_UP)
                elif current_fps > TARGET_FPS + hysteresis:
                    current_dynamic_threshold = max(MIN_DIRTY_RECT_THRESHOLD,
                                                    current_dynamic_threshold - THRESHOLD_ADJUSTMENT_STEP_DOWN)

                if old_threshold != current_dynamic_threshold:
                    CURRENT_DYNAMIC_THRESHOLD_VALUE.set(current_dynamic_threshold)
            # --- Конец адаптации ---

            frames_queue.task_done()
            FRAME_PROCESSING_TIME.labels(stage='full_consumer_loop_thread').observe(time.monotonic() - actual_processing_start_time)

        except queue.Empty:
            continue
        except socket.error as e:
            # Этот блок ловит ошибку от sendall ИЛИ от raise socket.error(...)
            print(f"[ConsumerThread] Завершение из-за ошибки сокета: {e}")
            # CONNECTION_ERRORS уже увеличен в месте возникновения
            CONSUMER_CALCULATED_FPS.set(0)
            break # <-- Выходим из цикла while потока БЕЗ установки stop_event
        except Exception as e:
            print(f"[ConsumerThread] Неожиданная ошибка: {e}")
            import traceback
            traceback.print_exc()
            CONSUMER_CALCULATED_FPS.set(0)
            break # <-- Выходим из цикла while потока БЕЗ установки stop_event

    print("[ConsumerThread] Остановлен.")


# --- Основная логика (main) ---
def main():
    cpu_monitor_instance = None
    prometheus_monitor_instance = None

    prometheus_http_thread = threading.Thread(target=start_prometheus_server, daemon=True)
    prometheus_http_thread.start()

    try:
        if IMAGE_SOURCE_MODE == "CPU_MONITOR":
            cpu_monitor_instance = CpuMonitorGenerator()
        elif IMAGE_SOURCE_MODE == "PROMETHEUS_MONITOR":
            prometheus_monitor_instance = PrometheusMonitorGenerator()

        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        server_socket.settimeout(1.0)
        server_socket.bind(('', ESP32_PORT))
        server_socket.listen(1)
        print(f"[*] Ожидание подключения ESP32 на порту {ESP32_PORT}...")

        conn_esp32 = None
        generator_thread_instance = None
        consumer_thread_instance = None

        while not stop_event.is_set():
            if conn_esp32 is None: # Если нет активного соединения
                # Очищаем очередь перед новым подключением
                while not frames_queue.empty():
                    try: frames_queue.get_nowait(); frames_queue.task_done()
                    except queue.Empty: break

                # Ожидаем завершения ПРЕДЫДУЩИХ потоков перед созданием новых
                if consumer_thread_instance and consumer_thread_instance.is_alive():
                    print("[MainLoop] Ожидание завершения предыдущего потока потребителя...")
                    # Здесь не нужен stop_event, т.к. мы просто ждем завершения старого потока
                    consumer_thread_instance.join(timeout=1)
                    if consumer_thread_instance.is_alive():
                        print("[MainLoop] Предупреждение: Предыдущий потребитель не завершился!")
                if generator_thread_instance and generator_thread_instance.is_alive():
                    print("[MainLoop] Ожидание завершения предыдущего потока генератора...")
                    generator_thread_instance.join(timeout=1)
                    if generator_thread_instance.is_alive():
                        print("[MainLoop] Предупреждение: Предыдущий генератор не завершился!")

                # Сбрасываем ссылки на всякий случай
                consumer_thread_instance = None
                generator_thread_instance = None

                if stop_event.is_set(): break # Проверяем еще раз перед accept

                try:
                    print(f"[*] Сервер слушает порт {ESP32_PORT}...") # Добавим лог
                    conn_esp32, addr_esp32_conn = server_socket.accept()
                    conn_esp32.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    conn_esp32.settimeout(2.0)
                    print(f"[*] ESP32 подключен: {addr_esp32_conn}")
                    RECONNECTIONS_TOTAL.inc()

                    CURRENT_DYNAMIC_THRESHOLD_VALUE.set(current_dynamic_threshold)
                    CONSUMER_CALCULATED_FPS.set(0)

                    generator_thread_instance = threading.Thread(
                        target=frame_generator_thread_func,
                        args=(IMAGE_SOURCE_MODE, cpu_monitor_instance, prometheus_monitor_instance, GENERATOR_TARGET_INTERVAL_SEC),
                        daemon=True
                    )
                    consumer_thread_instance = threading.Thread(
                        target=frame_consumer_thread_func,
                        args=(conn_esp32,),
                        daemon=True
                    )
                    generator_thread_instance.start()
                    consumer_thread_instance.start()
                    print("[MainLoop] Новые потоки генератора и потребителя запущены.")

                except socket.timeout:
                    continue # Просто продолжаем цикл ожидания
                except Exception as e:
                    print(f"[MainLoop] Ошибка при подключении клиента: {e}")
                    if conn_esp32:
                        try: conn_esp32.close()
                        except: pass
                    conn_esp32 = None
                    time.sleep(1)
                    continue

            elif generator_thread_instance and consumer_thread_instance: # Если соединение есть и потоки должны работать
                if not consumer_thread_instance.is_alive():
                    print("[MainLoop] Поток потребителя завершился (вероятно, разрыв соединения или ошибка).")
                    # НЕ УСТАНАВЛИВАЕМ stop_event здесь!

                    if conn_esp32:
                        try:
                            conn_esp32.close()
                            print("[MainLoop] Соединение с ESP32 закрыто.")
                        except Exception as e:
                            print(f"[MainLoop] Ошибка при закрытии сокета после смерти потребителя: {e}")
                    conn_esp32 = None # Готовимся к новому подключению

                    # Ссылки на потоки будут сброшены и потоки присоединены
                    # на следующей итерации в блоке `if conn_esp32 is None:`
                    print("[MainLoop] Подготовка к ожиданию нового подключения...")
                    continue # Переходим к следующей итерации главного цикла немедленно

                elif not generator_thread_instance.is_alive():
                    # Если генератор умер сам по себе - это проблема, останавливаем все.
                    print("[MainLoop] Поток генератора неожиданно завершился!")
                    if not stop_event.is_set(): stop_event.set()
                    break # Выходим из главного цикла

                # Если оба потока живы и есть соединение, просто ждем
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[*] Завершение работы по KeyboardInterrupt...")
        if not stop_event.is_set(): stop_event.set()
    except Exception as e:
        print(f"[MainLoop] Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        if not stop_event.is_set(): stop_event.set()
    finally:
        print("[*] Начало процедуры остановки...")
        if not stop_event.is_set():
            stop_event.set()

        if cpu_monitor_instance and hasattr(cpu_monitor_instance, 'stop'):
            print("[*] Остановка CPU монитора...")
            cpu_monitor_instance.stop()
        if prometheus_monitor_instance and hasattr(prometheus_monitor_instance, 'stop'):
            print("[*] Остановка Prometheus монитора...")
            prometheus_monitor_instance.stop()

        if 'generator_thread_instance' in locals() and generator_thread_instance and generator_thread_instance.is_alive():
            print("[*] Ожидание остановки потока генератора...")
            generator_thread_instance.join(timeout=2)
            if generator_thread_instance.is_alive(): print("[!] Поток генератора не остановился корректно.")

        if 'consumer_thread_instance' in locals() and consumer_thread_instance and consumer_thread_instance.is_alive():
            print("[*] Ожидание остановки потока потребителя...")
            consumer_thread_instance.join(timeout=3)
            if consumer_thread_instance.is_alive(): print("[!] Поток потребителя не остановился корректно.")

        if 'conn_esp32' in locals() and conn_esp32:
            try:
                print("[*] Закрытие соединения с ESP32...")
                conn_esp32.close()
            except Exception as e:
                print(f"Ошибка при закрытии соединения с ESP32: {e}")

        if 'server_socket' in locals() and server_socket:
            try:
                print("[*] Закрытие серверного сокета...")
                server_socket.close()
            except Exception as e:
                print(f"Ошибка при закрытии серверного сокета: {e}")

        if 'prometheus_http_thread' in locals() and prometheus_http_thread and prometheus_http_thread.is_alive():
             print("[*] Prometheus HTTP сервер (daemon) должен завершиться автоматически.")

        print("[*] Сервер полностью остановлен.")

if __name__ == "__main__":
    main()