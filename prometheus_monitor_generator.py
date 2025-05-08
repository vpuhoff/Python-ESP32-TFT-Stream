# prometheus_monitor_generator.py

import time
import threading
import math
from collections import deque
from PIL import Image # Для создания начального холста
# Импорт нового графического движка
from graphics_engine import MonitorGraphicsEngine # Убедитесь, что graphics_engine.py доступен
# Клиент Prometheus все еще нужен здесь
from prometheus_api_client import PrometheusConnect, PrometheusApiClientException
from copy import deepcopy # Для глубокого копирования словарей и списков

# --- Конфигурация по умолчанию для этого модуля (может быть переопределена извне) ---
DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090/"
DEFAULT_RESOLUTION = (640, 480) # Это разрешение холста, на котором будет рисовать graphics_engine
DEFAULT_HISTORY_LENGTH = 120    # Количество точек истории для хранения и отображения
DEFAULT_UPDATE_INTERVAL = 1.0   # Интервал обновления данных Prometheus в секундах
DEFAULT_FONT_PATH = "arial.ttf" # Путь к файлу шрифта по умолчанию

# Размеры шрифтов по умолчанию (будут переданы в graphics_engine)
DEFAULT_TITLE_FONT_SIZE = 18
DEFAULT_VALUE_FONT_SIZE = 36
DEFAULT_UNIT_FONT_SIZE = 20

# Цвета по умолчанию (будут переданы в graphics_engine)
DEFAULT_MAIN_COLOR = (0, 255, 255) # Яркий циан/бирюзовый
DEFAULT_COLORS = {
    "background": (10, 15, 25),
    "foreground": (200, 220, 220),
    "value_color": DEFAULT_MAIN_COLOR,
    "graph_line": DEFAULT_MAIN_COLOR, # Общий цвет для графиков по умолчанию
    "grid_lines": (80, 110, 140),
    "cell_border": (60, 80, 100),
    "error": (255, 80, 80),
    # Можно добавить специфичные цвета для графиков, если graphics_engine их ожидает
    # "gpu_load_color": DEFAULT_MAIN_COLOR, # Пример
}

# Конфигурация метрик по умолчанию
# Эта структура определяет, какие метрики запрашивать и как их отображать.
# 'query_used' и 'query_total' для RAM, 'query_write' и 'query_read' для диска.
DEFAULT_METRIC_CONFIG = {
    "gpu_load": {
        "title": "GPU LOAD",
        "query": 'avg(nvidia_smi_utilization_gpu_ratio * 100) or on() vector(0)', # or on() vector(0) для значения по умолчанию
        "unit": "%",
        "color": DEFAULT_COLORS["graph_line"], # Используем общий цвет или можно задать специфичный
        "range": (0, 100), # Диапазон для оси Y графика
    },
    "gpu_ram": {
        "title": "GPU RAM",
        "query": 'avg(nvidia_smi_memory_used_bytes / nvidia_smi_memory_total_bytes * 100) or on() vector(0)',
        "unit": "%",
        "color": DEFAULT_COLORS["graph_line"],
        "range": (0, 100),
    },
    "gpu_temp": {
        "title": "GPU TEMP",
        "query": 'avg(nvidia_smi_temperature_gpu) or on() vector(0)',
        "unit": "°C",
        "color": DEFAULT_COLORS["graph_line"],
        "range": (20, 100),
    },
    "cpu_load": {
        "title": "CPU LOAD",
        # Запрос для Windows. Для Linux/macOS используйте node_exporter метрики, например:
        # 'avg(100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)) or on() vector(0)'
        "query": '(1 - avg(rate(windows_cpu_time_total{mode="idle"}[1m]))) * 100 or on() vector(0)',
        "unit": "%",
        "color": DEFAULT_COLORS["graph_line"],
        "range": (0, 100),
    },
    "ram_usage": {
        "title": "RAM USAGE",
        # Запросы для Windows. Для Linux/macOS:
        # 'query_used': 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes or on() vector(0)',
        # 'query_total': 'node_memory_MemTotal_bytes or on() vector(0)',
        "query_used": 'windows_os_visible_memory_bytes - windows_os_physical_memory_free_bytes or on() vector(0)',
        "query_total": 'windows_os_visible_memory_bytes or on() vector(0)', # Используется для динамического диапазона графика
        "unit": "GB", # Единица отображения значения
        "color": DEFAULT_COLORS["graph_line"],
        "range": None, # Динамический диапазон (0 до total RAM)
    },
    "disk_usage": {
        "title": "DISK R/W",
        # Запросы для Windows. Для Linux/macOS:
        # 'query_write': 'sum(rate(node_disk_written_bytes_total[1m])) or on() vector(0)',
        # 'query_read': 'sum(rate(node_disk_read_bytes_total[1m])) or on() vector(0)',
        "query_write": 'sum(rate(windows_logical_disk_write_bytes_total[1m])) or on() vector(0)',
        "query_read": 'sum(rate(windows_logical_disk_read_bytes_total[1m])) or on() vector(0)',
        "unit": "B/s", # Базовая единица для форматирования отображения
        "color_write": DEFAULT_COLORS["graph_line"], # Можно задать разные цвета
        "color_read": (0, 180, 200), # Пример другого цвета для чтения
        "range": None, # Динамический диапазон на основе недавнего максимума
    },
}

