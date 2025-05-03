# cpu_monitor_generator.py

import time
import threading
from collections import deque
import psutil
from PIL import Image, ImageDraw, ImageFont

# Попробуем импортировать cpuinfo для имени процессора
try:
    import cpuinfo
    _CPUINFO_AVAILABLE = True
except ImportError:
    _CPUINFO_AVAILABLE = False

# --- Конфигурация по умолчанию ---
DEFAULT_RESOLUTION = (600, 400)
DEFAULT_HISTORY_LENGTH = 60 # Должно совпадать с GRAPH_POINTS ниже
DEFAULT_UPDATE_INTERVAL = 0.5 # Секунды
DEFAULT_FONT_PATH = "arial.ttf"
TITLE_FONT_SIZE = 24
SUBTITLE_FONT_SIZE = 14

COLORS = {
    "background": (30, 30, 30),
    "foreground": (210, 210, 210),
    "grid_lines": (60, 60, 60),
    "graph_line": (0, 180, 220),
}

# Параметры сетки отрисовки
GRID_ROWS = 4
GRID_COLS = 5
GRAPH_POINTS = DEFAULT_HISTORY_LENGTH # Количество точек истории для отрисовки

class CpuMonitorGenerator:
    """
    Класс, генерирующий кадры с изображением монитора ЦП.
    Использует фоновый поток для сбора метрик.
    """
    def __init__(self,
                 resolution=DEFAULT_RESOLUTION,
                 history_length=DEFAULT_HISTORY_LENGTH,
                 update_interval=DEFAULT_UPDATE_INTERVAL,
                 font_path=DEFAULT_FONT_PATH,
                 cpu_name_override=None):
        """
        Инициализирует генератор монитора ЦП.

        Args:
            resolution (tuple): Разрешение (ширина, высота) генерируемых кадров.
            history_length (int): Количество точек истории для хранения и отображения.
            update_interval (float): Интервал обновления данных ЦП в секундах.
            font_path (str): Путь к файлу шрифта (.ttf).
            cpu_name_override (str, optional): Принудительно установить имя ЦП.
                                                 Если None, попытается определить автоматически.
        """
        print("Initializing CpuMonitorGenerator...")
        self.resolution = resolution
        self.history_length = history_length
        self.update_interval = update_interval
        self._font_path = font_path
        self._colors = COLORS # Используем константу
        self._grid_rows = GRID_ROWS
        self._grid_cols = GRID_COLS
        self._graph_points = history_length # Для отрисовки используем ту же длину

        # --- Инициализация данных и psutil ---
        self.num_cores = psutil.cpu_count(logical=True)
        if not self.num_cores:
             raise RuntimeError("Не удалось определить количество ядер ЦП.")

        self._cpu_usage_history = [
            deque([0.0] * self.history_length, maxlen=self.history_length)
            for _ in range(self.num_cores)
        ]
        self._cpu_name = cpu_name_override if cpu_name_override else self._fetch_cpu_name()

        # --- Загрузка шрифтов ---
        self._font_title = self._load_font(font_size=TITLE_FONT_SIZE)
        self._font_sub = self._load_font(font_size=SUBTITLE_FONT_SIZE)

        # --- Настройка потока ---
        self._lock = threading.Lock() # Блокировка для доступа к _cpu_usage_history
        self._stop_event = threading.Event() # Событие для сигнала остановки потока
        self._data_thread = threading.Thread(target=self._data_collection_loop, daemon=True) # daemon=True для автозавершения

        # --- Запуск сбора данных ---
        # Первый вызов для инициализации psutil перед запуском потока
        try:
            psutil.cpu_percent(interval=None, percpu=True)
            time.sleep(0.05) # Очень короткая пауза
            self._data_thread.start()
            print("Data collection thread started.")
        except psutil.Error as e:
            raise RuntimeError(f"Failed to initialize psutil: {e}") from e

        print(f"CpuMonitorGenerator initialized. Cores: {self.num_cores}, CPU: {self._cpu_name}")

    def _load_font(self, font_size):
        """Загружает шрифт, выбрасывает исключение при ошибке."""
        try:
            return ImageFont.truetype(self._font_path, font_size)
        except IOError:
            print(f"Критическая ошибка: Шрифт '{self._font_path}' не найден.")
            raise # Перевыбрасываем исключение
        except ImportError:
             print(f"Критическая ошибка: Pillow не смог загрузить TTF шрифты.")
             raise # Перевыбрасываем исключение

    def _fetch_cpu_name(self):
        """Пытается получить маркетинговое имя ЦП."""
        name = f"Logical Cores: {self.num_cores}" # Имя по умолчанию
        if _CPUINFO_AVAILABLE:
            try:
                info = cpuinfo.get_cpu_info()
                name = info.get('brand_raw', name)
            except Exception as e:
                print(f"Предупреждение: Не удалось получить имя ЦП через cpuinfo: {e}")
        else:
            print("Предупреждение: Библиотека py-cpuinfo не найдена. Имя ЦП может быть неточным.")
        return name

    def _data_collection_loop(self):
        """Целевая функция для фонового потока сбора данных."""
        print("Data collection loop starting.")
        while not self._stop_event.is_set():
            try:
                current_usage = psutil.cpu_percent(interval=None, percpu=True)
                if current_usage is not None and len(current_usage) == self.num_cores:
                    with self._lock: # Захватываем блокировку для обновления
                        for i in range(self.num_cores):
                            self._cpu_usage_history[i].append(current_usage[i])
                else:
                    print(f"Предупреждение: Получены некорректные данные от psutil ({current_usage})")
                    # Можно добавить нули в историю при ошибке
                    # with self._lock:
                    #    for i in range(self.num_cores): self._cpu_usage_history[i].append(0.0)

            except psutil.Error as e:
                 print(f"Ошибка psutil в потоке сбора данных: {e}")
                 # Можно добавить нули или просто пропустить итерацию
                 # with self._lock:
                 #    for i in range(self.num_cores): self._cpu_usage_history[i].append(0.0)
            except Exception as e:
                 print(f"Неожиданная ошибка в потоке сбора данных: {e}")
                 # В серьезных случаях можно остановить поток
                 # self.stop()
                 # break

            # Ждем до следующего интервала или сигнала остановки
            self._stop_event.wait(self.update_interval)
        print("Data collection loop stopped.")

    def draw_frame(self, target_image):
        """
        Рисует текущее состояние монитора ЦП на предоставленном изображении.

        Args:
            target_image (PIL.Image.Image): Объект Pillow Image для рисования.
                                             Размер должен соответствовать разрешению генератора.

        Returns:
            PIL.Image.Image: Измененный target_image.
        """
        if target_image.size != self.resolution:
            print(f"Предупреждение: Размер target_image {target_image.size} не совпадает с разрешением генератора {self.resolution}. Результат может быть искажен.")
            # Можно добавить обрезку/масштабирование target_image при необходимости

        draw = ImageDraw.Draw(target_image)
        width, height = self.resolution

        # --- Получаем копию данных для кадра ---
        with self._lock: # Блокировка на время копирования
            current_history_copy = [list(core_deque) for core_deque in self._cpu_usage_history]

        # --- Отрисовка (аналогично предыдущей версии, но использует self для настроек) ---
        # 1. Фон
        draw.rectangle([0, 0, width, height], fill=self._colors["background"])

        # 2. Заголовок
        title_y = 15
        draw.text((10, title_y), "ЦП", fill=self._colors["foreground"], font=self._font_title)
        draw.text((70, title_y + 4), self._cpu_name, fill=self._colors["foreground"], font=self._font_sub)

        # 3. Подзаголовок
        subtitle_y = title_y + TITLE_FONT_SIZE + 10
        subtitle_text_left = f"% использования более {self._graph_points} секунд"
        subtitle_text_right = "100%"
        draw.text((10, subtitle_y), subtitle_text_left, fill=self._colors["foreground"], font=self._font_sub)
        right_text_bbox = draw.textbbox((0,0), subtitle_text_right, font=self._font_sub)
        right_text_width = right_text_bbox[2] - right_text_bbox[0]
        draw.text((width - right_text_width - 10, subtitle_y), subtitle_text_right, fill=self._colors["foreground"], font=self._font_sub)

        # 4. Сетка графиков
        grid_area_y_start = subtitle_y + SUBTITLE_FONT_SIZE + 15
        grid_area_height = height - grid_area_y_start - 10
        grid_area_width = width - 20
        cell_width = grid_area_width // self._grid_cols
        cell_height = grid_area_height // self._grid_rows
        padding_x = 5
        padding_y = 5

        num_graphs_available = len(current_history_copy)
        num_graphs_to_draw = min(num_graphs_available, self._grid_rows * self._grid_cols)
        graph_index = 0

        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                if graph_index >= num_graphs_to_draw: break

                cell_x = 10 + c * cell_width
                cell_y = grid_area_y_start + r * cell_height
                cell_inner_x = cell_x + padding_x
                cell_inner_y = cell_y + padding_y
                cell_inner_width = cell_width - 2 * padding_x
                cell_inner_height = cell_height - 2 * padding_y

                # Рисуем сетку ячейки
                num_h_lines = 4; num_v_lines = 5
                for i in range(1, num_h_lines):
                    line_y = cell_inner_y + i * (cell_inner_height / num_h_lines)
                    draw.line([(cell_inner_x, line_y), (cell_inner_x + cell_inner_width, line_y)], fill=self._colors["grid_lines"], width=1)
                for i in range(1, num_v_lines):
                    line_x = cell_inner_x + i * (cell_inner_width / num_v_lines)
                    draw.line([(line_x, cell_inner_y), (line_x, cell_inner_y + cell_inner_height)], fill=self._colors["grid_lines"], width=1)

                # Рисуем график загрузки из скопированных данных
                core_history = current_history_copy[graph_index]
                points_to_draw = []
                num_history_points = len(core_history)

                if num_history_points > 1:
                    for i, usage in enumerate(core_history):
                        try: usage_float = float(usage)
                        except (ValueError, TypeError): usage_float = 0.0
                        point_x = cell_inner_x + (i / (num_history_points - 1)) * cell_inner_width
                        point_y = cell_inner_y + cell_inner_height - (usage_float / 100.0) * cell_inner_height
                        points_to_draw.append((point_x, point_y))

                    if points_to_draw:
                        draw.line(points_to_draw, fill=self._colors["graph_line"], width=1)

                graph_index += 1
            if graph_index >= num_graphs_to_draw: break

        return target_image

    def stop(self):
        """Останавливает фоновый поток сбора данных."""
        print("Stopping data collection thread...")
        self._stop_event.set()
        if self._data_thread.is_alive():
             # Добавим таймаут на случай зависания потока
             self._data_thread.join(timeout=self.update_interval * 2 + 1)
        if self._data_thread.is_alive():
             print("Warning: Data collection thread did not stop gracefully.")
        else:
             print("Data collection thread stopped.")

    # --- Для использования с 'with' ---
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # --- Для автоматической остановки при удалении объекта (менее надежно) ---
    # def __del__(self):
    #     self.stop()