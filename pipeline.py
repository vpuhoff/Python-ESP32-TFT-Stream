# pipeline.py
import socket
import threading
import queue
import time
from PIL import Image, ImageDraw # ImageDraw для заглушки при ошибке
import struct
import numpy as np
from collections import deque
import mss # Для SCREEN_CAPTURE
from numba import jit, types


# Импорт ваших генераторов
# Убедитесь, что эти файлы находятся в том же каталоге или доступны через PYTHONPATH
from bios_drawer import draw_bios_on_image
from cpu_monitor_generator import CpuMonitorGenerator
from prometheus_monitor_generator import PrometheusMonitorGenerator
from window_capture import WindowScreenshotter

# from graphics_engine import MonitorGraphicsEngine # Если используется напрямую

# Вспомогательная функция для преобразования RGB в RGB565
@jit(nopython=True)
def rgb_to_rgb565(r, g, b):
    """Преобразует значения R, G, B в 16-битный формат RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

# The decorator's return type signature is now corrected
@jit(types.UniTuple(types.UniTuple(types.float32, 3), 2)(types.float32, types.float32, types.float32), nopython=True)
def _quantize_and_get_error_numba(r, g, b):
    # This part of the code remains the same as the logic is sound
    
    new_r = round(r / 8.0) * 8.0
    if new_r < 0.0: new_r = 0.0
    if new_r > 255.0: new_r = 255.0

    new_g = round(g / 4.0) * 4.0
    if new_g < 0.0: new_g = 0.0
    if new_g > 255.0: new_g = 255.0
    
    new_b = round(b / 8.0) * 8.0
    if new_b < 0.0: new_b = 0.0
    if new_b > 255.0: new_b = 255.0
    
    error_r = r - new_r
    error_g = g - new_g
    error_b = b - new_b
    
    # The return statement's structure remains correct
    return (new_r, new_g, new_b), (error_r, error_g, error_b)

# This function remains without changes
@jit(nopython=True)
def _apply_dithering_numba(pixels, width, height):
    for y in range(height):
        for x in range(width):
            old_r, old_g, old_b = pixels[y, x]
            
            (new_r, new_g, new_b), (error_r, error_g, error_b) = _quantize_and_get_error_numba(old_r, old_g, old_b)
            
            pixels[y, x] = (new_r, new_g, new_b)
            
            if x < width - 1:
                pixels[y, x + 1, 0] += error_r * 7 / 16.0
                pixels[y, x + 1, 1] += error_g * 7 / 16.0
                pixels[y, x + 1, 2] += error_b * 7 / 16.0
            
            if y < height - 1:
                if x > 0:
                    pixels[y + 1, x - 1, 0] += error_r * 3 / 16.0
                    pixels[y + 1, x - 1, 1] += error_g * 3 / 16.0
                    pixels[y + 1, x - 1, 2] += error_b * 3 / 16.0
                
                pixels[y + 1, x, 0] += error_r * 5 / 16.0
                pixels[y + 1, x, 1] += error_g * 5 / 16.0
                pixels[y + 1, x, 2] += error_b * 5 / 16.0
                
                if x < width - 1:
                    pixels[y + 1, x + 1, 0] += error_r * 1 / 16.0
                    pixels[y + 1, x + 1, 1] += error_g * 1 / 16.0
                    pixels[y + 1, x + 1, 2] += error_b * 1 / 16.0


            
class StreamPipeline:
    """
    Управляет одним потоковым пайплайном: прослушивание порта, генерация кадров,
    обработка и отправка клиенту.
    """
    def __init__(self, device_config, global_server_stop_event, prometheus_metrics_objects):
        self.config = device_config
        self.name = self.config.get('name', 'UnnamedPipeline')
        self.port = self.config['esp32_port'] # Обязательный параметр
        self.global_server_stop_event = global_server_stop_event
        self.metrics = prometheus_metrics_objects # Словарь с объектами метрик Prometheus

        self.frames_queue = queue.Queue(maxsize=self.config.get('frames_queue_max_size', 5))
        self.pipeline_internal_stop_event = threading.Event() # Для остановки генератора/потребителя этого пайплайна

        self.server_socket = None # Серверный сокет для прослушивания
        self.client_connection = None # Активное соединение с клиентом
        self.manager_thread = None # Поток, в котором выполняется _listening_loop

        self._generator_thread = None # Поток для _generator_loop
        self._consumer_thread = None  # Поток для _consumer_loop
        self._generator_instance = None # Экземпляр генератора (CpuMonitorGenerator, etc.)
        self._sct_instance_local_to_generator_thread = None # Экземпляр mss.mss(), создаваемый в потоке генератора

        # Состояние для потребителя, сбрасывается для каждой новой сессии клиента
        self._prev_processed_image = None
        self._current_dynamic_threshold = self.config.get('min_dirty_rect_threshold', 10)
        self._frame_processing_times_history = deque(maxlen=self.config.get('fps_history_size', 10))

        self._log(f"Экземпляр Pipeline создан для порта {self.port}.")

    def _log(self, message, level="INFO"):
        """Логирование сообщений с именем пайплайна."""
        print(f"[{level}][{self.name}] {message}")

    def _initialize_generator_instance(self):
        """Инициализирует или переинициализирует конкретный генератор изображений."""
        self._log("Инициализация настроек для генератора...")
        source_mode = self.config['image_source_mode']
        generator_canvas_resolution = (self.config['target_width'], self.config['target_height'])

        # Очистка предыдущих экземпляров (важно при переподключении клиента)
        if self._generator_instance and hasattr(self._generator_instance, 'stop'):
            try: self._generator_instance.stop()
            except Exception as e: self._log(f"Ошибка при остановке предыдущего генератора: {e}", "WARN")
        # _sct_instance_local_to_generator_thread будет очищен в конце _generator_loop
        self._generator_instance = None


        try:
            if source_mode == "CPU_MONITOR":
                self._generator_instance = CpuMonitorGenerator(
                    resolution=generator_canvas_resolution,
                    history_length=self.config.get('cpu_monitor_history_length', 60),
                    update_interval=self.config.get('cpu_monitor_update_interval', 0.5),
                    font_path=self.config.get('cpu_monitor_font_path', self.config.get('font_path', "arial.ttf"))
                )
            elif source_mode == "PROMETHEUS_MONITOR":
                self._generator_instance = PrometheusMonitorGenerator(
                    prometheus_url=self.config.get('prometheus_url', "http://127.0.0.1:9090/"),
                    resolution=generator_canvas_resolution,
                    font_path=self.config.get('prometheus_font_path', self.config.get('font_path', "arial.ttf")),
                    colors=self.config.get('prometheus_colors'),
                    metric_config=self.config.get('prometheus_metric_config'),
                    grid_layout=self.config.get('prometheus_grid_layout'),
                    history_length=self.config.get('prometheus_history_length', 120),
                    update_interval=self.config.get('prometheus_update_interval', 1.0),
                    # Передача размеров шрифтов из конфига, которые вызывают ошибку:
                    title_font_size=self.config.get('prometheus_title_font_size', 18),
                    value_font_size=self.config.get('prometheus_value_font_size', 36),
                    unit_font_size=self.config.get('prometheus_unit_font_size', 20),
                )
            elif source_mode == "WINDOW_CAPTURE":
                self._generator_instance = WindowScreenshotter(self.config.get('window_title', None),
                                                               self.config.get('crop_alignment', 'left'))
            elif source_mode == "SCREEN_CAPTURE": 
                capture_region = self.config.get('capture_region')
                if not capture_region:
                    self._log("capture_region не указан для режима SCREEN_CAPTURE!", "ERROR")
                    return False
                # Экземпляр mss.mss() будет создан в _generator_loop
            elif source_mode == "BIOS":
                pass # Для BIOS не нужен отдельный долгоживущий экземпляр генератора
            else:
                self._log(f"Неподдерживаемый image_source_mode: {source_mode}", "ERROR")
                return False
            self._log(f"Настройки для генератора '{source_mode}' успешно подготовлены.")
            return True
        except Exception as e:
            self._log(f"КРИТИЧЕСКАЯ ОШИБКА подготовки генератора для '{source_mode}': {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return False

    def _apply_gamma_and_white_balance(self, img: Image.Image) -> Image.Image:
        """Применяет гамма-коррекцию и баланс белого к изображению."""
        if img.mode != 'RGB': img = img.convert('RGB')
        img_array = np.array(img, dtype=np.float32) / 255.0
        
        gamma = self.config.get('gamma', 1.0)
        img_corrected = np.power(img_array, gamma)
        
        wb_scale_config = self.config.get('wb_scale', (1.0, 1.0, 1.0))
        if not (isinstance(wb_scale_config, (list, tuple)) and len(wb_scale_config) == 3):
            self._log(f"Некорректный формат wb_scale: {wb_scale_config}. Используется (1.0, 1.0, 1.0).", "WARN")
            wb_scale_config = (1.0, 1.0, 1.0)
            
        scale_np = np.array(wb_scale_config).reshape(1, 1, 3)
        img_balanced = img_corrected * scale_np
        
        img_final_np = np.clip(img_balanced * 255.0, 0, 255).astype(np.uint8)

        # Меняем местами 0-й (R) и 2-й (B) каналы
        # Способ 1: Явное присваивание (возможно, безопаснее)

        return Image.fromarray(img_final_np, 'RGB')

    def _apply_dithering_to_rgb565_bytes(self, img: Image.Image) -> bytes:
        processing_start_time = time.monotonic()
        try:
            processed_img = self._apply_gamma_and_white_balance(img)
        except Exception as e:
            self._log(f"Ошибка при применении гаммы/ББ: {e}. Используется исходное изображение.", "WARN")
            processed_img = img.convert('RGB') if img.mode != 'RGB' else img
        self.metrics['frame_processing_time'].labels(stage='color_correction', pipeline_name=self.name).observe(time.monotonic() - processing_start_time)

        conversion_start_time = time.monotonic()

        if processed_img.mode != 'RGB':
            processed_img = processed_img.convert('RGB')
        
        pixels = np.array(processed_img, dtype=np.float32)
        width, height = processed_img.size
        
        _apply_dithering_numba(pixels, width, height)
        
        pixels = np.clip(pixels, 0, 255).astype(np.uint8)
        r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        
        rgb565_val = ((r >> 3).astype(np.uint16) << 11) | \
                    ((g >> 2).astype(np.uint16) << 5)  | \
                    (b >> 3).astype(np.uint16)

        byte_data = rgb565_val.astype('>H').tobytes()
        
        self.metrics['frame_processing_time'].labels(stage='rgb565_conversion', pipeline_name=self.name).observe(time.monotonic() - conversion_start_time)

        return byte_data


    def _image_to_rgb565_bytes(self, img: Image.Image) -> bytes:
        """Конвертирует PIL Image в байты RGB565 после применения коррекций."""
        processing_start_time = time.monotonic()
        try:
            processed_img = self._apply_gamma_and_white_balance(img)
        except Exception as e:
            self._log(f"Ошибка при применении гаммы/ББ: {e}. Используется исходное изображение.", "WARN")
            processed_img = img.convert('RGB') if img.mode != 'RGB' else img
        self.metrics['frame_processing_time'].labels(stage='color_correction', pipeline_name=self.name).observe(time.monotonic() - processing_start_time)

        conversion_start_time = time.monotonic()
        # Убедимся, что processed_img это RGB перед загрузкой пикселей
        if processed_img.mode != 'RGB':
            processed_img = processed_img.convert('RGB')
            
        pixels = processed_img.load()
        width, height = processed_img.size
        byte_data = bytearray(width * height * 2)
        idx = 0
        for y_coord in range(height):
            for x_coord in range(width):
                try:
                    pixel_val = pixels[x_coord, y_coord]
                    if isinstance(pixel_val, int): # для 'L' или 'P' мода после неудачной конвертации
                         r, g, b = pixel_val, pixel_val, pixel_val
                    else:
                         r, g, b = pixel_val[:3] # Берем первые 3 компоненты, если есть альфа
                except (TypeError, IndexError) as e:
                    self._log(f"Некорректный пиксель ({pixels[x_coord, y_coord]}, mode: {processed_img.mode}) при конвертации в RGB565: {e}. Пропуск кадра.", "ERROR")
                    return b'' 
                rgb565_val = rgb_to_rgb565(r, g, b)
                struct.pack_into('!H', byte_data, idx, rgb565_val)
                idx += 2
        self.metrics['frame_processing_time'].labels(stage='rgb565_conversion', pipeline_name=self.name).observe(time.monotonic() - conversion_start_time)
        return bytes(byte_data)

    def _find_dirty_rects(self, img_prev: Image.Image | None, img_curr: Image.Image):
        """Находит измененные прямоугольники между двумя изображениями."""
        diff_start_time = time.monotonic()
        
        current_img_rgb = img_curr.convert('RGB') if img_curr.mode != 'RGB' else img_curr

        if img_prev is None or img_prev.size != current_img_rgb.size:
            self.metrics['frame_processing_time'].labels(stage='diff_calculation', pipeline_name=self.name).observe(time.monotonic() - diff_start_time)
            yield (0, 0, current_img_rgb.width, current_img_rgb.height)
            return

        img_prev_rgb = img_prev.convert('RGB') if img_prev.mode != 'RGB' else img_prev
        
        arr_prev = np.array(img_prev_rgb, dtype=np.int16)
        arr_curr = np.array(current_img_rgb, dtype=np.int16)

        if arr_prev.shape != arr_curr.shape:
            self._log(f"Расхождение в размерах массивов при поиске dirty_rects: prev{arr_prev.shape}, curr{arr_curr.shape}. Отправка полного кадра.", "WARN")
            self.metrics['frame_processing_time'].labels(stage='diff_calculation', pipeline_name=self.name).observe(time.monotonic() - diff_start_time)
            yield (0, 0, current_img_rgb.width, current_img_rgb.height)
            return

        abs_diff_arr = np.sum(np.abs(arr_curr - arr_prev), axis=2)
        changed_pixels_mask = abs_diff_arr > self._current_dynamic_threshold
        
        changed_y_coords, changed_x_coords = np.where(changed_pixels_mask)

        if changed_y_coords.size > 0:
            min_x = int(np.min(changed_x_coords))
            max_x = int(np.max(changed_x_coords))
            min_y = int(np.min(changed_y_coords))
            max_y = int(np.max(changed_y_coords))
            rect_w = max_x - min_x + 1
            rect_h = max_y - min_y + 1
            yield (min_x, min_y, rect_w, rect_h)
        
        self.metrics['frame_processing_time'].labels(stage='diff_calculation', pipeline_name=self.name).observe(time.monotonic() - diff_start_time)

    def _pack_update_packet(self, x, y, w, h, data: bytes) -> bytes:
        """Упаковывает данные обновления в пакет с заголовком."""
        pack_start_time = time.monotonic()
        data_len = len(data)
        header = struct.pack('!HHHH I', x, y, w, h, data_len)
        packet = header + data
        self.metrics['frame_processing_time'].labels(stage='packet_packing', pipeline_name=self.name).observe(time.monotonic() - pack_start_time)
        self.metrics['packet_size_bytes'].labels(pipeline_name=self.name).observe(len(packet))
        return packet

    def _generator_loop(self):
        self._log("Поток генератора запускается.")
        
        source_mode = self.config['image_source_mode']
        sct_instance_local = None # Локальный экземпляр mss для этого потока

        if source_mode == "SCREEN_CAPTURE":
            try:
                sct_instance_local = mss.mss()
                self._log("Экземпляр MSS (sct) успешно создан в потоке генератора.")
                # Сохраняем ссылку, чтобы можно было закрыть в _cleanup_active_session, если поток аварийно завершится
                self._sct_instance_local_to_generator_thread = sct_instance_local
            except Exception as e:
                self._log(f"КРИТИЧЕСКАЯ ОШИБКА создания MSS в потоке генератора: {e}", "ERROR")
                self.pipeline_internal_stop_event.set() # Останавливаем пайплайн
                return 

        target_interval = self.config.get('generator_target_interval_sec', 0.05)
        low_water_mark = self.config.get('generator_low_water_mark', 2)

        while not self.pipeline_internal_stop_event.is_set() and not self.global_server_stop_event.is_set():
            loop_start_time = time.monotonic()
            q_size = self.frames_queue.qsize()
            self.metrics['frames_queue_size'].labels(pipeline_name=self.name).observe(q_size)

            if q_size < low_water_mark:
                gen_start_time = time.monotonic()
                generated_image: Image.Image | None = None
                metric_stage_label = f"generate_{source_mode.lower()}"
                canvas_resolution = (self.config['target_width'], self.config['target_height'])

                try:
                    if source_mode == "SCREEN_CAPTURE":
                        if sct_instance_local:
                            capture_region = self.config['capture_region']
                            sct_img = sct_instance_local.grab(capture_region)
                            generated_image = Image.frombytes('RGB', (sct_img.width, sct_img.height), sct_img.rgb, 'raw', 'RGB')
                        else:
                            self._log("Экземпляр MSS (sct) не доступен в генераторе SCREEN_CAPTURE!", "ERROR")
                            self.pipeline_internal_stop_event.set(); break 
                    elif source_mode == "BIOS":
                        generated_image = Image.new('RGB', canvas_resolution)
                        draw_bios_on_image(generated_image) # TODO: Передать параметры шрифта/цветов из config если нужно
                    elif self._generator_instance: # CPU_MONITOR или PROMETHEUS_MONITOR или WINDOW_CAPTURE
                        if self._generator_instance.resolution != canvas_resolution and source_mode != "PROMETHEUS_MONITOR": # Prometheus может иметь свою логику разрешения
                             self._log(f"Разрешение генератора ({source_mode}) {self._generator_instance.resolution} не совпадает с целевым холстом {canvas_resolution}!", "WARN")
                        
                        bg_color_tuple = (0,0,0) # Default background
                        if hasattr(self._generator_instance, '_colors') and isinstance(self._generator_instance._colors, dict):
                            bg_color_conf = self._generator_instance._colors.get("background")
                            if isinstance(bg_color_conf, (list, tuple)) and len(bg_color_conf) == 3:
                                bg_color_tuple = tuple(bg_color_conf)

                        canvas = Image.new('RGB', self._generator_instance.resolution, color=bg_color_tuple)

                        if hasattr(self._generator_instance, 'draw_frame'):
                            self._generator_instance.draw_frame(canvas)
                        elif hasattr(self._generator_instance, 'generate_image_frame'):
                            self._generator_instance.generate_image_frame(canvas)
                        else:
                            self._log(f"У генератора {type(self._generator_instance)} нет ожидаемого метода отрисовки.", "ERROR")
                            self.pipeline_internal_stop_event.set(); break
                        generated_image = canvas
                    else:
                        self._log(f"Генератор для режима '{source_mode}' не инициализирован или недоступен.", "WARN")
                        generated_image = Image.new('RGB', canvas_resolution, color="red")
                        draw = ImageDraw.Draw(generated_image)
                        try: draw.text((10,10), f"Error: Gen {source_mode}", fill="white")
                        except: pass 
                        time.sleep(0.5)

                    if generated_image:
                        self.metrics['frame_processing_time'].labels(stage=metric_stage_label, pipeline_name=self.name).observe(time.monotonic() - gen_start_time)
                        self.frames_queue.put(generated_image, timeout=0.1)
                        self.metrics['frames_generated_total'].labels(pipeline_name=self.name).inc()

                except mss.exception.ScreenShotError as e:
                    self._log(f"Ошибка захвата экрана: {e}. Попытка переинициализации mss...", "WARN")
                    if sct_instance_local:
                        try: sct_instance_local.close()
                        except: pass
                    try:
                        sct_instance_local = mss.mss() # Попытка пересоздать
                        self._sct_instance_local_to_generator_thread = sct_instance_local
                        self._log("Экземпляр MSS (sct) пересоздан после ошибки.")
                    except Exception as e_reinit:
                        self._log(f"Не удалось пересоздать MSS после ошибки: {e_reinit}. Остановка генератора.", "ERROR")
                        self.pipeline_internal_stop_event.set(); break
                    time.sleep(1.0) # Пауза после переинициализации
                    continue
                except queue.Full:
                    pass 
                except Exception as e:
                    self._log(f"Ошибка в цикле генератора (режим: {source_mode}): {e}", "ERROR")
                    import traceback
                    traceback.print_exc()
                    time.sleep(0.1)

                elapsed_this_loop = time.monotonic() - loop_start_time
                sleep_duration = target_interval - elapsed_this_loop
                if sleep_duration > 0:
                    self.pipeline_internal_stop_event.wait(sleep_duration)
            else: 
                self.pipeline_internal_stop_event.wait(0.01)

        # Очистка при выходе из цикла
        if sct_instance_local: # Закрываем локальный экземпляр mss
            try:
                sct_instance_local.close()
                self._log("Локальный экземпляр MSS (sct) закрыт.")
            except Exception as e:
                self._log(f"Ошибка при закрытии локального экземпляра MSS: {e}", "WARN")
        self._sct_instance_local_to_generator_thread = None # Сбрасываем ссылку

        self._log("Поток генератора остановлен.")

    def _consumer_loop(self):
        """Цикл потока обработки и отправки кадров клиенту."""
        self._log("Поток потребителя запускается.")
        self._prev_processed_image = None
        self._current_dynamic_threshold = self.config.get('min_dirty_rect_threshold', 10)
        self._frame_processing_times_history.clear()

        self.metrics['current_dynamic_threshold'].labels(pipeline_name=self.name).set(self._current_dynamic_threshold)
        self.metrics['consumer_calculated_fps'].labels(pipeline_name=self.name).set(0)

        target_fps_val = self.config.get('target_fps', 15.0)
        history_size = self.config.get('fps_history_size', 10)
        hyst_factor = self.config.get('fps_hysteresis_factor', 0.1)
        min_thresh = self.config.get('min_dirty_rect_threshold', 5)
        max_thresh = self.config.get('max_dirty_rect_threshold', 220)
        step_up = self.config.get('threshold_adjustment_step_up', 10)
        step_down = self.config.get('threshold_adjustment_step_down', 5)
        max_chunk_data = self.config.get('max_chunk_data_size', 8192)

        while not self.pipeline_internal_stop_event.is_set() and not self.global_server_stop_event.is_set():
            try:
                raw_frame = self.frames_queue.get(timeout=0.1)
                q_size = self.frames_queue.qsize()
                self.metrics['frames_queue_size'].labels(pipeline_name=self.name).observe(q_size)

                loop_processing_start_time = time.monotonic()

                if not isinstance(raw_frame, Image.Image):
                    self._log(f"Получен неверный тип кадра: {type(raw_frame)}. Пропуск.", "WARN")
                    self.frames_queue.task_done(); continue
                
                resize_start_time = time.monotonic()
                img_resized = raw_frame.resize(
                    (self.config['target_width'], self.config['target_height']),
                    Image.Resampling.LANCZOS
                )
                self.metrics['frame_processing_time'].labels(stage='resize_thread', pipeline_name=self.name).observe(time.monotonic() - resize_start_time)

                dirty_rects_list = list(self._find_dirty_rects(self._prev_processed_image, img_resized))

                socket_error_this_frame = False
                chunks_sent_this_frame = 0
                send_duration_this_frame_start = 0

                if dirty_rects_list:
                    send_duration_this_frame_start = time.monotonic()
                    for x, y, w, h in dirty_rects_list:
                        full_rect_data_size = w * h * 2
                        if full_rect_data_size > max_chunk_data:
                            bytes_per_row = w * 2
                            if bytes_per_row == 0: continue
                            chunk_h = max(1, max_chunk_data // bytes_per_row if bytes_per_row > 0 else h)
                            
                            for current_y_offset in range(0, h, chunk_h):
                                actual_chunk_h = min(chunk_h, h - current_y_offset)
                                if actual_chunk_h <= 0: continue

                                chunk_img_to_send = img_resized.crop((x, y + current_y_offset, x + w, y + current_y_offset + actual_chunk_h))
                                chunk_data_bytes = self._apply_dithering_to_rgb565_bytes(chunk_img_to_send)
                                if not chunk_data_bytes: continue

                                packet_to_send = self._pack_update_packet(x, y + current_y_offset, w, actual_chunk_h, chunk_data_bytes)
                                
                                try:
                                    if not self.client_connection: raise socket.error("Client connection is None")
                                    self.client_connection.sendall(packet_to_send)
                                    chunks_sent_this_frame +=1
                                except socket.error as e:
                                    self._log(f"Ошибка сокета при отправке чанка: {e}", "WARN")
                                    self.metrics['connection_errors_total'].labels(pipeline_name=self.name).inc()
                                    socket_error_this_frame = True; break 
                            if socket_error_this_frame: break
                        else: 
                            region_img_to_send = img_resized.crop((x,y, x+w, y+h))
                            region_data_bytes = self._apply_dithering_to_rgb565_bytes(region_img_to_send)
                            if not region_data_bytes: continue

                            packet_to_send = self._pack_update_packet(x,y,w,h,region_data_bytes)
                            try:
                                if not self.client_connection: raise socket.error("Client connection is None")
                                self.client_connection.sendall(packet_to_send)
                                chunks_sent_this_frame += 1
                            except socket.error as e:
                                self._log(f"Ошибка сокета при отправке региона: {e}", "WARN")
                                self.metrics['connection_errors_total'].labels(pipeline_name=self.name).inc()
                                socket_error_this_frame = True; break
                    
                    if socket_error_this_frame:
                        raise socket.error("Ошибка сокета при отправке данных кадра") 

                    self.metrics['chunks_per_frame'].labels(pipeline_name=self.name).observe(chunks_sent_this_frame)
                    if send_duration_this_frame_start > 0 :
                        self.metrics['dirty_rects_send_duration_seconds'].labels(pipeline_name=self.name).observe(time.monotonic() - send_duration_this_frame_start)

                self._prev_processed_image = img_resized
                self.metrics['frames_processed_total'].labels(pipeline_name=self.name).inc()

                frame_total_processing_time = time.monotonic() - loop_processing_start_time
                self._frame_processing_times_history.append(frame_total_processing_time)
                
                if len(self._frame_processing_times_history) >= history_size and history_size > 0:
                    avg_time = sum(self._frame_processing_times_history) / len(self._frame_processing_times_history)
                    current_fps = 1.0 / avg_time if avg_time > 0 else 0.0
                    self.metrics['consumer_calculated_fps'].labels(pipeline_name=self.name).set(current_fps)

                    hysteresis = target_fps_val * hyst_factor
                    old_thresh = self._current_dynamic_threshold

                    if current_fps < target_fps_val - hysteresis:
                        self._current_dynamic_threshold = min(max_thresh, self._current_dynamic_threshold + step_up)
                    elif current_fps > target_fps_val + hysteresis:
                         self._current_dynamic_threshold = max(min_thresh, self._current_dynamic_threshold - step_down)
                    
                    if old_thresh != self._current_dynamic_threshold:
                        self.metrics['current_dynamic_threshold'].labels(pipeline_name=self.name).set(self._current_dynamic_threshold)
                
                self.metrics['frame_processing_time'].labels(stage='full_consumer_loop_thread', pipeline_name=self.name).observe(frame_total_processing_time)
                self.frames_queue.task_done()

            except queue.Empty:
                continue 
            except socket.error as e: 
                self._log(f"Ошибка сокета в цикле потребителя: {e}. Остановка потребителя для текущей сессии.", "WARN")
                self.metrics['consumer_calculated_fps'].labels(pipeline_name=self.name).set(0)
                break 
            except Exception as e:
                self._log(f"Неожиданная ошибка в цикле потребителя: {e}", "ERROR")
                import traceback
                traceback.print_exc()
                self.metrics['consumer_calculated_fps'].labels(pipeline_name=self.name).set(0)
                break

        self._log("Поток потребителя остановлен.")

    def _cleanup_active_session(self):
        """Останавливает внутренние потоки и закрывает клиентское соединение."""
        self._log("Очистка активной сессии клиента...")
        if not self.pipeline_internal_stop_event.is_set():
            self.pipeline_internal_stop_event.set()

        if self._generator_thread and self._generator_thread.is_alive():
            self._log("Ожидание остановки потока генератора...")
            self._generator_thread.join(timeout=2)
            if self._generator_thread.is_alive(): self._log("Поток генератора не остановился.", "WARN")
        self._generator_thread = None 

        if self._consumer_thread and self._consumer_thread.is_alive():
            self._log("Ожидание остановки потока потребителя...")
            self._consumer_thread.join(timeout=3) 
            if self._consumer_thread.is_alive(): self._log("Поток потребителя не остановился.", "WARN")
        self._consumer_thread = None 

        if self.client_connection:
            try:
                self.client_connection.close()
                self._log("Клиентское соединение закрыто.")
            except Exception as e:
                self._log(f"Ошибка при закрытии клиентского соединения: {e}", "WARN")
        self.client_connection = None
        
        if self._generator_instance and hasattr(self._generator_instance, 'stop'):
            try:
                self._log(f"Остановка экземпляра генератора ({type(self._generator_instance).__name__})...")
                self._generator_instance.stop()
            except Exception as e: self._log(f"Ошибка при вызове stop() для экземпляра генератора: {e}", "WARN")
        self._generator_instance = None

        if self._sct_instance_local_to_generator_thread: # Проверяем и закрываем, если был создан
            try:
                self._log("Закрытие локального экземпляра mss (если был)...")
                self._sct_instance_local_to_generator_thread.close()
            except Exception as e: self._log(f"Ошибка при закрытии локального экземпляра sct: {e}", "WARN")
        self._sct_instance_local_to_generator_thread = None
        
        self._log("Очистка очереди кадров...")
        while not self.frames_queue.empty():
            try: self.frames_queue.get_nowait(); self.frames_queue.task_done()
            except queue.Empty: break
        self._prev_processed_image = None # Сброс для следующей сессии
        self._log("Активная сессия очищена.")

    def start_pipeline_manager(self):
        """Запускает основной цикл прослушивания и управления пайплайном в отдельном потоке."""
        self.manager_thread = threading.Thread(target=self._listening_loop, daemon=True, name=f"Manager_{self.name}")
        self.manager_thread.start()
        self._log("Управляющий поток (менеджер пайплайна) запущен.")

    def stop_pipeline_manager(self):
        """Инициирует полную остановку пайплайна (вызывается извне)."""
        self._log("Получен внешний сигнал на полную остановку пайплайна.")
        if not self.pipeline_internal_stop_event.is_set(): # Сигнал для generator/consumer
            self.pipeline_internal_stop_event.set()
        # global_server_stop_event уже должен быть установлен извне для остановки _listening_loop

        if self.server_socket: # Закрываем слушающий сокет, чтобы прервать accept
            try:
                self.server_socket.close() 
                self._log("Серверный (слушающий) сокет закрыт для прерывания accept.")
            except Exception as e:
                self._log(f"Ошибка при закрытии серверного сокета во время остановки: {e}", "WARN")
        
    def join_manager_thread(self, timeout=None):
        """Ожидает завершения управляющего потока пайплайна (_listening_loop)."""
        if self.manager_thread and self.manager_thread.is_alive():
            self._log(f"Ожидание завершения управляющего потока (timeout={timeout}s)...")
            self.manager_thread.join(timeout=timeout)
            if self.manager_thread.is_alive():
                self._log(f"Управляющий поток не завершился за {timeout}s.", "WARN")
            else:
                self._log("Управляющий поток успешно завершен.")

    def _listening_loop(self):
        """Основной цикл менеджера пайплайна."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(1.0) 

        try:
            self.server_socket.bind(('', self.port))
            self.server_socket.listen(1)
            self._log(f"Прослушивание порта {self.port} успешно запущено...")
        except Exception as e:
            self._log(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось забиндить или слушать порт {self.port}: {e}", "ERROR")
            if self.server_socket: self.server_socket.close() 
            return 

        while not self.global_server_stop_event.is_set():
            try:
                self.client_connection, client_address = self.server_socket.accept()
                self.client_connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_connection.settimeout(self.config.get('socket_timeout', 2.0))
                
                self._log(f"Клиент {client_address} успешно подключен.")
                self.metrics['reconnections_total'].labels(pipeline_name=self.name).inc()

                self.pipeline_internal_stop_event.clear()

                if not self._initialize_generator_instance():
                    self._log("Не удалось инициализировать экземпляр генератора. Закрытие соединения.", "ERROR")
                    if self.client_connection: self.client_connection.close()
                    self.client_connection = None
                    continue

                self._generator_thread = threading.Thread(target=self._generator_loop, daemon=True, name=f"{self.name}_Gen")
                self._consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True, name=f"{self.name}_Con")

                self._generator_thread.start()
                self._consumer_thread.start()

                while self._consumer_thread.is_alive() and not self.global_server_stop_event.is_set():
                    self._consumer_thread.join(timeout=0.2) 

                if self.global_server_stop_event.is_set():
                    self._log("Получен глобальный сигнал остановки сервера во время активной сессии клиента.")
                elif not self._consumer_thread.is_alive(): 
                    self._log("Поток потребителя завершил работу.")
                
                self._cleanup_active_session()

            except socket.timeout: 
                continue 
            except OSError as e:
                if self.global_server_stop_event.is_set(): # Ошибка из-за закрытия сокета при остановке
                    self._log(f"Ошибка сокета '{e}' при accept, вероятно, из-за остановки сервера.")
                    break 
                else: # Другая ошибка сокета
                    self._log(f"Ошибка сокета '{e}' при accept. Пауза перед повторной попыткой...", "ERROR")
                    time.sleep(1) 
            except Exception as e:
                self._log(f"Неожиданная ошибка в цикле прослушивания/подключения: {e}", "ERROR")
                import traceback
                traceback.print_exc()
                if self.client_connection or self._consumer_thread or self._generator_thread:
                     self._cleanup_active_session()
                time.sleep(1)

        self._log("Получен сигнал глобальной остановки или критическая ошибка. Завершение цикла прослушивания.")
        self._cleanup_active_session() 
        
        if self.server_socket:
            try:
                self.server_socket.close()
                self._log("Серверный (слушающий) сокет успешно закрыт.")
            except Exception as e_sock:
                self._log(f"Ошибка при закрытии серверного (слушающего) сокета: {e_sock}", "WARN")
        
        self._log("Управляющий поток (менеджер пайплайна) полностью остановлен.")