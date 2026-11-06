I have a corrupted binary log file from a temperature/humidity/pressure monitoring system at `/app/sensor.log`. The system crashed during a write operation, and the flash storage may have introduced additional errors.

The binary format is documented in `/app/format.h`. Recover all valid sensor records from the corrupted file and write them as a JSON array to `/app/recovered.json`. Each record should have these fields with their native types:

- `timestamp` (integer)
- `sensor_id` (integer)
- `reading_type` (integer: 1, 2, or 3)
- `value` (number)

For example: `{"timestamp": 1700000000, "sensor_id": 27, "reading_type": 3, "value": 980.51}`

Some records may be unrecoverable due to corruption — skip those. The file may have structural issues beyond just corrupted record data.