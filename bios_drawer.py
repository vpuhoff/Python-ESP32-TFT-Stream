# bios_drawer.py

from PIL import Image, ImageDraw, ImageFont
import datetime

# --- Конфигурация для 320x240 ---
DEFAULT_BIOS_RESOLUTION = (320, 240)
# Подбираем меньший размер шрифта, возможно, потребуется другой файл шрифта
# или просто уменьшение размера существующего. "cour.ttf" может плохо выглядеть
# при малых размерах. Возможно, лучше подойдет "pixelated" шрифт.
# Попробуем с Courier New размером 10-12.
SMALL_FONT_PATH = "cour.ttf" # Или другой моноширинный/пиксельный шрифт
SMALL_FONT_SIZE = 14 # Уменьшаем размер шрифта

# Цвета оставляем те же
COLORS = {
    "background": (0, 0, 170),      # Dark Blue
    "foreground": (255, 255, 255),  # White
    "highlight_bg": (85, 85, 85),   # Grey for menu bar bg
    "highlight_fg": (255, 255, 0),  # Yellow for menu text/highlights
    "label": (170, 170, 170),       # Lighter grey for labels
}

def _load_font(font_path=SMALL_FONT_PATH, font_size=SMALL_FONT_SIZE):
    """Вспомогательная функция для загрузки шрифта с запасным вариантом."""
    try:
        # Для очень маленьких размеров может потребоваться встроенный растровый шрифт PIL
        # если TTF выглядит плохо. Раскомментируйте строку ниже, если нужно.
        # if font_size <= 8: return ImageFont.load_default()
        return ImageFont.truetype(font_path, font_size)
    except IOError:
        print(f"Предупреждение: Шрифт '{font_path}' не найден. Используется шрифт PIL по умолчанию.")
        return ImageFont.load_default()
    except ImportError:
         print(f"Предупреждение: Pillow не смог загрузить TTF шрифты. Используется шрифт PIL по умолчанию.")
         return ImageFont.load_default()


def draw_bios_on_image(target_image):
    """
    Рисует упрощенный статический BIOS-интерфейс на объекте Pillow Image
    размером 320x240.

    Args:
        target_image (PIL.Image.Image): Объект Pillow Image размером 320x240.
                                         Будет изменен на месте.
    Returns:
        PIL.Image.Image: Измененный target_image.
    """
    if target_image.size != DEFAULT_BIOS_RESOLUTION:
        print(f"Предупреждение: Размер изображения {target_image.size} не равен {DEFAULT_BIOS_RESOLUTION}. Результат может быть некорректным.")
        # Можно добавить обрезку или масштабирование, если нужно:
        # target_image = target_image.crop((0, 0, SMALL_BIOS_RESOLUTION[0], SMALL_BIOS_RESOLUTION[1]))

    draw = ImageDraw.Draw(target_image)
    font = _load_font()

    width, height = target_image.size
    x_offset, y_offset = 0, 0 # Рисуем прямо на изображении

    # Определение высоты строки на основе шрифта
    try:
        # Попытка получить высоту символа 'A' для оценки строки
        line_height = font.getbbox("A")[3] + 4 # Высота символа + небольшой отступ
    except AttributeError:
         # У старых или стандартных шрифтов может не быть getbbox
         line_height = font.getsize("A")[1] + 4

    # --- Начинаем рисование ---

    # 1. Фон
    draw.rectangle(
        [x_offset, y_offset, x_offset + width, y_offset + height],
        fill=COLORS["background"]
    )

    # 2. Заголовок (укороченный)
    title = "BIOS SETUP"
    title_bbox = draw.textbbox((0,0), title, font=font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(
        (x_offset + (width - title_width) // 2, y_offset + 5), # Ближе к верху
        title,
        fill=COLORS["foreground"],
        font=font
    )

    # 3. Меню (укороченное)
    menu_y_start = y_offset + 5 + line_height + 2 # Сразу под заголовком
    menu_y_end = menu_y_start + line_height # Только одна строка меню
    draw.rectangle(
        [x_offset, menu_y_start, x_offset + width, menu_y_end],
        fill=COLORS["highlight_bg"]
    )
    # Укороченные пункты и меньше пробелов
    menu_items = " Main    Advanced    Boot    Exit "
    draw.text(
        (x_offset + 5, menu_y_start + 2), # Отступ слева и небольшой по вертикали
        menu_items,
        fill=COLORS["highlight_fg"],
        font=font
    )

    # 4. Основной контент (сильно сокращен)
    content_y_start = menu_y_end + line_height # Отступ от меню
    content_x_label = x_offset + 10
    content_x_value = x_offset + 100 # Где начинаются значения

    # Время и Дата
    now = datetime.datetime.now()
    draw.text((content_x_label, content_y_start + line_height * 0), "Time", fill=COLORS["label"], font=font)
    draw.text((content_x_value, content_y_start + line_height * 0), f": [{now.strftime('%H:%M:%S')}]", fill=COLORS["foreground"], font=font)
    draw.text((content_x_label, content_y_start + line_height * 1), "Date", fill=COLORS["label"], font=font)
    draw.text((content_x_value, content_y_start + line_height * 1), f": [{now.strftime('%m/%d/%y')}]", fill=COLORS["foreground"], font=font) # Короткий формат даты

    # Пример другой информации
    draw.text((content_x_label, content_y_start + line_height * 3), "BIOS Ver", fill=COLORS["label"], font=font)
    draw.text((content_x_value, content_y_start + line_height * 3), ": 1.0a", fill=COLORS["foreground"], font=font)
    draw.text((content_x_label, content_y_start + line_height * 4), "Memory", fill=COLORS["label"], font=font)
    draw.text((content_x_value, content_y_start + line_height * 4), ": 1024MB", fill=COLORS["foreground"], font=font)

    # Убираем боковую панель помощи, так как места нет

    # 5. Нижняя строка (минимальная информация)
    bottom_y = y_offset + height - line_height # Прямо у нижнего края
    bottom_text = "F1:Help  F10:Save   ESC:Exit"
    draw.text((x_offset + 10, bottom_y), bottom_text, fill=COLORS["foreground"], font=font)

    # --- Конец рисования ---

    return target_image # Возвращаем измененное изображение