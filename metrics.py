from prometheus_client import Histogram, Counter, Gauge

# --- Определения метрик Prometheus ---
# Важно: для каждой метрики, которая будет использоваться пайплайнами,
# добавляем 'pipeline_name' в labelnames, чтобы различать метрики
# от разных пайплайнов в Prometheus/Grafana.

# Время обработки кадра на различных этапах
FRAME_PROCESSING_TIME = Histogram(
    'esp32_frame_processing_seconds',
    'Время обработки одного кадра на различных этапах',
    ['stage', 'pipeline_name']  # stage: e.g., 'capture', 'resize', 'diff', 'send', 'full_consumer_loop'
                                # pipeline_name: имя пайплайна из конфигурации
)

# Размеры отправляемых пакетов (включая чанки)
# Определяем "корзины" (buckets) для гистограммы размеров пакетов
PACKET_SIZE_BUCKETS = (
    12, 256, 512, 1024, 2048, 4096, 8192, 10000,
    16384, 25000, 32768, 50000, float('inf') # 'inf' для пакетов больше последнего значения
)
PACKET_SIZE_BYTES = Histogram(
    'esp32_packet_size_bytes',
    'Размер отправленных пакетов (включая заголовок) в байтах',
    buckets=PACKET_SIZE_BUCKETS,
    labelnames=['pipeline_name']
)

# Количество чанков (или регионов), отправленных для одного полного кадра
CHUNKS_PER_FRAME = Histogram(
    'esp32_chunks_per_frame',
    'Количество отправленных чанков/регионов на один обработанный кадр',
    labelnames=['pipeline_name']
)

# Общее количество ошибок соединения при отправке данных клиенту
CONNECTION_ERRORS_TOTAL = Counter(
    'esp32_connection_errors_total',
    'Общее количество ошибок TCP соединения при отправке данных',
    labelnames=['pipeline_name']
)

# Общее количество успешных переподключений клиента ESP32
RECONNECTIONS_TOTAL = Counter(
    'esp32_reconnections_total',
    'Общее количество успешных переподключений ESP32 к пайплайну',
    labelnames=['pipeline_name']
)

# Количество кадров, сгенерированных потоком-генератором
FRAMES_GENERATED_TOTAL = Counter(
    'esp32_frames_generated_total',
    'Общее количество кадров, успешно сгенерированных потоком-генератором',
    labelnames=['pipeline_name']
)

# Количество кадров, успешно обработанных и (попытка) отправленных потоком-потребителем
FRAMES_PROCESSED_TOTAL = Counter(
    'esp32_frames_processed_total',
    'Общее количество кадров, обработанных и отправленных (или попытка отправки) потоком-потребителем',
    labelnames=['pipeline_name']
)

# Размер очереди кадров между генератором и потребителем
# Гистограмма, чтобы видеть распределение, но можно и Gauge для текущего значения
FRAMES_QUEUE_SIZE = Histogram(
    'esp32_frames_queue_size',
    'Наблюдаемый размер очереди кадров между генератором и потребителем',
    labelnames=['pipeline_name']
    # buckets можно настроить, если нужно, например, [0, 1, 2, 3, 5, 10, float('inf')]
)
# Альтернатива для текущего размера очереди:
# FRAMES_QUEUE_CURRENT_SIZE = Gauge(
# 'esp32_frames_queue_current_size',
# 'Текущий размер очереди кадров',
#     labelnames=['pipeline_name']
# )


# Общее время, затраченное на отправку всех dirty_rects (чанков) для одного кадра
DIRTY_RECTS_SEND_DURATION_SECONDS = Histogram(
    'esp32_dirty_rects_send_duration_seconds',
    'Общее время, затраченное на отправку всех измененных регионов (dirty_rects) для одного кадра',
    labelnames=['pipeline_name']
)

# Текущее значение динамического порога для определения dirty_rects
CURRENT_DYNAMIC_THRESHOLD = Gauge(
    'esp32_current_dynamic_threshold',
    'Текущее значение адаптивного порога для определения измененных регионов (dirty_rects)',
    labelnames=['pipeline_name']
)

# Расчетная частота кадров (FPS) на стороне потребителя
CONSUMER_CALCULATED_FPS = Gauge(
    'esp32_consumer_calculated_fps',
    'Расчетная частота кадров (FPS) на основе времени обработки в потоке-потребителе',
    labelnames=['pipeline_name']
)

# --- Словарь для удобного доступа ко всем метрикам ---
# Это позволит передавать все метрики в класс Pipeline одним объектом.
ALL_METRICS = {
    'frame_processing_time': FRAME_PROCESSING_TIME,
    'packet_size_bytes': PACKET_SIZE_BYTES,
    'chunks_per_frame': CHUNKS_PER_FRAME,
    'connection_errors_total': CONNECTION_ERRORS_TOTAL,
    'reconnections_total': RECONNECTIONS_TOTAL,
    'frames_generated_total': FRAMES_GENERATED_TOTAL,
    'frames_processed_total': FRAMES_PROCESSED_TOTAL,
    'frames_queue_size': FRAMES_QUEUE_SIZE,
    # 'frames_queue_current_size': FRAMES_QUEUE_CURRENT_SIZE, # Если используете Gauge для текущего размера
    'dirty_rects_send_duration_seconds': DIRTY_RECTS_SEND_DURATION_SECONDS,
    'current_dynamic_threshold': CURRENT_DYNAMIC_THRESHOLD,
    'consumer_calculated_fps': CONSUMER_CALCULATED_FPS,
}

if __name__ == '__main__':
    # Пример того, как можно было бы использовать эти метрики (для тестирования)
    # В реальном приложении эти вызовы будут в коде пайплайна.
    # Для этого примера нужен запущенный Prometheus сервер, чтобы увидеть метрики,
    # или просто для проверки, что определения корректны.

    # Для локального тестирования без сервера Prometheus, можно закомментировать start_http_server
    # from prometheus_client import start_http_server
    # start_http_server(8001) # Запускаем на другом порту для теста
    # print("Тестовый экспортер метрик запущен на порту 8001 (если раскомментировано start_http_server)")

    # Имитация работы пайплайна "TestPipeline"
    pipeline_name_test = "TestPipeline"

    print(f"Определено {len(ALL_METRICS)} метрик.")

    # Пример использования метрик:
    ALL_METRICS['frames_generated_total'].labels(pipeline_name=pipeline_name_test).inc()
    ALL_METRICS['frames_queue_size'].labels(pipeline_name=pipeline_name_test).observe(3)
    ALL_METRICS['frame_processing_time'].labels(stage='resize', pipeline_name=pipeline_name_test).observe(0.015)
    ALL_METRICS['consumer_calculated_fps'].labels(pipeline_name=pipeline_name_test).set(25.5)
    ALL_METRICS['current_dynamic_threshold'].labels(pipeline_name=pipeline_name_test).set(50)
    ALL_METRICS['packet_size_bytes'].labels(pipeline_name=pipeline_name_test).observe(1024)
    ALL_METRICS['chunks_per_frame'].labels(pipeline_name=pipeline_name_test).observe(2)

    print(f"Метрики для пайплайна '{pipeline_name_test}' имитированы.")
    print("Проверьте конечную точку /metrics на соответствующем порту, если сервер запущен.")

    # Чтобы этот скрипт не завершался сразу при запуске с start_http_server:
    # import time
    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     print("\nЗавершение тестового скрипта метрик.")