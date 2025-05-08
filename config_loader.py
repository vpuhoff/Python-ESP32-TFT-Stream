import yaml
import os
from copy import deepcopy # Для глубокого копирования

DEFAULT_CONFIG_FILE_PATH = 'config.yaml'

def load_raw_config_from_file(config_path=DEFAULT_CONFIG_FILE_PATH):
    """Загружает "сырую" конфигурацию из YAML файла."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
    if raw_config is None: # Если файл пустой
        raw_config = {}
    return raw_config

def process_configs(raw_config):
    """
    Обрабатывает сырую конфигурацию, применяя настройки по умолчанию.
    Включает конвертацию списков цветов в кортежи в разных местах.
    """
    default_pipeline_settings = raw_config.get('default_pipeline_settings', {})
    processed_pipelines_list = []

    for p_conf_override in raw_config.get('pipelines', []):
        final_pipeline_conf = deepcopy(default_pipeline_settings)

        # "Умное" слияние словарей
        for key, value in p_conf_override.items():
            if isinstance(value, dict) and key in final_pipeline_conf and isinstance(final_pipeline_conf[key], dict):
                merged_dict = deepcopy(final_pipeline_conf[key])
                merged_dict.update(value)
                final_pipeline_conf[key] = merged_dict
            else:
                final_pipeline_conf[key] = value

        # Валидация обязательных полей (как раньше)
        required_keys = ['name', 'esp32_port', 'target_width', 'target_height', 'image_source_mode']
        pipeline_name_for_error = final_pipeline_conf.get('name', str(p_conf_override)) # Для сообщения об ошибке
        for key in required_keys:
            if key not in final_pipeline_conf:
                raise ValueError(f"Отсутствует обязательный ключ '{key}' в конфигурации пайплайна: '{pipeline_name_for_error}'")

        # --- Преобразование типов и цветов ---

        # 1. wb_scale (если есть)
        if 'wb_scale' in final_pipeline_conf and isinstance(final_pipeline_conf['wb_scale'], list):
             if len(final_pipeline_conf['wb_scale']) == 3:
                  final_pipeline_conf['wb_scale'] = tuple(final_pipeline_conf['wb_scale'])
             else:
                  print(f"[WARN][ConfigLoader] Некорректная длина wb_scale для '{pipeline_name_for_error}': {final_pipeline_conf['wb_scale']}. Используется дефолтное значение (если есть) или (1,1,1).")
                  # Можно оставить как есть или сбросить на дефолт
                  if 'wb_scale' in default_pipeline_settings and isinstance(default_pipeline_settings['wb_scale'], tuple):
                      final_pipeline_conf['wb_scale'] = default_pipeline_settings['wb_scale']
                  else:
                      final_pipeline_conf['wb_scale'] = (1.0, 1.0, 1.0)


        # 2. Цвета в словаре prometheus_colors (если есть)
        if 'prometheus_colors' in final_pipeline_conf and isinstance(final_pipeline_conf['prometheus_colors'], dict):
            for color_key, color_value in final_pipeline_conf['prometheus_colors'].items():
                if isinstance(color_value, list) and len(color_value) in [3, 4]: # Проверка на RGB или RGBA
                    final_pipeline_conf['prometheus_colors'][color_key] = tuple(color_value)

        # 3. Цвета внутри prometheus_metric_config (если есть) - ИСПРАВЛЕНИЕ ЗДЕСЬ
        if 'prometheus_metric_config' in final_pipeline_conf and isinstance(final_pipeline_conf['prometheus_metric_config'], dict):
            for metric_key, metric_details in final_pipeline_conf['prometheus_metric_config'].items():
                if isinstance(metric_details, dict):
                    # Проверяем ключи, которые могут содержать цвета ('color', 'color_read', 'color_write')
                    for color_attr in ['color', 'color_read', 'color_write']:
                         if color_attr in metric_details and isinstance(metric_details[color_attr], list):
                             # Проверяем, что это похоже на цвет (3 или 4 компоненты)
                             if len(metric_details[color_attr]) in [3, 4]:
                                 metric_details[color_attr] = tuple(metric_details[color_attr])
                             else:
                                 print(f"[WARN][ConfigLoader] Некорректная длина цвета для '{color_attr}' в метрике '{metric_key}' пайплайна '{pipeline_name_for_error}': {metric_details[color_attr]}. Цвет не будет преобразован.")


        # ... Другие преобразования типов по необходимости ...

        processed_pipelines_list.append(final_pipeline_conf)

    global_settings = raw_config.get('global_settings', {})
    return global_settings, processed_pipelines_list

def get_app_config(config_path=DEFAULT_CONFIG_FILE_PATH):
    """Главная функция для загрузки и обработки конфигурации приложения."""
    raw_config = load_raw_config_from_file(config_path)
    global_settings, processed_pipelines = process_configs(raw_config)
    return global_settings, processed_pipelines

if __name__ == '__main__':
    # Пример использования и тестирования модуля
    try:
        # Создайте тестовый config.yaml для проверки
        # Например, config.yaml:
        # global_settings:
        #   prometheus_exporter_port: 8000
        # default_pipeline_settings:
        #   gamma: 2.2
        #   target_fps: 15
        #   prometheus_colors:
        #      background: [0,0,0]
        #      foreground: [255,255,255]
        # pipelines:
        #   - name: "Test1"
        #     esp32_port: 8888
        #     target_width: 320
        #     target_height: 240
        #     image_source_mode: "BIOS"
        #     gamma: 2.8 # Override
        #     prometheus_colors: # Override and merge
        #        background: [10,10,10] # Override background
        #        new_color: [1,2,3] # Add new color
        #   - name: "Test2"
        #     esp32_port: 8889
        #     target_width: 100
        #     target_height: 100
        #     image_source_mode: "CPU"
        #     # fps_history_size: 20 # Добавит ключ, отсутствующий в defaults
        #     # если это нежелательно, то нужно либо добавлять все ключи в defaults
        #     # либо более строгую схему валидации

        gs, pipelines = get_app_config()
        print("Global Settings:", gs)
        for p_idx, p_conf in enumerate(pipelines):
            print(f"\nPipeline {p_idx + 1} ('{p_conf.get('name')}'):")
            for key, value in p_conf.items():
                print(f"  {key}: {value}")

    except Exception as e:
        print(f"Ошибка при загрузке/обработке конфигурации: {e}")