# Расположение ячеек на сетке по умолчанию
DEFAULT_GRID_LAYOUT = [
    ["gpu_load", "gpu_ram", "gpu_temp"],
    ["cpu_load", "ram_usage", "disk_usage"],
]


class PrometheusMonitorGenerator:
    """
    Извлекает системные метрики из Prometheus и использует графический движок
    для генерации кадров визуализации.
    """
    def __init__(self,
                 resolution=DEFAULT_RESOLUTION,
                 font_path=DEFAULT_FONT_PATH,
                 colors=None, # Ожидается словарь, если None, используются DEFAULT_COLORS
                 metric_config=None, # Ожидается словарь, если None, DEFAULT_METRIC_CONFIG
                 grid_layout=None, # Ожидается список списков, если None, DEFAULT_GRID_LAYOUT
                 title_font_size=DEFAULT_TITLE_FONT_SIZE,
                 value_font_size=DEFAULT_VALUE_FONT_SIZE,
                 unit_font_size=DEFAULT_UNIT_FONT_SIZE,
                 prometheus_url=DEFAULT_PROMETHEUS_URL,
                 history_length=DEFAULT_HISTORY_LENGTH,
                 update_interval=DEFAULT_UPDATE_INTERVAL
                 ):
        print("Инициализация PrometheusMonitorGenerator...")
        self.prometheus_url = prometheus_url
        self.resolution = resolution # Разрешение холста для отрисовки
        self.history_length = history_length
        self.update_interval = update_interval

        # Используем переданные конфигурации или значения по умолчанию
        self._font_path = font_path
        self._colors = colors if colors is not None else deepcopy(DEFAULT_COLORS)
        self._metric_config = metric_config if metric_config is not None else deepcopy(DEFAULT_METRIC_CONFIG)
        self._grid_layout = grid_layout if grid_layout is not None else deepcopy(DEFAULT_GRID_LAYOUT)
        
        # Размеры шрифтов
        self._title_font_size = title_font_size
        self._value_font_size = value_font_size
        self._unit_font_size = unit_font_size

        self.prom = None
        try:
            # Таймаут соединения можно настроить через custom_config={'timeout': 5}
            self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
            if not self.prom.check_prometheus_connection():
                print(f"ПРЕДУПРЕЖДЕНИЕ: Начальная проверка соединения с Prometheus не удалась по адресу {self.prometheus_url}. Попытки будут продолжены.")
            else:
                 print(f"Успешное подключение к Prometheus по адресу {self.prometheus_url}")
        except Exception as e:
            print(f"ПРЕДУПРЕЖДЕНИЕ: Не удалось подключиться или проверить Prometheus по адресу {self.prometheus_url}: {e}. Попытки будут продолжены.")

        # Инициализация хранения данных метрик
        self.metric_data = {}
        for key in self._metric_config.keys(): # Итерируемся по ключам из self._metric_config
             sub_metrics = []
             if key == "disk_usage":
                 sub_metrics = ['disk_read', 'disk_write']
                 self.metric_data['disk_range_max'] = 10 * 1024 * 1024 # Начальный максимум для диапазона диска: 10 МБ/с
             elif key == "ram_usage":
                 sub_metrics = ['ram_used', 'ram_total']
             else:
                 sub_metrics = [key] # Для метрик с одним значением (gpu_load, cpu_load и т.д.)

             for sub_key in sub_metrics:
                 # Длина истории 1 для статичных значений (например, total RAM)
                 hist_len = 1 if 'total' in sub_key else self.history_length
                 default_val = 0.0 if 'total' not in sub_key else 0.0 # Или другое значение для total, если нужно
                 self.metric_data[sub_key] = {
                     "history": deque([default_val] * hist_len, maxlen=hist_len),
                     "current": default_val
                 }
        # Если disk_usage не в _metric_config, disk_range_max не будет создан, это нормально,
        # graphics_engine должен будет это обработать (например, не использовать динамический диапазон для диска)
        if 'disk_usage' not in self._metric_config and 'disk_range_max' in self.metric_data:
            del self.metric_data['disk_range_max']


        # --- Инициализация графического движка ---
        try:
             self.graphics = MonitorGraphicsEngine(
                 resolution=self.resolution,
                 font_path=self._font_path,
                 colors=self._colors,
                 grid_layout=self._grid_layout,
                 metric_config=self._metric_config,
                 title_font_size=self._title_font_size,
                 value_font_size=self._value_font_size,
                 unit_font_size=self._unit_font_size,
                 history_length=self.history_length
             )
        except (ValueError, RuntimeError, IOError) as e:
             print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать MonitorGraphicsEngine: {e}")
             raise # Перевыбрасываем критическую ошибку

        # Настройка и запуск потока сбора данных
        self._lock = threading.Lock() # Блокировка для доступа к self.metric_data
        self._stop_event = threading.Event() # Событие для сигнала остановки потока
        self._data_thread = threading.Thread(target=self._data_collection_loop, daemon=True)
        self._data_thread.start()
        print("Фоновый поток сбора данных Prometheus запущен.")
        print("PrometheusMonitorGenerator успешно инициализирован.")

    def _fetch_metric(self, query):
        """Извлекает одно значение метрики из Prometheus."""
        if not self.prom:
            # print("ПРЕДУПРЕЖДЕНИЕ: Соединение с Prometheus недоступно для извлечения метрики.")
            return None 

        try:
            result = self.prom.custom_query(query=query)
            if result and isinstance(result, list) and len(result) > 0 and \
               isinstance(result[0], dict) and 'value' in result[0] and \
               isinstance(result[0]['value'], (list, tuple)) and len(result[0]['value']) == 2:
                raw_value = result[0]['value'][1] # Второе значение - это само значение метрики
                try:
                    if isinstance(raw_value, str) and raw_value.lower() == 'nan':
                         return math.nan 
                    return float(raw_value)
                except (ValueError, TypeError):
                     print(f"ПРЕДУПРЕЖДЕНИЕ: Не удалось конвертировать значение '{raw_value}' в float для запроса '{query}'.")
                     return None
            else:
                # Запрос вернул данные, но не в ожидаемом формате, или пустой результат
                # print(f"ПРЕДУПРЕЖДЕНИЕ: Нет данных или неожиданный формат для запроса '{query}'. Результат: {result}")
                return None
        except PrometheusApiClientException as e: # Специфичная ошибка клиента API
            print(f"ОШИБКА API Prometheus при запросе '{query}': {e}")
            # Можно попытаться переустановить соединение self.prom = None ?
            return None
        except ConnectionError as e: # requests.exceptions.ConnectionError
            print(f"ОШИБКА СОЕДИНЕНИЯ Prometheus при запросе '{query}': {e}")
            self.prom = None # Сбрасываем соединение, чтобы попытаться переподключиться в _data_collection_loop
            return None
        except Exception as e:
            print(f"ПРЕДУПРЕЖДЕНИЕ: Неожиданная ошибка при извлечении запроса '{query}': {type(e).__name__} - {e}")
            return None

    def _data_collection_loop(self):
        """Фоновый цикл для периодического извлечения метрик из Prometheus."""
        print("Цикл сбора данных Prometheus запускается.")
        while not self._stop_event.is_set():
            loop_start_time = time.time()
            
            # Попытка (пере)подключения, если self.prom отсутствует
            if not self.prom:
                try:
                    print("Попытка установить/восстановить соединение с Prometheus...")
                    self.prom = PrometheusConnect(url=self.prometheus_url, disable_ssl=True)
                    if not self.prom.check_prometheus_connection():
                        print(f"ПРЕДУПРЕЖДЕНИЕ: Попытка (пере)подключения к Prometheus не удалась.")
                        self.prom = None # Оставляем None, если не удалось
                    else:
                        print("Соединение с Prometheus успешно установлено/восстановлено.")
                except Exception as e:
                    print(f"ПРЕДУПРЕЖДЕНИЕ: Ошибка при попытке (пере)подключения к Prometheus: {e}")
                    self.prom = None

            current_fetched_data = {}
            if self.prom: # Запрашиваем данные только если есть соединение
                for key, config_item in self._metric_config.items():
                    if key == "disk_usage":
                        current_fetched_data['disk_read'] = self._fetch_metric(config_item['query_read'])
                        current_fetched_data['disk_write'] = self._fetch_metric(config_item['query_write'])
                    elif key == "ram_usage":
                        current_fetched_data['ram_used'] = self._fetch_metric(config_item['query_used'])
                        current_fetched_data['ram_total'] = self._fetch_metric(config_item['query_total'])
                    else: # Для метрик с одним запросом
                        current_fetched_data[key] = self._fetch_metric(config_item['query'])
            else: # Если соединения нет, заполняем None
                for key in self._metric_config.keys():
                    if key == "disk_usage":
                        current_fetched_data['disk_read'] = None; current_fetched_data['disk_write'] = None
                    elif key == "ram_usage":
                        current_fetched_data['ram_used'] = None; current_fetched_data['ram_total'] = None
                    else: current_fetched_data[key] = None
            
            # Обновляем общую структуру self.metric_data под блокировкой
            with self._lock:
                for data_key, value in current_fetched_data.items():
                    if data_key not in self.metric_data: 
                        # Это может произойти, если _metric_config изменился, а self.metric_data еще не обновлен
                        # print(f"ПРЕДУПРЕЖДЕНИЕ: Ключ '{data_key}' из запроса отсутствует в self.metric_data. Пропускается.")
                        continue

                    # 'current' хранит фактическое значение (может быть None или NaN)
                    self.metric_data[data_key]["current"] = value 
                    
                    # Для истории используем 0.0, если значение None или NaN
                    value_for_history = 0.0
                    if value is not None and not (isinstance(value, float) and math.isnan(value)):
                        value_for_history = value
                    
                    if 'total' not in data_key: # Для временных рядов
                        self.metric_data[data_key]["history"].append(value_for_history)
                    else: # Для статических "total" значений (например, общий объем RAM)
                        self.metric_data[data_key]["history"].clear()
                        self.metric_data[data_key]["history"].append(value_for_history)

                # Обновление динамического максимума для диапазона диска
                if 'disk_usage' in self._metric_config: # Только если диск вообще настроен
                    disk_read_val = self.metric_data.get('disk_read', {}).get('current')
                    disk_write_val = self.metric_data.get('disk_write', {}).get('current')
                    current_disk_max_range = self.metric_data.get('disk_range_max', 1.0) # Безопасное значение по умолчанию

                    # Проверяем, что значения не None и не NaN
                    valid_read = disk_read_val is not None and not math.isnan(disk_read_val)
                    valid_write = disk_write_val is not None and not math.isnan(disk_write_val)

                    if valid_read and disk_read_val > current_disk_max_range * 0.9:
                        current_disk_max_range = disk_read_val * 1.2
                    if valid_write and disk_write_val > current_disk_max_range * 0.9:
                        current_disk_max_range = disk_write_val * 1.2
                    
                    # Можно добавить логику для медленного уменьшения current_disk_max_range, если значения долго остаются низкими
                    # Например, если max(read_history + write_history) < current_disk_max_range * 0.5, то уменьшить.
                    # Но для простоты пока только увеличиваем.
                    # Минимальное значение, чтобы избежать слишком маленького диапазона
                    self.metric_data['disk_range_max'] = max(current_disk_max_range, 1024 * 1024) # хотя бы 1MB/s


            # Ожидание до следующего интервала обновления
            fetch_duration = time.time() - loop_start_time
            sleep_time = max(0, self.update_interval - fetch_duration)
            self._stop_event.wait(sleep_time) # Ждем с возможностью прерывания

        print("Цикл сбора данных Prometheus остановлен.")

    def generate_image_frame(self, target_image: Image.Image):
        """
        Генерирует кадр монитора на предоставленном target_image, используя графический движок.
        target_image - это PIL.Image объект, на котором будет производиться отрисовка.
        """
        if not hasattr(self, 'graphics') or self.graphics is None:
             print("ОШИБКА: Графический движок не инициализирован в PrometheusMonitorGenerator.")
             # Можно нарисовать сообщение об ошибке на target_image
             try:
                draw = ImageDraw.Draw(target_image)
                draw.rectangle([0,0, *target_image.size], fill=self._colors.get('background', (0,0,0)))
                error_font = ImageFont.load_default() # Простой шрифт
                draw.text((10,10), "Error: Graphics Engine Failed", fill=self._colors.get('error', (255,0,0)), font=error_font)
             except Exception: pass # Если даже это не удалось
             return target_image

        # Получаем потокобезопасную копию текущих данных
        data_copy_for_frame = {}
        with self._lock:
            # Глубокое копирование, если есть вложенные изменяемые структуры (особенно deques)
            for key, value_dict in self.metric_data.items():
                 if isinstance(value_dict, dict) and "history" in value_dict and "current" in value_dict:
                      data_copy_for_frame[key] = {
                          "history": value_dict["history"].copy(), # Копируем deque
                          "current": value_dict["current"]         # Копируем текущее значение
                          }
                 else: # Для простых значений, как disk_range_max
                      data_copy_for_frame[key] = value_dict 

        # Вызываем метод отрисовки графического движка
        try:
             self.graphics.draw_frame(target_image, data_copy_for_frame)
        except Exception as e:
             print(f"ОШИБКА во время вызова self.graphics.draw_frame: {e}")
             import traceback
             traceback.print_exc()
             # Можно также нарисовать сообщение об ошибке на изображении здесь

        return target_image # Возвращаем измененное изображение

    def stop(self):
        """Останавливает фоновый поток сбора данных."""
        print("Остановка потока сбора данных PrometheusMonitorGenerator...")
        self._stop_event.set()
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            # Таймаут, чтобы не блокировать основной поток надолго
            self._data_thread.join(timeout=max(1.0, self.update_interval * 2 + 1)) 
        if hasattr(self, '_data_thread') and self._data_thread.is_alive():
            print("ПРЕДУПРЕЖДЕНИЕ: Поток сбора данных Prometheus не остановился корректно.")
        else:
            print("Поток сбора данных Prometheus остановлен.")

    # --- Для использования с 'with' (если нужно) ---
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

