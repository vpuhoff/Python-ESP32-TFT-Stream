from PIL import Image
import mss
import pygetwindow as gw

class WindowScreenshotter:
    """
    Класс для создания скриншотов определенного окна.
    Размер скриншота может быть изменен до размера переданного объекта Image.
    """
    resolution = (320, 240)  # Размер по умолчанию для скриншотов
    
    def __init__(self, window_title=None, crop_alignment='center'):
        """
        Инициализирует класс для захвата скриншотов.

        Args:
            window_title (str, optional): Заголовок окна для захвата.
            crop_alignment (str): Способ выравнивания и обрезки, если пропорции
                не совпадают. Может быть 'left', 'center' или 'right'.
        """
        self.window_title = window_title
        self.window = None
        self.crop_alignment = crop_alignment # Теперь это свойство класса

    def _find_window(self):
        """
        Внутренний метод для поиска нужного окна по заголовку.
        """
        if not self.window_title:
            return None

        try:
            windows = gw.getWindowsWithTitle(self.window_title)
            if not windows:
                return None
            self.window = windows[0]
            return self.window

        except gw.PyGetWindowException as e:
            print(f"Произошла ошибка при поиске окна: {e}")
            return None

    def draw_frame(self, target_image=None):
        """
        Выполняет скриншот заданного окна и, при необходимости,
        изменяет его размер до размера переданного target_image.

        Args:
            target_image (PIL.Image.Image, optional): Объект Pillow Image,
                чей размер будет использован для изменения размера скриншота.
                Если None, размер скриншота не меняется.

        Returns:
            PIL.Image.Image: Объект Pillow Image со скриншотом или None в случае ошибки.
        """
        with mss.mss() as sct:
            self._find_window()
            
            if self.window:
                bbox = {
                    "top": self.window.top,
                    "left": self.window.left,
                    "width": self.window.width,
                    "height": self.window.height
                }
            else:
                bbox = sct.monitors[0]

            try:
                sct_img = sct.grab(bbox)
                screenshot = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                if target_image:
                    target_width, target_height = target_image.size
                    shot_width, shot_height = screenshot.size
                    
                    target_aspect = target_width / target_height
                    shot_aspect = shot_width / shot_height
                    
                    if abs(target_aspect - shot_aspect) > 0.01:
                        if shot_aspect > target_aspect:
                            new_width = int(shot_height * target_aspect)
                            if self.crop_alignment == 'left':
                                crop_box = (0, 0, new_width, shot_height)
                            elif self.crop_alignment == 'right':
                                crop_box = (shot_width - new_width, 0, shot_width, shot_height)
                            else: # 'center'
                                left = (shot_width - new_width) // 2
                                crop_box = (left, 0, left + new_width, shot_height)
                        else:
                            new_height = int(shot_width / target_aspect)
                            top = (shot_height - new_height) // 2
                            crop_box = (0, top, shot_width, top + new_height)
                            
                        screenshot = screenshot.crop(crop_box)
                    
                    screenshot = screenshot.resize((target_width, target_height), Image.Resampling.LANCZOS)
                    target_image.paste(screenshot, (0, 0))
                
                return screenshot
            except Exception as e:
                print(f"Произошла ошибка при создании скриншота: {e}")
                return None

### Пример использования
if __name__ == "__main__":
    DEFAULT_CANVAS_RESOLUTION = (320, 240)
    
    # Создаем скриншотер с обрезкой по центру
    screenshotter_center = WindowScreenshotter(window_title='Visual Studio Code', crop_alignment='center')
    generated_image_center = Image.new('RGB', DEFAULT_CANVAS_RESOLUTION)
    screenshot_center = screenshotter_center.draw_frame(target_image=generated_image_center)
    if screenshot_center:
        screenshot_center.save("screenshot_center.png")
        print("Скриншот с обрезкой по центру сохранен.")

    # Создаем скриншотер с обрезкой слева
    screenshotter_left = WindowScreenshotter(window_title='Visual Studio Code', crop_alignment='left')
    generated_image_left = Image.new('RGB', DEFAULT_CANVAS_RESOLUTION)
    screenshot_left = screenshotter_left.draw_frame(target_image=generated_image_left)
    if screenshot_left:
        screenshot_left.save("screenshot_left.png")
        print("Скриншот с обрезкой слева сохранен.")