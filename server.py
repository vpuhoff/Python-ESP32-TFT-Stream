# server.py
import threading
import time
import os # Для доступа к переменным окружения
from prometheus_client import start_http_server

# Импорт новых модулей
from config_loader import get_app_config, DEFAULT_CONFIG_FILE_PATH
from pipeline import StreamPipeline # Импортируем класс StreamPipeline
from metrics import ALL_METRICS     # Импортируем словарь с объектами метрик

# Глобальное событие для корректной остановки всех пайплайнов
global_server_stop_event = threading.Event()

def start_prometheus_http_server(port, host='0.0.0.0'):
    """Запускает HTTP сервер для экспорта метрик Prometheus."""
    try:
        start_http_server(port, addr=host)
        print(f"[*] Prometheus exporter (для метрик сервера) запущен на http://{host}:{port}/metrics")
    except Exception as e:
        print(f"[!] Не удалось запустить Prometheus exporter на порту {port}: {e}")
        print("[!] Метрики сервера не будут доступны через HTTP.")

def main():
    """
    Главная функция сервера:
    - Загружает конфигурацию.
    - Запускает Prometheus HTTP сервер для метрик.
    - Создает и запускает экземпляры StreamPipeline для каждой конфигурации.
    - Ожидает сигнала завершения (Ctrl+C) и корректно останавливает все пайплайны.
    """
    global_settings = {}
    pipeline_configurations = [] # Будет список словарей конфигураций для каждого пайплайна

    try:
        # Путь к файлу конфигурации можно задать через переменную окружения
        config_file_path = os.environ.get('APP_CONFIG_FILE', DEFAULT_CONFIG_FILE_PATH)
        print(f"[*] Загрузка конфигурации из файла: {config_file_path}")
        global_settings, pipeline_configurations = get_app_config(config_file_path)
    except FileNotFoundError:
        print(f"ОШИБКА: Файл конфигурации '{config_file_path}' не найден. Сервер не может быть запущен.")
        return # Выход, если нет конфигурации
    except ValueError as e: # Ошибки валидации конфигурации из config_loader
        print(f"ОШИБКА В ФАЙЛЕ КОНФИГУРАЦИИ: {e}. Сервер не может быть запущен.")
        return
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА при загрузке или обработке конфигурации: {e}")
        import traceback
        traceback.print_exc()
        return

    if not pipeline_configurations:
        print("В конфигурации не определено ни одного пайплайна. Завершение работы.")
        return

    # Запуск HTTP сервера для метрик Prometheus самого сервера
    # Порт берется из глобальных настроек YAML или используется значение по умолчанию
    prometheus_exporter_port = global_settings.get('prometheus_exporter_port', 8000)
    if prometheus_exporter_port: # Запускаем, только если порт указан и не None/0
        prom_server_thread = threading.Thread(
            target=start_prometheus_http_server,
            args=(prometheus_exporter_port,),
            daemon=True, # Daemon, чтобы не мешал завершению основной программы
            name="PrometheusExporterThread"
        )
        prom_server_thread.start()
    else:
        print("[INFO] Экспортер метрик Prometheus для сервера не запущен (порт не указан или 0).")


    active_pipelines = [] # Список для хранения активных экземпляров StreamPipeline

    for p_conf_dict in pipeline_configurations:
        pipeline_name = p_conf_dict.get('name', 'UnknownPipeline')
        try:
            print(f"[*] Инициализация пайплайна: '{pipeline_name}'...")
            # Передаем конфигурацию пайплайна, глобальное событие остановки и объекты метрик
            pipeline_instance = StreamPipeline(p_conf_dict, global_server_stop_event, ALL_METRICS)
            pipeline_instance.start_pipeline_manager() # Запускает _listening_loop в отдельном потоке
            active_pipelines.append(pipeline_instance)
            print(f"[*] Пайплайн '{pipeline_name}' успешно запущен и слушает порт {p_conf_dict['esp32_port']}.")
        except Exception as e:
            # Логируем ошибку, но не останавливаем весь сервер, если один пайплайн не запустился
            print(f"КРИТИЧЕСКАЯ ОШИБКА при создании или запуске пайплайна '{pipeline_name}': {e}")
            import traceback
            traceback.print_exc()
            print(f"[*] Пайплайн '{pipeline_name}' не будет запущен.")


    if not active_pipelines:
        print("Ни один пайплайн не был успешно запущен. Завершение работы сервера.")
        if prom_server_thread and prom_server_thread.is_alive(): # На случай, если экспортёр запустился, а пайплайны нет
             global_server_stop_event.set() # Даем сигнал, если что-то еще могло запуститься
        return

    num_pipelines = len(active_pipelines)
    print(f"[*] Успешно запущено {num_pipelines} пайплайн(ов). Сервер работает.")
    print("[*] Нажмите Ctrl+C для остановки сервера.")

    try:
        # Главный поток остается активным, ожидая сигнала завершения
        while not global_server_stop_event.is_set():
            # Периодически можно проверять состояние пайплайнов, если это необходимо,
            # но основная логика остановки будет через global_server_stop_event.
            any_pipeline_manager_alive = any(p.manager_thread and p.manager_thread.is_alive() for p in active_pipelines)
            if not any_pipeline_manager_alive and active_pipelines: # Если все менеджеры пайплайнов неожиданно завершились
                print("[ПРЕДУПРЕЖДЕНИЕ] Все управляющие потоки пайплайнов завершились! Остановка сервера...")
                if not global_server_stop_event.is_set():
                    global_server_stop_event.set()
                break # Выход из цикла ожидания
            time.sleep(1) # Проверка раз в секунду
    except KeyboardInterrupt:
        print("\n[*] Получен сигнал KeyboardInterrupt (Ctrl+C). Начинается остановка сервера...")
    except Exception as e:
        print(f"[КРИТИЧЕСКАЯ ОШИБКА В ГЛАВНОМ ПОТОКЕ]: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[*] Начало процедуры корректной остановки всех активных пайплайнов...")
        if not global_server_stop_event.is_set():
            global_server_stop_event.set() # Устанавливаем глобальный флаг остановки

        for pipeline in active_pipelines:
            pipeline_name = pipeline.name if hasattr(pipeline, 'name') else 'Неизвестный пайплайн'
            print(f"[*] Инициирование остановки для пайплайна '{pipeline_name}'...")
            try:
                pipeline.stop_pipeline_manager() # Сообщаем пайплайну, что нужно начать остановку
            except Exception as e_stop:
                print(f"[ОШИБКА] при вызове stop_pipeline_manager для '{pipeline_name}': {e_stop}")


        # Ожидаем завершения управляющих потоков каждого пайплайна
        # Это важно, чтобы дать им время закрыть сокеты и остановить свои внутренние потоки
        for pipeline in active_pipelines:
            pipeline_name = pipeline.name if hasattr(pipeline, 'name') else 'Неизвестный пайплайн'
            print(f"[*] Ожидание завершения управляющего потока для пайплайна '{pipeline_name}'...")
            try:
                # Даем таймаут на завершение, например, 5-7 секунд
                pipeline.join_manager_thread(timeout=7)
                if pipeline.manager_thread and pipeline.manager_thread.is_alive():
                    print(f"[ПРЕДУПРЕЖДЕНИЕ] Управляющий поток для пайплайна '{pipeline_name}' не завершился в течение таймаута.")
                else:
                    print(f"[*] Управляющий поток для пайплайна '{pipeline_name}' успешно завершен.")
            except Exception as e_join:
                 print(f"[ОШИБКА] при ожидании завершения для '{pipeline_name}': {e_join}")


        # prom_server_thread является daemon, он должен завершиться автоматически при выходе
        # основного потока, но можно добавить явное ожидание для чистоты, если он не daemon.
        if prom_server_thread and prom_server_thread.is_alive() and not prom_server_thread.daemon:
            print("[*] Ожидание завершения потока Prometheus Exporter...")
            prom_server_thread.join(timeout=1)

        print("[*] Сервер полностью остановлен.")

if __name__ == "__main__":
    main()