# --- Пример использования (если запускать этот файл напрямую) ---
if __name__ == "__main__":
    print("Запуск примера Prometheus Monitor Generator...")
    # Для копирования вложенных словарей при использовании значений по умолчанию
    from copy import deepcopy 

    monitor_instance = None 
    try:
        # Создаем экземпляр генератора (он также создает графический движок)
        # Конфигурация берется из констант, определенных выше в этом файле,
        # или может быть передана в конструктор.
        monitor_instance = PrometheusMonitorGenerator(
            # Здесь можно переопределить параметры, например:
            # resolution=(320,240),
            # prometheus_url="http://другой-прометеус:9090",
            # title_font_size=16 
        )

        # Создаем начальный пустой холст изображения
        # Разрешение должно соответствовать разрешению, с которым был инициализирован генератор
        img_canvas = Image.new('RGB', monitor_instance.resolution, color=monitor_instance._colors.get("background", (0,0,0)))

        frame_count = 0
        max_frames_to_generate = 10 # Сгенерируем 10 кадров для примера
        print(f"Генерация до {max_frames_to_generate} кадров (нажмите Ctrl+C для остановки)...")

        while frame_count < max_frames_to_generate:
            start_render_time = time.time()

            # Используем метод для генерации содержимого кадра
            monitor_instance.generate_image_frame(img_canvas)

            # Сохраняем или отображаем кадр
            try:
                img_canvas.save(f"prometheus_monitor_frame_{frame_count:03d}.png")
                print(f"\rСгенерирован кадр: {frame_count+1}/{max_frames_to_generate}", end="")
            except Exception as e:
                print(f"\nОшибка сохранения кадра изображения: {e}")
                # Решить, должен ли цикл прерываться при ошибке сохранения

            frame_count += 1

            # Ожидаем перед следующим кадром
            elapsed_time = time.time() - start_render_time
            # Используем интервал обновления монитора как целевую частоту кадров
            sleep_duration = max(0, monitor_instance.update_interval - elapsed_time)
            time.sleep(sleep_duration)
            
            if monitor_instance._stop_event.is_set(): # Если поток сбора данных остановился
                print("\nПоток сбора данных был остановлен, прекращаем генерацию.")
                break


        print("\nЗавершение генерации кадров.")

    except ConnectionError as e: # Если Prometheus недоступен при инициализации
         print(f"\nОшибка соединения: {e}")
    except RuntimeError as e: # Например, ошибка инициализации graphics_engine
         print(f"\nОшибка выполнения: {e}")
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
    except Exception as e:
        print(f"\nПроизошла непредвиденная ошибка: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
    finally:
        if monitor_instance:
            print("Обеспечение остановки монитора...")
            monitor_instance.stop()
        print("Пример Prometheus Monitor Generator завершен.")