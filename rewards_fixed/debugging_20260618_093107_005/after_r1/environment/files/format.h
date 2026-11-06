/* Sensor Log Binary Format - v1.0
 *
 * This file describes the binary format used by the SensorLog data acquisition
 * system. The system writes fixed-size records to a binary log file.
 *
 * Record Structure (16 bytes per record):
 *   Offset  Size  C Type    Description
 *     0      4    uint32_t  Unix timestamp
 *     4      2    uint16_t  Sensor identifier (valid range: 1-50)
 *     6      2    uint16_t  Reading type: 1=temperature(C), 2=humidity(%), 3=pressure(hPa)
 *     8      4    float     Measurement value (IEEE 754 single-precision)
 *    12      4    uint32_t  CRC-32 checksum of bytes 0-11
 *
 * CRC-32 uses the standard IEEE 802.3 polynomial (0xEDB88320 / reflected form).
 * The checksum is computed over the first 12 bytes of the record.
 *
 * Byte Order:
 *   All multi-byte integer and float fields are stored in the native byte order
 *   of the recording device. Most devices use little-endian (x86), but some
 *   older ARM-based devices use big-endian. The checksum field is always
 *   stored in little-endian byte order regardless of the device.
 *
 * Valid Ranges (for data validation):
 *   - Timestamp:    roughly 1.6e9 to 1.8e9 (Unix epoch seconds, circa 2023)
 *   - Sensor ID:    1 to 50
 *   - Reading type: 1, 2, or 3
 *   - Temperature:  10.0 to 40.0 (degrees Celsius)
 *   - Humidity:     20.0 to 100.0 (percent)
 *   - Pressure:     900.0 to 1100.0 (hPa)
 */

#pragma pack(push, 1)
typedef struct {
    uint32_t timestamp;      /* Unix timestamp */
    uint16_t sensor_id;      /* Sensor identifier (1-50) */
    uint16_t reading_type;   /* 1=temp, 2=humidity, 3=pressure */
    float    value;          /* Measurement value */
    uint32_t checksum;       /* CRC-32 of bytes 0-11 (little-endian) */
} sensor_record_t;
#pragma pack(pop)