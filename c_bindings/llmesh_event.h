/*
 * llmesh_event.h — C ABI for SensorEvent (v1, 2026-05-07).
 *
 * Header-only library for embedding LLMesh SensorEvent generation
 * inside RTOS / microcontroller / embedded firmware.  The wire format
 * is byte-identical to what `llmesh.industrial.SensorEvent.create()`
 * produces in Python, so any LLMesh gateway can consume events from
 * either side.
 *
 * License: Apache-2.0
 *
 * Memory model: caller-supplied buffers, no malloc.
 * Thread safety: pure functions, fully reentrant.
 *
 * Tested on: Zephyr, FreeRTOS, μITRON-derived TOPPERS, NuttX, Mbed OS,
 * AUTOSAR Classic, bare-metal Cortex-M0+.
 */

#ifndef LLMESH_EVENT_H
#define LLMESH_EVENT_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* Constants — keep in sync with llmesh/industrial/sensor_event.py    */
/* ------------------------------------------------------------------ */

#define LLMESH_EVENT_MAGIC    0x4C4D4553u   /* "LMES" */
#define LLMESH_EVENT_VERSION  1u

/* Protocol identifiers — match Python protocol strings.
 * Values are stable; never renumber. */
typedef enum {
    LLMESH_PROTOCOL_UNKNOWN  = 0,
    LLMESH_PROTOCOL_MODBUS   = 1,
    LLMESH_PROTOCOL_OPCUA    = 2,
    LLMESH_PROTOCOL_MQTT     = 3,
    LLMESH_PROTOCOL_ETHERCAT = 4,
    LLMESH_PROTOCOL_CAN      = 5,
    LLMESH_PROTOCOL_BACNET   = 6,
    LLMESH_PROTOCOL_AOI      = 7,
    LLMESH_PROTOCOL_DEPTH    = 8,
    LLMESH_PROTOCOL_DVS      = 9,
    LLMESH_PROTOCOL_SERIAL   = 10,
    LLMESH_PROTOCOL_HART     = 11,
    LLMESH_PROTOCOL_DNP3     = 12,
    LLMESH_PROTOCOL_TRON     = 13,
    LLMESH_PROTOCOL_ZEPHYR   = 14,
} llmesh_protocol_t;

typedef enum {
    LLMESH_PRIORITY_NORMAL   = 0,
    LLMESH_PRIORITY_HIGH     = 1,
    LLMESH_PRIORITY_CRITICAL = 2,
} llmesh_priority_t;

/* Maximum lengths — chosen to fit comfortably in 64 KiB total event */
#define LLMESH_MAX_SENSOR_ID_LEN   128u
#define LLMESH_MAX_DEVICE_ID_LEN   128u
#define LLMESH_MAX_SENSOR_TYPE_LEN  64u
#define LLMESH_MAX_UNIT_LEN          16u
#define LLMESH_MAX_PAYLOAD_LEN    65536u

/* Header size: 44 bytes (packed, little-endian)
 * Layout: 4 + 2 + 2 + 8 + 4*5 + 1 + 7 = 44
 */
#define LLMESH_EVENT_HEADER_SIZE 44u

/* ------------------------------------------------------------------ */
/* Wire-format header                                                 */
/* ------------------------------------------------------------------ */

#if defined(_MSC_VER)
#  pragma pack(push, 1)
#  define LLMESH_PACKED
#elif defined(__GNUC__) || defined(__clang__)
#  define LLMESH_PACKED __attribute__((packed))
#else
#  define LLMESH_PACKED
#endif

typedef struct LLMESH_PACKED {
    uint32_t magic;            /* LLMESH_EVENT_MAGIC */
    uint16_t version;          /* LLMESH_EVENT_VERSION */
    uint16_t protocol_id;      /* llmesh_protocol_t */
    uint64_t timestamp_ns;     /* UNIX epoch nanoseconds, LE */
    uint32_t sensor_id_len;    /* in bytes */
    uint32_t device_id_len;
    uint32_t sensor_type_len;
    uint32_t unit_len;
    uint32_t payload_len;
    uint8_t  priority;         /* llmesh_priority_t */
    uint8_t  reserved[7];
} llmesh_event_header_t;

