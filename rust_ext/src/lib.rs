//! llmesh_rust — Rust acceleration for LLMesh hot paths.
//!
//! Provides drop-in replacements for the most performance-critical
//! serialization routines:
//!
//!   * `pc_to_bytes(points)`           — PointCloud encode (3 × float32 LE)
//!   * `pc_from_bytes(data)`           — PointCloud decode
//!   * `dvs_encode(events)`            — DVS event encode (9 bytes/event LE)
//!   * `dvs_decode(data)`              — DVS event decode
//!   * `dvs_batch_stats(data, n)`      — DVS batch summary statistics
//!
//! The Python-side `PointCloud` / `event_adapter` modules import these
//! lazily and fall back to the pure-Python implementation when the Rust
//! extension is not built (developer convenience).
//!
//! Wire formats are byte-identical to the pure-Python implementation —
//! verified by property-based round-trip tests in `tests/`.

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use pyo3::exceptions::PyValueError;

// ---------------------------------------------------------------------------
// Module constants — must match Python side exactly.
// ---------------------------------------------------------------------------

const POINT_BYTES: usize = 12;            // 3 × f32 little-endian
const EVENT_BYTES: usize = 9;             // u16 + u16 + u32 + u8
const MAX_EVENTS_PER_BATCH: usize = 1_000_000;

// ---------------------------------------------------------------------------
// PointCloud encode / decode
// ---------------------------------------------------------------------------

/// Encode an iterable of (x, y, z) tuples to little-endian f32 triples.
#[pyfunction]
fn pc_to_bytes<'py>(py: Python<'py>, points: &Bound<'py, PyList>) -> PyResult<Bound<'py, PyBytes>> {
    let n = points.len();
    let mut out = vec![0u8; n * POINT_BYTES];
    for (i, item) in points.iter().enumerate() {
        let tup = item.downcast::<PyTuple>()
            .map_err(|_| PyValueError::new_err("each point must be a tuple of 3 floats"))?;
        if tup.len() != 3 {
            return Err(PyValueError::new_err("each point must have exactly 3 elements"));
        }
        let x: f32 = tup.get_item(0)?.extract()?;
        let y: f32 = tup.get_item(1)?.extract()?;
        let z: f32 = tup.get_item(2)?.extract()?;
        let off = i * POINT_BYTES;
        out[off..off + 4].copy_from_slice(&x.to_le_bytes());
        out[off + 4..off + 8].copy_from_slice(&y.to_le_bytes());
        out[off + 8..off + 12].copy_from_slice(&z.to_le_bytes());
    }
    Ok(PyBytes::new_bound(py, &out))
}

/// Decode a byte string back into a list of (x, y, z) f32 tuples.
#[pyfunction]
fn pc_from_bytes<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyList>> {
    let n = data.len() / POINT_BYTES;
    let list = PyList::empty_bound(py);
    for i in 0..n {
        let off = i * POINT_BYTES;
        let x = f32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        let y = f32::from_le_bytes(data[off + 4..off + 8].try_into().unwrap());
        let z = f32::from_le_bytes(data[off + 8..off + 12].try_into().unwrap());
        list.append((x, y, z))?;
    }
    Ok(list)
}

// ---------------------------------------------------------------------------
// DVS encode / decode
// ---------------------------------------------------------------------------

/// Encode a list of (x, y, t_us, polarity_bool) tuples to 9-byte records.
#[pyfunction]
fn dvs_encode<'py>(py: Python<'py>, events: &Bound<'py, PyList>) -> PyResult<Bound<'py, PyBytes>> {
    let n = events.len();
    let mut out = vec![0u8; n * EVENT_BYTES];
    for (i, item) in events.iter().enumerate() {
        let tup = item.downcast::<PyTuple>()
            .map_err(|_| PyValueError::new_err("event must be (x, y, t_us, polarity)"))?;
        if tup.len() != 4 {
            return Err(PyValueError::new_err("event must have 4 fields"));
        }
        let x: u16 = tup.get_item(0)?.extract()?;
        let y: u16 = tup.get_item(1)?.extract()?;
        let t_us: u32 = tup.get_item(2)?.extract()?;
        let polarity: bool = tup.get_item(3)?.extract()?;
        let off = i * EVENT_BYTES;
        out[off..off + 2].copy_from_slice(&x.to_le_bytes());
        out[off + 2..off + 4].copy_from_slice(&y.to_le_bytes());
        out[off + 4..off + 8].copy_from_slice(&t_us.to_le_bytes());
        out[off + 8] = if polarity { 1 } else { 0 };
    }
    Ok(PyBytes::new_bound(py, &out))
}

/// Decode bytes back into a list of (x, y, t_us, polarity_bool) tuples.
#[pyfunction]
fn dvs_decode<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyList>> {
    let mut n = data.len() / EVENT_BYTES;
    if n > MAX_EVENTS_PER_BATCH {
        n = MAX_EVENTS_PER_BATCH;
    }
    let list = PyList::empty_bound(py);
    for i in 0..n {
        let off = i * EVENT_BYTES;
        let x = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        let y = u16::from_le_bytes(data[off + 2..off + 4].try_into().unwrap());
        let t_us = u32::from_le_bytes(data[off + 4..off + 8].try_into().unwrap());
        // Treat any non-zero polarity byte as True (Python-side packs as int(bool))
        let polarity = data[off + 8] != 0;
        list.append((x, y, t_us, polarity))?;
    }
    Ok(list)
}

/// Compute summary stats over a DVS batch without allocating events.
#[pyfunction]
fn dvs_batch_stats<'py>(py: Python<'py>, data: &[u8], n: usize) -> PyResult<Bound<'py, PyDict>> {
    let n = std::cmp::min(n, data.len() / EVENT_BYTES);
    let mut pos: u64 = 0;
    let mut t_min: u64 = u64::MAX;
    let mut t_max: u64 = 0;
    for i in 0..n {
        let off = i * EVENT_BYTES;
        let t_us = u32::from_le_bytes(data[off + 4..off + 8].try_into().unwrap()) as u64;
        let polarity = data[off + 8] != 0;
        if t_us < t_min { t_min = t_us; }
        if t_us > t_max { t_max = t_us; }
        if polarity { pos += 1; }
    }
    if n == 0 {
        t_min = 0;
    }
    let dict = PyDict::new_bound(py);
    dict.set_item("event_count", n)?;
    dict.set_item("positive_events", pos as usize)?;
    dict.set_item("negative_events", (n as u64 - pos) as usize)?;
    dict.set_item("t_start_us", t_min)?;
    dict.set_item("t_end_us", t_max)?;
    dict.set_item("duration_us", t_max.saturating_sub(t_min))?;
    Ok(dict)
}

// ---------------------------------------------------------------------------
// Module init
// ---------------------------------------------------------------------------

#[pymodule]
fn llmesh_rust(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pc_to_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(pc_from_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(dvs_encode, m)?)?;
    m.add_function(wrap_pyfunction!(dvs_decode, m)?)?;
    m.add_function(wrap_pyfunction!(dvs_batch_stats, m)?)?;
    m.add("__version__", "0.1.0")?;
    Ok(())
}
