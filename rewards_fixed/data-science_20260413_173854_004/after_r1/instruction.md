I have temperature sensor data from an IoT deployment in Cairo that needs to be aggregated for reporting. The raw readings are in `/app/data/sensor_readings.csv` with columns `timestamp`, `sensor_id`, and `temperature`. The timestamps are in local Cairo time but stored as naive datetime strings.

The data spans April 25-27, 2024 (including the DST transition on April 26 when clocks jumped forward at midnight). I need hourly aggregated statistics (mean, min, max temperature) for each sensor written to `/app/output/hourly_aggregates.csv` with columns: `sensor_id`, `hour_start`, `mean_temp`, `min_temp`, `max_temp`. The `hour_start` column should be timezone-aware timestamps in UTC (format: `YYYY-MM-DD HH:MM:SS+00:00`).

The sensors sometimes send duplicate readings when reconnecting, so you'll need to handle those appropriately. The timezone handling is tricky due to the DST transition - some timestamps in the data may not exist in Cairo timezone. These are firmware wall-time records: shift each nonexistent timestamp forward to the first valid Cairo wall time before converting it to UTC.