#if defined(_MSC_VER)
#  pragma pack(pop)
#endif

/* Compile-time guard */
typedef char llmesh_static_assert_header_size
    [(sizeof(llmesh_event_header_t) == LLMESH_EVENT_HEADER_SIZE) ? 1 : -1];

/* ------------------------------------------------------------------ */
/* Public API                                                         */
/* ------------------------------------------------------------------ */

/**
 * Pack a SensorEvent into *buffer*.  Returns total written bytes on
 * success, or 0 on failure (insufficient buffer or invalid args).
 *
 * The string fields (sensor_id, device_id, sensor_type, unit) are
 * NOT null-terminated in the wire format; pass the length explicitly.
 * Pass NULL/0 for unused string fields.
 */
static inline size_t llmesh_event_pack(
    uint8_t* buffer, size_t buffer_capacity,
    llmesh_protocol_t   protocol_id,
    uint64_t            timestamp_ns,
    const char*         sensor_id,    size_t sensor_id_len,
    const char*         device_id,    size_t device_id_len,
    const char*         sensor_type,  size_t sensor_type_len,
    const char*         unit,         size_t unit_len,
    const uint8_t*      payload,      size_t payload_len,
    llmesh_priority_t   priority
) {
    if (buffer == NULL) return 0;
    if (sensor_id_len > LLMESH_MAX_SENSOR_ID_LEN) return 0;
    if (device_id_len > LLMESH_MAX_DEVICE_ID_LEN) return 0;
    if (sensor_type_len > LLMESH_MAX_SENSOR_TYPE_LEN) return 0;
    if (unit_len > LLMESH_MAX_UNIT_LEN) return 0;
    if (payload_len > LLMESH_MAX_PAYLOAD_LEN) return 0;

    size_t total = LLMESH_EVENT_HEADER_SIZE
                 + sensor_id_len + device_id_len
                 + sensor_type_len + unit_len + payload_len;
    if (total > buffer_capacity) return 0;

    llmesh_event_header_t hdr;
    memset(&hdr, 0, sizeof(hdr));
    hdr.magic           = LLMESH_EVENT_MAGIC;
    hdr.version         = LLMESH_EVENT_VERSION;
    hdr.protocol_id     = (uint16_t)protocol_id;
    hdr.timestamp_ns    = timestamp_ns;
    hdr.sensor_id_len   = (uint32_t)sensor_id_len;
    hdr.device_id_len   = (uint32_t)device_id_len;
    hdr.sensor_type_len = (uint32_t)sensor_type_len;
    hdr.unit_len        = (uint32_t)unit_len;
    hdr.payload_len     = (uint32_t)payload_len;
    hdr.priority        = (uint8_t)priority;

    /* TODO(future): if running on a big-endian RTOS host, byte-swap
     * the integer fields here.  Most modern targets are little-endian. */

    uint8_t* p = buffer;
    memcpy(p, &hdr, sizeof(hdr)); p += sizeof(hdr);
    if (sensor_id_len)   { memcpy(p, sensor_id,   sensor_id_len);   p += sensor_id_len; }
    if (device_id_len)   { memcpy(p, device_id,   device_id_len);   p += device_id_len; }
    if (sensor_type_len) { memcpy(p, sensor_type, sensor_type_len); p += sensor_type_len; }
    if (unit_len)        { memcpy(p, unit,        unit_len);        p += unit_len; }
    if (payload_len)     { memcpy(p, payload,     payload_len);     p += payload_len; }

    return total;
}

/**
 * Validate a packed event header.  Returns 1 if magic / version match,
 * 0 otherwise.  Does NOT validate the field lengths against the buffer.
 */
static inline int llmesh_event_validate_header(const uint8_t* buffer, size_t len)
{
    if (buffer == NULL || len < LLMESH_EVENT_HEADER_SIZE) return 0;
    llmesh_event_header_t hdr;
    memcpy(&hdr, buffer, sizeof(hdr));
    if (hdr.magic != LLMESH_EVENT_MAGIC) return 0;
    if (hdr.version != LLMESH_EVENT_VERSION) return 0;
    return 1;
}

#ifdef __cplusplus
}  /* extern "C" */
#endif

#endif  /* LLMESH_EVENT_H */
